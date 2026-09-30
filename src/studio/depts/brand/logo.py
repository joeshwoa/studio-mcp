"""Logo engine: text is shaped with HarfBuzz (correct Arabic joining, kerning, ligatures) and converted
to real outlines with fontTools, so every logo is a clean, font-independent SVG. Marks are built from
simple geometry on a 100-unit grid (circles, arcs, quarter-circles, bars, petals, arches, sparks) —
the vocabulary of professional geometric identities — with knock-outs done by SVG masks so every
variant (colour, reversed, mono) stays transparent and editable in Inkscape/Illustrator."""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..design import color as col
from ..design import fonts as F


# ---------------------------------------------------------------------------------------- text → path

@lru_cache(maxsize=32)
def _font(path: str):
    from ...core import deps
    deps.need("harfbuzz")
    deps.need("fonttools")
    import uharfbuzz as hb
    from fontTools.ttLib import TTFont
    blob = hb.Blob.from_file_path(path)
    face = hb.Face(blob)
    tt = TTFont(path)
    return hb.Font(face), face.upem, tt, tt.getGlyphSet(), tt.getGlyphOrder()


@dataclass
class TextPath:
    d: str
    x0: float
    y0: float
    x1: float
    y1: float
    cap: float        # cap height (px, positive)
    xh: float         # x-height
    asc: float
    desc: float
    rtl: bool

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0


def text_path(text: str, family: str, weight: int = 700, size: float = 100, tracking: float = 0.0) -> TextPath:
    """Outline `text` in `family` at `size` px (baseline y=0, SVG y-down). tracking in em (Latin only)."""
    import uharfbuzz as hb
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    fp = str(F.ensure(family, [weight])[weight])
    font, upem, tt, gs, order = _font(fp)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": True})
    rtl = buf.direction == "rtl"
    s = size / upem
    trk = 0 if rtl else tracking * upem
    pen = SVGPathPen(gs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    bp = BoundsPen(gs)
    x = 0.0
    n = len(buf.glyph_infos)
    for i, (info, pos) in enumerate(zip(buf.glyph_infos, buf.glyph_positions)):
        g = order[info.codepoint]
        m = (s, 0, 0, -s, (x + pos.x_offset) * s, -pos.y_offset * s)
        gs[g].draw(TransformPen(pen, m))
        gs[g].draw(TransformPen(bp, m))
        x += pos.x_advance + (trk if i < n - 1 else 0)
    os2 = tt["OS/2"] if "OS/2" in tt else None
    cap = (getattr(os2, "sCapHeight", 0) or 0.7 * upem) * s
    xh = (getattr(os2, "sxHeight", 0) or 0.5 * upem) * s
    hhea = tt["hhea"]
    b = bp.bounds or (0, -cap, x * s, 0)
    return TextPath(pen.getCommands(), b[0], b[1], b[2], b[3], cap, xh, hhea.ascent * s, -hhea.descent * s, rtl)


def glyph_fit(text: str, family: str, weight: int, box: tuple[float, float, float, float], by: str = "cap") -> str:
    """Path of text scaled/centred into box (x, y, w, h) — used for monograms. by='cap' centres on the
    cap height (optically right for Latin capitals), 'bbox' on the real outline bounds."""
    tp = text_path(text, family, weight, 100)
    bx, by_, bw, bh = box
    if by == "cap" and not tp.rtl:
        hh = max(tp.cap, 1)
        sc = min(bh / hh, bw / max(tp.w, 1))
        cx = bx + bw / 2 - (tp.x0 + tp.w / 2) * sc
        cy = by_ + bh / 2 + (hh / 2) * sc
    else:
        sc = min(bh / max(tp.h, 1), bw / max(tp.w, 1))
        cx = bx + bw / 2 - (tp.x0 + tp.w / 2) * sc
        cy = by_ + bh / 2 - (tp.y0 + tp.h / 2) * sc
    return f'<path transform="translate({cx:.2f} {cy:.2f}) scale({sc:.4f})" d="{tp.d}"/>'


# ---------------------------------------------------------------------------------------- marks

@dataclass
class Mark:
    """A logo mark on a 100×100 grid: layers (svg element + colour role) and knock-out cuts."""
    kind: str
    layers: list = field(default_factory=list)     # [(svg_element_without_fill, role)]
    cuts: list = field(default_factory=list)       # [svg_element] removed from all layers (mask)
    w: float = 100
    h: float = 100


def _circle(cx, cy, r):
    return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}"/>'


def _p(d):
    return f'<path d="{d}"/>'


def _sk(d, w, cut=False, transform=""):
    """Stroked open path (steam, handles, bean crease). In a cut it is drawn black in the mask."""
    col_ = "#000" if cut else "currentColor"
    tr = f' transform="{transform}"' if transform else ""
    return f'<path d="{d}" fill="none" stroke="{col_}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round"{tr}/>'


def _quarter(x, y, s, corner):
    """Quarter circle filling an s×s cell, centred on the given corner (0 tl,1 tr,2 br,3 bl)."""
    cx, cy = [(x, y), (x + s, y), (x + s, y + s), (x, y + s)][corner]
    sx = s if corner in (0, 3) else -s
    sy = s if corner in (0, 1) else -s
    sweep = 1 if corner in (0, 2) else 0
    return _p(f"M{cx:.2f} {cy:.2f}L{cx + sx:.2f} {cy:.2f}A{s:.2f} {s:.2f} 0 0 {sweep} {cx:.2f} {cy + sy:.2f}Z")


def mark(kind: str, initials: str, font: str, weight: int, seed: int) -> Mark:
    rnd = random.Random(seed)
    m = Mark(kind)
    L = initials[:1] or "A"
    if kind == "monogram_circle":
        m.layers.append((_circle(50, 50, 50), "primary"))
        m.cuts.append(glyph_fit(L, font, weight, (25, 26, 50, 48)))
    elif kind == "monogram_square":
        m.layers.append(('<rect x="0" y="0" width="100" height="100" rx="24"/>', "primary"))
        ini = initials[:2]
        m.cuts.append(glyph_fit(ini, font, weight, (16, 24, 68, 44)))
        m.layers.append((_circle(83, 83, 6.5), "accent"))
    elif kind == "monogram_letter":
        m.layers.append((glyph_fit(L, font, weight, (6, 4, 80, 92)), "primary"))
        m.layers.append((_circle(86, 88, 9), "accent"))
    elif kind == "letter_split":
        g = glyph_fit(L, font, weight, (8, 4, 84, 92))
        m.layers.append((f'<g clip-path="url(#cl-a)">{g}</g>', "primary"))
        m.layers.append((f'<g clip-path="url(#cl-b)">{g}</g>', "accent"))
        m.defs = ('<clipPath id="cl-a"><path d="M-10 -10H110V38L-10 70Z"/></clipPath>'
                  '<clipPath id="cl-b"><path d="M-10 74L110 42V110H-10Z"/></clipPath>')
    elif kind == "hexagon":
        pts = " ".join(f"{50 + 50 * math.cos(math.radians(a - 90)):.2f},{50 + 50 * math.sin(math.radians(a - 90)):.2f}" for a in range(0, 360, 60))
        m.layers.append((f'<polygon points="{pts}"/>', "primary"))
        m.cuts.append(glyph_fit(L, font, weight, (28, 29, 44, 42)))
        m.w = m.h = 100
    elif kind == "quarters":
        cells = [(0, 0), (52, 0), (0, 52), (52, 52)]
        roles = ["primary", "primary", "accent", "primary"]
        rnd.shuffle(roles)
        for (x, y), role in zip(cells, roles):
            t = rnd.choice(["q", "q", "q", "c", "h"])
            if t == "q":
                m.layers.append((_quarter(x, y, 48, rnd.randrange(4)), role))
            elif t == "c":
                m.layers.append((_circle(x + 24, y + 24, 24), role))
            else:
                horiz = rnd.random() < .5
                m.layers.append((_p(f"M{x} {y + 48}A24 24 0 0 1 {x + 48} {y + 48}Z") if horiz else
                                 _p(f"M{x} {y}A24 24 0 0 1 {x} {y + 48}Z"), role))
    elif kind == "orbit":
        m.layers.append((_circle(50, 50, 46), "primary"))
        m.cuts.append(_circle(50, 50, 31))
        a = math.radians(rnd.choice([-45, -30, 225, 210]))
        cx, cy = 50 + 38.5 * math.cos(a), 50 + 38.5 * math.sin(a)
        m.cuts.append(_circle(cx, cy, 17))
        m.layers.append((_circle(cx, cy, 11.5), "accent+"))
        m.layers.append((_circle(50, 50, 14), "primary+"))
    elif kind == "stack":
        ws = [100, 74, 48]
        for i, w in enumerate(ws):
            m.layers.append((f'<rect x="0" y="{i * 36}" width="{w}" height="26" rx="13"/>', "accent" if i == 2 else "primary"))
        m.h = 98
    elif kind == "petals":
        n = rnd.choice([4, 6, 8])
        for i in range(n):
            a = 360 * i / n
            m.layers.append((f'<path transform="rotate({a:.1f} 50 50)" d="M50 50C38 38 38 14 50 2C62 14 62 38 50 50Z"/>',
                             "accent" if i % (n // 2 if n > 4 else 2) == 0 and i else "primary"))
        m.layers.append((_circle(50, 50, 6), "accent"))
    elif kind == "arch":
        m.layers.append((_p("M6 100V50A44 44 0 0 1 94 50V100Z"), "primary"))
        m.cuts.append(_p("M24 100V52A26 26 0 0 1 76 52V100Z"))
        m.layers.append((_p("M36 100V56A14 14 0 0 1 64 56V100Z"), "accent+"))
    elif kind == "spark":
        m.layers.append((_p("M50 0C54 30 70 46 100 50C70 54 54 70 50 100C46 70 30 54 0 50C30 46 46 30 50 0Z"), "primary"))
        m.layers.append((_p("M84 4C85 11 89 15 96 16C89 17 85 21 84 28C83 21 79 17 72 16C79 15 83 11 84 4Z"), "accent"))
    elif kind == "chevrons":
        m.layers.append((_p("M4 10H34L64 50L34 90H4L34 50Z"), "primary"))
        m.layers.append((_p("M40 10H70L100 50L70 90H40L70 50Z"), "accent"))
    elif kind == "leaf":
        m.layers.append((_p("M10 90C10 40 40 10 90 10C90 60 60 90 10 90Z"), "primary"))
        m.cuts.append(_p("M16 84L78 22L81 25L19 87Z"))
        m.layers.append((_circle(82, 82, 9), "accent+"))
    elif kind == "wave":
        for i in range(3):
            y = 18 + i * 28
            m.layers.append((f'<path d="M0 {y + 10}C16 {y - 6} 34 {y - 6} 50 {y + 10}S84 {y + 26} 100 {y + 10}V{y + 24}C84 {y + 40} 66 {y + 40} 50 {y + 24}S16 {y + 8} 0 {y + 24}Z"/>',
                             "accent" if i == 1 else "primary"))
    elif kind == "cup":
        m.layers.append((_p("M16 38H76V60C76 79 63 92 46 92C29 92 16 79 16 60Z"), "primary"))
        m.layers.append((_sk("M76 46H80A11 11 0 0 1 80 68H75", 7), "primary"))
        for x in (36, 55):
            m.layers.append((_sk(f"M{x} 29C{x - 6} 23 {x + 6} 17 {x} 9", 5), "accent"))
    elif kind == "bean":
        m.layers.append(('<ellipse cx="50" cy="50" rx="30" ry="45" transform="rotate(35 50 50)"/>', "primary"))
        m.cuts.append(_sk("M50 7C36 30 64 70 50 93", 7, cut=True, transform="rotate(35 50 50)"))
    elif kind == "bowl":
        m.layers.append((_p("M6 48H94C94 73 74 92 50 92C26 92 6 73 6 48Z"), "primary"))
        for x in (32, 50, 68):
            m.layers.append((_sk(f"M{x} 38C{x - 6} 31 {x + 6} 24 {x} 16", 5), "accent"))
    elif kind == "drop":
        m.layers.append((_p("M50 4C50 4 16 44 16 64A34 34 0 0 0 84 64C84 44 50 4 50 4Z"), "primary"))
        m.cuts.append(_sk("M32 64A18 18 0 0 0 50 82", 6, cut=True))
    elif kind == "roof":
        m.layers.append((_p("M50 6L96 46H84V94H16V46H4Z"), "primary"))
        m.cuts.append(_p("M42 94V70A8 8 0 0 1 58 70V94Z"))
        m.layers.append((_circle(50, 44, 7), "accent+"))
    elif kind == "book":
        m.layers.append((_p("M4 22C20 13 37 13 47 23V92C37 82 20 82 4 90Z"), "primary"))
        m.layers.append((_p("M96 22C80 13 63 13 53 23V92C63 82 80 82 96 90Z"), "primary"))
        m.layers.append((_p("M68 20H80V48L74 42L68 48Z"), "accent+"))
    elif kind == "shield":
        m.layers.append((_p("M50 4L90 18V48C90 72 72 88 50 96C28 88 10 72 10 48V18Z"), "primary"))
        m.cuts.append(glyph_fit(L, font, weight, (30, 28, 40, 40)))
    elif kind == "bars":
        for i, h in enumerate((42, 66, 92)):
            m.layers.append((f'<rect x="{4 + i * 33}" y="{96 - h}" width="26" height="{h}" rx="7"/>', "accent" if i == 2 else "primary"))
    elif kind == "bolt":
        m.layers.append((_p("M60 2L14 58H46L38 98L86 40H54Z"), "primary"))
        m.layers.append((_circle(84, 84, 9), "accent"))
    elif kind == "heart":
        m.layers.append((_p("M50 92C20 72 4 54 4 33A23 23 0 0 1 50 22A23 23 0 0 1 96 33C96 54 80 72 50 92Z"), "primary"))
        m.layers.append((_circle(72, 34, 8), "accent+"))
    elif kind == "plus":
        m.layers.append(('<rect x="33" y="4" width="34" height="92" rx="11"/>', "primary"))
        m.layers.append(('<rect x="4" y="33" width="92" height="34" rx="11"/>', "primary"))
        m.layers.append((_circle(50, 50, 9), "accent+"))
    elif kind == "star8":
        m.layers.append(('<rect x="18" y="18" width="64" height="64" rx="3"/>', "primary"))
        m.layers.append(('<rect x="18" y="18" width="64" height="64" rx="3" transform="rotate(45 50 50)"/>', "primary"))
        m.cuts.append(_circle(50, 50, 17))
        m.layers.append((_circle(50, 50, 9), "accent+"))
    elif kind == "sun":
        m.layers.append((_circle(50, 50, 25), "primary"))
        for i in range(12):
            m.layers.append((f'<rect x="47" y="1" width="6" height="15" rx="3" transform="rotate({i * 30} 50 50)"/>', "accent"))
    elif kind == "pyramid":
        m.layers.append((_p("M46 10L94 92H2Z"), "primary"))
        m.layers.append((_p("M46 10L94 92H60Z"), "secondary+"))
        m.layers.append((_circle(84, 20, 10), "accent"))
    elif kind == "bubble":
        m.layers.append((_p("M14 8H86A10 10 0 0 1 96 18V64A10 10 0 0 1 86 74H46L24 94V74H14A10 10 0 0 1 4 64V18A10 10 0 0 1 14 8Z"), "primary"))
        for x in (30, 50, 70):
            m.cuts.append(_circle(x, 41, 7))
    elif kind == "pin":
        m.layers.append((_p("M50 97C50 97 13 61 13 38A37 37 0 0 1 87 38C87 61 50 97 50 97Z"), "primary"))
        m.cuts.append(_circle(50, 38, 16))
        m.layers.append((_circle(50, 38, 8), "accent+"))
    elif kind == "sprout":
        m.layers.append(('<rect x="46" y="42" width="8" height="54" rx="4"/>', "primary"))
        m.layers.append((_p("M47 62C47 39 31 27 6 27C6 51 22 62 47 62Z"), "accent"))
        m.layers.append((_p("M53 50C53 25 69 10 95 10C95 37 79 50 53 50Z"), "primary"))
    else:
        raise ValueError(kind)
    return m


MARK_KINDS = ["monogram_circle", "monogram_square", "monogram_letter", "letter_split", "hexagon", "quarters", "orbit",
              "stack", "petals", "arch", "spark", "chevrons", "leaf", "wave",
              # subject marks (relate to what the business does)
              "cup", "bean", "bowl", "drop", "roof", "book", "shield", "bars", "bolt", "heart", "plus", "star8", "sun",
              "pyramid", "bubble", "pin", "sprout"]
MONOGRAMS = ("monogram_circle", "monogram_square", "monogram_letter", "letter_split", "hexagon", "shield")

def pick_marks(brief: str, n: int, name: str = "") -> list[str]:
    """Marks for a brief: subject marks for the detected sector first, then geometry, and (n ≥ 3) always
    one monogram so the client can compare symbol vs lettermark."""
    from .brief import direction
    kinds = direction(name, brief)["marks"]
    for k in ["monogram_square", "orbit", "quarters", "spark", "arch", "monogram_circle"]:
        if k not in kinds:
            kinds.append(k)
    out = kinds[:n]
    if n >= 3 and not any(k in MONOGRAMS for k in out):
        out[-1] = next(k for k in kinds if k in MONOGRAMS)
    return out


# ---------------------------------------------------------------------------------------- SVG assembly

def _roles(palette: dict, variant: str) -> dict:
    """Colour per role for a variant: color | reversed | black | white."""
    if variant == "black":
        return {r: "#111111" for r in ("primary", "accent", "ink", "secondary")}
    if variant == "white":
        return {r: "#FFFFFF" for r in ("primary", "accent", "ink", "secondary")}
    p, a, ink, paper = palette["primary"], palette["accent"], palette["ink"], palette["paper"]
    if variant == "reversed":   # on the dark brand colour (secondary)
        bg = palette.get("secondary", ink)
        acc = a if col.contrast(a, bg) >= 2.2 else paper
        prim = paper if col.contrast(p, bg) < 3 else p
        return {"primary": prim, "accent": acc, "ink": paper, "secondary": paper}
    prim = p if col.contrast(p, paper) >= 2.2 else ink
    acc = a if col.contrast(a, paper) >= 1.6 else p
    word = ink if col.contrast(p, paper) < 4.5 else p
    return {"primary": prim, "accent": acc, "ink": word, "secondary": palette.get("secondary", ink)}


_uid = [0]


def mark_group(m: Mark, colors: dict, x: float, y: float, size: float) -> tuple[str, str]:
    """(defs, group) drawing mark m at (x, y) scaled so its height == size."""
    _uid[0] += 1
    uid = f"k{_uid[0]}"
    s = size / m.h
    defs = getattr(m, "defs", "")
    if defs:
        defs = defs.replace('id="cl-a"', f'id="{uid}a"').replace('id="cl-b"', f'id="{uid}b"')
    body, top = [], []
    for el, role in m.layers:
        el2 = el.replace("url(#cl-a)", f"url(#{uid}a)").replace("url(#cl-b)", f"url(#{uid}b)")
        c_ = colors.get(role.rstrip("+"), colors["primary"])
        (top if role.endswith("+") else body).append(f'<g fill="{c_}" color="{c_}">{el2}</g>')
    mask_attr = ""
    if m.cuts:
        defs += (f'<mask id="{uid}m" maskUnits="userSpaceOnUse" x="-5" y="-5" width="{m.w + 10}" height="{m.h + 10}">'
                 f'<rect x="-5" y="-5" width="{m.w + 10}" height="{m.h + 10}" fill="#fff"/>'
                 f'<g fill="#000">{"".join(m.cuts)}</g></mask>')
        mask_attr = f' mask="url(#{uid}m)"'
    g = f'<g transform="translate({x:.2f} {y:.2f}) scale({s:.4f})"><g{mask_attr}>{"".join(body)}</g>{"".join(top)}</g>'
    return defs, g


def svg_doc(w: float, h: float, defs: str, body: str, title: str, bg: str | None = None) -> str:
    bgr = f'<rect width="{w:.2f}" height="{h:.2f}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.2f} {h:.2f}" width="{w:.0f}" height="{h:.0f}">'
            f'<title>{title}</title>{f"<defs>{defs}</defs>" if defs else ""}{bgr}{body}</svg>')


@dataclass
class Concept:
    id: str
    mark: str
    font: str
    weight: int
    case: str = "title"        # title | upper | lower | as-is
    tracking: float = 0.0
    font_ar: str = "Cairo"
    weight_ar: int = 700
    dot: bool = False          # accent full stop after the wordmark
    seed: int = 1

    def to_dict(self):
        return dict(self.__dict__)


def cased(name: str, case: str) -> str:
    return name.upper() if case == "upper" else name.lower() if case == "lower" else name


def wordmark_parts(c: Concept, name: str, name_ar: str, colors: dict, x: float, y_base: float, size: float,
                   align: str = "left", size_ar: float | None = None) -> tuple[str, float, float, float, float]:
    """Wordmark (EN, optional AR line below) → (svg, x0, y_top, x1, y_bottom)."""
    tp = text_path(cased(name, c.case), c.font, c.weight, size, c.tracking)
    parts = []
    w = tp.w
    dot_r = size * 0.085
    extra = (dot_r * 2 + size * 0.06) if c.dot else 0
    total_w = w + extra
    ar = None
    if name_ar:
        ar = text_path(name_ar, c.font_ar, c.weight_ar, size_ar or size * 0.62)
        if not size_ar and ar.w < 0.5 * (w + extra):  # wide tracked caps: don't let the Arabic line shrink to a caption
            ar = text_path(name_ar, c.font_ar, c.weight_ar, min(size * 0.85, size * 0.62 * 0.5 * (w + extra) / max(ar.w, 1)))
        total_w = max(total_w, ar.w)
    def ox(width):
        return x - tp.x0 if align == "left" else x + (total_w - width) / 2 - tp.x0 if align == "center" else x + total_w - width - tp.x0
    tx = ox(w + extra)
    parts.append(f'<path fill="{colors["ink"]}" transform="translate({tx:.2f} {y_base:.2f})" d="{tp.d}"/>')
    if c.dot:
        parts.append(f'<circle fill="{colors["accent"]}" cx="{tx + tp.x1 + size * 0.06 + dot_r:.2f}" cy="{y_base - dot_r:.2f}" r="{dot_r:.2f}"/>')
    y_bottom = y_base + max(0, tp.y1)
    top = y_base + tp.y0
    if ar:
        gap = size * 0.34
        yb2 = y_base + max(0, tp.y1) + gap - ar.y0
        ax = (x - ar.x0 if align == "left" else x + (total_w - ar.w) / 2 - ar.x0 if align == "center" else x + total_w - ar.w - ar.x0)
        if align == "left":  # Arabic line reads RTL: align it to the wordmark's right edge
            ax = x + (w + extra) - ar.w - ar.x0 if ar.w < w + extra else x - ar.x0
        parts.append(f'<path fill="{colors["ink"]}" transform="translate({ax:.2f} {yb2:.2f})" d="{ar.d}"/>')
        y_bottom = yb2 + ar.y1
    return "".join(parts), x, top, x + total_w, y_bottom


def build_svgs(c: Concept, name: str, name_ar: str, initials: str, palette: dict) -> dict[str, str]:
    """All logo variants for a concept → {key: svg string}."""
    m = mark(c.mark, initials, c.font if c.mark.startswith(("mono", "letter", "hex")) else c.font, 800 if c.weight < 700 and c.mark.startswith("mono") else c.weight, c.seed)
    out: dict[str, str] = {}
    pad = 0.12
    for variant in ("color", "reversed", "black", "white"):
        cols = _roles(palette, variant)
        sfx = "" if variant == "color" else "_" + ("reversed" if variant == "reversed" else "mono_" + variant)
        # icon
        d, g = mark_group(m, cols, 10, 10, 100)
        iw = m.w / m.h * 100
        out["icon" + sfx] = svg_doc(iw + 20, 120, d, g, f"{name} icon")
        # wordmark
        size = 100
        wm, x0, top, x1, bot = wordmark_parts(c, name, "", cols, 0, 0, size)
        P = size * pad
        out["wordmark" + sfx] = svg_doc(x1 - x0 + 2 * P, bot - top + 2 * P, "",
                                        f'<g transform="translate({P - x0:.2f} {P - top:.2f})">{wm}</g>', f"{name} wordmark")
        if name_ar:
            tpa = text_path(name_ar, c.font_ar, c.weight_ar, size)
            out["wordmark_ar" + sfx] = svg_doc(tpa.w + 2 * P, tpa.h + 2 * P, "",
                                               f'<path fill="{cols["ink"]}" transform="translate({P - tpa.x0:.2f} {P - tpa.y0:.2f})" d="{tpa.d}"/>',
                                               f"{name_ar} wordmark")
        # horizontal lockup: icon height ≈ 1.9 × cap height (2.6 with an Arabic line)
        tp = text_path(cased(name, c.case), c.font, c.weight, size, c.tracking)
        wmh, hx0, htop, hx1, hbot = wordmark_parts(c, name, name_ar, cols, 0, 0, size, "left")
        block_h = hbot - (-tp.cap)
        ih = max(tp.cap * 1.9, block_h * 1.15)
        gap = ih * 0.36
        cy = (-tp.cap + hbot) / 2 if name_ar else -tp.cap / 2
        iy = cy - ih / 2
        iw2 = ih * m.w / m.h
        d2, g2 = mark_group(m, cols, 0, iy, ih)
        text_g = f'<g transform="translate({iw2 + gap:.2f} 0)">{wmh}</g>'
        X0, Y0 = min(0, iw2 + gap + hx0), min(iy, htop)
        X1, Y1 = iw2 + gap + hx1, max(iy + ih, hbot)
        P = ih * 0.18
        out["horizontal" + sfx] = svg_doc(X1 - X0 + 2 * P, Y1 - Y0 + 2 * P, d2,
                                          f'<g transform="translate({P - X0:.2f} {P - Y0:.2f})">{g2}{text_g}</g>', f"{name} logo")
        # stacked lockup
        wms, sx0, stop, sx1, sbot = wordmark_parts(c, name, name_ar, cols, 0, 0, size, "center")
        tw = sx1 - sx0
        ih3 = max(tp.cap * 2.6, min(tw * 0.62, tp.cap * 3.4))
        iw3 = ih3 * m.w / m.h
        gap3 = tp.cap * 0.75
        d3, g3 = mark_group(m, cols, (tw - iw3) / 2, 0, ih3)
        ty = ih3 + gap3 - stop
        H = ty + sbot
        P = ih3 * 0.16
        out["stacked" + sfx] = svg_doc(tw + 2 * P, H + 2 * P, d3,
                                       f'<g transform="translate({P:.2f} {P:.2f})">{g3}<g transform="translate({-sx0:.2f} {ty:.2f})">{wms}</g></g>',
                                       f"{name} logo stacked")
    # app icon / favicon: mark on a primary (or paper) rounded plate
    plate = palette["primary"]
    cols = _roles(palette, "color")
    on_plate = {"primary": palette["paper"] if col.contrast(palette["paper"], plate) >= 2.5 else palette["ink"], "accent": palette["accent"]
                if col.contrast(palette["accent"], plate) >= 1.8 else palette["paper"], "ink": palette["paper"], "secondary": palette["paper"]}
    if c.mark in ("monogram_circle", "monogram_square", "hexagon"):
        on_plate = cols
        plate = palette["paper"]
    inner = 64
    d4, g4 = mark_group(m, on_plate, (100 - inner * m.w / m.h) / 2, (100 - inner) / 2, inner)
    out["app_icon"] = svg_doc(100, 100, d4, f'<rect width="100" height="100" rx="22" fill="{plate}"/>{g4}', f"{name} app icon")
    d5, g5 = mark_group(m, on_plate, (100 - 76 * m.w / m.h) / 2, 12, 76)
    out["favicon"] = svg_doc(100, 100, d5, f'<rect width="100" height="100" rx="20" fill="{plate}"/>{g5}', f"{name} favicon")
    # monogram: initials in the head font inside a ring
    mono_d = glyph_fit(initials[:2], c.font, max(c.weight, 700), (22, 30, 56, 40))
    out["monogram"] = svg_doc(100, 100, "", f'<circle cx="50" cy="50" r="46" fill="none" stroke="{cols["primary"]}" stroke-width="4"/>'
                                            f'<g fill="{cols["ink"]}">{mono_d}</g>', f"{name} monogram")
    return out


def concepts_for(name: str, brief: str, pair: dict, n: int = 4, seed_extra: str = "",
                 alt_pairs: list[dict] | None = None, marks: list[str] | None = None) -> list[Concept]:
    """n distinct concepts: brief-driven marks (subject marks first, always one monogram) × wordmark
    treatments from the brand's pairing and the sector's alternative pairings (so type varies too)."""
    from .brief import LUX_WORDS, TECH_WORDS, words
    base = int(hashlib.md5((name + seed_extra).encode()).hexdigest()[:8], 16)
    kinds = list(marks) if marks else pick_marks(brief, max(n, 4), name)
    for k in ["monogram_square", "orbit", "quarters", "spark", "arch", "monogram_circle"]:
        if len(kinds) >= max(n, 4):
            break
        if k not in kinds:
            kinds.append(k)
    if seed_extra and len(kinds) > 1:  # a new variation starts somewhere else in the list
        r = base % len(kinds)
        kinds = kinds[r:] + kinds[:r]
    if n >= 3 and not any(k in MONOGRAMS for k in kinds[:n]):
        kinds[min(n, len(kinds)) - 1] = "monogram_circle"
    ws = set(words(brief))
    lux = bool(ws & LUX_WORDS)
    tech = bool(ws & TECH_WORDS)
    alts = [a for a in (alt_pairs or []) if a.get("head") != pair["head"]]
    alt = alts[0] if alts else {"head": pair["body"] if pair["body"] != pair["head"] else "Manrope", "head_weight": 700}
    alt2 = alts[1] if len(alts) > 1 else pair
    hw = pair.get("head_weight", 700)
    treatments = [
        {"font": pair["head"], "weight": hw, "case": "upper" if lux else "lower" if tech else "title",
         "tracking": 0.12 if lux else -0.02, "dot": False},
        {"font": alt["head"], "weight": alt.get("head_weight", 700), "case": "title" if not tech else "lower",
         "tracking": 0.0 if not lux else 0.08, "dot": not lux},
        {"font": pair["head"], "weight": min(900, hw + 100), "case": "upper", "tracking": 0.14, "dot": False},
        {"font": alt2["head"], "weight": alt2.get("head_weight", 700), "case": "title", "tracking": -0.01, "dot": False},
    ]
    out = []
    for i in range(n):
        tr = treatments[i % len(treatments)]
        wgt = F.nearest_weight(tr["font"], tr["weight"])
        out.append(Concept(id=f"c{i + 1}", mark=kinds[i % len(kinds)], font=tr["font"], weight=wgt, case=tr["case"],
                           tracking=tr["tracking"], font_ar=pair.get("head_ar", "Cairo"),
                           weight_ar=F.nearest_weight(pair.get("head_ar", "Cairo"), 700), dot=tr["dot"], seed=base + i))
    return out


def initials_of(name: str) -> str:
    words = [w for w in name.replace("-", " ").split() if w[:1].isalnum()]
    if not words:
        return "A"
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[1][0]).upper()
