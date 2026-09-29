"""SVG operations: info, text → outlines, optimize/clean, boolean ops, recolour to a palette."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from ...core.registry import tool
from ...core.result import Result, ToolError
from ._svg import (SVG_NS, compare_preview, hex_rgb, inkscape_run, iter_colors, norm_color, out_file, preview,
                   read_svg, rgb_lab, stem_of, svg_size, visual_diff, write_tree)

SHAPES = ("path", "rect", "circle", "ellipse", "polygon", "polyline", "line", "text", "image", "use")


def _local(tag: str) -> str:
    return tag.split("}")[-1]


@tool("vector")
def vector_info(svg: str) -> Result:
    """Inspect an SVG before editing: size/viewBox, element counts, top-level ids (for vector_boolean),
    colours used (for vector_recolor), fonts and whether text is live, embedded rasters, file size.
    Returns numbers + a preview."""
    p, tree = read_svg(svg)
    root = tree.getroot()
    counts: dict[str, int] = {}
    fonts, ids = set(), []
    for el in root.iter():
        t = _local(el.tag)
        counts[t] = counts.get(t, 0) + 1
        ff = el.get("font-family") or (re.search(r"font-family:\s*([^;]+)", el.get("style", "")) or [None, None])[1]
        if ff:
            fonts.add(ff.strip().strip("'\""))
    for el in root:
        if el.get("id") and _local(el.tag) not in ("defs", "metadata", "title", "desc", "namedview"):
            ids.append(f"{el.get('id')} ({_local(el.tag)})")
    colors: dict[str, int] = {}
    for _, _, _, c in iter_colors(root):
        n = norm_color(c)
        if n:
            colors[n] = colors.get(n, 0) + 1
    w, h = svg_size(root)
    data = {"width": w, "height": h, "viewBox": root.get("viewBox"), "counts": counts, "top_level_ids": ids,
            "colors": dict(sorted(colors.items(), key=lambda kv: -kv[1])), "fonts": sorted(fonts),
            "live_text": counts.get("text", 0), "raster_images": counts.get("image", 0), "bytes": p.stat().st_size}
    warns = []
    if counts.get("text"):
        warns.append(f"{counts['text']} live text element(s) — outline them (vector_text_to_outlines) before sending to print")
    if counts.get("image"):
        warns.append(f"{counts['image']} embedded raster image(s) — they won't scale like vectors")
    return Result(f"{p.name}: {w:g}×{h:g}, {sum(counts.get(s, 0) for s in SHAPES)} shapes, {len(colors)} colours, "
                  f"{counts.get('text', 0)} text.", data=data, previews=[preview(p)], warnings=warns)


@tool("vector")
def vector_text_to_outlines(svg: str, project: str = "", out: str = "") -> Result:
    """Convert all live text to vector outlines (paths) with Inkscape — the print-safe version to send to
    printers/sign makers so fonts can't go missing. Checks that no <text> remains and that the render is
    visually unchanged. Keep the original SVG as the editable master."""
    p, tree = read_svg(svg)
    n_text = sum(1 for el in tree.getroot().iter() if _local(el.tag) == "text")
    o = out_file(project, out, stem_of(svg, "outlined"), ".svg")
    inkscape_run([str(p), "--export-text-to-path", "--export-plain-svg", f"--export-filename={o}"])
    left = sum(1 for el in ET.parse(o).getroot().iter() if _local(el.tag) == "text")
    diff = visual_diff(p, o)
    warns = []
    if left:
        warns.append(f"{left} text element(s) could not be outlined")
    if diff > 2:
        warns.append(f"outlined render differs from the original (mean diff {diff}/255) — a font may be missing on this machine")
    return Result(f"Outlined {n_text} text element(s) → {o.name} (visual diff {diff}/255).", files=[str(o)],
                  previews=[compare_preview(p, o, o.parent, o.stem + "-compare", ("Live text", "Outlined"))],
                  warnings=warns, data={"text_before": n_text, "text_after": left, "visual_diff": diff})


_NUM = re.compile(r"-?\d*\.\d+(?:[eE][-+]?\d+)?|-?\d+(?:[eE][-+]?\d+)?")
_EDITOR_NS = ("inkscape.org", "sodipodi", "adobe.com", "sketch", "figma", "bohemian", "openxmlformats", "purl.org/dc",
              "creativecommons", "w3.org/1999/02/22-rdf")


def _round_nums(s: str, prec: int) -> str:
    def r(m):
        v = float(m.group(0))
        out = f"{v:.{prec}f}".rstrip("0").rstrip(".")
        if out in ("-0", ""):
            out = "0"
        if out.startswith("0.") and len(out) > 2:
            out = out[1:]
        elif out.startswith("-0."):
            out = "-" + out[2:]
        return out
    return _NUM.sub(r, s)


@tool("vector")
def vector_optimize(svg: str, precision: int = 2, keep_ids: bool = False, keep_metadata: bool = False,
                    project: str = "", out: str = "") -> Result:
    """Clean and shrink an SVG for web/apps: removes editor junk (Inkscape/Illustrator/Figma/Sketch
    namespaces, metadata, comments), unused defs and ids, empty groups, rounds coordinates to
    `precision` decimals and collapses whitespace. Verifies the render is visually identical and reports
    the size saving."""
    p, tree = read_svg(svg)
    root = tree.getroot()
    before = p.stat().st_size
    # remove editor namespaced elements / attributes
    for parent in list(root.iter()):
        for ch in list(parent):
            tag = ch.tag if isinstance(ch.tag, str) else ""
            if not tag or any(ns in tag for ns in _EDITOR_NS) or (_local(tag) in ("metadata",) and not keep_metadata):
                parent.remove(ch)
        for a in list(parent.attrib):
            if any(ns in a for ns in _EDITOR_NS) or a.startswith("data-") or a in ("enable-background",):
                del parent.attrib[a]
    # references
    text = ET.tostring(root, encoding="unicode")
    refs = set(re.findall(r"url\(#([^)]+)\)", text)) | set(re.findall(r'href="#([^"]+)"', text))
    for parent in list(root.iter()):
        for ch in list(parent):
            if _local(ch.tag) == "defs":
                for d in list(ch):
                    if d.get("id") and d.get("id") not in refs:
                        # gradients may chain via href; keep anything referenced transitively
                        ch.remove(d)
                if not len(ch):
                    parent.remove(ch)
    attrs_num = ("d", "points", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width", "height",
                 "transform", "stroke-width", "viewBox", "offset", "fx", "fy", "gradientTransform")
    for el in root.iter():
        for a in attrs_num:
            if el.get(a) and a != "viewBox":
                el.set(a, re.sub(r"\s+", " ", _round_nums(el.get(a), precision)).strip())
            elif el.get(a) and a == "viewBox":
                el.set(a, _round_nums(el.get(a), max(precision, 3)))
        if el.get("style"):
            el.set("style", ";".join(x.strip() for x in el.get("style").split(";") if x.strip()))
        if not keep_ids and el.get("id") and el.get("id") not in refs and el is not root:
            del el.attrib["id"]
    # remove empty groups
    changed = True
    while changed:
        changed = False
        for parent in list(root.iter()):
            for ch in list(parent):
                if _local(ch.tag) == "g" and not len(ch) and not (ch.text or "").strip():
                    parent.remove(ch)
                    changed = True
    o = out_file(project, out, stem_of(svg, "min"), ".svg")
    data = ET.tostring(root, encoding="unicode")
    data = re.sub(r">\s+<", "><", data)
    data = data.replace(' xmlns:ns0="http://www.w3.org/2000/svg"', "").replace("ns0:", "")
    o.write_text(data, encoding="utf-8")
    after = o.stat().st_size
    diff = visual_diff(p, o)
    warns = [f"render changed (mean diff {diff}/255) — raise precision"] if diff > 1.0 else []
    return Result(f"Optimized {p.name}: {before / 1024:.1f} KB → {after / 1024:.1f} KB ({(after - before) / before * 100:+.0f}%), "
                  f"visual diff {diff}/255.", files=[str(o)], previews=[compare_preview(p, o, o.parent, o.stem + "-compare", ("Original", "Optimized"))],
                  warnings=warns, data={"bytes_before": before, "bytes_after": after, "visual_diff": diff})


BOOL_OPS = {"union": "path-union", "difference": "path-difference", "intersection": "path-intersection",
            "exclusion": "path-exclusion", "division": "path-division", "cut": "path-cut", "combine": "path-combine"}


@tool("vector")
def vector_boolean(svg: str, operation: str = "union", ids: list[str] = [], project: str = "", out: str = "") -> Result:
    """Boolean path operations via Inkscape: union, difference (bottom minus top), intersection,
    exclusion, division, cut, combine. ids = the objects to combine (see vector_info top_level_ids; order
    in the file decides top/bottom); empty = every top-level shape. The result keeps the bottom object's
    fill/stroke. Returns the new SVG + before/after preview."""
    if operation not in BOOL_OPS:
        raise ToolError(f"unknown operation {operation!r}", ", ".join(BOOL_OPS))
    p, tree = read_svg(svg)
    root = tree.getroot()
    if not ids:
        ids = []
        for el in root:
            if _local(el.tag) in SHAPES or _local(el.tag) == "g":
                if not el.get("id"):
                    el.set("id", f"obj{len(ids) + 1}")
                ids.append(el.get("id"))
        work = out_file(project, out, stem_of(svg, "ids"), ".svg")
        write_tree(tree, work)
        src = work
    else:
        src = p
    if len(ids) < 2 and operation not in ("combine",):
        raise ToolError("need at least two objects", "pass ids=[…] (see vector_info)")
    style = {}
    idx = {el.get("id"): el for el in ET.parse(src).getroot().iter() if el.get("id")}
    missing = [i for i in ids if i not in idx]
    if missing:
        raise ToolError(f"ids not found: {missing}")
    order = [el.get("id") for el in ET.parse(src).getroot().iter() if el.get("id") in ids]
    bottom = idx[order[0]]
    for a in ("fill", "stroke", "stroke-width", "opacity", "style", "fill-rule"):
        if bottom.get(a):
            style[a] = bottom.get(a)
    o = out_file(project, out, stem_of(svg, operation), ".svg")
    acts = f"select-by-id:{','.join(ids)};object-to-path;{BOOL_OPS[operation]};export-filename:{o};export-plain-svg;export-do"
    inkscape_run([f"--actions={acts}", str(src)])
    if src != p:
        src.unlink(missing_ok=True)
    if not o.exists():
        raise ToolError("Inkscape produced no output", "check the ids and that the objects overlap")
    t2 = ET.parse(o)
    for el in t2.getroot().iter():
        if _local(el.tag) == "path" and el.get("id") in ids and not el.get("fill") and "fill:" not in (el.get("style") or ""):
            for k, v in style.items():
                el.set(k, v)
    write_tree(t2, o)
    n_paths = sum(1 for el in t2.getroot().iter() if _local(el.tag) == "path")
    return Result(f"{operation} of {len(ids)} objects → {o.name} ({n_paths} path(s)).", files=[str(o)],
                  previews=[compare_preview(p, o, o.parent, o.stem + "-compare", ("Before", operation.title()))],
                  data={"ids": ids, "paths": n_paths})


def _lum(h):
    r, g, b = hex_rgb(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@tool("vector")
def vector_recolor(svg: str, palette: list[str] = [], mapping: dict = {}, method: str = "luminance",
                   project: str = "", out: str = "") -> Result:
    """Recolour an SVG (fills, strokes, gradient stops, CSS) to a brand palette. mapping={'#old':'#new'}
    for exact swaps; or palette=['#0f172a','#f59e0b',…] with method 'luminance' (darkest source colour →
    darkest palette colour… keeps contrast/hierarchy, best for logos & icons) or 'nearest' (closest
    perceptual colour in CIE Lab). Returns recoloured SVG, the mapping used, and before/after preview."""
    p, tree = read_svg(svg)
    root = tree.getroot()
    used: dict[str, int] = {}
    for _, _, _, c in iter_colors(root):
        n = norm_color(c)
        if n:
            used[n] = used.get(n, 0) + 1
    if not used:
        raise ToolError("no literal colours found in the SVG")
    m: dict[str, str] = {}
    if mapping:
        for k, v in mapping.items():
            nk = norm_color(k)
            if not nk:
                raise ToolError(f"bad colour {k!r} in mapping")
            m[nk] = v
    elif palette:
        pal = [norm_color(c) or c for c in palette]
        if method == "nearest":
            pl = rgb_lab([hex_rgb(c) for c in pal])
            for c in used:
                d = np.linalg.norm(pl - rgb_lab(hex_rgb(c)), axis=1)
                m[c] = pal[int(np.argmin(d))]
        elif method == "luminance":
            src = sorted(used, key=_lum)
            ps = sorted(pal, key=_lum)
            for i, c in enumerate(src):
                j = 0 if len(src) == 1 else round(i * (len(ps) - 1) / (len(src) - 1))
                m[c] = ps[j]
        else:
            raise ToolError("method must be 'luminance' or 'nearest'")
    else:
        raise ToolError("give palette=[…] or mapping={…}")
    count = 0
    for el, where, key, c in list(iter_colors(root)):
        n = norm_color(c)
        if not n or n not in m:
            continue
        if where == "attr":
            el.set(key, m[n])
        elif where == "style":
            el.set("style", re.sub(rf"({re.escape(key)}\s*:\s*){re.escape(c)}", rf"\g<1>{m[n]}", el.get("style")))
        elif where == "css":
            el.text = el.text.replace(c, m[n])
        count += 1
    o = out_file(project, out, stem_of(svg, "recolor"), ".svg")
    write_tree(tree, o)
    return Result(f"Recoloured {count} colour uses ({len(m)} colours) in {p.name}.", files=[str(o)],
                  previews=[compare_preview(p, o, o.parent, o.stem + "-compare", ("Original", "Recoloured"))],
                  data={"mapping": m})
