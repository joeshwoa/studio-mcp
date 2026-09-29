"""Shared helpers for the photo department (not tools)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageOps

from ...config import output_dir, unique_path, slug
from ...core import qc
from ...core.result import ToolError

KIND = "photo"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".avif", ".heic", ".heif"}
FORMATS = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "webp": ".webp", "avif": ".avif", "tif": ".tif",
           "tiff": ".tif", "bmp": ".bmp", "gif": ".gif"}


# ----------------------------------------------------------------------------- IO
def open_image(path: str, mode: str | None = None) -> Image.Image:
    p = Path(str(path)).expanduser()
    if not p.exists():
        raise ToolError(f"image not found: {p}")
    if p.suffix.lower() in (".heic", ".heif"):
        try:
            import pillow_heif  # type: ignore
            pillow_heif.register_heif_opener()
        except ImportError:
            raise ToolError("HEIC needs pillow-heif", "pip install pillow-heif (or convert with macOS Preview)")
    if p.suffix.lower() == ".svg":
        import subprocess, tempfile
        from ...core.deps import need
        rsvg = need("rsvg")
        tmp = Path(tempfile.mkstemp(suffix=".png")[1])
        subprocess.run([rsvg, "-w", "2048", "-a", str(p), "-o", str(tmp)], check=True, capture_output=True)
        im = Image.open(tmp)
        im.load()
        tmp.unlink(missing_ok=True)
    else:
        try:
            im = Image.open(p)
            im.load()
        except Exception as e:
            raise ToolError(f"cannot read image {p.name}: {e}")
        im = ImageOps.exif_transpose(im)
    if im.mode in ("P", "LA", "PA", "I;16", "I", "F", "CMYK", "1", "L") and mode is None:
        im = im.convert("RGBA" if ("A" in im.getbands() or "transparency" in im.info) else "RGB")
    if mode:
        im = im.convert(mode)
    return im


def has_alpha(im: Image.Image) -> bool:
    if im.mode not in ("RGBA", "LA"):
        return False
    return im.getchannel("A").getextrema()[0] < 255


def out_file(project: str, out: str, stem: str, ext: str, src: str = "") -> Path:
    """Resolve an output path. `out` may be a file or a folder; never overwrites."""
    ext = ext if ext.startswith(".") else "." + ext
    stem = slug(stem, 60) if not re.match(r"^[\w\-.]+$", stem) else stem
    if out:
        o = Path(out).expanduser()
        if o.suffix and not o.is_dir():
            o.parent.mkdir(parents=True, exist_ok=True)
            return unique_path(o.parent, o.stem, o.suffix)
        o.mkdir(parents=True, exist_ok=True)
        return unique_path(o, stem, ext)
    return unique_path(output_dir(project or None, KIND), stem, ext)


def stem_of(path: str, suffix: str) -> str:
    s = Path(str(path)).stem
    s = re.sub(r"[^\w\-]+", "-", s).strip("-") or "image"
    return f"{s}-{suffix}" if suffix else s


def save_image(im: Image.Image, path: Path, quality: int = 92, background: str = "#ffffff",
               icc: bytes | None = None, exif: bytes | None = None) -> Path:
    ext = path.suffix.lower()
    kw: dict = {}
    if icc:
        kw["icc_profile"] = icc
    if ext in (".jpg", ".jpeg"):
        if im.mode in ("RGBA", "LA", "P"):
            im = flatten(im, background)
        im = im.convert("RGB")
        kw.update(quality=int(quality), optimize=True, progressive=True, subsampling=0 if quality >= 90 else 2)
        if exif:
            kw["exif"] = exif
        im.save(path, "JPEG", **kw)
    elif ext == ".webp":
        kw.update(quality=int(quality), method=6)
        if quality >= 100:
            kw["lossless"] = True
        im.save(path, "WEBP", **kw)
    elif ext == ".avif":
        kw.update(quality=int(quality))
        im.save(path, "AVIF", **kw)
    elif ext in (".tif", ".tiff"):
        im.save(path, "TIFF", compression="tiff_lzw", **kw)
    elif ext == ".png":
        im.save(path, "PNG", optimize=im.width * im.height < 6_000_000, **kw)
    elif ext == ".gif":
        im.convert("RGBA" if has_alpha(im) else "RGB").save(path, "GIF")
    elif ext == ".bmp":
        flatten(im, background).save(path, "BMP")
    else:
        raise ToolError(f"unsupported output format {ext}", "png, jpg, webp, avif, tif, gif, bmp")
    return path


def flatten(im: Image.Image, background: str = "#ffffff") -> Image.Image:
    if im.mode not in ("RGBA", "LA", "P"):
        return im.convert("RGB")
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, parse_color(background))
    bg.alpha_composite(im)
    return bg.convert("RGB")


# ----------------------------------------------------------------------------- previews
def checkerboard(size: tuple[int, int], cell: int = 16) -> Image.Image:
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((xx // cell) + (yy // cell)) % 2).astype(np.uint8)
    arr = np.where(c[..., None] == 1, np.array([205, 205, 205], np.uint8), np.array([245, 245, 245], np.uint8))
    return Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")


def preview_dir(path: Path) -> Path:
    d = path.parent / "_previews"
    d.mkdir(exist_ok=True)
    return d


def preview(path: Path | str, max_side: int = 1024, label: str = "") -> str:
    """PNG preview (transparent images shown on a checkerboard)."""
    path = Path(path)
    dest = unique_path(preview_dir(path), path.stem + "-preview", ".png")
    if path.suffix.lower() in (".svg", ".pdf"):
        return str(qc.image_preview(path, dest, max_side=max_side, background="white"))
    im = open_image(str(path))
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    if has_alpha(im):
        bg = checkerboard(im.size)
        bg.alpha_composite(im.convert("RGBA"))
        im = bg
    if label:
        im = labelled(im.convert("RGB"), label)
    im.convert("RGB").save(dest)
    return str(dest)


def labelled(im: Image.Image, text: str) -> Image.Image:
    from .fonts import pil_font
    f, _, _ = pil_font("", max(14, im.width // 40), 600, text=text)
    im = im.convert("RGB").copy()
    d = ImageDraw.Draw(im, "RGBA")
    pad = max(6, im.width // 120)
    bb = d.textbbox((0, 0), text, font=f)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    d.rounded_rectangle((pad, pad, pad * 3 + w, pad * 3 + h), radius=pad, fill=(0, 0, 0, 150))
    d.text((pad * 2 - bb[0], pad * 2 - bb[1]), text, font=f, fill="white")
    return im


def side_by_side(a: Image.Image, b: Image.Image, dest: Path, labels=("Before", "After"), max_side: int = 900) -> str:
    ims = []
    for im, lab in zip((a, b), labels):
        im = im.copy()
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        if has_alpha(im):
            bg = checkerboard(im.size)
            bg.alpha_composite(im.convert("RGBA"))
            im = bg
        ims.append(labelled(im.convert("RGB"), lab))
    h = max(i.height for i in ims)
    gap = 12
    sheet = Image.new("RGB", (sum(i.width for i in ims) + gap * 3, h + gap * 2), (30, 30, 32))
    x = gap
    for i in ims:
        sheet.paste(i, (x, gap + (h - i.height) // 2))
        x += i.width + gap
    dest = unique_path(dest.parent, dest.stem, ".png")
    sheet.save(dest)
    return str(dest)


def before_after_preview(before: Image.Image, after: Image.Image, out_path: Path, labels=("Before", "After")) -> str:
    return side_by_side(before, after, preview_dir(out_path) / (out_path.stem + "-compare.png"), labels)


# ----------------------------------------------------------------------------- parsing
def parse_color(c, default=(0, 0, 0, 255)) -> tuple[int, int, int, int]:
    if c is None or c == "":
        return default
    if isinstance(c, (list, tuple)):
        v = [int(x) for x in c] + [255] * (4 - len(c))
        return tuple(v[:4])  # type: ignore
    s = str(c).strip()
    if s.lower() in ("transparent", "none"):
        return (0, 0, 0, 0)
    if re.fullmatch(r"#?[0-9a-fA-F]{8}", s):
        s = s.lstrip("#")
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore
    try:
        v = ImageColor.getcolor(s if not re.fullmatch(r"[0-9a-fA-F]{6}|[0-9a-fA-F]{3}", s) else "#" + s, "RGBA")
        return tuple(v)  # type: ignore
    except ValueError:
        raise ToolError(f"bad colour {c!r}", "use #rrggbb, #rrggbbaa, rgb(…), or a CSS colour name")


def parse_ratio(r: str | float) -> float:
    if isinstance(r, (int, float)):
        return float(r)
    s = str(r).strip().lower()
    named = {"square": 1.0, "portrait": 4 / 5, "story": 9 / 16, "reel": 9 / 16, "landscape": 16 / 9,
             "widescreen": 16 / 9, "cinema": 2.39, "a4": 1 / 1.4142, "golden": 1.618}
    if s in named:
        return named[s]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)) / float(m.group(2))
    try:
        return float(s)
    except ValueError:
        raise ToolError(f"bad aspect ratio {r!r}", "use '16:9', '4:5', '1:1', 'story', or a number")


def list_images(folder: str, pattern: str = "*") -> list[Path]:
    d = Path(folder).expanduser()
    if not d.is_dir():
        raise ToolError(f"not a folder: {d}")
    return sorted(p for p in d.glob(pattern) if p.is_file() and p.suffix.lower() in IMG_EXTS)


# ----------------------------------------------------------------------------- measurements
def to_float(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0


def from_float(arr: np.ndarray, alpha: Image.Image | None = None) -> Image.Image:
    im = Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    if alpha is not None:
        im.putalpha(alpha)
    return im


def luma(arr: np.ndarray) -> np.ndarray:
    return arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722


def image_stats(im: Image.Image) -> dict:
    a = to_float(im)
    y = luma(a)
    return {
        "mean_luma": round(float(y.mean()), 3),
        "contrast_std": round(float(y.std()), 3),
        "clipped_highlights_pct": round(float((a.max(axis=2) >= 0.995).mean() * 100), 2),
        "crushed_shadows_pct": round(float((a.max(axis=2) <= 0.01).mean() * 100), 2),
        "saturation": round(float((a.max(axis=2) - a.min(axis=2)).mean()), 3),
    }


def stats_warnings(s: dict, before: dict | None = None) -> list[str]:
    w = []
    if s["clipped_highlights_pct"] > 3 and (not before or s["clipped_highlights_pct"] > before["clipped_highlights_pct"] + 1):
        w.append(f"{s['clipped_highlights_pct']}% of pixels are blown-out highlights")
    if s["crushed_shadows_pct"] > 5 and (not before or s["crushed_shadows_pct"] > before["crushed_shadows_pct"] + 1):
        w.append(f"{s['crushed_shadows_pct']}% of pixels are crushed to black")
    if s["mean_luma"] < 0.12:
        w.append("image is very dark overall")
    if s["mean_luma"] > 0.88:
        w.append("image is very bright overall")
    return w


def env_threads() -> int:
    return max(1, min(8, (os.cpu_count() or 2)))
