"""Shared helpers for the vector department (not tools)."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

from ...config import output_dir, slug, unique_path
from ...core.deps import need, run
from ...core.result import ToolError

KIND = "vector"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK)

COLOR_ATTRS = ("fill", "stroke", "stop-color", "flood-color", "lighting-color", "color")
CSS_COLORS = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
              "yellow": "#ffff00", "gray": "#808080", "grey": "#808080", "orange": "#ffa500", "purple": "#800080",
              "navy": "#000080", "teal": "#008080", "silver": "#c0c0c0", "maroon": "#800000", "lime": "#00ff00",
              "aqua": "#00ffff", "cyan": "#00ffff", "magenta": "#ff00ff", "fuchsia": "#ff00ff", "olive": "#808000"}


def out_file(project: str, out: str, stem: str, ext: str) -> Path:
    ext = ext if ext.startswith(".") else "." + ext
    stem = re.sub(r"[^\w\-.]+", "-", stem).strip("-") or "vector"
    if out:
        o = Path(out).expanduser()
        if o.suffix and not o.is_dir():
            o.parent.mkdir(parents=True, exist_ok=True)
            return unique_path(o.parent, o.stem, o.suffix)
        o.mkdir(parents=True, exist_ok=True)
        return unique_path(o, stem, ext)
    return unique_path(output_dir(project or None, KIND), stem, ext)


def out_folder(project: str, out: str, name: str) -> Path:
    base = Path(out).expanduser() if out else output_dir(project or None, KIND)
    base.mkdir(parents=True, exist_ok=True)
    d = unique_path(base, slug(name, 50), "")
    d = Path(str(d).rstrip("."))
    d.mkdir(parents=True, exist_ok=True)
    return d


def stem_of(path: str, suffix: str = "") -> str:
    s = re.sub(r"[^\w\-]+", "-", Path(path).stem).strip("-") or "vector"
    return f"{s}-{suffix}" if suffix else s


def read_svg(path: str) -> tuple[Path, ET.ElementTree]:
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"SVG not found: {p}")
    try:
        return p, ET.parse(p)
    except ET.ParseError as e:
        raise ToolError(f"{p.name} is not valid SVG/XML: {e}")


def svg_size(root: ET.Element) -> tuple[float, float]:
    def num(v):
        m = re.match(r"\s*([\d.]+)\s*(px|pt|mm|cm|in)?", v or "")
        if not m:
            return None
        f = {"pt": 1.3333, "mm": 3.7795, "cm": 37.795, "in": 96}.get(m.group(2) or "px", 1)
        return float(m.group(1)) * f
    w, h = num(root.get("width")), num(root.get("height"))
    vb = root.get("viewBox")
    if vb:
        parts = [float(x) for x in re.split(r"[ ,]+", vb.strip())]
        if len(parts) == 4:
            w = w or parts[2]
            h = h or parts[3]
    return (w or 300.0, h or 150.0)


def rsvg() -> str:
    return need("rsvg")


def render_png(svg: Path, dest: Path, width: int = 0, height: int = 0, dpi: float = 0, background: str = "") -> Path:
    cmd = [rsvg()]
    if width:
        cmd += ["-w", str(width)]
    if height:
        cmd += ["-h", str(height)]
    if width and height:
        cmd += ["-a"]  # keep aspect ratio inside the box
    if dpi:
        cmd += ["-d", str(dpi), "-p", str(dpi)]
    if background:
        cmd += ["-b", background]
    cmd += [str(svg), "-o", str(dest)]
    run(cmd, timeout=180)
    return dest


def render_array(svg: Path, width: int = 512, background: str = "white") -> np.ndarray:
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        render_png(svg, Path(tmp), width=width, background=background)
        return np.asarray(Image.open(tmp).convert("RGB"), np.float32)
    finally:
        os.unlink(tmp)


def visual_diff(a: Path, b: Path, width: int = 512) -> float:
    """Mean absolute pixel difference (0–255) between two SVG renders."""
    x, y = render_array(a, width), render_array(b, width)
    if x.shape != y.shape:
        yi = Image.fromarray(y.astype(np.uint8)).resize((x.shape[1], x.shape[0]))
        y = np.asarray(yi, np.float32)
    return round(float(np.abs(x - y).mean()), 3)


def preview(svg: Path, max_side: int = 900, label: str = "") -> str:
    d = svg.parent / "_previews"
    d.mkdir(exist_ok=True)
    dest = unique_path(d, svg.stem + "-preview", ".png")
    root = ET.parse(svg).getroot()
    w, h = svg_size(root)
    s = max_side / max(w, h)
    # checkerboard shows transparency
    render_png(svg, dest, width=max(1, int(w * s)))
    im = Image.open(dest).convert("RGBA")
    from ..photo._common import checkerboard, labelled
    bg = checkerboard(im.size, 12)
    bg.alpha_composite(im)
    out = bg.convert("RGB")
    if label:
        out = labelled(out, label)
    out.save(dest)
    return str(dest)


def compare_preview(a: Path, b: Path, dest_dir: Path, name: str, labels=("Before", "After")) -> str:
    from ..photo._common import side_by_side
    ia = Image.open(preview(a)) if a.suffix.lower() == ".svg" else Image.open(a)
    ib = Image.open(preview(b)) if b.suffix.lower() == ".svg" else Image.open(b)
    d = dest_dir / "_previews"
    d.mkdir(exist_ok=True)
    return side_by_side(ia, ib, d / f"{name}.png", labels)


def inkscape() -> str:
    return need("inkscape")


def inkscape_run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    exe = inkscape()
    env = {"SELF_CALL": "1", "NO_AT_BRIDGE": "1"}
    r = run([exe, *args], timeout=timeout, env=env)
    return r


def norm_color(c: str) -> str | None:
    """#rrggbb for any literal colour we can map, else None (gradients refs, none, currentColor)."""
    if not c:
        return None
    s = c.strip().lower()
    if s in ("none", "transparent", "inherit", "currentcolor") or s.startswith("url("):
        return None
    if s in CSS_COLORS:
        return CSS_COLORS[s]
    m = re.fullmatch(r"#([0-9a-f]{3})", s)
    if m:
        return "#" + "".join(ch * 2 for ch in m.group(1))
    m = re.fullmatch(r"#([0-9a-f]{6})([0-9a-f]{2})?", s)
    if m:
        return "#" + m.group(1)
    m = re.fullmatch(r"rgba?\(\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)", s)
    if m:
        vals = [float(v[:-1]) * 2.55 if v.endswith("%") else float(v) for v in m.groups()]
        return "#%02x%02x%02x" % tuple(int(round(min(255, v))) for v in vals)
    return None


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def rgb_lab(rgb) -> np.ndarray:
    c = np.asarray(rgb, np.float64) / 255
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def iter_colors(root: ET.Element):
    """Yield (element, where, key, colour-string) for every colour in attributes and style=."""
    for el in root.iter():
        for a in COLOR_ATTRS:
            if el.get(a):
                yield el, "attr", a, el.get(a)
        st = el.get("style")
        if st:
            for part in st.split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    if k.strip() in COLOR_ATTRS:
                        yield el, "style", k.strip(), v.strip()
        if el.tag.endswith("style") and el.text:
            for m in re.finditer(r"(fill|stroke|stop-color|color)\s*:\s*([^;}\s]+)", el.text):
                yield el, "css", m.group(1), m.group(2)


def write_tree(tree: ET.ElementTree, path: Path) -> Path:
    ET.indent(tree, space=" ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path
