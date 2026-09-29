"""Layered composition from a JSON spec → editable PSD + XCF (live GIMP text) + flattened PNG."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ...core.registry import tool
from ...core.result import MissingTool, Result, ToolError
from ._common import checkerboard, out_file, parse_color, preview_dir, save_image
from .fonts import family_name
from .psd import BLEND_KEYS, PsdLayer, write_psd
from .render import composite_onto, gradient_fill, render_image_layer, render_shape, render_text, shadow_of

SPEC_HELP = """{
 "width": 1080, "height": 1350, "background": "#0f172a" | "linear:#0f172a,#1e3a8a,90" | "/path/photo.jpg" | "transparent",
 "layers": [   // bottom → top
  {"type": "image", "name": "Photo", "src": "/path/a.jpg", "x": 0, "y": 0, "width": 1080, "height": 900,
   "fit": "cover|contain|stretch", "radius": 24, "remove_background": false, "rotate": 0},
  {"type": "shape", "name": "Card", "shape": "rect|ellipse|polygon|line", "x": 60, "y": 980, "width": 960, "height": 300,
   "radius": 32, "fill": "#ffffff" | {"type":"linear","colors":["#f59e0b","#ef4444"],"angle":0}, "stroke": {"color":"#000","width":2},
   "points": [[x,y],…] (polygon/line), "line_width": 6},
  {"type": "text", "name": "Headline", "text": "عرض خاص النهارده بس", "font": "Cairo", "weight": 800, "size": 96,
   "color": "#ffffff", "x": 80, "y": 1000, "width": 920, "align": "right|left|center", "direction": "auto|rtl|ltr",
   "line_height": 1.25, "letter_spacing": 0, "stroke": {"color": "#000", "width": 3}},
  {"type": "fill", "name": "Tint", "fill": "#000000" | gradient, "opacity": 0.3, "blend": "multiply"}
 ]
 // every layer: "opacity" 0–1, "blend" (normal multiply screen overlay soft_light hard_light darken lighten
 // difference exclusion color_dodge color_burn add hue saturation color luminosity), "visible",
 // "shadow": true | {"color":"#000","dx":0,"dy":12,"blur":24,"opacity":0.4,"spread":0}  (becomes its own layer)
}"""


def _load_spec(spec) -> dict:
    if isinstance(spec, str):
        p = Path(spec).expanduser()
        if p.exists():
            spec = json.loads(p.read_text(encoding="utf-8"))
        else:
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError:
                raise ToolError("spec must be a dict, a JSON string or a path to a .json file")
    if not isinstance(spec, dict) or "layers" not in spec:
        raise ToolError("spec needs 'width', 'height' and 'layers'", SPEC_HELP)
    return spec


def build_layers(spec: dict) -> tuple[int, int, list[PsdLayer], list[dict], list[str]]:
    W, H = int(spec.get("width", 1080)), int(spec.get("height", 1080))
    if W * H > 60e6:
        raise ToolError("canvas too large (max 60 MP)")
    layers: list[PsdLayer] = []
    texts: list[dict] = []
    warns: list[str] = []
    names: set[str] = set()

    def uniq(n: str) -> str:
        base, k = n, 2
        while n in names:
            n = f"{base} {k}"
            k += 1
        names.add(n)
        return n

    bg = spec.get("background", "#ffffff")
    if bg and bg != "transparent":
        layers.append(PsdLayer(uniq("Background"), gradient_fill(bg, (W, H)), 0, 0))
    for i, L in enumerate(spec["layers"]):
        t = L.get("type", "image")
        name = uniq(str(L.get("name") or f"{t.title()} {i + 1}"))
        op, blend, vis = float(L.get("opacity", 1.0)), L.get("blend", "normal"), bool(L.get("visible", True))
        if blend not in BLEND_KEYS:
            raise ToolError(f"layer '{name}': unknown blend {blend!r}", ", ".join(BLEND_KEYS))
        info = None
        if t == "image":
            im, x, y, w2 = render_image_layer(L, (W, H))
            warns += w2
        elif t == "text":
            im, x, y, w2, info = render_text(L, (W, H))
            warns += w2
        elif t == "shape":
            im, x, y = render_shape(L)
        elif t in ("fill", "gradient"):
            im, x, y = gradient_fill(L.get("fill", L.get("colors") and {"type": "linear", "colors": L["colors"], "angle": L.get("angle", 90)} or "#000"), (W, H)), 0, 0
        else:
            raise ToolError(f"layer '{name}': unknown type {t!r}", "image | text | shape | fill")
        if L.get("shadow"):
            sh, sx, sy = shadow_of(im, x, y, L["shadow"])
            layers.append(PsdLayer(uniq(f"{name} shadow"), sh, sx, sy, op, "multiply" if blend == "normal" else "normal", vis))
        # crop layer to what's visible on canvas (PSD layers can extend, but keep files lean)
        bb = im.getchannel("A").getbbox()
        if bb:
            l, tp, r, b = bb
            l2, t2 = max(l, -x), max(tp, -y)
            r2, b2 = min(r, W - x), min(b, H - y)
            if r2 > l2 and b2 > t2:
                im, x, y = im.crop((l2, t2, r2, b2)), x + l2, y + t2
            else:
                warns.append(f"layer '{name}' is entirely outside the canvas")
        layers.append(PsdLayer(name, im, x, y, op, blend, vis))
        if info:
            fam_file = Path(info["font_file"])
            from PIL import ImageFont
            fnt = ImageFont.truetype(str(fam_file), 20)
            fam, style = fnt.getname()
            asc, desc = ImageFont.truetype(str(fam_file), info["size"]).getmetrics()
            col = parse_color(L.get("color", "#111111"))
            texts.append({"name": name, "text": "\n".join(info["lines"]), "font": f"{fam} {style}".replace(" Regular", ""),
                          "family": fam, "size": info["size"], "align": info["align"], "direction": info["direction"],
                          "x": info["box"][0], "y": info["box"][1], "w": info["box"][2] + 4,
                          "h": info["box"][3] + int(info["size"] * 0.6),
                          "line_spacing": round(info["line_height_px"] - (asc + desc), 2),
                          "letter_spacing": float(L.get("letter_spacing", 0) or 0), "rgb": list(col[:3]),
                          "opacity": op, "visible": vis, "has_stroke": bool((L.get("stroke") or {}).get("width"))})
    return W, H, layers, texts, warns


def flatten_layers(W: int, H: int, layers: list[PsdLayer]) -> Image.Image:
    base = np.zeros((H, W, 4), np.float32)
    for L in layers:
        if L.visible:
            composite_onto(base, L.image.convert("RGBA"), L.left, L.top, L.opacity, L.blend)
    return Image.fromarray((np.clip(base, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")


def layers_sheet(W: int, H: int, layers: list[PsdLayer], dest: Path) -> Path:
    from .fonts import pil_font
    tile = 220
    f, _, _ = pil_font("", 15, 600, text="".join(L.name for L in layers))
    cols = min(5, len(layers))
    rows = (len(layers) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tile + 12) + 12, rows * (tile + 44) + 12), (28, 28, 30))
    d = ImageDraw.Draw(sheet)
    s = tile / max(W, H)
    tw, th = int(W * s), int(H * s)
    for i, L in enumerate(reversed(layers)):  # top layer first, like the Layers panel
        c = checkerboard((tw, th), 8)
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        lay.alpha_composite(L.image.convert("RGBA"), (max(0, L.left), max(0, L.top))) if L.left >= 0 and L.top >= 0 else lay.paste(L.image, (L.left, L.top), L.image)
        c.alpha_composite(lay.resize((tw, th), Image.LANCZOS))
        x = 12 + (i % cols) * (tile + 12)
        y = 12 + (i // cols) * (tile + 44)
        sheet.paste(c.convert("RGB"), (x + (tile - tw) // 2, y + (tile - th) // 2))
        label = f"{L.name}  {L.blend} {int(L.opacity * 100)}%{'' if L.visible else ' (hidden)'}"
        d.text((x, y + tile + 8), label[:34], font=f, fill=(230, 230, 230))
    sheet.save(dest)
    return dest


def photo_compose_layers(spec: dict, name: str = "composition", psd: bool = True, xcf: bool = True,
                         png: bool = True, jpg: bool = False, verify: bool = True, project: str = "", out: str = "") -> Result:
    """The 'Photoshop file' deliverable: build a layered design from a JSON spec (photos, cut-outs, text
    incl. Arabic/RTL with any Google/system font, shapes, gradients, blend modes, opacity, drop shadows as
    separate layers) and write (1) an editable layered PSD (opens in Photoshop/Photopea/GIMP/Krita/
    Affinity — pixel layers with names, positions, blend modes, opacity; text as pixels), (2) a GIMP XCF
    where text layers are LIVE editable GIMP text (needs GIMP, 2.10 or 3.x), (3) flattened PNG (and JPG).
    Returns files + the flattened preview + a layers sheet; with verify, GIMP re-renders the XCF and the
    difference to our PNG is reported. spec is a dict or a path to a .json file (format below). Fonts
    missing locally are fetched free from Google Fonts."""
    spec = _load_spec(spec)
    W, H, layers, texts, warns = build_layers(spec)
    flat = flatten_layers(W, H, layers)
    files, previews = [], []
    stem = name
    base_png = out_file(project, out, stem, ".png")
    save_image(flat if spec.get("background") == "transparent" else flat.convert("RGB"), base_png)
    if png:
        files.append(str(base_png))
    if jpg:
        jp = out_file(project, out, stem, ".jpg")
        save_image(flat.convert("RGB"), jp, 92)
        files.append(str(jp))
    psd_path = out_file(project, out, stem, ".psd")
    write_psd(psd_path, W, H, layers, flat, dpi=int(spec.get("dpi", 72)))
    if psd:
        files.insert(0, str(psd_path))
    data: dict = {"size": [W, H], "layers": [{"name": L.name, "blend": L.blend, "opacity": L.opacity,
                                               "bounds": [L.left, L.top, L.image.width, L.image.height]} for L in layers]}
    if xcf:
        xcf_path = out_file(project, out, stem, ".xcf")
        check = preview_dir(base_png) / f"{xcf_path.stem}-gimp-render.png" if verify else None
        try:
            from .gimp import psd_to_xcf
            info = psd_to_xcf(psd_path, xcf_path, texts, check)
            files.insert(1, str(xcf_path))
            data["gimp"] = {k: v for k, v in info.items() if k != "log_tail"}
            stroked = [t["name"] for t in texts if t["has_stroke"]]
            if stroked:
                warns.append(f"text stroke/outline is not a GIMP text property — in the XCF these are plain text: {', '.join(stroked)}")
            if check and check.exists():
                g = np.asarray(Image.open(check).convert("RGB").resize((W, H)), np.float32)
                o = np.asarray(flat.convert("RGB"), np.float32)
                diff = float(np.abs(g - o).mean())
                data["xcf_vs_png_mean_diff"] = round(diff, 2)
                previews.append(str(check))
                if diff > 6:
                    warns.append(f"GIMP's render of the XCF differs from the PNG (mean diff {diff:.1f}/255) — "
                                 "usually text metrics; open the XCF and nudge text layers if needed")
        except MissingTool as e:
            warns.append(f"XCF skipped: {e} ({e.hint}). The PSD also opens in GIMP.")
        except ToolError as e:
            warns.append(f"XCF failed: {e}")
    if not psd:
        psd_path.unlink(missing_ok=True)
    previews.insert(0, str(base_png) if W * H <= 4e6 else _small(base_png))
    previews.insert(1, str(layers_sheet(W, H, layers, preview_dir(base_png) / f"{stem}-layers.png")))
    texts_ar = [t["name"] for t in texts if t["direction"] == "rtl"]
    return Result(f"Layered composition {W}×{H} with {len(layers)} layers ({len(texts)} text{', RTL: ' + ', '.join(texts_ar) if texts_ar else ''}).",
                  files=files, previews=previews, warnings=warns, data=data,
                  next_steps=["open_in_app the .xcf (GIMP) or .psd (Photoshop/Photopea) to edit by hand",
                              "photo_mockup to place the flattened PNG on a phone/poster"])


photo_compose_layers.__doc__ += "\n\nSPEC format:\n" + SPEC_HELP
tool("photo", network=True)(photo_compose_layers)


def _small(p: Path) -> str:
    from ._common import preview
    return preview(p)
