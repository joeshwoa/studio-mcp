"""Motion department (After Effects replacement): deterministic motion graphics.

HTML/CSS/Web-Animations templates rendered frame-by-frame by headless Chromium with a virtual clock
(see engine.py) → PNG sequence → MP4 / WebM-alpha / ProRes 4444 / GIF. Every template is brand-aware
(colors, Google Fonts with an Arabic companion face), RTL-aware (Arabic text keeps its joined
letterforms: animations split Arabic by word, never by letter) and scales to any frame size
(16:9, 9:16, 1:1, 4:5, 4K)."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ...config import output_dir, unique_path, slug
from ...core import qc
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import engine

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "motion"

SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
         "4k": (3840, 2160), "720p": (1280, 720), "1080p": (1920, 1080), "reels": (1080, 1920),
         "shorts": (1080, 1920), "tiktok": (1080, 1920), "square": (1080, 1080), "portrait": (1080, 1350)}

DEFAULT_FONTS = {"head": "Montserrat", "body": "Inter", "head_ar": "Cairo", "body_ar": "Cairo"}
STYLE_FONTS = {  # pairing id (design dept) → used when fonts="<id>"
}


def parse_size(size: str) -> tuple[int, int]:
    s = (size or "1920x1080").strip().lower()
    if s in SIZES:
        return SIZES[s]
    m = re.match(r"^(\d{2,5})\s*[x×:]\s*(\d{2,5})$", s)
    if not m:
        raise ToolError(f"bad size {size!r}", "use WIDTHxHEIGHT (1920x1080) or one of " + ", ".join(SIZES))
    w, h = int(m.group(1)), int(m.group(2))
    return w - w % 2, h - h % 2


def _resolve_fonts(fonts) -> dict:
    """fonts: {} → defaults; dict with head/body/head_ar/body_ar; or a design pairing id / mood words."""
    f = dict(DEFAULT_FONTS)
    if isinstance(fonts, str) and fonts.strip():
        try:
            from ..design import fonts as dfonts
            pr = next((x for x in dfonts.PAIRS if x["id"] == fonts.strip()), None) or dfonts.pair_for(fonts)
            f.update({k: pr[k] for k in ("head", "body", "head_ar", "body_ar") if pr.get(k)})
        except Exception:
            f["head"] = fonts.strip()
    elif isinstance(fonts, dict):
        f.update({k: v for k, v in fonts.items() if v and k in ("head", "body", "head_ar", "body_ar")})
    return f


def brand_theme(brand: str) -> dict:
    """Brand kit (slug/name/path) → {colors, fonts, logo_svg, logo_png, icon_svg, name, name_ar, tagline, kit}.
    Motion graphics run on a DARK stage: background = the brand's dark colour, text = paper."""
    from ..design.builder import load_brand, brand_file
    from ..design import color as col
    kit = load_brand(brand)
    c = kit.get("colors", {})
    paper, ink = c.get("paper", "#FFFFFF"), c.get("ink", "#111111")
    primary, accent, secondary = c.get("primary", "#FF5A36"), c.get("accent", "#FFC53D"), c.get("secondary", ink)
    bg = secondary if col.luminance(secondary) < 0.12 else ink
    text = paper if col.contrast(paper, bg) >= 7 else "#FFFFFF"
    prim = primary if col.contrast(primary, bg) >= 3 else col.ensure_contrast(primary, bg, 3.2)
    acc = accent if col.contrast(accent, bg) >= 3 else col.ensure_contrast(accent, bg, 3.2)
    colors = {"primary": prim, "accent": acc, "background": bg, "text": text,
              "muted": col.mix(text, bg, 0.3), "panel": col.mix(bg, "#FFFFFF", 0.07),
              "on_primary": col.text_on(prim, paper, ink)}
    kf = kit.get("fonts", {})
    fonts = {k: kf[k] for k in ("head", "body", "head_ar", "body_ar") if kf.get(k)}

    def f(*keys):
        for k in keys:
            p = brand_file(kit, k)
            if p:
                return str(p)
        return ""
    return {"colors": colors, "fonts": fonts, "kit": kit, "name": kit.get("name", ""), "name_ar": kit.get("name_ar", ""),
            "tagline": kit.get("tagline", ""), "tagline_ar": kit.get("tagline_ar", ""),
            "logo_svg": f("horizontal_reversed", "horizontal_mono_white", "horizontal", "stacked_reversed", "stacked"),
            "icon_svg": f("icon_reversed", "icon", "app_icon"),
            "stacked_svg": f("stacked_reversed", "stacked", "horizontal_reversed", "horizontal")}


def apply_brand(brand: str, colors: dict, fonts, warnings: list[str]) -> tuple[dict, object, dict]:
    """Merge a brand kit under explicit colors/fonts (explicit args win). Returns (colors, fonts, theme)."""
    if not brand:
        return dict(colors or {}), fonts, {}
    th = brand_theme(brand)
    cols = {**th["colors"], **(colors or {})}
    fnt = fonts if (fonts and (isinstance(fonts, str) or any(fonts.values()))) else th["fonts"]
    return cols, fnt, th


def font_css(fonts: dict, warnings: list[str]) -> str:
    """@font-face CSS for roles sf-head / sf-body (Latin face + Arabic face on Arabic code points)."""
    try:
        from ..design import fonts as dfonts
    except Exception as e:  # design dept unavailable → system fonts
        warnings.append(f"font helper unavailable ({e}); using system fonts")
        return ":root{--font-head:system-ui;--font-body:system-ui}"
    css = []
    for role, lat, ar, weights in (("sf-head", fonts["head"], fonts.get("head_ar"), [300, 500, 600, 700, 800, 900]),
                                   ("sf-body", fonts["body"], fonts.get("body_ar"), [400, 500, 600, 700])):
        try:
            css.append(dfonts.role_css(role, lat, ar, weights, lambda p: Path(p).resolve().as_uri()))
        except Exception as e:
            warnings.append(f"font {lat}/{ar} could not be loaded ({e}); falling back to system fonts")
    css.append(":root{--font-head:'sf-head';--font-body:'sf-body'}")
    return "\n".join(css)


# Template scripts measure text (fit-to-width, bar lengths), so they must run AFTER the web fonts are
# loaded — otherwise they measure the fallback font. They are stored as text/x-template and run by the
# gate below; the renderer awaits window.__ready before the first frame.
FONT_GATE = r"""
window.__ready = (async () => {
  const sample = 'Abc 123 أبجد';
  const loads = [];
  for (const fam of ['sf-head', 'sf-body']) for (const w of [300, 400, 500, 600, 700, 800, 900])
    loads.push(document.fonts.load(`${w} 40px "${fam}"`, sample).catch(() => null));
  await Promise.all(loads); await document.fonts.ready;
  for (const s of document.querySelectorAll('script[type="text/x-template"]')) (0, eval)(s.textContent);
})();
"""


def _deferred(tpl_html: str) -> str:
    return re.sub(r"<script>", '<script type="text/x-template">', tpl_html)


def build_page(template: str, params: dict, fonts, work: Path, warnings: list[str], extra_head: str = "") -> Path:
    tpl = ASSETS / f"{template}.html"
    if not tpl.exists():
        raise ToolError(f"unknown motion template {template!r}")
    fcss = font_css(_resolve_fonts(fonts), warnings)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>{fcss}</style>
<style>{(ASSETS / 'kit.css').read_text(encoding='utf-8')}</style>
{extra_head}
</head><body>
<div id="stage"></div>
<script>window.PARAMS = {json.dumps(params, ensure_ascii=False)};</script>
<script>{(ASSETS / 'kit.js').read_text(encoding='utf-8')}</script>
{_deferred(tpl.read_text(encoding='utf-8'))}
<script>{FONT_GATE}</script>
</body></html>"""
    page = work / f"{template}.html"
    page.write_text(html, encoding="utf-8")
    return page


def _formats(formats, transparent: bool) -> list[str]:
    if isinstance(formats, str):
        formats = [x.strip() for x in formats.split(",") if x.strip()]
    fl = [f.lower().lstrip(".") for f in (formats or [])]
    if not fl:
        fl = ["mov", "webm"] if transparent else ["mp4"]
    for f in fl:
        if f not in engine.FORMATS:
            raise ToolError(f"unknown format {f!r}", f"use any of {engine.FORMATS}")
    return fl


def _out_target(out: str, project: str, name: str) -> tuple[Path, str]:
    if out:
        op = Path(out).expanduser()
        if op.suffix:
            op.parent.mkdir(parents=True, exist_ok=True)
            return op.parent, op.stem
        op.mkdir(parents=True, exist_ok=True)
        return op, name
    return output_dir(project, "motion"), name


def render_page(page: Path, *, name: str, width: int, height: int, fps: float, duration: float | None,
                transparent: bool, formats, project: str, out: str, warnings: list[str],
                summary: str, data: dict | None = None, background: str = "#000000", keep_html: bool = True,
                backdrop: str | None = None, workers: int = 0, grain: float | None = None, motion_blur: int = 0,
                shutter: float = 180.0, post: dict | None = None, keyframes: int = 0, extra_files: list[str] | None = None) -> Result:
    work = engine.scratch_dir()
    try:
        r = engine.render_frames(page, work / "frames", width, height, fps, duration, transparent, workers=workers,
                                 motion_blur=motion_blur, shutter=shutter, post=post)
        return deliver_frames(r, work / "frames", page=page if keep_html else None, name=name, width=width, height=height,
                              fps=fps, transparent=transparent, formats=formats, project=project, out=out, warnings=warnings,
                              summary=summary, data=data, background=background, backdrop=backdrop, grain=grain,
                              keyframes=keyframes, extra_files=extra_files)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _unique_dir(parent: Path, stem: str) -> Path:
    """parent/stem, parent/stem-2 … (config.unique_path would append a bare '.' for an empty extension)."""
    p, n = parent / stem, 2
    while p.exists():
        p, n = parent / f"{stem}-{n}", n + 1
    return p


def deliver_frames(r: dict, frames_dir: Path, *, page: Path | None, name: str, width: int, height: int, fps: float,
                   transparent: bool, formats, project: str, out: str, warnings: list[str], summary: str,
                   data: dict | None = None, background: str = "#000000", backdrop: str | None = None,
                   grain: float | None = None, keyframes: int = 0, extra_files: list[str] | None = None) -> Result:
    """Encode a rendered PNG sequence (r = {frames, duration, console, grain}) into every format, make the
    timecoded contact sheet + key frames, measure it, and build the Result (shared by every engine)."""
    keep_html = page is not None
    fmts = _formats(formats, transparent)
    if True:
        frames = r["frames"]
        odir, stem = _out_target(out, project, name)
        g = (5.0 if r.get("grain") else 0.0) if grain is None else grain
        files = []
        for f in fmts:
            dest = _unique_dir(odir, stem + "-frames") if f == "png" else unique_path(odir, stem, f)
            engine.encode(frames_dir, fps, f, dest, transparent, background=background, grain=g)
            files.append(str(dest))
        sheet = unique_path(odir, stem + "-sheet", "png")
        engine.frames_sheet(frames, sheet, transparent, backdrop=backdrop, fps=fps)
        stats = engine.frame_stats(frames, transparent)
        if keep_html:
            master = unique_path(odir, stem + "-source", "html")
            shutil.copy2(page, master)
            files.append(str(master))
        files += list(extra_files or [])
        res = Result(summary, files=files, previews=[str(sheet)], warnings=list(warnings))
        for kf in key_frames(frames, odir, stem, keyframes, fps, transparent):
            res.previews.append(kf)
        for f in files:
            if f.endswith((".mp4", ".webm", ".mov", ".gif")):
                pr = qc.probe(f)
                res.data.setdefault("outputs", []).append({"file": Path(f).name, **{k: pr.get(k) for k in ("duration", "width", "height", "fps", "codec", "pix_fmt")}})
        res.data.update({"frames": len(frames), "fps": fps, "duration": round(r["duration"], 3), "size": f"{width}x{height}",
                         "transparent": transparent, "qc": stats, **(data or {})})
        if stats["blank_frames"] >= stats["sampled"]:
            res.warnings.append("every sampled frame is empty — the animation did not render")
            res.ok = False
        if stats["motion"] < 0.05 and len(frames) > 2:
            res.warnings.append("almost no motion between frames — check the preview")
        for line in r.get("console", [])[:5]:
            res.warnings.append("page " + line)
        if transparent:
            res.next_steps.append("overlay on footage: video_overlay, or put it on a timeline (video_edit) as "
                                  "{'type':'overlay','src':<.mov/.webm>,'at':<s>} — .mov is ProRes 4444 with alpha "
                                  "(Premiere/Kdenlive/Resolve/FCP), .webm is VP9 alpha (web, small)")
        if keep_html:
            res.next_steps.append("edit the -source.html (plain HTML/CSS/JS) and re-render it with motion_render_html for full control")
        return res


def key_frames(frames: list[Path], odir: Path, stem: str, n: int, fps: float, alpha: bool) -> list[str]:
    """Save n full frames (≤1280 px, alpha over a checkerboard) at telling moments: after the build-in
    (~30%), the hold (~60%) and the end (~92%) — what a reviewer would scrub to."""
    from PIL import Image
    out = []
    if n <= 0 or not frames:
        return out
    at = [0.3, 0.6, 0.92, 0.12, 0.8][:n]
    for a in at:
        i = min(len(frames) - 1, int(a * (len(frames) - 1)))
        im = Image.open(frames[i]).convert("RGBA")
        im.thumbnail((1280, 1280))
        if alpha:
            base = engine._checker(im.size, 16)
            base.alpha_composite(im)
            im = base
        dest = unique_path(odir, f"{stem}-frame-{engine.timecode(i / fps, fps).replace(':', '')}", "png")
        im.convert("RGB").save(dest)
        out.append(str(dest))
    return out


def render_template(template: str, params: dict, *, size: str, fps: int, duration: float, transparent: bool,
                    formats, colors: dict, fonts, project: str, out: str, summary: str, name: str = "",
                    data: dict | None = None, extra_head: str = "", brand: str = "") -> Result:
    width, height = parse_size(size)
    warnings: list[str] = []
    colors, fonts, th = apply_brand(brand, colors, fonts, warnings)
    params = {k: v for k, v in params.items() if v not in (None, "")}
    params.update({"colors": colors or {}, "transparent": bool(transparent)})
    if duration and duration > 0:
        params["duration"] = float(duration)
    if not (1 <= fps <= 120):
        raise ToolError(f"fps {fps} out of range", "use 24, 25, 30, 50 or 60")
    work = engine.scratch_dir("page-")
    try:
        page = build_page(template, params, fonts, work, warnings, extra_head)
        base = name or slug(str(params.get("title") or params.get("name") or params.get("text") or template))[:40]
        bg = (colors or {}).get("background", "#0E0F1A")
        res = render_page(page, name=base if base.startswith(template) else f"{template}-{base}", width=width, height=height,
                          fps=fps, duration=None, transparent=transparent, formats=formats, project=project, out=out,
                          warnings=warnings, summary=summary, data=data, background=bg)
        if th:
            res.data["brand"] = th["kit"].get("slug", brand)
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ======================================================================================================
# Tools
# ======================================================================================================
BRAND_DOC = ("brand: a saved brand kit slug (brand_create) — its colours, Arabic+Latin fonts and logo are used; "
             "explicit colors/fonts still win.")


@tool("motion")
def motion_title_card(title: str, subtitle: str = "", kicker: str = "", style: str = "bold", align: str = "auto",
                      duration: float = 5.0, size: str = "1920x1080", fps: int = 30, transparent: bool = False,
                      brand: str = "", colors: dict = {}, fonts: dict = {}, formats: list[str] = [], project: str = "",
                      out: str = "") -> Result:
    """Animated title card / opener: headline revealed word by word through masks, kicker label,
    accent bar, subtitle, animated gradient backdrop with film grain, and a clean outro. Returns the
    MP4 (or MOV/WebM with alpha when transparent=true), a contact sheet to LOOK at, and the editable
    HTML source.

    style: bold (default, left-aligned editorial) | minimal (centered, letter-by-letter blur-in for
    Latin, word-level for Arabic) | split (colour panels wipe across, then the title). Wrap a word in
    *asterisks* to colour it with the primary colour. Arabic/English both work (RTL detected).
    size: 1920x1080 | 9:16 | 1:1 | 4:5 | 4k | WxH. colors: {primary, accent, background, text, muted}.
    fonts: {head, body, head_ar, body_ar} Google Fonts families, or a mood/pairing word ("luxury").
    brand: saved brand kit slug (colours + fonts)."""
    return render_template("title_card", {"title": title, "subtitle": subtitle, "kicker": kicker, "style": style, "align": align},
                           size=size, fps=fps, duration=duration, transparent=transparent, formats=formats, colors=colors,
                           fonts=fonts, project=project, out=out, brand=brand, summary=f"Title card '{title}' ({style}).")


@tool("motion")
def motion_lower_third(name: str, role: str = "", style: str = "modern", position: str = "auto", duration: float = 6.0,
                       size: str = "1920x1080", fps: int = 30, photo: str = "", brand: str = "", colors: dict = {},
                       fonts: dict = {}, formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Lower-third name/role super with alpha, ready to overlay on an interview or talking head.
    Returns ProRes 4444 .mov + VP9 .webm (both with transparency), a contact sheet (shown over a
    checkerboard) and the HTML source.

    style: modern (accent bar + wiping name/role panels — broadcast) | glass (rounded translucent card
    with initials badge, or `photo` as the badge) | line (minimal text with a drawn accent line).
    position: auto (left for Latin, right for Arabic) | left | right | center. Sits inside the
    title-safe area (higher on 9:16 to clear platform UI). Arabic names/roles are shaped correctly.
    brand: saved brand kit slug."""
    params = {"name": name, "role": role, "style": style, "position": position}
    if photo:
        pp = Path(photo).expanduser()
        if not pp.exists():
            raise ToolError(f"photo not found: {pp}")
        params["photo_uri"] = pp.resolve().as_uri()
    return render_template("lower_third", params, size=size, fps=fps, duration=duration, transparent=True, formats=formats,
                           colors=colors, fonts=fonts, project=project, out=out, brand=brand,
                           summary=f"Lower third for '{name}' ({style}, alpha).")


@tool("motion")
def motion_logo_reveal(logo: str = "", tagline: str = "", style: str = "draw", duration: float = 4.5, size: str = "1920x1080",
                       fps: int = 30, transparent: bool = False, fade_out: bool = False, brand: str = "", colors: dict = {},
                       fonts: dict = {}, formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Logo reveal / sting from an SVG (or PNG) logo — or a brand kit's own logo with brand=<slug>
    (logo may then be omitted; the tagline defaults to the brand's). Returns the MP4 (or alpha
    MOV/WebM), contact sheet and HTML source.

    style: draw (SVG outlines draw on, then fills fade in — needs an SVG with paths/shapes) |
    pop (spring scale-in with ring burst and particles) | mask (circular iris reveal, ring and a light
    sweep across the logo). The logo holds on the last frame (fade_out=true fades it to the
    background instead). tagline appears under the logo (Arabic ok)."""
    warnings: list[str] = []
    th = brand_theme(brand) if brand else {}
    if not logo and th:
        try:
            w_, h_ = (int(v) for v in str(size).lower().split("x")[:2])
        except ValueError:
            w_, h_ = 16, 9
        # portrait / square canvases: the stacked lockup fills the frame far better than a wide one
        key = "stacked_svg" if h_ >= w_ * 0.95 else "logo_svg"
        logo = th.get(key) or th.get("logo_svg") or th.get("icon_svg") or ""
        if not tagline:
            tagline = th.get("tagline") or ""
    if not logo:
        raise ToolError("no logo given", "pass logo=<path to .svg/.png> or brand=<saved brand slug>")
    p = Path(logo).expanduser()
    if not p.exists():
        raise ToolError(f"logo not found: {p}")
    params = {"style": style, "tagline": tagline, "logo_uri": p.resolve().as_uri(), "logo_ext": p.suffix.lower(),
              "fade_out": bool(fade_out)}
    if p.suffix.lower() == ".svg":
        svg = p.read_text(encoding="utf-8", errors="ignore")
        svg = re.sub(r"<\?xml.*?\?>|<!DOCTYPE.*?>|<!--.*?-->", "", svg, flags=re.S)
        params["svg"] = svg
    else:
        from PIL import Image
        with Image.open(p) as im:
            params["logo_ar"] = im.width / max(1, im.height)
        if style == "draw":
            style = params["style"] = "pop"
            warnings.append("raster logo: the 'draw' style needs an SVG, used 'pop'")
    res = render_template("logo_reveal", params, size=size, fps=fps, duration=duration, transparent=transparent,
                          formats=formats, colors=colors, fonts=fonts, project=project, out=out, name=slug(p.stem),
                          brand=brand, summary=f"Logo reveal of {p.name} ({style}).")
    res.warnings += warnings
    return res


@tool("motion")
def motion_kinetic_text(text: str, style: str = "punch", size: str = "1920x1080", fps: int = 30, duration: float = 0,
                        transparent: bool = False, brand: str = "", colors: dict = {}, fonts: dict = {},
                        formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Kinetic typography from text (Arabic or English): phrases separated by new lines or '|' become
    beats; words animate in on a rhythm; *asterisk* words are emphasised in the accent colour with a
    highlight. Duration is automatic from the word count unless given. Returns MP4 (or alpha),
    contact sheet, HTML source.

    style: punch (one phrase at a time, words punch in, centred — reels/promo energy) | stack (lines
    stack up big and bold, earlier lines dim — explainer/manifesto) | type (typewriter with caret;
    Arabic types word by word so letters stay joined). brand: saved brand kit slug."""
    return render_template("kinetic_text", {"text": text, "style": style}, size=size, fps=fps, duration=duration,
                           transparent=transparent, formats=formats, colors=colors, fonts=fonts, project=project, out=out,
                           brand=brand, summary=f"Kinetic typography ({style}).", name="kinetic")


def _parse_srt(path: str) -> list[dict]:
    txt = Path(path).expanduser().read_text(encoding="utf-8-sig", errors="ignore")
    segs = []
    for block in re.split(r"\n\s*\n", txt.replace("\r", "")):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = block[m.end():].strip()
        body = re.sub(r"<[^>]+>|\{[^}]+\}", "", body).replace("\n", " ").strip()
        if body:
            segs.append({"start": round(st, 3), "end": round(en, 3), "text": body})
    return segs


@tool("motion")
def motion_captions(srt: str = "", segments: list = [], words: list = [], style: str = "pop", size: str = "1080x1920",
                    fps: int = 30, words_per_chunk: int = 3, position: str = "auto", brand: str = "", colors: dict = {},
                    fonts: dict = {}, formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Animated social captions as a transparent overlay (ProRes 4444 .mov + VP9 .webm) timed to an
    .srt file, a segments list [{start, end, text}] or word timings words=[{word,start,end}] (e.g.
    audio_transcribe's data.words — exact timing). Words are grouped into short chunks and the spoken
    word is highlighted; without word timings they are estimated by length within each subtitle
    (approximate — reported in warnings).

    style: pop (chunk pops in, current word gets an accent box — reels style) | karaoke (words fill
    with the accent colour as spoken) | clean (sentence fades in, subtle). Arabic chunks read RTL.
    For burned captions directly on a video (faster, libass), use video_captions instead."""
    segs = list(segments or [])
    if srt:
        segs = _parse_srt(srt)
    wl = [{"word": str(w.get("word", "")).strip(), "start": float(w["start"]), "end": float(w["end"])}
          for w in (words or []) if str(w.get("word", "")).strip()]
    if not segs and not wl:
        raise ToolError("no captions given", "pass srt=<path>, segments=[{start,end,text}] or words=[{word,start,end}]")
    ends = [float(s["end"]) for s in segs] + [w["end"] for w in wl]
    dur = max(ends) + 0.4
    if dur > 600:
        raise ToolError("captions longer than 10 minutes", "split the video, or use video_captions (libass, much faster)")
    res = render_template("captions", {"segments": segs, "words": wl, "style": style, "chunk": max(1, int(words_per_chunk)),
                                       "position": position},
                          size=size, fps=fps, duration=dur, transparent=True, formats=formats, colors=colors, fonts=fonts,
                          project=project, out=out, name="captions", brand=brand,
                          summary=f"Animated captions ({style}, {len(wl) or len(segs)} {'words' if wl else 'segments'}, {dur:.1f}s alpha overlay).")
    if not wl:
        res.warnings.append("word timing estimated from word length inside each subtitle (no word timestamps given)")
    return res


@tool("motion")
def motion_cta(kind: str = "subscribe", text: str = "", handle: str = "", position: str = "auto", duration: float = 4.0,
               size: str = "1920x1080", fps: int = 30, brand: str = "", colors: dict = {}, fonts: dict = {},
               formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Call-to-action overlay with alpha: subscribe (button + cursor click → 'Subscribed', bell and
    like pop), follow (avatar/handle card whose + turns into ✓), link ("link in bio" pill with
    bouncing arrow). text overrides the button label (Arabic ok, e.g. "اشترك"). With brand= the
    brand icon is used as the avatar. Returns MOV/WebM alpha + contact sheet + HTML."""
    params = {"kind": kind, "text": text, "handle": handle, "position": position}
    if brand:
        th = brand_theme(brand)
        if th.get("icon_svg"):
            params["avatar_uri"] = Path(th["icon_svg"]).resolve().as_uri()
        if not handle:
            params["handle"] = "@" + slug(th["name"]).replace("-", "")
    return render_template("cta", params, size=size, fps=fps, duration=duration, transparent=True, formats=formats,
                           colors=colors, fonts=fonts, project=project, out=out, name=kind, brand=brand,
                           summary=f"Call-to-action '{kind}' (alpha overlay).")


@tool("motion")
def motion_countdown(start: int = 5, end_text: str = "", style: str = "ring", size: str = "1920x1080", fps: int = 30,
                     digits: str = "latin", transparent: bool = False, brand: str = "", colors: dict = {}, fonts: dict = {},
                     formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Countdown from `start` to 1 (one second per number) with number transitions, optionally
    ending on end_text (e.g. "GO" / "يلا"). style: ring (progress ring sweeps each second) | bold
    (huge numbers slam in with a flash). digits: latin | arabic (٥ ٤ ٣). Returns MP4 (or alpha) +
    contact sheet."""
    start = max(1, min(int(start), 60))
    return render_template("countdown", {"start": start, "end_text": end_text, "style": style, "digits": digits}, size=size,
                           fps=fps, duration=start + (1.4 if end_text else 0.3), transparent=transparent, formats=formats,
                           colors=colors, fonts=fonts, project=project, out=out, name=f"countdown-{start}", brand=brand,
                           summary=f"Countdown {start}→1{' → ' + end_text if end_text else ''}.")


@tool("motion")
def motion_transition(shape: str = "diagonal", duration: float = 1.2, size: str = "1920x1080", fps: int = 30,
                      brand: str = "", colors: dict = {}, formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Shape-wipe transition as an alpha overlay: the shapes fully cover the frame at the midpoint,
    so cut from clip A to clip B exactly at data.cut_at. shape: diagonal (skewed colour panels
    sweep across) | circle (iris grows and closes) | bars (staggered vertical bars) | split (halves
    close and open). Returns MOV/WebM alpha + contact sheet; data.cut_at = the cut time in seconds."""
    res = render_template("transition", {"shape": shape}, size=size, fps=fps, duration=duration, transparent=True,
                          formats=formats, colors=colors, fonts={}, project=project, out=out, name=shape, brand=brand,
                          summary=f"'{shape}' shape transition (alpha), cut at {duration / 2:.2f}s.",
                          data={"cut_at": round(duration / 2, 3)})
    res.next_steps.insert(0, f"on a timeline (video_edit): add it as an overlay at (cut time − {duration / 2:.2f}s)")
    return res


@tool("motion")
def motion_counter(value: float = 0, label: str = "", prefix: str = "", suffix: str = "", decimals: int = 0,
                   items: list = [], digits: str = "latin", duration: float = 4.0, size: str = "1920x1080", fps: int = 30,
                   transparent: bool = False, brand: str = "", colors: dict = {}, fonts: dict = {}, formats: list[str] = [],
                   project: str = "", out: str = "") -> Result:
    """Animated number counter(s): counts up to `value` with prefix/suffix ("$", "%", "K+") and a
    label, plus an underline progress bar. For a stat row pass items=[{value,label,prefix,suffix}]
    (up to 4). digits: latin | arabic (٠١٢٣…). Returns MP4 (or alpha) + contact sheet."""
    its = [dict(i) for i in (items or [])] or [{"value": value, "label": label, "prefix": prefix, "suffix": suffix}]
    for i in its:
        try:
            i["value"] = float(i.get("value", 0))
        except (TypeError, ValueError):
            raise ToolError(f"counter value {i.get('value')!r} is not a number")
    return render_template("counter", {"items": its[:4], "decimals": decimals, "digits": digits, "label": label},
                           size=size, fps=fps, duration=duration, transparent=transparent, formats=formats, colors=colors,
                           fonts=fonts, project=project, out=out, name="counter", brand=brand,
                           summary=f"Counter ({len(its[:4])} value(s)).")


@tool("motion")
def motion_infographic(data: list, title: str = "", kind: str = "bars", unit: str = "", duration: float = 6.0,
                       size: str = "1920x1080", fps: int = 30, transparent: bool = False, digits: str = "latin",
                       brand: str = "", colors: dict = {}, fonts: dict = {}, formats: list[str] = [], project: str = "",
                       out: str = "") -> Result:
    """Animated infographic from data=[{label, value}] (≤ 8 rows): kind bars (horizontal bars grow
    with counting values) | columns (vertical) | donut (segments draw in with a legend and total).
    Labels may be Arabic. Values are drawn to scale — this is a real chart, not decoration.
    Returns MP4 (or alpha) + contact sheet + HTML."""
    rows = []
    for d in (data or [])[:8]:
        try:
            rows.append({"label": str(d.get("label", "")), "value": float(d.get("value", 0))})
        except (TypeError, ValueError):
            raise ToolError(f"bad data row {d!r}", "each row needs a numeric value")
    if not rows:
        raise ToolError("data is empty", "pass data=[{\"label\":\"A\",\"value\":10}, …]")
    if kind == "donut" and any(r["value"] < 0 for r in rows):
        raise ToolError("donut charts need non-negative values")
    res = render_template("infographic", {"data": rows, "title": title, "kind": kind, "unit": unit, "digits": digits},
                          size=size, fps=fps, duration=duration, transparent=transparent, formats=formats, colors=colors,
                          fonts=fonts, project=project, out=out, name=kind, brand=brand,
                          summary=f"Infographic ({kind}, {len(rows)} rows).")
    if len(data or []) > 8:
        res.warnings.append(f"only the first 8 of {len(data)} rows are shown")
    return res


@tool("motion")
def motion_render_html(html: str, duration: float = 0, size: str = "1920x1080", fps: int = 30, transparent: bool = False,
                       formats: list[str] = [], wait_ms: int = 0, workers: int = 1, grain: float = 0,
                       project: str = "", out: str = "") -> Result:
    """Power tool: render ANY HTML animation (a file path or an HTML string) frame-exactly to video.
    CSS animations/transitions, element.animate(), GSAP timelines, requestAnimationFrame/canvas
    loops, setTimeout sequences and <video> elements are all driven by a virtual clock, so the
    output never drops or stutters. duration: seconds (0 = window.__duration if the page sets it,
    else the natural end of its animations). transparent=true keeps the page background see-through
    (set html/body background: transparent) → ProRes 4444 / WebM alpha. Returns files + contact sheet.

    Page hooks (optional): window.__duration = seconds; window.__ready = Promise to await (e.g. data
    loading); window.__onseek = async (t) => {...} called every frame with the time in seconds.
    workers>1 renders chunks in parallel pages — only for pages whose look depends purely on time
    (not on state accumulated frame by frame). grain: 0–20 film grain added at encode time."""
    width, height = parse_size(size)
    warnings: list[str] = []
    work = engine.scratch_dir("html-")
    try:
        if "<" in html and Path(html).suffix.lower() not in (".html", ".htm"):
            page = work / "page.html"
            page.write_text(html, encoding="utf-8")
            name = "html-animation"
        else:
            src = Path(html).expanduser()
            if not src.exists():
                raise ToolError(f"no such file: {src}")
            page, name = src, slug(src.stem)
        fmts = _formats(formats, transparent)
        return render_page(page, name=name, width=width, height=height, fps=fps, duration=duration or None,
                           transparent=transparent, formats=fmts, project=project, out=out, warnings=warnings,
                           summary="Rendered HTML animation frame-by-frame.", keep_html=page.parent == work,
                           workers=max(1, int(workers)), grain=float(grain))
    finally:
        shutil.rmtree(work, ignore_errors=True)


@tool("motion")
def motion_templates() -> Result:
    """List the motion templates, their styles and what each is for (quick catalog for choosing)."""
    cat = {
        "motion_title_card": "bold | minimal | split — openers, chapter cards (opaque or alpha)",
        "motion_lower_third": "modern | glass | line — name/role supers (alpha)",
        "motion_logo_reveal": "draw | pop | mask — logo stings from SVG/PNG or brand=<slug>",
        "motion_kinetic_text": "punch | stack | type — kinetic typography, Arabic word-level",
        "motion_captions": "pop | karaoke | clean — animated caption overlay from SRT/words (alpha)",
        "motion_cta": "subscribe | follow | link — call-to-action overlays (alpha)",
        "motion_countdown": "ring | bold — countdown timers",
        "motion_transition": "diagonal | circle | bars | split — shape-wipe transitions (alpha), cut at midpoint",
        "motion_counter": "animated numbers / stat rows",
        "motion_infographic": "bars | columns | donut — animated charts from data",
        "motion_render_html": "render any HTML/CSS/GSAP/canvas animation deterministically",
        "motion_compose": "PRO: JSON scene spec → GSAP composition (layers, 40 presets, transitions, camera, charts, Lottie, 3D)",
        "motion_lottie": "PRO: render/recolour/retime Lottie (.json/.lottie) frame-exactly",
        "motion_3d": "PRO: three.js 3D titles (Arabic ok), logos, product turntables, devices, particles, abstract",
        "motion_blender": "PRO: Blender (Cycles/EEVEE) photoreal 3D title/logo/turntable/abstract + editable .blend",
        "motion_remotion": "PRO: render an existing Remotion (React) project (licence check)",
        "motion_plan": "PRO: router — which engine/tool/template for a brief, with a spec skeleton",
    }
    pro = {}
    try:
        from .presets import TEMPLATES
        pro = {k: v[1] + "  — fields: " + (v[0].__doc__ or "").strip().replace("\n", " ") for k, v in TEMPLATES.items()}
    except Exception:
        pass
    txt = "\n".join(f"{k}: {v}" for k, v in cat.items())
    if pro:
        txt += "\n\nPro templates — motion_compose(template=<name>, fields={…}); preview=true for quick stills:\n" + "\n".join(
            f"{k}: {v}" for k, v in pro.items())
    return Result(txt + "\n\nEvery template takes brand=<slug>, size (16:9, 9:16, 1:1, 4:5, 4k, WxH), fps, colors, fonts, "
                  "formats (mp4, webm, mov, gif, png).", data={**cat, "pro_templates": pro})
