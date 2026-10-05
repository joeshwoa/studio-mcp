"""motion_compose — After-Effects-style compositions from a JSON scene spec, animated with GSAP.

The AI (or a template in presets.py) writes a spec: canvas, scenes with transitions, layers (text, shape,
image, video, svg, lottie, icon, chart, counter, 3d, group) with transforms, timing and animation
presets. Python validates it, resolves files/brand/fonts and writes ONE standalone HTML page (the
runtime is assets/motion/compose.js); the deterministic renderer (engine.py) turns it into video.
The page and the spec JSON are kept next to the video as editable masters.
"""
from __future__ import annotations

import base64
import copy
import json
import re
import shutil
from pathlib import Path

from ...config import slug, unique_path
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import engine, libs
from . import (ASSETS, BRAND_DOC, _formats, _out_target, _resolve_fonts, apply_brand, font_css, parse_size,
               render_page)

ASSETS3D = ASSETS.parent / "motion3d"

LAYER_TYPES = ("text", "shape", "image", "video", "svg", "lottie", "icon", "chart", "counter", "3d", "group")
PRESETS = ("fade", "slide", "rise", "drop", "scale", "zoom", "pop", "scale-pop", "blur", "elastic", "bounce", "flip", "flip-3d",
           "spin", "swing", "wipe", "mask", "clip-reveal", "iris", "block", "split-chars", "split-words", "split-lines",
           "typewriter", "scramble", "highlight", "underline", "shine", "glitch", "draw", "morph", "motion-path", "counter",
           "wiggle", "shake", "float", "parallax", "pulse", "spin-loop", "kenburns")
TRANSITIONS = ("cut", "crossfade", "fade", "dip", "wipe", "slide", "push", "zoom", "blur", "iris", "whip", "shape", "glitch")
CAMERA = ("push-in", "pull-out", "zoom-in", "zoom-out", "pan-left", "pan-right", "tilt-up", "tilt-down", "drift", "orbit",
          "roll", "handheld", "shake")
EASES = ("power1-4.in/out/inOut", "expo.*", "sine.*", "circ.*", "back.out(1.7)", "elastic.out(1,0.4)", "bounce.out",
         "steps(n)", "cubic-bezier(a,b,c,d)", "aliases: smooth snappy soft overshoot spring bouncy anticipate cinematic whip linear")

GATE = r"""
window.__ready = (async () => {
  const sample = 'Abc 123 أبجد';
  const fams = ['sf-head', 'sf-body'].concat(Object.values((window.SPEC || {}).fontmap || {}));
  const loads = [];
  for (const fam of fams) for (const w of [300, 400, 500, 600, 700, 800, 900])
    loads.push(document.fonts.load(`${w} 40px "${fam}"`, sample).catch(() => null));
  await Promise.all(loads); await document.fonts.ready;
  if (window.__need3d) await window.__s3d;
  await window.__compose();
})();
"""

MODULE_3D = """<script type="module">
import {{createScene}} from '{uri}';
window.__studio3d = {{createScene}};
window.__s3dResolve && window.__s3dResolve();
</script>"""


# ------------------------------------------------------------------------------------------- spec loading
def load_spec(spec) -> dict:
    if isinstance(spec, dict):
        return copy.deepcopy(spec)
    if isinstance(spec, str) and spec.strip():
        s = spec.strip()
        if s.startswith("{"):
            try:
                return json.loads(s)
            except json.JSONDecodeError as e:
                raise ToolError(f"spec is not valid JSON ({e})")
        p = Path(s).expanduser()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        raise ToolError(f"spec file not found: {p}")
    raise ToolError("no spec given", "pass spec={...} (see docs/motion.md) or template=<name> (motion_templates)")


def _svg_markup(path: Path) -> tuple[str, float]:
    svg = path.read_text(encoding="utf-8", errors="ignore")
    svg = re.sub(r"<\?xml.*?\?>|<!DOCTYPE.*?>|<!--.*?-->", "", svg, flags=re.S).strip()
    return svg, _svg_aspect(svg)


def _svg_aspect(svg: str) -> float:
    m = re.search(r'viewBox\s*=\s*"([\d.\-eE\s,]+)"', svg)
    if m:
        v = [float(x) for x in re.split(r"[\s,]+", m.group(1).strip()) if x]
        if len(v) == 4 and v[3]:
            return v[2] / v[3]
    w = re.search(r'\bwidth="([\d.]+)', svg)
    h = re.search(r'\bheight="([\d.]+)', svg)
    if w and h and float(h.group(1)):
        return float(w.group(1)) / float(h.group(1))
    return 1.0


def _file(src: str, what: str) -> Path:
    p = Path(str(src)).expanduser()
    if not p.exists():
        raise ToolError(f"{what} not found: {p}")
    return p.resolve()


class Ctx:
    def __init__(self, theme: dict, warnings: list[str], width: int, height: int):
        self.theme, self.warnings, self.width, self.height = theme, warnings, width, height
        self.need = set()
        self.families: set[str] = set()


def _brand_asset(src: str, cx: Ctx) -> str:
    """'brand:logo' | 'brand:icon' | 'brand:stacked' → the kit's SVG path."""
    key = src.split(":", 1)[1] or "logo"
    if not cx.theme:
        raise ToolError(f"{src!r} needs brand=<slug>")
    p = cx.theme.get({"logo": "logo_svg", "icon": "icon_svg", "stacked": "stacked_svg"}.get(key, "logo_svg"))
    if not p:
        raise ToolError(f"brand kit has no {key} file")
    return p


def _resolve_layer(lo: dict, cx: Ctx, path: str) -> dict:
    if not isinstance(lo, dict):
        raise ToolError(f"{path}: a layer must be an object, got {type(lo).__name__}")
    t = lo.get("type") or ("text" if "text" in lo else "group" if "children" in lo else "shape")
    if t not in LAYER_TYPES:
        raise ToolError(f"{path}: unknown layer type {t!r}", f"one of {', '.join(LAYER_TYPES)}")
    lo["type"] = t
    src = lo.get("src")
    if isinstance(src, str) and src.startswith("brand:"):
        src = lo["src"] = _brand_asset(src, cx)
    if t in ("image", "video") and src:
        if re.match(r"^(https?:|data:)", src):
            cx.warnings.append(f"{path}: remote/data src used as is")
        else:
            p = _file(src, t)
            lo["src"] = p.as_uri()
            if t == "image":
                from PIL import Image
                with Image.open(p) as im:
                    lo.setdefault("natural_aspect", im.width / max(1, im.height))
            else:
                from ...core import qc
                pr = qc.probe(p)
                if pr.get("width") and pr.get("height"):
                    lo.setdefault("natural_aspect", pr["width"] / pr["height"])
    if t == "svg":
        if src and not lo.get("svg"):
            p = _file(src, "svg")
            if p.suffix.lower() != ".svg":  # raster logo given to an svg layer → treat as image
                lo["type"] = "image"
                lo["fit"] = lo.get("fit", "contain")
                return _resolve_layer(lo, cx, path)
            lo["svg"], ar = _svg_markup(p)
            lo.setdefault("natural_aspect", ar)
        elif lo.get("svg"):
            lo.setdefault("natural_aspect", _svg_aspect(lo["svg"]))
        lo.pop("src", None)
    if t == "lottie":
        from .lottie import load_lottie, recolor
        if not (src or lo.get("data")):
            raise ToolError(f"{path}: lottie layer needs src (.json / .lottie)")
        data = lo.get("data") or load_lottie(_file(src, "lottie"))
        if lo.get("recolor"):
            data, _ = recolor(data, lo["recolor"], cx.theme.get("colors") if cx.theme else None)
        lo["data"] = data
        lo.setdefault("natural_aspect", (data.get("w") or 1) / max(1, data.get("h") or 1))
        lo.pop("src", None)
        cx.need.add("lottie")
    if t == "3d":
        from .three_d import scene_options
        lo["scene"] = scene_options(lo.get("scene") or {k: v for k, v in lo.items() if k not in ("type", "x", "y", "w", "h", "in", "out", "animate", "enter", "exit")},
                                    cx.theme, cx.warnings)
        cx.need.add("three")
    if t in ("text", "counter"):
        f = lo.get("font")
        if f and f not in ("head", "body"):
            cx.families.add(f)
    for key in ("enter", "exit"):
        v = lo.get(key)
        name = v if isinstance(v, str) else (v or {}).get("preset") if isinstance(v, dict) else None
        if name and re.sub(r"-(in|out)$", "", name) not in PRESETS:
            cx.warnings.append(f"{path}.{key}: unknown preset {name!r} (ignored by the renderer)")
    for i, a in enumerate(lo.get("animate") or []):
        name = a if isinstance(a, str) else a.get("preset", "")
        if re.sub(r"-(in|out)$", "", str(name)) not in PRESETS:
            cx.warnings.append(f"{path}.animate[{i}]: unknown preset {name!r}")
    if t == "group":
        lo["children"] = [_resolve_layer(c, cx, f"{path}.children[{i}]") for i, c in enumerate(lo.get("children") or [])]
    return lo


def _resolve_bg(bg, cx: Ctx):
    if isinstance(bg, str) and re.search(r"\.(png|jpe?g|webp|mp4|mov|webm)$", bg, re.I):
        bg = {"src": bg}
    if isinstance(bg, dict) and bg.get("src"):
        p = _file(bg["src"], "background")
        bg = dict(bg, src=p.as_uri(), video=p.suffix.lower() in (".mp4", ".mov", ".webm"))
    return bg


def _scene_duration(sc: dict) -> float:
    ends = []
    for lo in sc.get("layers") or []:
        for k in ("out", "in"):
            if isinstance(lo.get(k), (int, float)):
                ends.append(float(lo[k]) + (0.8 if k == "in" else 0))
    return round(max(ends + [3.0]) + 0.5, 3)


def normalise(spec: dict, *, size: str = "", fps: int = 0, brand: str = "", colors: dict | None = None, fonts=None,
              transparent: bool | None = None, warnings: list[str] | None = None) -> tuple[dict, dict, Ctx]:
    """Validate + resolve the spec. Returns (spec, fonts, ctx)."""
    warnings = warnings if warnings is not None else []
    sz = size or spec.get("size") or (f"{spec['width']}x{spec['height']}" if spec.get("width") else "1920x1080")
    width, height = parse_size(str(sz))
    spec["width"], spec["height"] = width, height
    spec["fps"] = int(fps or spec.get("fps") or 30)
    if not (1 <= spec["fps"] <= 120):
        raise ToolError(f"fps {spec['fps']} out of range")
    if transparent is not None:
        spec["transparent"] = bool(transparent) or bool(spec.get("transparent"))
    cols, fnt, theme = apply_brand(brand or spec.get("brand", ""), {**(spec.get("colors") or {}), **(colors or {})},
                                   fonts or spec.get("fonts") or {}, warnings)
    spec["colors"] = cols
    cx = Ctx(theme, warnings, width, height)
    if "scenes" not in spec:
        spec["scenes"] = [{"duration": spec.get("duration") or None, "layers": spec.pop("layers", []),
                           "camera": spec.pop("scene_camera", None)}]
        if spec["scenes"][0]["duration"] is None:
            spec["scenes"][0]["duration"] = _scene_duration(spec["scenes"][0])
            spec.pop("duration", None)
    if not spec["scenes"]:
        raise ToolError("spec has no scenes/layers")
    for i, sc in enumerate(spec["scenes"]):
        if not sc.get("duration"):
            sc["duration"] = _scene_duration(sc)
        tr = sc.get("transition")
        tname = tr if isinstance(tr, str) else (tr or {}).get("type")
        if tname and tname not in TRANSITIONS:
            warnings.append(f"scenes[{i}].transition: unknown {tname!r} (crossfade used)")
        if "background" in sc:
            sc["background"] = _resolve_bg(sc["background"], cx)
        sc["layers"] = [_resolve_layer(lo, cx, f"scenes[{i}].layers[{j}]") for j, lo in enumerate(sc.get("layers") or [])]
    if "background" in spec:
        spec["background"] = _resolve_bg(spec["background"], cx)
    total = sum(float(s["duration"]) for s in spec["scenes"])
    if total > 600:
        raise ToolError(f"composition is {total:.0f}s long", "keep motion compositions under 10 minutes; split it")
    return spec, fnt, cx


def build_compose_page(spec: dict, fonts, cx: Ctx, work: Path) -> Path:
    warnings = cx.warnings
    f = _resolve_fonts(fonts)
    css = [font_css(f, warnings)]
    fontmap = {}
    if cx.families:
        from ..design import fonts as dfonts
        for fam in sorted(cx.families):
            name = "sf-x-" + slug(fam)
            try:
                css.append(dfonts.role_css(name, fam, f.get("head_ar"), [300, 400, 500, 600, 700, 800, 900],
                                           lambda p: Path(p).resolve().as_uri()))
                fontmap[fam] = name
            except Exception as e:
                warnings.append(f"font {fam!r} unavailable ({e}); using the head font")
                fontmap[fam] = "sf-head"
    spec["fontmap"] = fontmap
    head = [libs.script_tags(["gsap"] + (["lottie"] if "lottie" in cx.need else []))]
    if "three" in cx.need:
        head.append(libs.three_importmap())
        head.append("<script>window.__need3d = true; window.__s3d = new Promise(r => window.__s3dResolve = r);</script>")
        head.append(MODULE_3D.format(uri=(ASSETS3D / "scene.js").resolve().as_uri()))
    spec_json = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<!-- studio motion_compose page: edit window.SPEC below (or the runtime) and re-render with motion_render_html -->
<style>{chr(10).join(css)}</style>
<style>{(ASSETS / 'compose.css').read_text(encoding='utf-8')}</style>
{chr(10).join(head)}
</head><body>
<svg width="0" height="0" style="position:absolute"><defs id="fx"></defs></svg>
<div id="comp"><div id="world"></div></div>
<script>window.SPEC = {spec_json};</script>
<script>{(ASSETS / 'compose.js').read_text(encoding='utf-8')}</script>
<script>{GATE}</script>
</body></html>"""
    page = work / "compose.html"
    page.write_text(html, encoding="utf-8")
    return page


def preview_stills(page: Path, spec: dict, transparent: bool, odir: Path, stem: str, n: int = 12) -> tuple[str, list[str], dict]:
    """Render only n instants (scene key moments) → labelled contact sheet. Fast iteration."""
    total = spec_total(spec)
    times = sorted({round(min(total - 1 / spec["fps"], max(0.0, total * (i + 0.5) / n)), 3) for i in range(n)})
    work = engine.scratch_dir("still-")
    try:
        r = engine.render_frames(page, work / "f", spec["width"], spec["height"], spec["fps"], total, transparent, times=times)
        sheet = unique_path(odir, stem + "-preview", "png")
        engine.frames_sheet(r["frames"], sheet, transparent, cols=4, times=times, fps=spec["fps"])
        return str(sheet), r.get("console", []), {"times": times}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render_spec(spec, *, template: str = "", fields: dict | None = None, size: str = "", fps: int = 0,
                transparent: bool | None = None, brand: str = "", colors: dict | None = None, fonts=None,
                formats=None, motion_blur: int = 0, preview: bool = False, project: str = "", out: str = "",
                name: str = "", summary: str = "") -> Result:
    warnings: list[str] = []
    if template:
        from .presets import build_template
        spec = build_template(template, fields or {}, size=size, brand=brand, base=load_spec(spec) if spec else None)
    else:
        spec = load_spec(spec)
    spec, fnt, cx = normalise(spec, size=size, fps=fps, brand=brand, colors=colors, fonts=fonts, transparent=transparent,
                              warnings=warnings)
    tr = bool(spec.get("transparent"))
    post = dict(spec.get("post") or {})
    mb = int(motion_blur or post.get("motion_blur") or 0)
    grain = float(post.get("grain", 0 if tr else 4))
    work = engine.scratch_dir("compose-")
    try:
        page = build_compose_page(spec, fnt, cx, work)
        base = name or slug(template or spec.get("name") or "compose")[:40]
        odir, stem = _out_target(out, project, base)
        spec_out = unique_path(odir, stem + "-spec", "json")
        spec_dump = {k: v for k, v in spec.items() if k not in ("fontmap",)}
        spec_out.write_text(json.dumps(_strip_inline(spec_dump), ensure_ascii=False, indent=1), encoding="utf-8")
        total = spec_total(spec)
        info = {"scenes": len(spec["scenes"]), "layers": sum(_count(s.get("layers") or []) for s in spec["scenes"]),
                "engine": "compose (GSAP " + libs.GSAP_V + ")", "spec": str(spec_out), "duration_planned": round(total, 3)}
        if template:
            info["template"] = template
        if preview:
            sheet, logs, d = preview_stills(page, spec, tr, odir, stem)
            master = unique_path(odir, stem + "-source", "html")
            shutil.copy2(page, master)
            res = Result(summary or f"Preview stills of the composition ({info['scenes']} scene(s), {total:.1f}s) — no video rendered.",
                         files=[str(spec_out), str(master)], previews=[sheet], warnings=warnings + [f"page {x}" for x in logs[:8]],
                         data={**info, **d})
            res.next_steps.append("happy with the stills → run again with preview=false for the video")
            return res
        post_fx = {k: post[k] for k in ("glow", "chroma") if post.get(k)}
        res = render_page(page, name=stem, width=spec["width"], height=spec["height"], fps=spec["fps"], duration=None,
                          transparent=tr, formats=formats, project=project, out=out, warnings=warnings,
                          summary=summary or f"Composition rendered: {info['scenes']} scene(s), {info['layers']} layer(s), {total:.1f}s"
                          + (f", motion blur {mb} samples" if mb > 1 else "") + ".",
                          data=info, background=spec["colors"].get("background", "#000000"), workers=0, grain=grain,
                          motion_blur=mb, post=post_fx, keyframes=3, extra_files=[str(spec_out)])
        res.next_steps.insert(0, f"tweak {spec_out.name} (layers, timing, presets) and re-render with motion_compose(spec=<that path>)")
        if cx.theme:
            res.data["brand"] = cx.theme["kit"].get("slug", brand)
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def spec_total(spec: dict) -> float:
    """Composition length exactly as the runtime computes it (transitions overlap scenes)."""
    if spec.get("duration"):
        return float(spec["duration"])
    t = 0.0
    for i, s in enumerate(spec["scenes"]):
        tr = s.get("transition") if i else None
        tr = {"type": tr} if isinstance(tr, str) else (tr or None)
        ov = float(tr.get("duration", 0.7)) if tr and tr.get("type") != "cut" else 0.0
        start = 0.0 if i == 0 else t - ov
        t = start + float(s["duration"])
    return t


def _count(layers: list) -> int:
    return sum(1 + _count(l.get("children") or []) for l in layers)


def _strip_inline(o):
    """Keep the saved spec readable: inline Lottie JSON / data URIs / 3D path data are replaced by a note."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k == "data" and isinstance(v, dict) and "layers" in v:
                out[k] = "<lottie JSON inlined at render time>"
            elif isinstance(v, str) and len(v) > 4000 and (v.startswith("data:") or k in ("svg", "glb")):
                out[k] = f"<{len(v)} chars inlined>"
            else:
                out[k] = _strip_inline(v)
        return out
    if isinstance(o, list):
        return [_strip_inline(x) for x in o]
    return o


# ================================================================================================ tool
@tool("motion")
def motion_compose(spec: dict = {}, template: str = "", fields: dict = {}, size: str = "", fps: int = 0,
                   transparent: bool = False, brand: str = "", colors: dict = {}, fonts: dict = {}, formats: list[str] = [],
                   motion_blur: int = 0, preview: bool = False, project: str = "", out: str = "") -> Result:
    """The pro motion-graphics engine (After Effects-style comp → video): write a JSON scene spec (or
    pick a ready template) and get a frame-exact MP4 / ProRes 4444 / WebM-alpha, a timecoded contact
    sheet + key frames to LOOK at, the standalone GSAP HTML and the spec JSON (editable masters).

    spec = {size, fps, background, colors, post:{grain, vignette, light_leaks, letterbox, glow, chroma,
    motion_blur}, camera, markers:{name: s}, scenes:[{duration, background, camera, transition:{type,
    duration}, layers:[…]}]} (or top-level layers for one scene). Layer: {type: text|shape|image|video|
    svg|lottie|icon|chart|counter|3d|group, id, x, y (or below|above: '<id of an earlier layer>' + gap — stacks on the
    MEASURED height, so wrapped/Arabic headlines never collide with the line under them), anchor, w, h, scale, rotation, opacity, z, blend, in,
    out, enter, exit, animate:[{preset, at, duration, ease, stagger, …}], keyframes:[{t,x,y,scale,
    rotation,opacity,ease}], tweens:[raw GSAP]} + type props (text: text with *emphasis*, font head|
    body|<Google family>, weight, size, color, align, rtl auto, bg, glow, shadow, stroke; shape: shape
    rect|circle|ring|line|star|polygon|heart|triangle|arrow|path, fill (colour or gradient), radius;
    image/video: src, fit, radius; svg: src or svg markup ('brand:logo'); lottie: src .json/.lottie,
    speed, loop, recolor; chart: chart bar|hbar|line|area|pie|donut, data [{label,value}]; 3d: scene
    {preset title|logo|product|particles|abstract|device, …}). Colours accept brand tokens (primary,
    accent, background, text, muted). Presets: fade slide rise drop scale zoom pop blur elastic bounce
    flip spin swing wipe iris block split-chars/words/lines (style rise|fade|pop|blur|rotate|punch)
    typewriter scramble highlight underline shine glitch draw morph motion-path counter + loops wiggle
    float parallax pulse kenburns shake; any preset takes -out for exits. Transitions: crossfade dip
    wipe slide push zoom blur iris whip shape glitch. Arabic text is shaped by Chromium and never split
    into letters. template=<name> + fields={…} builds a ready spec (see motion_templates);
    preview=true renders 12 stills only (fast iteration). motion_blur=8 → real sub-frame motion blur
    (8× render time). Full reference: docs/motion.md."""
    return render_spec(spec or None, template=template, fields=fields, size=size, fps=fps, transparent=transparent or None,
                       brand=brand, colors=colors, fonts=fonts, formats=formats, motion_blur=motion_blur, preview=preview,
                       project=project, out=out)
