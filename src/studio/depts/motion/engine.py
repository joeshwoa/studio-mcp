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
    const vids = [...document.querySelectorAll('video')];
    await Promise.all(vids.map(v => new Promise(res => {
      try { v.pause(); const tt = Math.min(now / 1000, (v.duration || 1e9) - 0.001);
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
                  workers: int = 0, start: float = 0.0) -> dict:
    """Render every frame of a local HTML page to out_dir/f_000001.png. Returns
    {frames, duration, console, grain} — duration resolved from arg → window.__duration → natural length.

    workers > 1 splits the frame range into contiguous chunks rendered by parallel pages; only safe for
    pages whose look is a pure function of time (all studio templates). Stateful rAF loops → workers=1."""
    deps.need("chromium")
    out_dir.mkdir(parents=True, exist_ok=True)
    workers = workers or default_workers()
    logs: list[str] = []

    async def open_page(browser):
        ctx = await browser.new_context(viewport={"width": int(width), "height": int(height)}, device_scale_factor=scale)
        await ctx.add_init_script(VCLOCK_JS)
        page = await ctx.new_page()
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        await page.goto(html.resolve().as_uri(), wait_until="load", timeout=60000)
        await page.evaluate("async () => { await document.fonts.ready; if (window.__ready) await window.__ready; }")
        if wait_ms:
            await page.wait_for_timeout(wait_ms)
        cdp = await ctx.new_cdp_session(page)
        if alpha:
            await cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        return ctx, page, cdp

    async def shoot(page, cdp, i: int) -> Path:
        await page.evaluate("(ms) => window.__vt.seek(ms)", start * 1000.0 + i * 1000.0 / fps)
        d = await cdp.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True,
                                                       "fromSurface": True, "captureBeyondViewport": False})
        f = out_dir / f"f_{i + 1:06d}.png"
        f.write_bytes(base64.b64decode(d["data"]))
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
                n = max(1, int(round(dur * fps)))
                k = max(1, min(workers, n // 20 or 1))
                chunks = [range(j * n // k, (j + 1) * n // k) for j in range(k)]
                pages = [(page, cdp)] + [await open_page(b) for _ in range(k - 1)]

                async def run(pg, cd, idx):
                    return [await shoot(pg, cd, i) for i in idx]
                res = await asyncio.gather(*[run(pc[-2], pc[-1], ch) for pc, ch in zip(pages, chunks)])
                frames = [f for r in res for f in r]
                return {"frames": frames, "duration": dur, "console": logs[:30], "grain": grain}
            finally:
                await b.close()
    return _run_async(job)


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


def frames_sheet(frames: list[Path], dest: Path, alpha: bool, cols: int = 4, rows: int = 3, tile_w: int = 400,
                 backdrop: str | None = None) -> Path:
    """Contact sheet straight from the PNG frames (alpha shown over a dark checkerboard or a backdrop image)."""
    n = cols * rows
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
        sheet.alpha_composite(im, (8 + (k % cols) * (tw + 8), 8 + (k // cols) * (th + 8)))
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
