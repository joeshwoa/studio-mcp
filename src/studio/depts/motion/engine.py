"""Deterministic HTML → video renderer.

Headless Chromium (Playwright) loads a page with a *virtual clock* injected before any page script:
performance.now / Date / requestAnimationFrame / setTimeout / setInterval are all driven by us, and
every Web Animation / CSS animation / GSAP timeline / <video> element is seeked to the exact frame
time before each screenshot. Nothing is captured in real time, so a slow machine produces the same
frames as a fast one (no dropped frames, no jitter).

Frames → PNG sequence → ffmpeg: MP4 (H.264), WebM (VP9 with alpha), MOV (ProRes 4444 with alpha /
ProRes 422 HQ), GIF, or the PNG sequence itself.
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import glob
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ...core import deps
from ...core.result import MissingTool, ToolError

# ------------------------------------------------------------------------------------------------
# Virtual clock (injected with add_init_script, runs before every page script)
# ------------------------------------------------------------------------------------------------
VCLOCK_JS = r"""
(() => {
  if (window.__vt) return;
  const EPOCH = 1735689600000;             // fixed wall clock: 2025-01-01T00:00:00Z
  // deterministic Math.random (mulberry32): particles, glitches and scrambles look the same on every render
  let __seed = (window.__seed >>> 0) || 0x5EED1234;
  Math.random = () => { __seed = (__seed + 0x6D2B79F5) >>> 0; let t = __seed; t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  let now = 0;                              // virtual ms since page start
  const realRAF = window.requestAnimationFrame.bind(window);
  const RealDate = Date;
  const raf = new Map(); let rafId = 1;
  const timers = new Map(); let timerId = 1;
  performance.now = () => now;
  class VDate extends RealDate {
    constructor(...a) { if (a.length === 0) super(EPOCH + now); else super(...a); }
    static now() { return EPOCH + now; }
  }
  window.Date = VDate;
  window.requestAnimationFrame = (cb) => { const id = rafId++; raf.set(id, cb); return id; };
  window.cancelAnimationFrame = (id) => raf.delete(id);
  window.setTimeout = (cb, ms = 0, ...args) => { const id = timerId++;
    timers.set(id, {cb, at: now + Math.max(0, +ms || 0), args, every: 0}); return id; };
  window.setInterval = (cb, ms = 0, ...args) => { const id = timerId++;
    const every = Math.max(1, +ms || 1); timers.set(id, {cb, at: now + every, args, every}); return id; };
  window.clearTimeout = window.clearInterval = (id) => timers.delete(id);
  const seen = new WeakMap();
  const origAnimate = Element.prototype.animate;
  Element.prototype.animate = function (...a) { const an = origAnimate.apply(this, a); seen.set(an, now); an.pause(); return an; };
  function fireTimers(until) {
    for (let guard = 0; guard < 100000; guard++) {
      let best = null, bid = 0;
      for (const [id, t] of timers) if (t.at <= until && (!best || t.at < best.at)) { best = t; bid = id; }
      if (!best) break;
      now = Math.max(now, best.at);
      if (best.every) best.at += best.every; else timers.delete(bid);
      try { typeof best.cb === 'function' ? best.cb(...best.args) : eval(best.cb); } catch (e) { console.error(e); }
    }
  }
  async function seek(ms) {
    if (ms < now) { now = ms; } else { fireTimers(ms); now = ms; }
    // rAF callbacks (canvas, GSAP ticker, custom loops): run once per frame at the new time
    const cbs = [...raf.values()]; raf.clear();
    for (const cb of cbs) { try { cb(now); } catch (e) { console.error(e); } }
    if (window.gsap) { try { gsap.ticker.lagSmoothing(0); gsap.globalTimeline.time(now / 1000, false); } catch (e) {} }
    for (const a of document.getAnimations()) {
      if (!seen.has(a)) seen.set(a, a instanceof CSSAnimation || a instanceof CSSTransition ? Math.max(0, now - 1) : 0);
      try { a.pause(); a.currentTime = Math.max(0, now - seen.get(a)); } catch (e) {}
    }
    // Lottie (lottie-web): every registered animation is stepped to the exact frame. Per-animation timing
    // can be set on anim.__studio = {start (s), speed, loop (true|false|count), hold}; defaults: start 0.
    if (window.lottie && lottie.getRegisteredAnimations) {
      for (const an of lottie.getRegisteredAnimations()) {
        try {
          const o = an.__studio || {}; if (o.manual) continue;
          const fr = an.frameRate || 30, total = Math.max(1, an.totalFrames - 0.001);
          let f = (now / 1000 - (o.start || 0)) * fr * (o.speed != null ? o.speed : (an.playSpeed || 1));
          const loop = o.loop != null ? o.loop : an.loop;
          if (f < 0) f = 0;
          else if (loop === true || (typeof loop === 'number' && f < total * loop)) f = f % total;
          else f = Math.min(f, total);
          an.goToAndStop(f, true);
        } catch (e) { console.error(e); }
      }
    }
    // <video>: data-start (s, when the clip begins on the page timeline), data-rate, data-loop
    const vids = [...document.querySelectorAll('video')];
    await Promise.all(vids.map(v => new Promise(res => {
      try { v.pause(); const st = parseFloat(v.dataset.start || '0') || 0, rate = parseFloat(v.dataset.rate || '1') || 1;
        const dur = v.duration || 1e9; let tt = Math.max(0, (now / 1000 - st) * rate + (parseFloat(v.dataset.offset || '0') || 0));
        if (v.dataset.loop === 'true' || v.loop) tt = tt % Math.max(0.001, dur - 0.001);
        tt = Math.min(tt, dur - 0.001);
        if (Math.abs(v.currentTime - tt) < 1e-4) return res(); v.addEventListener('seeked', () => res(), {once: true});
        v.currentTime = tt; setTimeoutReal(res, 3000); } catch (e) { res(); } })));
    if (typeof window.__onseek === 'function') { try { await window.__onseek(now / 1000); } catch (e) { console.error(e); } }
    await new Promise(r => realRAF(() => r()));
    return now;
  }
  const setTimeoutReal = (fn, ms) => { const t0 = RealDate.now(); const loop = () => (RealDate.now() - t0 >= ms ? fn() : realRAF(loop)); realRAF(loop); };
  function naturalDuration() {
    let end = 0;
    for (const a of document.getAnimations()) {
      try { const ct = a.effect.getComputedTiming(); if (isFinite(ct.endTime)) end = Math.max(end, (seen.get(a) || 0) + ct.endTime); } catch (e) {}
    }
    if (window.gsap) { try { end = Math.max(end, gsap.globalTimeline.duration() * 1000); } catch (e) {} }
    return end / 1000;
  }
  window.__vt = { seek, naturalDuration, get now() { return now; } };
})();
"""


# ------------------------------------------------------------------------------------------------
# Browser
# ------------------------------------------------------------------------------------------------
ARGS = ["--disable-gpu-vsync", "--font-render-hinting=none", "--hide-scrollbars", "--mute-audio",
        "--autoplay-policy=no-user-gesture-required", "--allow-file-access-from-files",
        "--disable-background-timer-throttling", "--disable-renderer-backgrounding"]


def _candidates() -> list[str]:
    try:  # the design department keeps the most complete list (macOS app bundles, headless shells)
        from ..design.engine import _candidates as dc
        return dc()
    except Exception:
        return []


async def _launch(p):
    try:
        return await p.chromium.launch(args=ARGS)
    except Exception as first:
        for exe in _candidates():
            try:
                return await p.chromium.launch(executable_path=exe, args=ARGS)
            except Exception:
                continue
        raise MissingTool(f"Chromium for Playwright is not installed ({str(first).splitlines()[0][:160]})",
                          "python -m playwright install chromium   (or: studio install core)")


def _run_async(coro_fn):
    """Run an async job to completion whether or not an event loop is already running (MCP server)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(coro_fn())).result()


def default_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2 or 1))


def render_frames(html: Path, out_dir: Path, width: int, height: int, fps: float, duration: float | None,
                  alpha: bool, max_seconds: float = 180.0, scale: float = 1.0, wait_ms: int = 0,
                  workers: int = 0, start: float = 0.0, motion_blur: int = 0, shutter: float = 180.0,
                  post: dict | None = None, times: list[float] | None = None) -> dict:
    """Render every frame of a local HTML page to out_dir/f_000001.png. Returns
    {frames, duration, console, grain} — duration resolved from arg → window.__duration → natural length.

    workers > 1 splits the frame range into contiguous chunks rendered by parallel pages; only safe for
    pages whose look is a pure function of time (all studio templates). Stateful rAF loops → workers=1.

    motion_blur = N > 1 renders N sub-frames spread over the shutter (degrees, 180 = film look, centred
    on the frame time) and averages them — true temporal motion blur like After Effects' "samples per
    frame". Costs N× render time. post = {glow, vignette, chroma} frame post-effects (see post_frame).
    times = [seconds, …] renders only those instants (fast stills for previews/iteration)."""
    deps.need("chromium")
    out_dir.mkdir(parents=True, exist_ok=True)
    workers = workers or default_workers()
    logs: list[str] = []

    async def open_page(browser):
        ctx = await browser.new_context(viewport={"width": int(width), "height": int(height)}, device_scale_factor=scale)
        await ctx.add_init_script(VCLOCK_JS)
        page = await ctx.new_page()
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}")
                if m.type in ("error", "warning") and "GL Driver Message" not in m.text and "GPU stall" not in m.text else None)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        await page.goto(html.resolve().as_uri(), wait_until="load", timeout=60000)
        try:
            await page.evaluate("async () => { await document.fonts.ready; if (window.__ready) await window.__ready; }")
        except Exception as e:
            msg = str(e).split("\n")[0][:400]
            raise ToolError(f"the page failed while setting up: {msg}",
                            "page console: " + " | ".join(logs[-6:]) if logs else "check the page's JavaScript")
        if wait_ms:
            await page.wait_for_timeout(wait_ms)
        cdp = await ctx.new_cdp_session(page)
        if alpha:
            await cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        return ctx, page, cdp

    samples = max(1, int(motion_blur or 1))
    post = {k: v for k, v in (post or {}).items() if v}

    async def grab(page, cdp, ms: float) -> bytes:
        await page.evaluate("(ms) => window.__vt.seek(ms)", ms)
        d = await cdp.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True,
                                                       "fromSurface": True, "captureBeyondViewport": False})
        return base64.b64decode(d["data"])

    async def shoot(page, cdp, i: int, at_ms: float | None = None) -> Path:
        t0 = start * 1000.0 + i * 1000.0 / fps if at_ms is None else at_ms
        f = out_dir / f"f_{i + 1:06d}.png"
        if samples == 1:
            raw = await grab(page, cdp, t0)
            if not post:
                f.write_bytes(raw)
                return f
            arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGBA"), np.float32)
        else:
            span = (1000.0 / fps) * max(1.0, min(360.0, shutter)) / 360.0
            acc = None
            for s_ in range(samples):
                ms = max(0.0, t0 + span * ((s_ + 0.5) / samples - 0.5))
                a = np.asarray(Image.open(io.BytesIO(await grab(page, cdp, ms))).convert("RGBA"), np.float32)
                if a.shape[2] == 4:  # premultiply so transparent sub-frames don't darken edges
                    a[..., :3] *= a[..., 3:4] / 255.0
                acc = a if acc is None else acc + a
            arr = acc / samples
            al = np.maximum(arr[..., 3:4], 1e-3)
            arr[..., :3] = np.where(arr[..., 3:4] > 0, arr[..., :3] * 255.0 / al, 0)
            await page.evaluate("(ms) => window.__vt.seek(ms)", t0)  # leave the page at the frame time
        if post:
            arr = post_frame(arr, post, alpha)
        Image.fromarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8), "RGBA").save(f, compress_level=1)
        return f

    async def job():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await _launch(p)
            try:
                ctx, page, cdp = await open_page(b)
                await page.evaluate("() => window.__vt.seek(0)")
                dur = duration
                if not dur:
                    dur = await page.evaluate("() => (window.__duration || window.__vt.naturalDuration() || 0)")
                if not dur or dur <= 0:
                    raise ToolError("could not tell how long the animation is",
                                    "pass duration=<seconds>, or set window.__duration in the page")
                dur = min(float(dur), max_seconds)
                grain = bool(await page.evaluate("() => !!window.__grain"))
                n = max(1, int(round(dur * fps))) if not times else len(times)
                k = max(1, min(workers, n // 20 or 1))
                chunks = [range(j * n // k, (j + 1) * n // k) for j in range(k)]
                pages = [(page, cdp)] + [await open_page(b) for _ in range(k - 1)]

                async def run(pg, cd, idx):
                    if times:
                        return [await shoot(pg, cd, i, times[i] * 1000.0) for i in idx]
                    return [await shoot(pg, cd, i) for i in idx]
                res = await asyncio.gather(*[run(pc[-2], pc[-1], ch) for pc, ch in zip(pages, chunks)])
                frames = [f for r in res for f in r]
                return {"frames": frames, "duration": dur, "console": logs[:30], "grain": grain}
            finally:
                await b.close()
    return _run_async(job)


def post_frame(a: np.ndarray, post: dict, alpha: bool = False) -> np.ndarray:
    """Frame post-effects on an RGBA float array (0–255):
    glow (0–1)   bloom: bright areas bleed soft light (threshold + wide blur, screen-added)
    vignette (0–1) darkened corners (opaque frames only)
    chroma (px)  chromatic aberration: red/blue channels offset outward (lens fringe)."""
    from PIL import ImageFilter
    h, w = a.shape[:2]
    rgb = a[..., :3]
    g = float(post.get("glow") or 0)
    if g > 0:
        lum = rgb.mean(axis=2, keepdims=True)
        bright = np.clip((lum - 150.0) / 105.0, 0, 1) * rgb
        small = Image.fromarray(bright.astype(np.uint8)).resize((max(1, w // 4), max(1, h // 4)), Image.BILINEAR)
        blur = np.zeros_like(rgb)
        for r_ in (4, 12):
            blur += np.asarray(small.filter(ImageFilter.GaussianBlur(r_ * max(w, h) / 1920)).resize((w, h), Image.BILINEAR), np.float32)
        add = blur * 0.6 * g
        rgb = 255.0 - (255.0 - rgb) * (255.0 - np.clip(add, 0, 255)) / 255.0  # screen
        if alpha:
            a[..., 3] = np.maximum(a[..., 3], np.clip(add.max(axis=2), 0, 255))
    c = float(post.get("chroma") or 0)
    if c > 0:
        k = max(1, int(round(c * w / 1920)))
        rgb = rgb.copy()
        rgb[:, k:, 0] = rgb[:, :-k, 0].copy()
        rgb[:, :-k, 2] = rgb[:, k:, 2].copy()
    v = float(post.get("vignette") or 0)
    if v > 0 and not alpha:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / 1.4142
        rgb = rgb * (1 - v * np.clip((d - 0.35) / 0.65, 0, 1) ** 1.6)[..., None]
    a[..., :3] = rgb
    return a


# ------------------------------------------------------------------------------------------------
# Encoding
# ------------------------------------------------------------------------------------------------
FORMATS = ("mp4", "webm", "mov", "gif", "png")


def encode(frames_dir: Path, fps: float, fmt: str, out: Path, alpha: bool, background: str = "#000000",
           crf: int = 16, grain: float = 0.0) -> Path:
    """grain > 0 adds a static film-grain texture at encode time (opaque outputs only) — far cheaper than
    rendering noise in the browser, and it hides gradient banding."""
    ff = deps.need("ffmpeg")
    pat = str(frames_dir / "f_%06d.png")
    base = [ff, "-y", "-v", "error", "-framerate", f"{fps}", "-i", pat]
    gr = f"noise=alls={int(grain)}," if grain and not alpha else ""  # static pattern: hides banding, compresses well
    if fmt == "mp4":
        if alpha:  # flatten over the background colour
            vf = f"color=c={background}:s=2x2:r={fps}[bg];[bg][0:v]scale2ref[bg2][fg];[bg2][fg]overlay=shortest=1:format=auto,format=yuv420p"
            cmd = base + ["-filter_complex", vf]
        else:
            cmd = base + ["-vf", gr + "format=yuv420p"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-movflags", "+faststart", str(out)]
    elif fmt == "webm":
        cmd = base + (["-vf", gr.rstrip(",")] if gr else []) + ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p" if alpha else "yuv420p", "-b:v", "0", "-crf", "24",
                      "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-auto-alt-ref", "0", str(out)]
    elif fmt == "mov":
        if alpha:
            cmd = base + ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                          "-alpha_bits", "16", "-vendor", "apl0", str(out)]
        else:
            cmd = base + (["-vf", gr.rstrip(",")] if gr else []) + ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0", str(out)]
    elif fmt == "gif":
        w = min(720, Image.open(frames_dir / "f_000001.png").width)
        g_fps = min(fps, 20)
        vf = (f"fps={g_fps},scale={w}:-2:flags=lanczos,split[a][b];[a]palettegen=reserve_transparent={1 if alpha else 0}"
              f":stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:alpha_threshold=128")
        cmd = base + ["-filter_complex", vf, "-loop", "0", str(out)]
    elif fmt == "png":
        out.mkdir(parents=True, exist_ok=True)
        for f in sorted(frames_dir.glob("f_*.png")):
            shutil.copy2(f, out / f.name)
        return out
    else:
        raise ToolError(f"unknown format {fmt!r}", f"one of {FORMATS}")
    deps.run(cmd, timeout=1800)
    return out


def _checker(size: tuple[int, int], cell: int = 16) -> Image.Image:
    im = Image.new("RGBA", size, (58, 60, 66, 255))
    d = ImageDraw.Draw(im)
    for y in range(0, size[1], cell):
        for x in range((y // cell) % 2 * cell, size[0], cell * 2):
            d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(78, 80, 88, 255))
    return im


def timecode(t: float, fps: float) -> str:
    fr = int(round(t * fps))
    f_ = int(round(fps))
    return f"{fr // f_ // 60:02d}:{fr // f_ % 60:02d}:{fr % f_:02d}"


def frames_sheet(frames: list[Path], dest: Path, alpha: bool, cols: int = 4, rows: int = 3, tile_w: int = 400,
                 backdrop: str | None = None, fps: float | None = None, times: list[float] | None = None,
                 labels: list[str] | None = None) -> Path:
    """Contact sheet straight from the PNG frames (alpha shown over a dark checkerboard or a backdrop image).
    With fps (or explicit times) every tile is labelled with its timecode (mm:ss:ff) and seconds."""
    from PIL import ImageFont
    n = min(cols * rows, len(frames)) if times else cols * rows
    if times:
        idx = list(range(len(frames)))[:n]
        rows = (n + cols - 1) // cols
    else:
        idx = [min(len(frames) - 1, round(i * (len(frames) - 1) / max(1, n - 1))) for i in range(n)]
    first = Image.open(frames[0])
    tw = tile_w
    th = max(1, round(first.height * tw / first.width))
    sheet = Image.new("RGBA", (cols * (tw + 8) + 8, rows * (th + 8) + 8), (24, 24, 27, 255))
    bd = None
    if backdrop:
        try:
            bd = Image.open(backdrop).convert("RGBA").resize((tw, th))
        except Exception:
            bd = None
    for k, i in enumerate(idx):
        im = Image.open(frames[i]).convert("RGBA").resize((tw, th), Image.LANCZOS)
        if alpha:
            base = bd.copy() if bd is not None else _checker((tw, th), 10)
            base.alpha_composite(im)
            im = base
        x0, y0 = 8 + (k % cols) * (tw + 8), 8 + (k // cols) * (th + 8)
        sheet.alpha_composite(im, (x0, y0))
        if fps or times or labels:
            t = times[k] if times else i / fps
            lab = (labels[k] + "  " if labels and k < len(labels) else "") + (timecode(t, fps or 30) + f"  {t:.2f}s")
            d = ImageDraw.Draw(sheet)
            try:
                font = ImageFont.load_default(size=max(11, tw // 26))
            except TypeError:
                font = ImageFont.load_default()
            bb = d.textbbox((0, 0), lab, font=font)
            d.rectangle([x0, y0 + th - (bb[3] - bb[1]) - 10, x0 + bb[2] - bb[0] + 12, y0 + th], fill=(0, 0, 0, 170))
            d.text((x0 + 6, y0 + th - (bb[3] - bb[1]) - 7), lab, fill=(255, 255, 255, 255), font=font)
    sheet.convert("RGB").save(dest)
    return dest


def frame_stats(frames: list[Path], alpha: bool) -> dict:
    """Cheap QC over a sample of frames: blank frames, alpha coverage, motion presence."""
    sample = frames[:: max(1, len(frames) // 24)]
    cov, diffs, prev = [], [], None
    blank = 0
    for f in sample:
        im = Image.open(f)
        im.thumbnail((256, 256))
        a = np.asarray(im.convert("RGBA")).astype(np.float32)
        if alpha:
            c = float((a[..., 3] > 8).mean())
            cov.append(c)
            if c < 0.001:
                blank += 1
        else:
            if a[..., :3].std() < 1.0:
                blank += 1
        if prev is not None and prev.shape == a.shape:
            diffs.append(float(np.abs(a - prev).mean()))
        prev = a
    return {"sampled": len(sample), "blank_frames": blank,
            "alpha_coverage_max": round(max(cov), 3) if cov else None,
            "motion": round(float(np.mean(diffs)), 3) if diffs else 0.0}


def scratch_dir(prefix: str = "motion-") -> Path:
    from ...config import cache_dir
    d = cache_dir() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=d))
