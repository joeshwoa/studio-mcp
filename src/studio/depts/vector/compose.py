"""vector_compose: JSON spec → clean, editable SVG (+ PDF + PNG), Arabic text included."""
from __future__ import annotations

import base64
import json
import mimetypes
import xml.etree.ElementTree as ET
from pathlib import Path

from ...core.registry import tool
from ...core.result import Result, ToolError
from ..photo.fonts import family_name, find_font, has_arabic, weight_value
from ._svg import SVG_NS, inkscape_run, out_file, preview, render_png, rsvg, svg_size, write_tree
from ...core.deps import have, run

SPEC_HELP = """{
 "width": 1000, "height": 1000, "background": "#ffffff" | {"type":"linear","colors":["#0f172a","#1e3a8a"],"angle":90} | null,
 "elements": [   // painted in order (first = back)
  {"type":"rect","id":"card","x":100,"y":100,"width":800,"height":500,"rx":40,"fill":"#fff","stroke":"#111","stroke_width":4},
  {"type":"circle","cx":500,"cy":500,"r":200,"fill":{"type":"radial","colors":["#fde68a","#f59e0b"]}},
  {"type":"ellipse","cx":..,"cy":..,"rx":..,"ry":..}, {"type":"line","x1":..,"y1":..,"x2":..,"y2":..,"stroke":"#000","stroke_width":6,"linecap":"round"},
  {"type":"polygon"|"polyline","points":[[x,y],…]}, {"type":"path","d":"M10 10 C …Z","fill":"#e11d48"},
  {"type":"text","text":"مرحبا بيك","x":500,"y":520,"font":"Cairo","weight":700,"size":96,"fill":"#111",
   "anchor":"start|middle|end","direction":"auto|rtl|ltr","letter_spacing":0,"line_height":1.2 (for \\n lines)},
  {"type":"image","href":"/path/photo.png","x":0,"y":0,"width":300,"height":300}   (embedded),
  {"type":"group","id":"logo","transform":"translate(100 50) rotate(-8)","elements":[…]}
 ]
 // any element: "id", "opacity", "transform", "fill"/"stroke" as colour or gradient dict
 // ({"type":"linear"|"radial","colors":[…],"stops":[0,1],"angle":deg}), "stroke_width", "linejoin",
 // "shadow": {"dx":0,"dy":8,"blur":12,"color":"#000","opacity":0.3}, "class"
}"""


class _Builder:
    def __init__(self):
        self.defs = ET.Element("defs")
        self.n = 0
        self.fonts: dict[str, str] = {}
        self.warnings: list[str] = []

    def uid(self, p: str) -> str:
        self.n += 1
        return f"{p}{self.n}"

    def paint(self, v) -> str:
        if isinstance(v, dict):
            kind = v.get("type", "linear")
            cols = v.get("colors") or ["#000", "#fff"]
            stops = v.get("stops") or [i / max(1, len(cols) - 1) for i in range(len(cols))]
            gid = v.get("id") or self.uid("grad")
            if kind == "radial":
                g = ET.SubElement(self.defs, "radialGradient", id=gid, cx=str(v.get("cx", 0.5)), cy=str(v.get("cy", 0.5)),
                                  r=str(v.get("r", 0.5)))
            else:
                import math
                a = math.radians(float(v.get("angle", 90)))
                x1, y1 = 0.5 - math.cos(a) / 2, 0.5 - math.sin(a) / 2
                g = ET.SubElement(self.defs, "linearGradient", id=gid, x1=f"{x1:.4f}", y1=f"{y1:.4f}",
                                  x2=f"{1 - x1:.4f}", y2=f"{1 - y1:.4f}")
            for c, o in zip(cols, stops):
                st = {"offset": f"{float(o):.4f}", "stop-color": c[:7] if c.startswith("#") and len(c) == 9 else c}
                if c.startswith("#") and len(c) == 9:
                    st["stop-opacity"] = f"{int(c[7:9], 16) / 255:.3f}"
                ET.SubElement(g, "stop", st)
            return f"url(#{gid})"
        return str(v)

    def shadow(self, s) -> str:
        s = {} if s is True else s
        fid = self.uid("shadow")
        f = ET.SubElement(self.defs, "filter", id=fid, x="-50%", y="-50%", width="200%", height="200%")
        ET.SubElement(f, "feDropShadow", dx=str(s.get("dx", 0)), dy=str(s.get("dy", 6)),
                      stdDeviation=str(s.get("blur", 8) / 2), **{"flood-color": s.get("color", "#000"),
                                                                "flood-opacity": str(s.get("opacity", 0.3))})
        return f"url(#{fid})"

    def common(self, el: ET.Element, spec: dict):
        for k in ("id", "transform", "opacity", "class"):
            if spec.get(k) is not None:
                el.set(k, str(spec[k]))
        if "fill" in spec:
            el.set("fill", self.paint(spec["fill"]))
        if spec.get("stroke"):
            el.set("stroke", self.paint(spec["stroke"]))
            el.set("stroke-width", str(spec.get("stroke_width", 1)))
            if spec.get("linecap"):
                el.set("stroke-linecap", spec["linecap"])
            el.set("stroke-linejoin", spec.get("linejoin", "round"))
        if spec.get("dash"):
            el.set("stroke-dasharray", " ".join(str(x) for x in spec["dash"]))
        if spec.get("shadow"):
            el.set("filter", self.shadow(spec["shadow"]))

    def element(self, parent: ET.Element, spec: dict):
        t = spec.get("type")
        num = lambda k, d=0: str(spec.get(k, d))  # noqa: E731
        if t == "rect":
            el = ET.SubElement(parent, "rect", x=num("x"), y=num("y"), width=num("width", 100), height=num("height", 100))
            if spec.get("rx"):
                el.set("rx", str(spec["rx"]))
                el.set("ry", str(spec.get("ry", spec["rx"])))
        elif t == "circle":
            el = ET.SubElement(parent, "circle", cx=num("cx"), cy=num("cy"), r=num("r", 50))
        elif t == "ellipse":
            el = ET.SubElement(parent, "ellipse", cx=num("cx"), cy=num("cy"), rx=num("rx", 50), ry=num("ry", 30))
        elif t == "line":
            el = ET.SubElement(parent, "line", x1=num("x1"), y1=num("y1"), x2=num("x2"), y2=num("y2"))
            spec = {"stroke": "#000", **spec}
        elif t in ("polygon", "polyline"):
            pts = " ".join(f"{p[0]},{p[1]}" for p in spec.get("points", []))
            el = ET.SubElement(parent, t, points=pts)
            if t == "polyline" and "fill" not in spec:
                el.set("fill", "none")
        elif t == "path":
            if not spec.get("d"):
                raise ToolError("path element needs 'd'")
            el = ET.SubElement(parent, "path", d=spec["d"])
            if spec.get("fill_rule"):
                el.set("fill-rule", spec["fill_rule"])
        elif t == "text":
            el = self.text(parent, spec)
        elif t == "image":
            href = spec.get("href") or spec.get("src")
            p = Path(str(href)).expanduser()
            if p.exists():
                mime = mimetypes.guess_type(p.name)[0] or "image/png"
                href = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
            el = ET.SubElement(parent, "image", x=num("x"), y=num("y"), width=num("width", 100), height=num("height", 100),
                               href=str(href), preserveAspectRatio=spec.get("preserve", "xMidYMid slice"))
            self.warnings.append("embedded raster image — it will not scale like vector art")
        elif t == "group":
            el = ET.SubElement(parent, "g")
            for c in spec.get("elements", []):
                self.element(el, c)
        else:
            raise ToolError(f"unknown element type {t!r}", "rect circle ellipse line polygon polyline path text image group")
        self.common(el, spec)
        return el

    def text(self, parent: ET.Element, spec: dict) -> ET.Element:
        txt = str(spec.get("text", ""))
        fam = spec.get("font") or ("Cairo" if has_arabic(txt) else "Inter")
        w = weight_value(spec.get("weight", 400))
        path, warns = find_font(fam, w, bool(spec.get("italic")), txt)
        self.warnings += warns
        real = family_name(path)
        self.fonts[real] = str(path)
        rtl = spec.get("direction", "auto") == "rtl" or (spec.get("direction", "auto") == "auto" and has_arabic(txt))
        anchor = spec.get("anchor", "start")
        el = ET.SubElement(parent, "text", x=str(spec.get("x", 0)), y=str(spec.get("y", 0)))
        el.set("font-family", f"'{real}', sans-serif")
        el.set("font-size", str(spec.get("size", 48)))
        el.set("font-weight", str(w))
        if spec.get("italic"):
            el.set("font-style", "italic")
        if rtl:
            el.set("direction", "rtl")
            # with direction=rtl, 'start' is the RIGHT edge — callers think visually, so map it
            anchor = {"start": "end", "end": "start"}.get(anchor, anchor) if spec.get("visual_anchor", True) else anchor
        el.set("text-anchor", anchor)
        if spec.get("letter_spacing"):
            if has_arabic(txt):
                self.warnings.append("letter_spacing ignored on Arabic text (breaks joining)")
            else:
                el.set("letter-spacing", str(spec["letter_spacing"]))
        if "fill" not in spec:
            el.set("fill", "#111111")
        lines = txt.split("\n")
        if len(lines) == 1:
            el.text = txt
        else:
            lh = float(spec.get("line_height", 1.25)) * float(spec.get("size", 48))
            for i, line in enumerate(lines):
                ts = ET.SubElement(el, "tspan", x=str(spec.get("x", 0)))
                ts.set("dy", "0" if i == 0 else f"{lh:.2f}")
                ts.text = line
        return el


def build_svg(spec: dict) -> tuple[ET.ElementTree, _Builder]:
    W, H = spec.get("width", 1000), spec.get("height", 1000)
    root = ET.Element("svg", {"xmlns": SVG_NS, "width": str(W), "height": str(H), "viewBox": f"0 0 {W} {H}", "version": "1.1"})
    if spec.get("title"):
        ET.SubElement(root, "title").text = str(spec["title"])
    b = _Builder()
    root.append(b.defs)
    bg = spec.get("background")
    if bg:
        ET.SubElement(root, "rect", id="background", x="0", y="0", width=str(W), height=str(H), fill=b.paint(bg))
    for e in spec.get("elements", []):
        b.element(root, e)
    if not len(b.defs):
        root.remove(b.defs)
    return ET.ElementTree(root), b


def load_spec(spec) -> dict:
    if isinstance(spec, str):
        p = Path(spec).expanduser()
        spec = json.loads(p.read_text(encoding="utf-8")) if p.exists() else json.loads(spec)
    if not isinstance(spec, dict) or "elements" not in spec:
        raise ToolError("spec needs 'width', 'height', 'elements'", SPEC_HELP)
    return spec


def to_pdf(svg: Path, pdf: Path, text_to_path: bool = False) -> Path:
    if text_to_path or not have("rsvg"):
        inkscape_run([str(svg), "--export-type=pdf", f"--export-filename={pdf}"] + (["--export-text-to-path"] if text_to_path else []))
    else:
        run([rsvg(), "-f", "pdf", str(svg), "-o", str(pdf)])
    return pdf


def vector_compose(spec: dict, name: str = "vector", pdf: bool = True, png: bool = True, png_scale: float = 2.0,
                   outline_text: bool = False, project: str = "", out: str = "") -> Result:
    """Illustrator-style vector artwork from a JSON spec (shapes, paths, gradients, groups, drop shadows,
    text incl. Arabic RTL with Google/system fonts) → clean hand-editable SVG (opens in Inkscape/
    Illustrator/Figma), vector PDF (print), PNG (png_scale × size). outline_text=true also writes a
    print-safe copy with text converted to outlines (Inkscape). Returns files + preview."""
    spec = load_spec(spec)
    tree, b = build_svg(spec)
    svg = out_file(project, out, name, ".svg")
    write_tree(tree, svg)
    files, warns = [str(svg)], list(dict.fromkeys(b.warnings))
    W, H = svg_size(tree.getroot())
    if outline_text:
        o = out_file(project, out, f"{name}-outlined", ".svg")
        try:
            inkscape_run([str(svg), "--export-text-to-path", "--export-plain-svg", f"--export-filename={o}"])
            files.append(str(o))
        except ToolError as e:
            warns.append(f"outlining skipped: {e}")
    if pdf:
        p = out_file(project, out, name, ".pdf")
        try:
            to_pdf(svg, p)
            files.append(str(p))
        except ToolError as e:
            warns.append(f"PDF skipped: {e}")
    if png:
        p = out_file(project, out, name, ".png")
        render_png(svg, p, width=int(W * png_scale))
        files.append(str(p))
    res = Result(f"Vector artwork {W:g}×{H:g} with {len(spec['elements'])} top-level elements"
                 f"{' and fonts ' + ', '.join(b.fonts) if b.fonts else ''}.", files=files, previews=[preview(svg)],
                 warnings=warns, data={"fonts": b.fonts, "size": [W, H]},
                 next_steps=["open_in_app the .svg in Inkscape to edit", "vector_export for icons/favicons/print formats"])
    if b.fonts:
        res.next_steps.append("send the outlined SVG/PDF to printers (fonts are embedded as shapes)")
    return res


vector_compose.__doc__ += "\n\nSPEC format:\n" + SPEC_HELP
tool("vector", network=True)(vector_compose)
