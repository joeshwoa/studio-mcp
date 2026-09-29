"""Collages / grids and before-after comparisons."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from ...core.registry import tool
from ...core.result import Result, ToolError
from ._common import list_images, open_image, out_file, parse_color, preview, save_image, stem_of
from .fonts import pil_font
from .saliency import best_crop


def _smart_fit(im: Image.Image, size: tuple[int, int], smart: bool) -> Image.Image:
    w, h = size
    if smart:
        box, _ = best_crop(im, w / h)
        im = im.crop(box)
    return ImageOps.fit(im, (w, h), Image.LANCZOS)


def _rounded(im: Image.Image, r: int) -> Image.Image:
    if r <= 0:
        return im.convert("RGBA")
    m = Image.new("L", (im.width * 3, im.height * 3), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, m.width - 1, m.height - 1), radius=r * 3, fill=255)
    out = im.convert("RGBA")
    out.putalpha(m.resize(im.size, Image.LANCZOS))
    return out


def _layout(n: int, W: int, H: int, gap: int, layout: str) -> list[tuple[int, int, int, int]]:
    """Cell rectangles (x, y, w, h)."""
    inner_w, inner_h = W - 2 * gap, H - 2 * gap
    if layout == "hero" and n >= 3:
        # one big cell on top, the rest in a row below
        top_h = int((inner_h - gap) * 0.62)
        cells = [(gap, gap, inner_w, top_h)]
        m = n - 1
        cw = (inner_w - gap * (m - 1)) / m
        for i in range(m):
            cells.append((int(gap + i * (cw + gap)), gap + top_h + gap, int(cw), inner_h - top_h - gap))
        return cells
    if layout == "mosaic" and n >= 3:
        # big left cell + stacked right column
        lw = int((inner_w - gap) * 0.6)
        cells = [(gap, gap, lw, inner_h)]
        m = n - 1
        ch = (inner_h - gap * (m - 1)) / m
        for i in range(m):
            cells.append((gap + lw + gap, int(gap + i * (ch + gap)), inner_w - lw - gap, int(ch)))
        return cells
    cols = int(np.ceil(np.sqrt(n * W / H)))
    cols = max(1, min(n, cols))
    rows = int(np.ceil(n / cols))
    cw = (inner_w - gap * (cols - 1)) / cols
    ch = (inner_h - gap * (rows - 1)) / rows
    cells = []
    for i in range(n):
        r, c = divmod(i, cols)
        # centre an incomplete last row
        in_row = min(cols, n - r * cols)
        off = (cols - in_row) * (cw + gap) / 2
        cells.append((int(gap + off + c * (cw + gap)), int(gap + r * (ch + gap)), int(cw), int(ch)))
    return cells


@tool("photo")
def photo_collage(images: list[str] = [], folder: str = "", width: int = 2000, height: int = 2000, layout: str = "grid",
                  gap: int = 16, radius: int = 0, background: str = "#ffffff", smart_crop: bool = True,
                  captions: list[str] = [], caption_color: str = "#ffffff", format: str = "jpg", quality: int = 92,
                  project: str = "", out: str = "") -> Result:
    """Collage / grid of photos: layout 'grid' (auto rows×cols for the canvas ratio, last row centred),
    'hero' (one big image + a row below), 'mosaic' (big left + stacked right column). Images come from
    `images` (paths, in order) or a `folder`. Each cell is filled with a subject-aware crop (faces kept),
    with gap, rounded corners, background colour and optional captions (Arabic works). Returns the
    collage + preview."""
    paths = [Path(p).expanduser() for p in images] if images else list_images(folder) if folder else []
    if not paths:
        raise ToolError("give images=[…] or folder=")
    if len(paths) > 64:
        raise ToolError("at most 64 images per collage")
    ims = [open_image(str(p)) for p in paths]
    canvas = Image.new("RGBA", (width, height), parse_color(background))
    cells = _layout(len(ims), width, height, gap, layout)
    warns = []
    for i, (im, (x, y, w, h)) in enumerate(zip(ims, cells)):
        if im.width < w * 0.6 or im.height < h * 0.6:
            warns.append(f"{paths[i].name} is small for its cell ({im.width}×{im.height} → {w}×{h}); it will look soft")
        cell = _rounded(_smart_fit(im.convert("RGB"), (w, h), smart_crop), radius)
        if i < len(captions) and captions[i]:
            txt = captions[i]
            f, fw, _ = pil_font("", max(18, h // 14), 600, text=txt)
            warns += fw
            ramp = (np.clip((np.arange(h) - h * 0.6) / (h * 0.4), 0, 1) * 170).astype(np.uint8)
            band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            band.putalpha(Image.fromarray(np.repeat(ramp[:, None], w, 1)))
            keep = cell.getchannel("A")
            cell.alpha_composite(band)
            cell.putalpha(keep)
            d = ImageDraw.Draw(cell)
            from .fonts import has_arabic
            rtl = has_arabic(txt)
            pad = max(12, w // 25)
            d.text((w - pad if rtl else pad, h - pad), txt, font=f, fill=parse_color(caption_color),
                   anchor="rd" if rtl else "ld", direction="rtl" if rtl else None)
        canvas.alpha_composite(cell, (x, y))
    ext = "." + format.lower().replace("jpeg", "jpg").lstrip(".")
    p = out_file(project, out, f"collage-{layout}-{len(ims)}", ext)
    save_image(canvas if ext == ".png" else canvas.convert("RGB"), p, quality)
    return Result(f"Collage ({layout}) of {len(ims)} photos at {width}×{height}.", files=[str(p)], previews=[preview(p)],
                  warnings=warns, data={"cells": cells})


@tool("photo")
def photo_compare(before: str, after: str, mode: str = "side_by_side", labels: list[str] = ["Before", "After"],
                  split: float = 0.5, width: int = 0, format: str = "jpg", quality: int = 92, project: str = "",
                  out: str = "") -> Result:
    """Before/after comparison image for portfolios and client approval: mode 'side_by_side', 'stacked'
    (vertical), or 'split' (one frame, left = before, right = after, divider with a handle at `split`
    0…1). Labels can be Arabic. `after` is resized to `before` when sizes differ."""
    a = open_image(before).convert("RGB")
    b = open_image(after).convert("RGB")
    if b.size != a.size:
        b = ImageOps.fit(b, a.size, Image.LANCZOS)
    W, H = a.size
    f, warns, _ = pil_font("", max(18, min(W, H) // 24), 700, text="".join(labels))

    def tag(im, text, right=False):
        if not text:
            return
        d = ImageDraw.Draw(im, "RGBA")
        pad = max(10, min(W, H) // 40)
        bb = d.textbbox((0, 0), text, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x = im.width - tw - 3 * pad if right else pad
        d.rounded_rectangle((x, pad, x + tw + 2 * pad, pad + th + 2 * pad), radius=pad, fill=(0, 0, 0, 150))
        d.text((x + pad - bb[0], 2 * pad - bb[1]), text, font=f, fill="white")

    if mode == "split":
        sx = int(W * max(0.02, min(0.98, split)))
        im = b.copy()
        im.paste(a.crop((0, 0, sx, H)), (0, 0))
        d = ImageDraw.Draw(im)
        lw = max(2, W // 400)
        d.rectangle((sx - lw, 0, sx + lw, H), fill="white")
        r = max(14, min(W, H) // 30)
        d.ellipse((sx - r, H // 2 - r, sx + r, H // 2 + r), fill="white")
        d.polygon([(sx - r * 0.55, H // 2), (sx - r * 0.15, H // 2 - r * 0.35), (sx - r * 0.15, H // 2 + r * 0.35)], fill=(40, 40, 40))
        d.polygon([(sx + r * 0.55, H // 2), (sx + r * 0.15, H // 2 - r * 0.35), (sx + r * 0.15, H // 2 + r * 0.35)], fill=(40, 40, 40))
        tag(im, labels[0] if labels else "")
        tag(im, labels[1] if len(labels) > 1 else "", right=True)
    elif mode in ("side_by_side", "stacked"):
        gap = max(6, W // 150)
        ta, tb = a.copy(), b.copy()
        tag(ta, labels[0] if labels else "")
        tag(tb, labels[1] if len(labels) > 1 else "")
        if mode == "side_by_side":
            im = Image.new("RGB", (W * 2 + gap, H), (255, 255, 255))
            im.paste(ta, (0, 0))
            im.paste(tb, (W + gap, 0))
        else:
            im = Image.new("RGB", (W, H * 2 + gap), (255, 255, 255))
            im.paste(ta, (0, 0))
            im.paste(tb, (0, H + gap))
    else:
        raise ToolError(f"unknown mode {mode!r}", "side_by_side | stacked | split")
    if width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    ext = "." + format.lower().replace("jpeg", "jpg").lstrip(".")
    p = out_file(project, out, stem_of(before, f"compare-{mode}"), ext)
    save_image(im, p, quality)
    return Result(f"Comparison ({mode}) {im.width}×{im.height}.", files=[str(p)], previews=[preview(p)], warnings=warns)
