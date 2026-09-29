"""Saving AI outputs the studio way: never overwrite, PNG metadata, sidecar JSON, labelled previews."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from ...config import output_dir, slug, unique_path

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def out_dir(project: str, kind: str, out: str) -> Path:
    if out:
        p = Path(out).expanduser()
        p = p if not p.suffix else p.parent
        p.mkdir(parents=True, exist_ok=True)
        return p
    return output_dir(project or None, kind)


def save_png(im: Image.Image, directory: Path, stem: str, meta: dict) -> tuple[Path, Path]:
    """PNG (with prompt/seed in tEXt chunks, like ComfyUI/A1111) + sidecar JSON of the same stem."""
    p = unique_path(directory, stem, ".png")
    info = PngInfo()
    for k in ("prompt", "negative", "seed", "backend", "model", "licence"):
        if meta.get(k) not in (None, ""):
            info.add_text(f"studio:{k}", str(meta[k]))
    info.add_text("studio:json", json.dumps(meta, ensure_ascii=False, default=str))
    im.save(p, pnginfo=info, optimize=True)
    side = p.with_suffix(".json")
    side.write_text(json.dumps({**meta, "file": str(p), "created": datetime.now().isoformat(timespec="seconds")},
                               ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return p, side


def write_sidecar(path: Path, meta: dict) -> Path:
    side = Path(path).with_suffix(".json")
    side.write_text(json.dumps({**meta, "file": str(path), "created": datetime.now().isoformat(timespec="seconds")},
                               ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return side


def stem_for(prompt: str, prefix: str = "") -> str:
    s = slug(prompt, 40)
    return f"{prefix}-{s}" if prefix else s


def labelled_sheet(images: list[Path | Image.Image], labels: list[str], dest: Path, tile: int = 420,
                   cols: int | None = None, title: str = "") -> Path:
    """Contact sheet with a caption under each tile (seed/backend) — for picking the best take."""
    ims = [Image.open(i).convert("RGB") if not isinstance(i, Image.Image) else i.convert("RGB") for i in images]
    n = len(ims)
    cols = cols or min(4, n)
    rows = (n + cols - 1) // cols
    cap = 30
    head = 44 if title else 0
    pad = 16
    W = cols * (tile + pad) + pad
    H = head + rows * (tile + cap + pad) + pad
    sheet = Image.new("RGB", (W, H), (24, 24, 27))
    d = ImageDraw.Draw(sheet)
    if title:
        ft = font(20)
        t = title
        while len(t) > 8 and d.textlength(t, font=ft) > W - 2 * pad:
            t = t[:-2]
        d.text((pad, 12), t if t == title else t.rstrip() + "…", fill=(235, 235, 240), font=ft)
    f = font(15)
    for i, im in enumerate(ims):
        t = im.copy()
        t.thumbnail((tile, tile), Image.LANCZOS)
        cx = pad + (i % cols) * (tile + pad)
        cy = head + pad + (i // cols) * (tile + cap + pad)
        sheet.paste(t, (cx + (tile - t.width) // 2, cy + (tile - t.height) // 2))
        if i < len(labels):
            d.text((cx, cy + tile + 6), labels[i][:60], fill=(190, 190, 200), font=f)
    sheet.save(dest)
    return dest


def before_after(before: Image.Image, after: Image.Image, dest: Path, labels=("before", "after"),
                 mask: Image.Image | None = None, tile: int = 640) -> Path:
    items: list[Image.Image] = [before]
    labs = [labels[0]]
    if mask is not None:
        ov = before.convert("RGB").resize(before.size)
        red = Image.new("RGB", ov.size, (255, 40, 60))
        m = mask.convert("L").resize(ov.size).point(lambda v: int(v * 0.55))
        ov = Image.composite(red, ov, m)
        items.append(ov)
        labs.append("mask (red = repainted)")
    items.append(after)
    labs.append(labels[1])
    return labelled_sheet(items, labs, dest, tile=tile, cols=len(items))


def detail_compare(original: Image.Image, upscaled: Image.Image, dest: Path, crop: int = 400) -> Path:
    """100% crops of the same region: nearest-neighbour enlargement vs. result — shows real added detail."""
    sx = upscaled.width / original.width
    cw = max(16, int(crop / sx))
    ox, oy = (original.width - cw) // 2, (original.height - cw) // 2
    a = original.crop((ox, oy, ox + cw, oy + cw)).resize((crop, crop), Image.NEAREST)
    ux, uy = int(ox * sx), int(oy * sx)
    b = upscaled.crop((ux, uy, ux + crop, uy + crop))
    return labelled_sheet([a, b], ["original (pixel-enlarged)", f"upscaled ×{sx:.2f} at 100%"], dest, tile=crop, cols=2)
