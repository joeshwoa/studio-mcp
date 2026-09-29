"""vector_trace: raster → vector (logo from a photo/scan/sketch) with vtracer, colour-count control and cleanup."""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from ...core.deps import need
from ...core.registry import tool
from ...core.result import Result, ToolError
from ..photo._common import open_image, side_by_side
from ._svg import SVG_NS, out_file, render_array, stem_of, write_tree

PRESETS = {  # vtracer settings
    "logo": dict(filter_speckle=8, corner_threshold=60, length_threshold=4.0, splice_threshold=45, path_precision=3,
                 layer_difference=24, color_precision=6, mode="spline"),
    "illustration": dict(filter_speckle=4, corner_threshold=60, length_threshold=4.0, splice_threshold=45,
                         path_precision=3, layer_difference=16, color_precision=6, mode="spline"),
    "photo": dict(filter_speckle=10, corner_threshold=180, length_threshold=4.0, splice_threshold=45, path_precision=2,
                  layer_difference=8, color_precision=8, mode="spline"),
    "pixel": dict(filter_speckle=0, corner_threshold=0, length_threshold=3.5, splice_threshold=45, path_precision=2,
                  layer_difference=8, color_precision=8, mode="polygon"),
    "sharp": dict(filter_speckle=8, corner_threshold=100, length_threshold=4.0, splice_threshold=45, path_precision=2,
                  layer_difference=24, color_precision=6, mode="polygon"),
}


def _kmeans_palette(arr: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    """k colours from opaque pixels (Lab-ish via plain RGB k-means++, fast on a sample)."""
    px = arr.reshape(-1, 3).astype(np.float32)
    rng = np.random.default_rng(seed)
    sample = px[rng.choice(len(px), min(len(px), 40000), replace=False)]
    try:
        import cv2
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.5)
        _, _, centers = cv2.kmeans(sample, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
        return centers
    except ImportError:
        im = Image.fromarray(sample.reshape(-1, 1, 3).astype(np.uint8)).quantize(k, method=Image.Quantize.MEDIANCUT)
        return np.array(im.getpalette()[:k * 3], np.float32).reshape(-1, 3)


def _quantize(im: Image.Image, colors: int, smooth: float) -> tuple[Image.Image, list[str]]:
    rgb = im.convert("RGB")
    if smooth:
        rgb = rgb.filter(ImageFilter.MedianFilter(3 if smooth < 1.5 else 5))
    a = np.asarray(rgb, np.float32)
    alpha = np.asarray(im.getchannel("A")) if im.mode == "RGBA" else None
    mask = alpha > 127 if alpha is not None else np.ones(a.shape[:2], bool)
    centers = _kmeans_palette(a[mask], colors)
    d = ((a[..., None, :] - centers[None, None]) ** 2).sum(-1)
    lab = d.argmin(-1)
    # majority filter removes stray single pixels between colour regions
    lab_im = Image.fromarray(lab.astype(np.uint8))
    lab = np.asarray(lab_im.filter(ImageFilter.ModeFilter(5 if smooth >= 1 else 3)))
    q = centers[lab].clip(0, 255).astype(np.uint8)
    out = Image.fromarray(q, "RGB").convert("RGBA")
    if alpha is not None:
        out.putalpha(Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)))
    hexes = ["#%02x%02x%02x" % tuple(int(v) for v in c) for c in centers]
    return out, hexes


def _key_background(im: Image.Image, tol: int = 28) -> tuple[Image.Image, str | None]:
    """Make a flat background (the colour on the image border) transparent."""
    rgb = np.asarray(im.convert("RGB")).astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    med = np.median(border, 0)
    if np.abs(border - med).max(-1).mean() > tol:  # border isn't uniform: no flat background
        return im.convert("RGBA"), None
    dist = np.abs(rgb - med).max(-1)
    # flood from the border so interior areas of the same colour (counters of letters) survive only if enclosed
    try:
        import cv2
        fg = (dist > tol).astype(np.uint8)
        bgmask = (1 - fg).astype(np.uint8)
        n, lab = cv2.connectedComponents(bgmask, connectivity=4)
        border_labels = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
        bg = np.isin(lab, list(border_labels))
        # anti-aliased fringe (half background colour) next to the background goes too — otherwise it
        # traces as a dark/light outline around every shape
        near = dist < tol * 3
        k = np.ones((3, 3), np.uint8)
        for _ in range(2):
            bg = bg | (cv2.dilate(bg.astype(np.uint8), k) > 0) & near
    except ImportError:
        bg = dist <= tol
    a = np.where(bg, 0, 255).astype(np.uint8)
    out = im.convert("RGBA")
    out.putalpha(Image.fromarray(a))
    return out, "#%02x%02x%02x" % tuple(int(v) for v in med)


@tool("vector")
def vector_trace(image: str, colors: int = 6, preset: str = "logo", remove_background: bool = True,
                 max_side: int = 1600, smooth: float = 1.0, black_and_white: bool = False, threshold: int = 128,
                 project: str = "", out: str = "") -> Result:
    """Trace a raster (logo photo, scan, sketch, icon, illustration) into a clean vector SVG with vtracer.
    colors = how many flat colours to keep (2–16; the image is first quantised with k-means so shapes come
    out clean); black_and_white=true traces a single ink colour (sketches, signatures; `threshold`).
    preset: logo (smooth curves, default) | illustration | sharp (straight edges) | pixel (pixel art) |
    photo (posterised look). remove_background keys out a flat border colour. smooth (0–2) denoises
    before tracing. Returns SVG + side-by-side (source vs vector) preview, path count and fidelity."""
    need("vtracer")
    import vtracer
    if preset not in PRESETS:
        raise ToolError(f"unknown preset {preset!r}", ", ".join(PRESETS))
    src = open_image(image).convert("RGBA")
    orig = src.copy()
    if max(src.size) > max_side:
        src.thumbnail((max_side, max_side), Image.LANCZOS)
    if max(src.size) < 600 and preset != "pixel":
        s = 1000 / max(src.size)
        src = src.resize((int(src.width * s), int(src.height * s)), Image.LANCZOS)  # more pixels → smoother curves
    warns = []
    bg_color = None
    if remove_background:
        src, bg_color = _key_background(src)
        if not bg_color:
            warns.append("background isn't a flat colour — kept it (use photo_remove_background first for photos)")
    settings = dict(PRESETS[preset])
    if black_and_white:
        g = np.asarray(ImageOps.autocontrast(src.convert("L")), np.uint8)
        if smooth:
            g = np.asarray(Image.fromarray(g).filter(ImageFilter.MedianFilter(3)))
        ink = g < threshold
        alpha = np.asarray(src.getchannel("A")) > 127
        ink &= alpha
        q = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).convert("RGBA")
        palette = ["#000000"]
        settings["colormode"] = "binary"
    else:
        colors = max(2, min(16, int(colors)))
        q, palette = _quantize(src, colors, smooth)
        settings["colormode"] = "color"
    buf = io.BytesIO()
    q.save(buf, "PNG")
    svg_text = vtracer.convert_raw_image_to_svg(buf.getvalue(), img_format="png", hierarchical="stacked", **settings)
    # tidy: proper viewBox, drop the vtracer comment
    svg_text = re.sub(r"<!--.*?-->", "", svg_text, flags=re.S)
    root = ET.fromstring(svg_text)
    w, h = int(float(root.get("width"))), int(float(root.get("height")))
    root.set("viewBox", f"0 0 {w} {h}")
    qa = np.asarray(q.getchannel("A"))
    if not black_and_white and qa.min() > 127:
        # opaque image: vtracer's stacked mode can leave a hole in the first (largest) layer — lay a base
        # rectangle of the dominant border colour underneath
        qr = np.asarray(q.convert("RGB"))
        border = np.concatenate([qr[0], qr[-1], qr[:, 0], qr[:, -1]])
        cols, cnt = np.unique(border, axis=0, return_counts=True)
        base = "#%02x%02x%02x" % tuple(int(v) for v in cols[cnt.argmax()])
        root.insert(0, ET.Element(f"{{{SVG_NS}}}rect", {"width": str(w), "height": str(h), "fill": base}))
    n_paths = sum(1 for el in root.iter() if el.tag.endswith("path"))
    o = out_file(project, out, stem_of(image, "traced"), ".svg")
    write_tree(ET.ElementTree(root), o)
    # fidelity: compare render with the (background-keyed) source on white
    show_bg = bg_color or "white"
    rend = render_array(o, width=w, background=show_bg)
    from ..photo._common import parse_color
    ref = Image.new("RGBA", src.size, parse_color(show_bg))
    ref.alpha_composite(q if black_and_white else src)
    refa = np.asarray(ref.convert("RGB").resize((rend.shape[1], rend.shape[0])), np.float32)
    diff = round(float(np.abs(rend - refa).mean()), 2)
    if n_paths > 1500:
        warns.append(f"{n_paths} paths — very detailed; lower `colors` or raise smooth for a cleaner logo")
    if diff > 25:
        warns.append(f"vector differs a lot from the source (mean diff {diff}/255) — try more colours or preset='illustration'")
    rim = Image.fromarray(rend.astype(np.uint8))
    prev_dir = o.parent / "_previews"
    prev_dir.mkdir(exist_ok=True)
    prev = side_by_side(orig, rim, prev_dir / f"{o.stem}-compare.png", ("Source raster", f"Vector ({n_paths} paths)"))
    return Result(f"Traced {Path(image).name} → {o.name}: {n_paths} paths, {len(palette)} colour(s), preset {preset}, "
                  f"fidelity diff {diff}/255.", files=[str(o)], previews=[prev], warnings=warns,
                  data={"paths": n_paths, "palette": palette, "background_removed": bg_color, "diff": diff},
                  next_steps=["vector_recolor to snap the traced colours to the brand palette",
                              "vector_optimize to shrink the file", "open_in_app the SVG in Inkscape for node clean-up"])
