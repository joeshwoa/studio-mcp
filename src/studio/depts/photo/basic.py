"""Basic edits: info, resize/fit/fill, smart crop, crop, rotate, canvas, convert, batch."""
from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ...core.qc import sheet_of_images
from ...core.registry import TOOLS, tool
from ...core.result import Result, ToolError
from ._common import (FORMATS, before_after_preview, flatten, has_alpha, image_stats, list_images, open_image,
                      out_file, parse_color, parse_ratio, preview, preview_dir, save_image, stem_of)
from .saliency import best_crop, detect_faces


def _ext_for(image: str, fmt: str) -> str:
    if fmt:
        f = fmt.lower().lstrip(".")
        if f not in FORMATS:
            raise ToolError(f"unknown format {fmt!r}", "png, jpg, webp, avif, tif, gif, bmp")
        return FORMATS[f]
    e = Path(image).suffix.lower()
    return e if e in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".tif", ".tiff") else ".png"


def _finish(im: Image.Image, image: str, suffix: str, project: str, out: str, fmt: str = "", quality: int = 92,
            src: Image.Image | None = None, summary: str = "", compare: bool = True) -> Result:
    ext = _ext_for(image, fmt)
    if has_alpha(im) and ext in (".jpg", ".jpeg", ".bmp"):
        ext = ".png"
        warn = ["output has transparency — saved as PNG instead of JPEG"]
    else:
        warn = []
    p = out_file(project, out, stem_of(image, suffix), ext)
    save_image(im, p, quality=quality)
    res = Result(summary or f"Saved {p.name} ({im.width}×{im.height}).", files=[str(p)], warnings=warn,
                 data={"width": im.width, "height": im.height, "path": str(p)})
    if compare and src is not None:
        res.previews.append(before_after_preview(src, im, p))
    else:
        res.previews.append(preview(p))
    return res


# ----------------------------------------------------------------------------- info
@tool("photo")
def photo_info(image: str) -> Result:
    """Inspect an image before editing: size, megapixels, mode, alpha, DPI, format, EXIF camera/date,
    colour profile, exposure stats (clipping, brightness, contrast) and detected faces. Returns the
    numbers in data and a preview PNG to look at."""
    p = Path(image).expanduser()
    raw = Image.open(p) if p.exists() else None
    im = open_image(image)
    st = image_stats(im)
    faces = detect_faces(im)
    exif = {}
    try:
        ex = raw.getexif() if raw else {}
        from PIL.ExifTags import TAGS
        for k, v in ex.items():
            n = TAGS.get(k, k)
            if n in ("Make", "Model", "DateTime", "Software", "Orientation"):
                exif[n] = str(v)
    except Exception:
        pass
    data = {"path": str(p), "format": raw.format if raw else "", "width": im.width, "height": im.height,
            "megapixels": round(im.width * im.height / 1e6, 2), "mode": raw.mode if raw else im.mode,
            "has_alpha": has_alpha(im), "dpi": (raw.info.get("dpi") if raw else None),
            "icc_profile": bool(raw and raw.info.get("icc_profile")), "exif": exif,
            "file_kb": round(p.stat().st_size / 1024, 1), "faces": [list(f) for f in faces], **st}
    from ._common import stats_warnings
    res = Result(f"{p.name}: {im.width}×{im.height} {data['format']} {data['mode']}, {len(faces)} face(s), "
                 f"brightness {st['mean_luma']}, contrast {st['contrast_std']}.", data=data,
                 warnings=stats_warnings(st))
    prev = im.copy()
    prev.thumbnail((1024, 1024))
    if faces:
        s = prev.width / im.width
        d = ImageDraw.Draw(prev)
        for (x, y, w, h) in faces:
            d.rectangle((x * s, y * s, (x + w) * s, (y + h) * s), outline=(255, 64, 64), width=3)
    from ...config import output_dir, unique_path
    pdir = output_dir(None, "photo") / "_previews"
    pdir.mkdir(exist_ok=True, parents=True)
    dest = unique_path(pdir, p.stem + "-info", ".png")
    if has_alpha(prev):
        from ._common import checkerboard
        bg = checkerboard(prev.size)
        bg.alpha_composite(prev.convert("RGBA"))
        prev = bg
    prev.convert("RGB").save(dest)
    res.previews.append(str(dest))
    return res


# ----------------------------------------------------------------------------- resize
@tool("photo")
def photo_resize(image: str, width: int = 0, height: int = 0, mode: str = "fit", scale: float = 0.0,
                 long_edge: int = 0, crop: str = "smart", pad_color: str = "#ffffff", format: str = "",
                 quality: int = 92, project: str = "", out: str = "") -> Result:
    """Resize a photo. mode: 'fit' (inside width×height, keeps ratio), 'fill' (cover the exact size and
    crop — crop='smart' keeps faces/subject, or center/top/bottom/left/right), 'pad' (fit then pad to
    exact size with pad_color, 'blur' for a blurred-photo fill, or 'transparent'), 'exact' (stretch).
    Or give scale (e.g. 0.5) or long_edge (px). High-quality Lanczos; upscaling >2× warns (use
    photo_upscale). Returns the resized file + preview."""
    src = open_image(image)
    W, H = src.size
    warns = []
    if scale:
        width, height, mode = round(W * scale), round(H * scale), "exact"
    elif long_edge:
        s = long_edge / max(W, H)
        width, height, mode = round(W * s), round(H * s), "exact"
    if not width and not height:
        raise ToolError("give width and/or height (or scale / long_edge)")
    if not width:
        width = round(W * height / H)
    if not height:
        height = round(H * width / W)
    info: dict = {}
    if mode == "fit":
        s = min(width / W, height / H)
        im = src.resize((max(1, round(W * s)), max(1, round(H * s))), Image.LANCZOS, reducing_gap=3.0)
    elif mode == "fill":
        ratio = width / height
        if crop == "smart":
            box, info = best_crop(src, ratio)
        else:
            if W / H > ratio:
                cw, ch = round(H * ratio), H
            else:
                cw, ch = W, round(W / ratio)
            gx = {"left": 0.0, "right": 1.0}.get(crop, 0.5)
            gy = {"top": 0.0, "bottom": 1.0}.get(crop, 0.5)
            l, t = round((W - cw) * gx), round((H - ch) * gy)
            box = (l, t, l + cw, t + ch)
        im = src.crop(box).resize((width, height), Image.LANCZOS, reducing_gap=3.0)
        info["crop_box"] = list(box)
    elif mode == "pad":
        s = min(width / W, height / H)
        fit = src.resize((max(1, round(W * s)), max(1, round(H * s))), Image.LANCZOS, reducing_gap=3.0)
        if pad_color == "blur":
            bg = ImageOps.fit(src.convert("RGB"), (width, height), Image.LANCZOS).filter(ImageFilter.GaussianBlur(max(width, height) / 30))
            bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.25).convert("RGBA")
        else:
            bg = Image.new("RGBA", (width, height), parse_color(pad_color))
        bg.alpha_composite(fit.convert("RGBA"), ((width - fit.width) // 2, (height - fit.height) // 2))
        im = bg if (has_alpha(bg) or has_alpha(src)) else bg.convert("RGB")
    elif mode == "exact":
        im = src.resize((width, height), Image.LANCZOS, reducing_gap=3.0)
        if abs((width / height) - (W / H)) > 0.01 and not scale and not long_edge:
            warns.append("mode=exact changed the aspect ratio (image is stretched) — use fill or pad to avoid distortion")
    else:
        raise ToolError(f"unknown mode {mode!r}", "fit | fill | pad | exact")
    up = max(im.width / W, im.height / H)
    if up > 2:
        warns.append(f"upscaled {up:.1f}× with Lanczos — soft result; photo_upscale (Real-ESRGAN) looks much better")
    res = _finish(im, image, f"{im.width}x{im.height}", project, out, format, quality, src,
                  f"Resized {W}×{H} → {im.width}×{im.height} ({mode}).")
    res.warnings += warns
    res.data.update(info)
    return res


@tool("photo")
def photo_smart_crop(image: str, ratio: str = "1:1", zoom: float = 1.0, use_subject_mask: bool = False,
                     width: int = 0, format: str = "", quality: int = 92, project: str = "", out: str = "") -> Result:
    """Subject-aware crop to any aspect ratio ('1:1', '4:5', '9:16', '16:9', 'story', 2.39 …). Finds faces
    (kept whole, eyes near the upper third) and the visually important region (saliency); with
    use_subject_mask=true it also uses the AI background-removal mask (slower, best for products).
    zoom>1 crops tighter. width>0 also resizes. Returns the crop and a preview showing the chosen box."""
    src = open_image(image)
    r = parse_ratio(ratio)
    mask = None
    if use_subject_mask:
        from .cutout import subject_mask
        mask, _ = subject_mask(src)
    box, info = best_crop(src, r, zoom=zoom, subject_mask=mask)
    im = src.crop(box)
    if width:
        im = im.resize((width, round(width / r)), Image.LANCZOS, reducing_gap=3.0)
    res = _finish(im, image, f"crop-{str(ratio).replace(':', 'x')}", project, out, format, quality, None,
                  f"Smart-cropped to {ratio} → {im.width}×{im.height} (faces found: {info['faces']}).", compare=False)
    # overlay preview of the box on the original
    ov = src.convert("RGB").copy()
    ov.thumbnail((900, 900))
    s = ov.width / src.width
    dim = Image.new("RGBA", ov.size, (0, 0, 0, 140))
    ImageDraw.Draw(dim).rectangle([c * s for c in box], fill=(0, 0, 0, 0))
    ov = Image.alpha_composite(ov.convert("RGBA"), dim)
    ImageDraw.Draw(ov).rectangle([c * s for c in box], outline=(255, 214, 0), width=3)
    dest = preview_dir(Path(res.files[0])) / (Path(res.files[0]).stem + "-box.png")
    ov.convert("RGB").save(dest)
    res.previews.insert(0, str(dest))
    res.data.update(info, crop_box=list(box))
    return res


@tool("photo")
def photo_crop(image: str, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0, box: list[int] = [],
               trim: bool = False, format: str = "", quality: int = 92, project: str = "", out: str = "") -> Result:
    """Exact crop. Either box=[left, top, right, bottom] in pixels, or margins to cut from each side
    (left/top/right/bottom px). trim=true removes uniform borders / transparent edges automatically."""
    src = open_image(image)
    W, H = src.size
    if trim:
        if has_alpha(src):
            bb = src.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        else:
            rgb = np.asarray(src.convert("RGB")).astype(np.int16)
            corner = rgb[0, 0]
            diff = np.abs(rgb - corner).max(-1) > 18
            ys, xs = np.nonzero(diff)
            bb = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) if len(xs) else None
        if not bb:
            raise ToolError("nothing to trim — the image is uniform")
        b = tuple(int(v) for v in bb)
    elif box:
        if len(box) != 4:
            raise ToolError("box must be [left, top, right, bottom]")
        b = tuple(int(v) for v in box)
    else:
        b = (left, top, W - right, H - bottom)
    l, t, r, bt = b
    if not (0 <= l < r <= W and 0 <= t < bt <= H):
        raise ToolError(f"crop box {b} is outside the {W}×{H} image")
    im = src.crop(b)
    res = _finish(im, image, "crop", project, out, format, quality, src, f"Cropped to {im.width}×{im.height} at {b}.")
    res.data["box"] = list(b)
    return res


@tool("photo")
def photo_rotate(image: str, angle: float = 0.0, flip: str = "", expand: bool = True, fill: str = "auto",
                 auto_straighten: bool = False, format: str = "", quality: int = 92, project: str = "", out: str = "") -> Result:
    """Rotate (degrees, counter-clockwise positive) and/or flip ('horizontal' | 'vertical').
    expand=true keeps the whole image (corners filled with `fill`: 'auto' = transparent for PNG, white
    for JPEG, 'crop' = crop to the largest clean rectangle). auto_straighten=true detects the horizon /
    dominant lines (OpenCV) and levels them."""
    src = open_image(image)
    im = src
    note = ""
    if auto_straighten:
        a = _detect_tilt(src)
        angle += a
        note = f" (auto-straighten {a:+.2f}°)" if a else " (auto-straighten: no clear horizon/architecture lines found — left as is)"
        if fill == "auto":
            fill = "crop"
    if flip:
        if flip.startswith("h"):
            im = ImageOps.mirror(im)
        elif flip.startswith("v"):
            im = ImageOps.flip(im)
        else:
            raise ToolError("flip must be 'horizontal' or 'vertical'")
    if angle % 360:
        if angle % 90 == 0:
            im = im.rotate(angle, expand=True)
        else:
            if fill == "crop":
                rot = im.convert("RGBA").rotate(angle, resample=Image.BICUBIC, expand=True)
                w, h = _rotated_rect_max(im.width, im.height, np.radians(angle))
                cx, cy = rot.width / 2, rot.height / 2
                im = rot.crop((int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)))
                if not has_alpha(src):
                    im = im.convert("RGB")
            else:
                ext = _ext_for(image, format)
                col = parse_color("#ffffff" if fill == "auto" and ext in (".jpg", ".jpeg") else
                                  ("transparent" if fill == "auto" else fill))
                im = im.convert("RGBA").rotate(angle, resample=Image.BICUBIC, expand=expand, fillcolor=col)
    res = _finish(im, image, "rotated", project, out, format, quality, src,
                  f"Rotated {angle:.2f}°{note}{', flipped ' + flip if flip else ''} → {im.width}×{im.height}.")
    res.data["angle"] = angle
    return res


def _rotated_rect_max(w: int, h: int, a: float) -> tuple[float, float]:
    """Largest axis-aligned rectangle inside a w×h rectangle rotated by a (radians)."""
    if w <= 0 or h <= 0:
        return 0, 0
    long_, short = (w, h) if w >= h else (h, w)
    sa, ca = abs(np.sin(a)), abs(np.cos(a))
    if short <= 2 * sa * ca * long_ or abs(sa - ca) < 1e-10:
        x = 0.5 * short
        wr, hr = (x / sa, x / ca) if w >= h else (x / ca, x / sa)
    else:
        cos2a = ca * ca - sa * sa
        wr, hr = (w * ca - h * sa) / cos2a, (h * ca - w * sa) / cos2a
    return wr, hr


def _detect_tilt(im: Image.Image) -> float:
    """Dominant near-horizontal/vertical line angle (degrees to rotate by). 0 when there's no clear
    evidence (organic scenes without horizon/architecture)."""
    try:
        import cv2
    except ImportError:
        raise ToolError("auto_straighten needs OpenCV", "pip install opencv-python-headless")
    w = min(1200, im.width)
    g = np.asarray(im.convert("L").resize((w, int(im.height * w / im.width))))
    g = cv2.GaussianBlur(g, (0, 0), 1.2)
    edges = cv2.Canny(g, 30, 90)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, 60, None, g.shape[1] // 12, 6)
    if lines is None:
        return 0.0
    l = np.asarray(lines).reshape(-1, 4).astype(np.float32)
    a = np.degrees(np.arctan2(l[:, 3] - l[:, 1], l[:, 2] - l[:, 0]))
    a = (a + 90) % 180 - 90                     # −90…90
    a = np.where(a > 45, a - 90, np.where(a < -45, a + 90, a))  # verticals fold onto horizontals
    wt = np.hypot(l[:, 3] - l[:, 1], l[:, 2] - l[:, 0])
    m = np.abs(a) < 12
    if wt[m].sum() < g.shape[1] * 1.5:          # not enough straight-line evidence
        return 0.0
    hist, bins = np.histogram(a[m], bins=96, range=(-12, 12), weights=wt[m])
    k = int(np.argmax(hist))
    if hist[k] < 0.3 * wt[m].sum():
        return 0.0
    sel = m & (np.abs(a - (bins[k] + bins[k + 1]) / 2) < 0.5)
    return round(float(np.average(a[sel], weights=wt[sel])), 2)


@tool("photo")
def photo_canvas(image: str, width: int = 0, height: int = 0, ratio: str = "", padding: int = 0,
                 color: str = "#ffffff", anchor: str = "center", format: str = "", quality: int = 92,
                 project: str = "", out: str = "") -> Result:
    """Extend the canvas without scaling the photo: to width×height, to an aspect ratio ('1:1', '9:16'),
    or by `padding` px on every side. color: any colour, 'transparent', or 'blur' (blurred extension of
    the photo — good for putting landscape photos into stories). anchor: center/top/bottom/left/right/
    top-left…"""
    src = open_image(image)
    W, H = src.size
    if ratio:
        r = parse_ratio(ratio)
        if W / H > r:
            width, height = W, round(W / r)
        else:
            width, height = round(H * r), H
    if padding:
        width, height = (width or W) + 2 * padding, (height or H) + 2 * padding
    width, height = max(width, W), max(height, H)
    ax = 0.0 if "left" in anchor else 1.0 if "right" in anchor else 0.5
    ay = 0.0 if "top" in anchor else 1.0 if "bottom" in anchor else 0.5
    x, y = round((width - W) * ax), round((height - H) * ay)
    if color == "blur":
        bg = ImageOps.fit(src.convert("RGB"), (width, height), Image.LANCZOS).filter(ImageFilter.GaussianBlur(max(width, height) / 25))
        bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.2).convert("RGBA")
    else:
        bg = Image.new("RGBA", (width, height), parse_color(color))
    bg.alpha_composite(src.convert("RGBA"), (x, y))
    im = bg if has_alpha(bg) else bg.convert("RGB")
    return _finish(im, image, "canvas", project, out, format, quality, src, f"Canvas {W}×{H} → {width}×{height}.")


# ----------------------------------------------------------------------------- convert
@tool("photo")
def photo_convert(image: str, format: str = "webp", quality: int = 88, max_side: int = 0, strip_metadata: bool = True,
                  background: str = "#ffffff", project: str = "", out: str = "") -> Result:
    """Convert to PNG / JPG / WebP / AVIF / TIFF / GIF (quality 1–100; 100 = lossless for WebP),
    optionally limit the long side (max_side px), strip or keep EXIF, and flatten transparency onto
    `background` for JPEG. Colour profiles are converted to sRGB for web formats. Reports the size
    saving."""
    p = Path(image).expanduser()
    src = open_image(image)
    raw = Image.open(p)
    icc = raw.info.get("icc_profile")
    im = src
    warns = []
    if icc:
        try:
            from PIL import ImageCms
            import io
            prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            srgb = ImageCms.createProfile("sRGB")
            mode = "RGBA" if has_alpha(im) else "RGB"
            im = ImageCms.profileToProfile(im.convert(mode), prof, srgb, outputMode=mode)
            icc = ImageCms.ImageCmsProfile(srgb).tobytes()
        except Exception:
            warns.append("could not convert the embedded colour profile to sRGB — kept pixel values as-is")
    if max_side and max(im.size) > max_side:
        im = im.copy()
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    ext = FORMATS.get(format.lower().lstrip("."))
    if not ext:
        raise ToolError(f"unknown format {format!r}", "png, jpg, webp, avif, tif, gif")
    if has_alpha(im) and ext in (".jpg", ".bmp"):
        warns.append(f"transparency flattened onto {background} (JPEG has no alpha)")
    exif = None if strip_metadata else raw.info.get("exif")
    dest = out_file(project, out, stem_of(image, ""), ext)
    save_image(im if ext not in (".jpg",) else flatten(im, background), dest, quality=quality, background=background,
               icc=icc, exif=exif)
    a, b = p.stat().st_size, dest.stat().st_size
    res = Result(f"Converted {p.name} → {dest.name}: {a / 1024:.0f} KB → {b / 1024:.0f} KB ({(b - a) / a * 100:+.0f}%).",
                 files=[str(dest)], previews=[preview(dest)], warnings=warns,
                 data={"bytes_before": a, "bytes_after": b, "width": im.width, "height": im.height})
    if ext in (".jpg", ".webp", ".avif") and quality < 60:
        res.warnings.append(f"quality {quality} is low — check the preview for blocking/banding")
    return res


# ----------------------------------------------------------------------------- batch
@tool("photo")
def photo_batch(folder: str, tool_name: str, args: dict = {}, pattern: str = "*", project: str = "",
                out: str = "") -> Result:
    """Run any single-image photo tool over every image in a folder (e.g. tool_name='photo_resize',
    args={'width':1080,'height':1350,'mode':'fill'}; or photo_convert, photo_adjust, photo_look,
    photo_remove_background, photo_apply_lut, photo_upscale …). `pattern` filters files ('*.jpg').
    Outputs go to one folder; returns every file plus a contact sheet of the results."""
    spec = TOOLS.get(tool_name)
    if not spec or spec.dept != "photo" or "image" not in inspect.signature(spec.fn).parameters:
        raise ToolError(f"{tool_name!r} is not a single-image photo tool",
                        "use one that takes `image`, e.g. photo_resize, photo_convert, photo_adjust")
    files = list_images(folder, pattern)
    if not files:
        raise ToolError(f"no images in {folder} matching {pattern}")
    from ...config import output_dir
    dest = Path(out).expanduser() if out else output_dir(project or None, "photo") / f"batch-{tool_name.replace('photo_', '')}"
    dest.mkdir(parents=True, exist_ok=True)
    outs, errs, warns = [], [], []
    for f in files:
        a = {**args, "image": str(f), "out": str(dest)}
        if "project" in inspect.signature(spec.fn).parameters:
            a["project"] = project
        try:
            from ...core.registry import call
            r = call(tool_name, a)
            outs += [x for x in r.files if Path(x).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".avif", ".tif", ".tiff")][:1]
            warns += [f"{f.name}: {w}" for w in r.warnings]
        except ToolError as e:
            errs.append(f"{f.name}: {e}")
    res = Result(f"{tool_name} on {len(files)} image(s): {len(outs)} done, {len(errs)} failed → {dest}",
                 files=outs, warnings=errs + warns[:20], data={"folder": str(dest), "count": len(outs)})
    if outs:
        (dest / "_previews").mkdir(exist_ok=True)
        from ...config import unique_path
        sheet = sheet_of_images(outs[:24], unique_path(dest / "_previews", "batch-sheet", ".png"), tile=280, cols=6)
        res.previews.append(str(sheet))
    res.ok = not errs or bool(outs)
    return res
