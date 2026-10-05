"""Real-time 3D motion graphics with three.js (WebGL in headless Chromium, driven by the virtual clock).

Text never goes through a three.js typeface: it is shaped with HarfBuzz (brand/logo.text_path → correct
Arabic joining, kerning and ligatures), turned into SVG outlines, and extruded with SVGLoader +
ExtrudeGeometry. The same path handles SVG logos.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import engine, libs
from . import ASSETS, BRAND_DOC, apply_brand, parse_size, render_page

ASSETS3D = ASSETS.parent / "motion3d"
PRESETS3D = ("title", "logo", "product", "particles", "abstract", "device")
CAMERAS = ("orbit", "push-in", "dolly", "crane", "static", "turntable")
MATERIALS = ("plastic", "metal", "chrome", "gold", "glass", "matte", "clay", "neon", "satin")
ANIMS = ("rise", "spin-in", "flip", "drop", "zoom-through", "turntable", "float")
AR_RE = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")


def text_svg(text: str, family: str = "", weight: int = 800, color: str = "#FFFFFF", fonts: dict | None = None,
             line_gap: float = 1.15) -> str:
    """Shaped text → SVG markup of outlines (one path per line, centred). Arabic uses the Arabic face."""
    from ..brand.logo import text_path
    fonts = fonts or {}
    lines = [ln for ln in str(text).replace("|", "\n").split("\n") if ln.strip()] or ["Studio"]
    size = 200.0
    out, y, wmax = [], 0.0, 1.0
    boxes = []
    for ln in lines:
        fam = family or (fonts.get("head_ar") or "Cairo" if AR_RE.search(ln) else fonts.get("head") or "Montserrat")
        try:
            tp = text_path(ln.strip(), fam, int(weight), size)
        except Exception as e:
            raise ToolError(f"could not outline the text with font {fam!r} ({e})", "check the font name or the internet connection (fonts download once)")
        boxes.append((tp, y))
        wmax = max(wmax, tp.w)
        y += size * line_gap
    for tp, yy in boxes:
        dx = (wmax - tp.w) / 2 - tp.x0
        out.append(f'<path transform="translate({dx:.2f},{yy:.2f})" d="{tp.d}" fill="{color}"/>')
    top = boxes[0][0].y0
    bottom = boxes[-1][1] + boxes[-1][0].y1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {top:.2f} {wmax:.2f} {bottom - top:.2f}">'
            + "".join(out) + "</svg>")


def scene_options(o: dict, theme: dict, warnings: list[str]) -> dict:
    """Validate + resolve 3D scene options (files → data/file URIs, text → shaped SVG)."""
    o = dict(o or {})
    preset = o.get("preset", "title")
    if preset not in PRESETS3D:
        raise ToolError(f"unknown 3D preset {preset!r}", f"one of {', '.join(PRESETS3D)}")
    cols = (theme or {}).get("colors") or {}
    o.setdefault("color", cols.get("primary", "#FF5A36"))
    o.setdefault("accent", cols.get("accent", "#FFC53D"))
    for k, allowed in (("camera", CAMERAS), ("material", MATERIALS)):
        if o.get(k) and o[k] not in allowed:
            raise ToolError(f"unknown 3D {k} {o[k]!r}", f"one of {', '.join(allowed)}")
    if o.get("animation") and o["animation"] not in ANIMS:
        raise ToolError(f"unknown 3D animation {o['animation']!r}", f"one of {', '.join(ANIMS)}")
    fonts = (theme or {}).get("fonts") or {}
    if preset == "title" and not o.get("svg"):
        o["svg"] = text_svg(o.get("text") or "Studio", o.get("font", ""), int(o.get("weight", 800)), o["color"], fonts)
    if preset == "logo" and not o.get("svg"):
        src = o.get("logo") or o.get("src") or (theme or {}).get("logo_svg") or (theme or {}).get("icon_svg")
        if not src:
            raise ToolError("3D logo needs logo=<path.svg> or brand=<slug>")
        p = Path(src).expanduser()
        if not p.exists() or p.suffix.lower() != ".svg":
            raise ToolError(f"3D logo needs an SVG file (got {p})", "vectorise a PNG first with vector_trace")
        svg = re.sub(r"<\?xml.*?\?>|<!DOCTYPE.*?>|<!--.*?-->", "", p.read_text(encoding="utf-8", errors="ignore"), flags=re.S)
        if "<mask" in svg or "<clipPath" in svg:
            warnings.append("the SVG uses masks/clip paths — three.js extrudes the raw shapes, knock-outs may fill in; prefer a flattened (outlined) SVG")
        o["svg"] = svg
    if preset == "product":
        src = o.get("glb") or o.get("model") or o.get("src")
        if not src:
            raise ToolError("product turntable needs model=<path to .glb>")
        p = Path(src).expanduser()
        if not p.exists():
            raise ToolError(f"model not found: {p}")
        if p.suffix.lower() not in (".glb",):
            raise ToolError("product needs a binary glTF (.glb)", "convert .gltf/.obj/.fbx to .glb (Blender: File › Export › glTF Binary)")
        if p.stat().st_size > 60e6:
            raise ToolError(f"{p.name} is {p.stat().st_size / 1e6:.0f} MB", "decimate/compress it (gltf-transform) under 60 MB, or use motion_blender")
        o["glb"] = "data:model/gltf-binary;base64," + base64.b64encode(p.read_bytes()).decode()
    if preset == "device":
        for key in ("screen_image", "screen_video", "screen"):
            v = o.get(key)
            if v and not str(v).startswith(("data:", "file:", "http")):
                p = Path(v).expanduser()
                if not p.exists():
                    raise ToolError(f"screen file not found: {p}")
                k = "screen_video" if p.suffix.lower() in (".mp4", ".mov", ".webm", ".m4v") else "screen_image"
                o[k] = p.resolve().as_uri()
                if key == "screen":
                    o.pop("screen")
    for k in ("text", "logo", "src", "model", "font", "weight"):
        o.pop(k, None)
    return o


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:{bg}}}canvas{{position:absolute;inset:0;width:100%;height:100%}}</style>
{importmap}</head><body><canvas id="c" width="{w}" height="{h}"></canvas>
<script>window.__ready = new Promise((res, rej) => {{ window.__res = res; window.__rej = rej; }});</script>
<script type="module">
import {{createScene}} from '{scene}';
try {{
  const sc = await createScene(document.getElementById('c'), {opts});
  window.__duration = {dur};
  window.__onseek = (t) => sc.render(t);
  window.__res();
}} catch (e) {{ console.error('motion3d: ' + e); window.__rej(e); }}
</script></body></html>"""


@tool("motion", network=True)
def motion_3d(preset: str = "title", text: str = "", logo: str = "", model: str = "", screen: str = "", device: str = "phone",
              material: str = "", camera: str = "", animation: str = "", duration: float = 5.0, size: str = "1920x1080",
              fps: int = 30, transparent: bool = False, background: str = "", color: str = "", accent: str = "",
              depth: float = 0, bloom: float = 0, motion_blur: int = 0, font: str = "", weight: int = 800, brand: str = "",
              options: dict = {}, formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Real-time 3D motion graphics (three.js/WebGL, studio lighting with HDR-style reflections, soft
    shadows, ACES tone mapping) rendered frame-exactly: 3D extruded titles, 3D logos, product
    turntables, device mockups, particle fields and abstract loops. Fast (seconds per second of video
    at 1080p) — use motion_blender instead for photoreal glass/caustics/real depth of field. Returns
    MP4 (or alpha MOV/WebM), a timecoded contact sheet and key frames.

    preset: title (text — any language: shaped with HarfBuzz so Arabic joins correctly, then extruded)
    | logo (an SVG, or brand=<slug>'s logo, extruded + bevelled) | product (model=<.glb> turntable on a
    shadow catcher) | device (phone|laptop mockup; screen=<image or video> plays on the display) |
    particles (depth field of glowing points flying toward the camera) | abstract (glossy glass/chrome
    shapes orbiting). material: plastic | metal | chrome | gold | glass | matte | clay | neon | satin.
    camera: orbit | push-in | dolly | crane | static | turntable. animation: rise | spin-in | flip |
    drop | zoom-through | turntable | float. depth: extrusion (world units, ~0.35 default).
    background: CSS colour/gradient ('' = brand-tinted studio gradient); transparent=true for alpha.
    bloom 0–1.5 = glow on bright parts (opaque only). motion_blur=6 → sub-frame motion blur.
    options = extra scene settings (fov, orbit_degrees, exposure, count, side_color, …)."""
    warnings: list[str] = []
    cols, fnt, theme = apply_brand(brand, {}, {}, warnings)
    th = dict(theme or {})
    th["colors"] = cols or {}
    th["fonts"] = fnt if isinstance(fnt, dict) else (theme or {}).get("fonts", {})
    o = dict(options or {})
    o.update({k: v for k, v in {"preset": preset, "text": text, "logo": logo, "model": model, "device": device, "material": material,
                                 "camera": camera, "animation": animation, "color": color, "accent": accent,
                                 "depth": depth or None, "bloom": bloom or None, "font": font, "weight": weight}.items() if v})
    if screen:
        o["screen"] = screen
    if preset == "product" and not material:
        o.pop("material", None)
    o.setdefault("material", {"title": "metal", "logo": "chrome", "abstract": "plastic"}.get(preset, "plastic"))
    o.setdefault("camera", {"product": "turntable", "particles": "push-in", "device": "orbit", "title": "push-in"}.get(preset, "orbit"))
    o = scene_options(o, th, warnings)
    w, h = parse_size(size)
    o.update({"duration": float(duration), "transparent": bool(transparent)})
    if bloom and transparent:
        warnings.append("bloom is skipped for transparent output (it would destroy the alpha)")
    c = th["colors"]
    bgc = c.get("background", "#0E0F1A")
    bg = "transparent" if transparent else (background or
         f"radial-gradient(ellipse at 50% 40%, {_mix(bgc, '#ffffff', 0.14)} 0%, {bgc} 55%, {_mix(bgc, '#000000', 0.5)} 100%)")
    work = engine.scratch_dir("three-")
    try:
        page = work / "scene3d.html"
        page.write_text(PAGE.format(bg=bg, importmap=libs.three_importmap(), w=w, h=h, scene=(ASSETS3D / "scene.js").resolve().as_uri(),
                                    opts=json.dumps(o), dur=float(duration)), encoding="utf-8")
        label = (text or Path(logo or model or "").stem or preset)[:30]
        res = render_page(page, name=f"3d-{preset}", width=w, height=h, fps=fps, duration=float(duration), transparent=transparent,
                          formats=formats, project=project, out=out, warnings=warnings,
                          summary=f"3D {preset} '{label}' — three.js r{libs.THREE_V.split('.')[1]}, {o.get('material')} material, "
                                  f"{o.get('camera')} camera, {duration:g}s" + (f", motion blur {motion_blur}×" if motion_blur > 1 else "") + ".",
                          data={"engine": "three.js " + libs.THREE_V, "preset": preset, "material": o.get("material"), "camera": o.get("camera")},
                          background=bgc, workers=0, grain=0 if transparent else 3, motion_blur=motion_blur, keyframes=3)
        res.next_steps.append("need photoreal glass/caustics/depth of field? the same job runs in Blender: motion_blender")
        res.next_steps.append("put it in a composition with 2D titles: motion_compose layer {\"type\":\"3d\",\"scene\":{…}}")
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _mix(a: str, b: str, t: float) -> str:
    try:
        from ..design import color as col
        return col.mix(a, b, t)
    except Exception:
        return a
