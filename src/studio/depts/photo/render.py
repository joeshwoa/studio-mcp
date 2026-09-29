"""Rendering primitives for layered compositions: text (Arabic-aware), shapes, images, shadows,
and Photoshop-compatible blend modes for the flattened preview."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ...core.result import ToolError
from ._common import open_image, parse_color
from .fonts import has_arabic, pil_font


# ----------------------------------------------------------------------------- text
def wrap_lines(text: str, font, max_width: int, direction: str | None) -> list[str]:
    out = []
    for para in text.split("\n"):
        if not max_width:
            out.append(para)
            continue
        words = para.split(" ")
        line = ""
        for w in words:
            cand = (line + " " + w).strip()
            if font.getlength(cand, direction=direction) <= max_width or not line:
                line = cand
            else:
                out.append(line)
                line = w
        out.append(line)
    return out


def render_text(spec: dict, canvas: tuple[int, int]) -> tuple[Image.Image, int, int, list[str], dict]:
    """Returns (RGBA tight image, left, top, warnings, layout info). Spec keys: text, font, weight, size,
    color, x, y, width (wrap box), align (left|center|right; default right for Arabic), direction
    (auto|rtl|ltr), line_height, letter_spacing (Latin only), stroke {color,width}, uppercase."""
    text = str(spec.get("text", ""))
    if spec.get("uppercase"):
        text = text.upper()
    size = int(spec.get("size", 64))
    font, warns, fpath = pil_font(spec.get("font", ""), size, spec.get("weight", 400), bool(spec.get("italic")), text)
    ar = has_arabic(text)
    direction = spec.get("direction", "auto")
    direction = ("rtl" if ar else "ltr") if direction == "auto" else direction
    align = spec.get("align") or ("right" if direction == "rtl" else "left")
    box_w = int(spec.get("width", 0) or 0)
    lines = wrap_lines(text, font, box_w, direction)
    asc, desc = font.getmetrics()
    lh = float(spec.get("line_height", 1.25 if ar else 1.15)) * size
    ls = float(spec.get("letter_spacing", 0) or 0)
    if ls and ar:
        warns.append("letter_spacing ignored for Arabic (it would break letter joining)")
        ls = 0
    widths = [(font.getlength(l, direction=direction) + ls * max(0, len(l) - 1)) for l in lines]
    content_w = int(max(widths or [0])) + 2
    W = max(box_w, content_w)
    stroke = spec.get("stroke") or {}
    sw = int(stroke.get("width", 0))
    pad = sw + int(size * 0.35)
    H = int(lh * (len(lines) - 1) + asc + desc)
    img = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = parse_color(spec.get("color", "#111111"))
    scol = parse_color(stroke.get("color", "#000000")) if sw else None
    for i, (line, lw) in enumerate(zip(lines, widths)):
        y = pad + i * lh
        if align == "center":
            x = pad + (W - lw) / 2
        elif align == "right":
            x = pad + W - lw
        else:
            x = pad
        if ls and not ar:
            cx = x
            for ch in line:
                d.text((cx, y), ch, font=font, fill=col, stroke_width=sw, stroke_fill=scol)
                cx += font.getlength(ch) + ls
        else:
            d.text((x, y), line, font=font, fill=col, direction=direction, stroke_width=sw, stroke_fill=scol,
                   features=["liga", "kern"] if not ar else None)
    left = int(spec.get("x", 0)) - pad
    top = int(spec.get("y", 0)) - pad
    info = {"font_file": str(fpath), "lines": lines, "box": [int(spec.get("x", 0)), int(spec.get("y", 0)), W, H],
            "align": align, "direction": direction, "size": size, "line_height_px": lh}
    if box_w and content_w > box_w + 2:
        warns.append(f"text '{text[:24]}…' has a word wider than its box ({content_w}px > {box_w}px)")
    if int(spec.get("x", 0)) + W > canvas[0] or int(spec.get("y", 0)) + H > canvas[1] or spec.get("x", 0) < 0:
        warns.append(f"text '{text[:24]}' runs outside the canvas")
    return img, left, top, warns, info


# ----------------------------------------------------------------------------- fills & shapes
def gradient_fill(spec, size: tuple[int, int]) -> Image.Image:
    """spec: colour string, or {'type':'linear'|'radial','colors':[…],'angle':deg} or 'linear:#a,#b,90'."""
    from .cutout import _background
    if isinstance(spec, dict):
        kind = spec.get("type", "linear")
        s = f"{kind}:" + ",".join(spec.get("colors", ["#fff", "#000"])) + (f",{spec.get('angle', 90)}" if kind == "linear" else "")
        return _background(s, size)
    return _background(str(spec), size)


def render_shape(spec: dict) -> tuple[Image.Image, int, int]:
    shape = spec.get("shape", "rect")
    x, y = int(spec.get("x", 0)), int(spec.get("y", 0))
    w, h = int(spec.get("width", 100)), int(spec.get("height", 100))
    stroke = spec.get("stroke") or {}
    sw = int(stroke.get("width", 0)) if isinstance(stroke, dict) else 0
    pad = sw
    if shape in ("polygon", "line"):
        pts = [(float(p[0]), float(p[1])) for p in spec["points"]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x, y = int(min(xs)), int(min(ys))
        w, h = int(max(xs) - x) + 1, int(max(ys) - y) + 1
        pad = sw + (int(spec.get("line_width", 4)) if shape == "line" else 0)
    ss = 3  # supersample for smooth edges
    W, H = (w + 2 * pad) * ss, (h + 2 * pad) * ss
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    box = (pad * ss, pad * ss, (pad + w) * ss - 1, (pad + h) * ss - 1)
    if shape == "rect":
        d.rounded_rectangle(box, radius=int(spec.get("radius", 0)) * ss, fill=255)
    elif shape in ("ellipse", "circle"):
        d.ellipse(box, fill=255)
    elif shape == "polygon":
        d.polygon([((px - x + pad) * ss, (py - y + pad) * ss) for px, py in pts], fill=255)
    elif shape == "line":
        lw = int(spec.get("line_width", 4))
        d.line([((px - x + pad) * ss, (py - y + pad) * ss) for px, py in pts], fill=255, width=lw * ss, joint="curve")
    else:
        raise ToolError(f"unknown shape {shape!r}", "rect | ellipse | polygon | line")
    fill = spec.get("fill", "#000000" if shape != "line" else spec.get("color", "#000000"))
    if shape == "line":
        fill = spec.get("color", spec.get("fill", "#000000"))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if fill and fill != "none":
        paint = gradient_fill(fill, (W, H)) if (isinstance(fill, dict) or ":" in str(fill)) else Image.new("RGBA", (W, H), parse_color(fill))
        a = np.asarray(paint.getchannel("A"), np.float32) * np.asarray(mask, np.float32) / 255
        paint.putalpha(Image.fromarray(a.astype(np.uint8)))
        out = paint
    if sw:
        dil = mask.filter(ImageFilter.MaxFilter(2 * sw * ss + 1 if sw * ss < 20 else 41))
        if sw * ss >= 20:  # big strokes: repeated dilation
            for _ in range(int(sw * ss / 20)):
                dil = dil.filter(ImageFilter.MaxFilter(41))
        ring = Image.fromarray(np.clip(np.asarray(dil, np.int16) - np.asarray(mask, np.int16), 0, 255).astype(np.uint8))
        sc = Image.new("RGBA", (W, H), parse_color(stroke.get("color", "#000")))
        sc.putalpha(ring)
        out = Image.alpha_composite(out, sc)
    out = out.resize((W // ss, H // ss), Image.LANCZOS)
    return out, x - pad, y - pad


def render_image_layer(spec: dict, canvas: tuple[int, int]) -> tuple[Image.Image, int, int, list[str]]:
    warns = []
    src = spec.get("src") or spec.get("path")
    if not src:
        raise ToolError("image layer needs 'src'")
    im = open_image(src).convert("RGBA")
    if spec.get("remove_background"):
        from .cutout import subject_mask, decontaminate
        m, w2 = subject_mask(im)
        warns += w2
        im = decontaminate(im.convert("RGB"), m)
    x, y = int(spec.get("x", 0)), int(spec.get("y", 0))
    w, h = spec.get("width"), spec.get("height")
    fit = spec.get("fit", "cover")
    if w or h:
        w = int(w or im.width * h / im.height)
        h = int(h or im.height * w / im.width)
        if fit == "cover":
            from .saliency import best_crop
            box, _ = best_crop(im, w / h) if spec.get("smart_crop", True) else ((0, 0, im.width, im.height), {})
            im = ImageOps.fit(im.crop(box), (w, h), Image.LANCZOS)
        elif fit == "contain":
            im = ImageOps.contain(im, (w, h), Image.LANCZOS)
            x += (w - im.width) // 2
            y += (h - im.height) // 2
        else:
            im = im.resize((w, h), Image.LANCZOS)
    if spec.get("radius"):
        r = int(spec["radius"])
        m = Image.new("L", (im.width * 3, im.height * 3), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, m.width - 1, m.height - 1), radius=r * 3, fill=255)
        m = m.resize(im.size, Image.LANCZOS)
        a = np.asarray(im.getchannel("A"), np.float32) * np.asarray(m, np.float32) / 255
        im.putalpha(Image.fromarray(a.astype(np.uint8)))
    if spec.get("rotate"):
        cx, cy = x + im.width / 2, y + im.height / 2
        im = im.rotate(float(spec["rotate"]), resample=Image.BICUBIC, expand=True)
        x, y = int(cx - im.width / 2), int(cy - im.height / 2)
    return im, x, y, warns


def shadow_of(im: Image.Image, left: int, top: int, spec) -> tuple[Image.Image, int, int]:
    """Drop shadow as its own layer. spec: true or {color, dx, dy, blur, opacity, spread}."""
    if spec is True:
        spec = {}
    col = parse_color(spec.get("color", "#000000"))
    blur = float(spec.get("blur", max(4, min(im.size) / 25)))
    dx, dy = int(spec.get("dx", 0)), int(spec.get("dy", max(2, blur / 2)))
    op = float(spec.get("opacity", 0.45))
    pad = int(blur * 2.5) + 2
    a = Image.new("L", (im.width + 2 * pad, im.height + 2 * pad), 0)
    a.paste(im.getchannel("A"), (pad, pad))
    if spec.get("spread"):
        a = a.filter(ImageFilter.MaxFilter(int(spec["spread"]) * 2 + 1))
    a = a.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * op * col[3] / 255))
    sh = Image.new("RGBA", a.size, col[:3] + (0,))
    sh.putalpha(a)
    return sh, left - pad + dx, top - pad + dy


# ----------------------------------------------------------------------------- blending
def _blend(cb: np.ndarray, cs: np.ndarray, mode: str) -> np.ndarray:
    if mode in ("normal", "dissolve"):
        return cs
    if mode == "multiply":
        return cb * cs
    if mode == "screen":
        return cb + cs - cb * cs
    if mode == "overlay":
        return np.where(cb <= 0.5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))
    if mode == "hard_light":
        return np.where(cs <= 0.5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))
    if mode == "soft_light":
        d = np.where(cb <= 0.25, ((16 * cb - 12) * cb + 4) * cb, np.sqrt(cb))
        return np.where(cs <= 0.5, cb - (1 - 2 * cs) * cb * (1 - cb), cb + (2 * cs - 1) * (d - cb))
    if mode == "darken":
        return np.minimum(cb, cs)
    if mode == "lighten":
        return np.maximum(cb, cs)
    if mode == "difference":
        return np.abs(cb - cs)
    if mode == "exclusion":
        return cb + cs - 2 * cb * cs
    if mode == "color_dodge":
        return np.where(cs >= 1, 1.0, np.minimum(1, cb / np.maximum(1 - cs, 1e-6)))
    if mode == "color_burn":
        return np.where(cs <= 0, 0.0, 1 - np.minimum(1, (1 - cb) / np.maximum(cs, 1e-6)))
    if mode in ("add", "linear_dodge"):
        return np.minimum(1, cb + cs)
    if mode in ("hue", "saturation", "color", "luminosity"):
        return _nonsep(cb, cs, mode)
    raise ToolError(f"unknown blend mode {mode!r}", "normal, multiply, screen, overlay, soft_light, hard_light, darken, lighten, "
                    "difference, exclusion, color_dodge, color_burn, add, hue, saturation, color, luminosity")


def _lum(c):
    return (0.3 * c[..., 0] + 0.59 * c[..., 1] + 0.11 * c[..., 2])[..., None]


def _clip_color(c):
    l = _lum(c)
    n, x = c.min(-1, keepdims=True), c.max(-1, keepdims=True)
    c = np.where(n < 0, l + (c - l) * l / np.maximum(l - n, 1e-6), c)
    c = np.where(x > 1, l + (c - l) * (1 - l) / np.maximum(x - l, 1e-6), c)
    return c


def _set_lum(c, l):
    return _clip_color(c + (l - _lum(c)))


def _sat(c):
    return c.max(-1, keepdims=True) - c.min(-1, keepdims=True)


def _set_sat(c, s):
    mx, mn = c.max(-1, keepdims=True), c.min(-1, keepdims=True)
    rng = np.maximum(mx - mn, 1e-6)
    return np.where(mx > mn, (c - mn) * s / rng, 0.0)


def _nonsep(cb, cs, mode):
    if mode == "hue":
        return _set_lum(_set_sat(cs, _sat(cb)), _lum(cb))
    if mode == "saturation":
        return _set_lum(_set_sat(cb, _sat(cs)), _lum(cb))
    if mode == "color":
        return _set_lum(cs, _lum(cb))
    return _set_lum(cb, _lum(cs))


def composite_onto(base: np.ndarray, layer: Image.Image, left: int, top: int, opacity: float = 1.0,
                   mode: str = "normal") -> None:
    """In-place blend of an RGBA layer onto a float RGBA canvas (W3C compositing formulas)."""
    H, W = base.shape[:2]
    lw, lh = layer.size
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(W, left + lw), min(H, top + lh)
    if x1 <= x0 or y1 <= y0:
        return
    src = np.asarray(layer, np.float32)[y0 - top:y1 - top, x0 - left:x1 - left] / 255
    cs, as_ = src[..., :3], src[..., 3:4] * opacity
    dst = base[y0:y1, x0:x1]
    cb, ab = dst[..., :3], dst[..., 3:4]
    mixed = (1 - ab) * cs + ab * np.clip(_blend(cb, cs, mode), 0, 1)
    ao = as_ + ab * (1 - as_)
    co = (as_ * mixed + ab * cb * (1 - as_)) / np.maximum(ao, 1e-6)
    dst[..., :3] = co
    dst[..., 3:4] = ao
