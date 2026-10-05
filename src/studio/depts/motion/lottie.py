"""Lottie (Bodymovin) animations: render frame-exactly through lottie-web in Chromium, recolour to brand
colours, retime, loop, alpha output; also used as layers inside motion_compose."""
from __future__ import annotations

import base64
import colorsys
import io
import json
import mimetypes
import shutil
import zipfile
from pathlib import Path

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import engine, libs
from . import BRAND_DOC, _formats, apply_brand, parse_size, render_page


# ------------------------------------------------------------------------------------------- loading
def load_lottie(path: Path) -> dict:
    """.json (Bodymovin) or .lottie (dotLottie zip, v1 animations/ or v2 a/) → animation dict with any
    image assets embedded as data URIs."""
    p = Path(path)
    if p.suffix.lower() == ".lottie" or zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            anim_name = None
            try:
                man = json.loads(z.read("manifest.json"))
                first = (man.get("animations") or [{}])[0].get("id")
                for cand in (f"animations/{first}.json", f"a/{first}.json"):
                    if cand in names:
                        anim_name = cand
            except Exception:
                pass
            anim_name = anim_name or next((n for n in names if n.endswith(".json") and n.split("/")[0] in ("animations", "a")), None)
            if not anim_name:
                raise ToolError(f"{p.name}: no animation JSON inside the .lottie archive")
            data = json.loads(z.read(anim_name))
            for a in data.get("assets", []):
                if a.get("p") and not str(a.get("p", "")).startswith("data:") and "layers" not in a:
                    for cand in (f"images/{a['p']}", f"i/{a['p']}", (a.get("u") or "").lstrip("/") + a["p"]):
                        if cand in names:
                            mt = mimetypes.guess_type(a["p"])[0] or "image/png"
                            a["p"] = f"data:{mt};base64," + base64.b64encode(z.read(cand)).decode()
                            a["u"], a["e"] = "", 1
                            break
            return data
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ToolError(f"{p.name} is not a Lottie JSON ({e})")
    if not isinstance(data, dict) or "layers" not in data or "fr" not in data:
        raise ToolError(f"{p.name} does not look like a Lottie animation (no layers/fr)")
    for a in data.get("assets", []):  # external images next to the json
        if a.get("p") and not a.get("e") and "layers" not in a:
            f = p.parent / (a.get("u") or "") / a["p"]
            if f.exists():
                mt = mimetypes.guess_type(f.name)[0] or "image/png"
                a["p"] = f"data:{mt};base64," + base64.b64encode(f.read_bytes()).decode()
                a["u"], a["e"] = "", 1
    return data


def info(data: dict) -> dict:
    fr = float(data.get("fr") or 30)
    ip, op = float(data.get("ip", 0)), float(data.get("op", 0))
    feats = set()

    def walk(o):
        if isinstance(o, dict):
            if o.get("ef"):
                feats.add("layer effects")
            if "x" in o and isinstance(o.get("x"), str) and o["x"].strip():
                feats.add("expressions")
            if o.get("ty") == 5:
                feats.add("text layers")
            if o.get("tt"):
                feats.add("track mattes")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data.get("layers", []))
    for a in data.get("assets", []):
        walk(a.get("layers", []))
    return {"fps": fr, "frames": op - ip, "seconds": round((op - ip) / fr, 3), "width": data.get("w"), "height": data.get("h"),
            "layers": len(data.get("layers", [])), "images": sum(1 for a in data.get("assets", []) if a.get("p")),
            "features": sorted(feats), "name": data.get("nm", "")}


# ------------------------------------------------------------------------------------------- colours
def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c * 255))) for c in rgb[:3])


def _rgb(hexs: str) -> tuple[float, float, float]:
    h = hexs.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _color_slots(data: dict):
    """Yield (container, key, index, kind) for every colour value: solid fills/strokes (static or
    animated), gradient stops, text fill/stroke, solid-layer colours."""
    def walk(o):
        if isinstance(o, dict):
            ty = o.get("ty")
            if ty in ("fl", "st") and isinstance(o.get("c"), dict):
                c = o["c"]
                if c.get("a"):
                    for kf in c.get("k", []):
                        for key in ("s", "e"):
                            if isinstance(kf, dict) and isinstance(kf.get(key), list) and len(kf[key]) >= 3:
                                yield kf, key, 0, "rgb"
                elif isinstance(c.get("k"), list) and len(c["k"]) >= 3 and not isinstance(c["k"][0], dict):
                    yield c, "k", 0, "rgb"
            if ty in ("gf", "gs") and isinstance(o.get("g"), dict):
                n = int(o["g"].get("p", 0))
                k = o["g"].get("k", {})
                if isinstance(k, dict) and not k.get("a") and isinstance(k.get("k"), list):
                    for i in range(n):
                        yield k["k"], None, i * 4 + 1, "grad"
                elif isinstance(k, dict) and k.get("a"):
                    for kf in k.get("k", []):
                        for key in ("s", "e"):
                            if isinstance(kf, dict) and isinstance(kf.get(key), list):
                                for i in range(n):
                                    yield kf[key], None, i * 4 + 1, "grad"
            if ty == 1 and isinstance(o.get("sc"), str):
                yield o, "sc", 0, "hex"
            if "t" in o and isinstance(o["t"], dict) and isinstance(o["t"].get("d"), dict):
                for kf in o["t"]["d"].get("k", []):
                    s = kf.get("s") if isinstance(kf, dict) else None
                    if isinstance(s, dict):
                        for key in ("fc", "sc"):
                            if isinstance(s.get(key), list) and len(s[key]) >= 3:
                                yield s, key, 0, "rgb"
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)
    yield from walk(data.get("layers", []))
    for a in data.get("assets", []):
        yield from walk(a.get("layers", []))


def _get(slot) -> tuple[float, float, float]:
    cont, key, i, kind = slot
    if kind == "hex":
        return _rgb(cont[key])
    if kind == "grad":
        return tuple(float(x) for x in cont[i:i + 3])
    v = cont[key]
    return tuple(float(x) for x in v[:3])


def _set(slot, rgb) -> None:
    cont, key, i, kind = slot
    if kind == "hex":
        cont[key] = _hex(rgb)
    elif kind == "grad":
        cont[i:i + 3] = [round(c, 4) for c in rgb]
    else:
        cont[key][:3] = [round(c, 4) for c in rgb]


def palette(data: dict) -> list[dict]:
    """Distinct colours used, most frequent first."""
    cnt: dict[str, int] = {}
    for s in _color_slots(data):
        try:
            h = _hex(_get(s))
        except Exception:
            continue
        cnt[h] = cnt.get(h, 0) + 1
    return [{"color": k, "uses": v} for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])]


def recolor(data: dict, mapping, brand_colors: dict | None = None, tolerance: float = 0.12) -> tuple[dict, dict]:
    """mapping: {"#from": "#to", …} (nearest match within tolerance) | "brand" (most used chromatic colours
    → primary, accent, secondary…; near-white/black kept) | "mono:#hex" (every colour becomes a shade of
    #hex, keeping its lightness) | {"palette": [...]} (like brand with these colours)."""
    data = json.loads(json.dumps(data))
    applied: dict[str, str] = {}
    slots = list(_color_slots(data))
    if isinstance(mapping, str) and mapping.startswith("mono:"):
        th, _tl, ts = colorsys.rgb_to_hls(*_rgb(mapping[5:]))
        for s in slots:
            r = _get(s)
            _h, l, _s = colorsys.rgb_to_hls(*r)
            nr = colorsys.hls_to_rgb(th, l, ts if _s > 0.05 else _s)
            applied[_hex(r)] = _hex(nr)
            _set(s, nr)
        return data, applied
    if mapping == "brand" or (isinstance(mapping, dict) and "palette" in mapping):
        pal = mapping["palette"] if isinstance(mapping, dict) else None
        bc = brand_colors or {}
        pal = pal or [bc.get(k) for k in ("primary", "accent", "secondary", "muted") if bc.get(k)] or ["#FF5A36", "#FFC53D"]
        chroma = [p["color"] for p in palette(data) if colorsys.rgb_to_hls(*_rgb(p["color"]))[2] > 0.15
                  and 0.08 < colorsys.rgb_to_hls(*_rgb(p["color"]))[1] < 0.94]
        mapping = {c: pal[i % len(pal)] for i, c in enumerate(chroma)}
    if not isinstance(mapping, dict):
        raise ToolError("recolor must be a {from: to} dict, 'brand', 'mono:#hex' or {palette: [...]}")
    pairs = [(_rgb(k), _rgb(v), k, v) for k, v in mapping.items()]
    for s in slots:
        r = _get(s)
        best = min(pairs, key=lambda p: sum((a - b) ** 2 for a, b in zip(r, p[0])) ** 0.5, default=None)
        if best and sum((a - b) ** 2 for a, b in zip(r, best[0])) ** 0.5 <= tolerance:
            _set(s, best[1])
            applied[_hex(r)] = best[3].upper()
    return data, applied


# ------------------------------------------------------------------------------------------- page
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:{bg}}}#a{{position:absolute;inset:{pad}px}}</style>
{libs}</head><body><div id="a"></div>
<script>
const DATA = {data};
const an = lottie.loadAnimation({{container: document.getElementById('a'), renderer: 'svg', loop: false, autoplay: false,
  animationData: DATA, rendererSettings: {{preserveAspectRatio: '{par}'}}}});
an.__studio = {{start: 0, speed: {speed}, loop: true}};
window.__duration = {duration};
window.__ready = new Promise(r => {{ if (an.isLoaded) r(); an.addEventListener('DOMLoaded', r); setTimeout(r, 8000); }});
</script></body></html>"""


@tool("motion", network=True)
def motion_lottie(src: str, size: str = "", fit: str = "contain", speed: float = 1.0, loops: int = 1, duration: float = 0,
                  recolor_map: dict = {}, recolor_mode: str = "", transparent: bool = False, background: str = "",
                  padding: int = 0, fps: int = 0, brand: str = "", formats: list[str] = [], project: str = "",
                  out: str = "") -> Result:
    """Render a Lottie animation (.json Bodymovin or .lottie dotLottie, e.g. from LottieFiles or an
    After Effects export) frame-exactly to MP4 / ProRes 4444 / WebM alpha / GIF, with optional brand
    recolouring and retiming. Returns the video, a timecoded contact sheet, key frames, and data
    (native fps/size/length, colours used, features such as expressions/effects that lottie-web may
    not fully support).

    size: '' = the animation's own size (capped at 1920) | 1080x1080 | 9:16 …; fit contain|cover.
    speed: 0.5 = half speed; loops: play N times; duration (s) overrides the length (loops forever).
    recolor_map {"#FF0000": "#00AA88", …} (nearest colour within tolerance) or recolor_mode brand
    (uses brand=<slug> colours on the most used colours) | mono:#hex (every colour → a shade of #hex).
    transparent=true → alpha (.mov + .webm); background: colour behind it otherwise (brand background
    or white). fps: 0 = the animation's own fps. To use a Lottie as a LAYER in a bigger composition use
    motion_compose with {"type": "lottie", "src": …}."""
    p = Path(src).expanduser()
    if not p.exists():
        raise ToolError(f"lottie file not found: {p}")
    warnings: list[str] = []
    data = load_lottie(p)
    meta = info(data)
    colors, _f, theme = apply_brand(brand, {}, {}, warnings)
    applied = {}
    mode = recolor_mode or ("brand" if brand and not recolor_map else "")
    if recolor_map or mode:
        data, applied = recolor(data, recolor_map or mode, colors or None)
        if not applied:
            warnings.append("recolour matched no colours — see data.palette for the colours used")
    if meta["features"]:
        warnings.append("uses " + ", ".join(meta["features"]) + " — lottie-web renders most of these, but check the "
                        "contact sheet (AE effects other than fills/strokes/blur are not supported by any Lottie player)")
    if size:
        w, h = parse_size(size)
    else:
        w, h = int(meta["width"] or 1080), int(meta["height"] or 1080)
        k = min(1.0, 1920 / max(w, h))
        w, h = int(w * k) // 2 * 2, int(h * k) // 2 * 2
    fps_ = int(fps or round(meta["fps"]) or 30)
    speed = max(0.05, float(speed or 1))
    dur = float(duration) if duration and duration > 0 else meta["seconds"] / speed * max(1, int(loops or 1))
    if dur <= 0:
        raise ToolError("the animation has no length (op ≤ ip)")
    bg = "transparent" if transparent else (background or (colors.get("background") if colors else "") or "#FFFFFF")
    par = "xMidYMid slice" if fit == "cover" else "xMidYMid meet"
    work = engine.scratch_dir("lottie-")
    try:
        page = work / "lottie.html"
        page.write_text(PAGE.format(bg=bg, pad=int(padding), libs=libs.script_tags(["lottie"]), data=json.dumps(data),
                                    par=par, speed=speed, duration=round(dur, 4)), encoding="utf-8")
        res = render_page(page, name="lottie-" + (p.stem[:30] or "anim"), width=w, height=h, fps=fps_, duration=dur,
                          transparent=transparent, formats=formats, project=project, out=out, warnings=warnings,
                          summary=f"Lottie '{p.name}' rendered frame-exactly ({dur:.2f}s at {fps_} fps"
                          + (f", {len(applied)} colour(s) remapped" if applied else "") + ").",
                          data={"lottie": meta, "palette": palette(data)[:12], "recolored": applied, "engine": "lottie-web " + libs.LOTTIE_V},
                          background=bg if bg != "transparent" else "#000000", keyframes=2, grain=0)
        res.next_steps.append("drop it into a composition as a layer: motion_compose(spec={… {\"type\":\"lottie\",\"src\":…}})")
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
