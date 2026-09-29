"""Minimal, dependency-free layered PSD writer (Photoshop 8BPS v1, RGB 8-bit).

Writes real pixel layers with name (incl. Unicode/Arabic via 'luni'), position, opacity, blend mode
and visibility, plus the merged composite so viewers (Finder/Preview/Photopea) show the result.
Opens in Photoshop, Photopea, GIMP, Krita, Affinity. Text is stored as pixels (Photoshop-native
editable text needs Adobe's text engine data, which only Photoshop writes); the XCF master keeps
text editable in GIMP.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

BLEND_KEYS = {
    "normal": b"norm", "multiply": b"mul ", "screen": b"scrn", "overlay": b"over", "soft_light": b"sLit",
    "hard_light": b"hLit", "darken": b"dark", "lighten": b"lite", "difference": b"diff", "color_dodge": b"div ",
    "color_burn": b"idiv", "add": b"lddg", "linear_dodge": b"lddg", "exclusion": b"smud", "hue": b"hue ",
    "saturation": b"sat ", "color": b"colr", "luminosity": b"lum ", "dissolve": b"diss",
}


@dataclass
class PsdLayer:
    name: str
    image: Image.Image          # RGBA, already cropped to its bounds
    left: int = 0
    top: int = 0
    opacity: float = 1.0
    blend: str = "normal"
    visible: bool = True


def _pascal(name: str) -> bytes:
    b = name.encode("ascii", "replace")[:255]
    raw = bytes([len(b)]) + b
    pad = (4 - len(raw) % 4) % 4
    return raw + b"\0" * pad


def _unicode_block(name: str) -> bytes:
    u = name.encode("utf-16-be")
    data = struct.pack(">I", len(name)) + u
    if len(data) % 2:
        data += b"\0"
    return b"8BIMluni" + struct.pack(">I", len(data)) + data


def write_psd(path: str | Path, width: int, height: int, layers: list[PsdLayer], composite: Image.Image,
              dpi: int = 72) -> Path:
    path = Path(path)
    recs, chans = [], []
    for L in layers:
        im = L.image.convert("RGBA")
        w, h = im.size
        if w == 0 or h == 0:
            im = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            w, h = 1, 1
        arr = np.asarray(im)
        ch_data = []
        for idx, ci in ((-1, 3), (0, 0), (1, 1), (2, 2)):
            plane = np.ascontiguousarray(arr[..., ci]).tobytes()
            ch_data.append((idx, b"\0\0" + plane))  # compression 0 = raw
        rec = struct.pack(">iiii", L.top, L.left, L.top + h, L.left + w)
        rec += struct.pack(">H", len(ch_data))
        for idx, d in ch_data:
            rec += struct.pack(">hI", idx, len(d))
        rec += b"8BIM" + BLEND_KEYS.get(L.blend, b"norm")
        flags = 0 if L.visible else 2
        rec += struct.pack(">BBBB", int(round(max(0, min(1, L.opacity)) * 255)), 0, flags, 0)
        extra = struct.pack(">I", 0) + struct.pack(">I", 0) + _pascal(L.name) + _unicode_block(L.name)
        rec += struct.pack(">I", len(extra)) + extra
        recs.append(rec)
        chans.append(b"".join(d for _, d in ch_data))
    layer_info = struct.pack(">h", -len(layers)) + b"".join(recs) + b"".join(chans)  # negative: first alpha = merged transparency
    if len(layer_info) % 2:
        layer_info += b"\0"
    layer_info_block = struct.pack(">I", len(layer_info)) + layer_info
    lmi = layer_info_block + struct.pack(">I", 0)  # empty global layer mask info
    # image resources: resolution info (0x03ED)
    res = struct.pack(">IHHIHH", dpi << 16, 1, 1, dpi << 16, 1, 1)
    ir = b"8BIM" + struct.pack(">H", 0x03ED) + b"\0\0" + struct.pack(">I", len(res)) + res
    comp = composite.convert("RGBA").resize((width, height)) if composite.size != (width, height) else composite.convert("RGBA")
    carr = np.asarray(comp)
    with open(path, "wb") as f:
        f.write(b"8BPS" + struct.pack(">H", 1) + b"\0" * 6)
        f.write(struct.pack(">HIIHH", 4, height, width, 8, 3))   # 4 channels (RGBA), 8-bit, RGB
        f.write(struct.pack(">I", 0))                            # colour mode data
        f.write(struct.pack(">I", len(ir)) + ir)
        f.write(struct.pack(">I", len(lmi)) + lmi)
        f.write(struct.pack(">H", 0))                            # raw composite
        for ci in (0, 1, 2, 3):
            f.write(np.ascontiguousarray(carr[..., ci]).tobytes())
    return path
