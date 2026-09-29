"""Colour science for the studio: hex/RGB/OKLCH conversion, WCAG 2.x contrast, text-colour picking,
and palette harmonies built in OKLCH (perceptually even, so tints/shades look intentional)."""
from __future__ import annotations

import colorsys
import math
import re

NAMED = {"black": "#000000", "white": "#ffffff", "red": "#e53935", "green": "#2e7d32", "blue": "#1e56d9",
         "navy": "#14213d", "gold": "#c9a227", "teal": "#0f766e", "orange": "#f97316", "purple": "#6d28d9",
         "pink": "#e84a8a", "yellow": "#f5c518", "grey": "#6b7280", "gray": "#6b7280", "brown": "#7c4a2d",
         "beige": "#efe6d8", "cream": "#f6f1e7", "coral": "#ff6b4a", "mint": "#3ecf8e", "maroon": "#7a1f2b",
         "olive": "#6b7a2e", "sand": "#d9c3a0", "turquoise": "#14b8a6", "cyan": "#06b6d4", "magenta": "#d61f8c",
         "burgundy": "#6d1a36", "emerald": "#047857", "indigo": "#3730a3", "lavender": "#a78bfa", "charcoal": "#26282b"}


def norm(c: str) -> str:
    c = (c or "").strip().lower()
    if c in NAMED:
        return NAMED[c]
    m = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", c)
    if not m:
        m2 = re.fullmatch(r"rgba?\((\d+)[, ]+(\d+)[, ]+(\d+).*\)", c)
        if m2:
            return rgb_hex(tuple(int(x) for x in m2.groups()))
        raise ValueError(f"not a colour: {c!r}")
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "#" + h


def hex_rgb(c: str) -> tuple[int, int, int]:
    h = norm(c)[1:]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v))):02x}" for v in rgb[:3])


def _lin(v: float) -> float:
    v /= 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _unlin(v: float) -> float:
    v = max(0.0, min(1.0, v))
    return 255 * (12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055)


def luminance(c) -> float:
    r, g, b = hex_rgb(c) if isinstance(c, str) else c
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def grade(ratio: float, large: bool = False) -> str:
    if large:
        return "AAA" if ratio >= 4.5 else "AA" if ratio >= 3 else "fail"
    return "AAA" if ratio >= 7 else "AA" if ratio >= 4.5 else "AA-large" if ratio >= 3 else "fail"


# ---- OKLab / OKLCH -------------------------------------------------------------------------

def to_oklch(c: str) -> tuple[float, float, float]:
    r, g, b = (_lin(v) for v in hex_rgb(c))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    C = math.hypot(A, B)
    H = math.degrees(math.atan2(B, A)) % 360
    return L, C, H


def _oklch_rgb_raw(L: float, C: float, H: float) -> tuple[float, float, float]:
    A, B = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l_ = L + 0.3963377774 * A + 0.2158037573 * B
    m_ = L - 0.1055613458 * A - 0.0638541728 * B
    s_ = L - 0.0894841775 * A - 1.2914855480 * B
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def from_oklch(L: float, C: float, H: float) -> str:
    """OKLCH → hex, reducing chroma until the colour is inside sRGB (keeps hue and lightness)."""
    L = max(0.0, min(1.0, L))
    c = C
    for _ in range(40):
        rgb = _oklch_rgb_raw(L, c, H)
        if all(-0.0005 <= v <= 1.0005 for v in rgb):
            break
        c *= 0.93
    return rgb_hex(tuple(_unlin(v) for v in _oklch_rgb_raw(L, c, H)))


def shade(c: str, L: float | None = None, dC: float = 1.0, dH: float = 0.0) -> str:
    l0, c0, h0 = to_oklch(c)
    return from_oklch(l0 if L is None else L, c0 * dC, (h0 + dH) % 360)


def scale(c: str, steps=(0.97, 0.93, 0.86, 0.76, 0.66, 0.56, 0.47, 0.39, 0.31, 0.23)) -> dict[str, str]:
    """Tailwind-like 50…900 ramp of one hue (OKLCH lightness steps, chroma eased at the ends)."""
    _, c0, h0 = to_oklch(c)
    names = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900"]
    out = {}
    for n, L in zip(names, steps):
        k = 1 - abs(L - 0.6) * 0.9
        out[n] = from_oklch(L, c0 * max(0.18, k), h0)
    return out


def text_on(bg: str, light: str = "#ffffff", dark: str = "#111111") -> str:
    """The better of two text colours on a background (highest WCAG contrast)."""
    return light if contrast(light, bg) >= contrast(dark, bg) else dark


def ensure_contrast(fg: str, bg: str, target: float = 4.5) -> str:
    """Move fg's OKLCH lightness away from bg until the WCAG ratio reaches target."""
    if contrast(fg, bg) >= target:
        return norm(fg)
    L, C, H = to_oklch(fg)
    go_dark = luminance(bg) > 0.18
    for i in range(1, 60):
        L2 = L - 0.015 * i if go_dark else L + 0.015 * i
        cand = from_oklch(L2, C, H)
        if contrast(cand, bg) >= target:
            return cand
        if L2 <= 0 or L2 >= 1:
            break
    return "#111111" if go_dark else "#ffffff"


def cmyk(c: str) -> tuple[int, int, int, int]:
    """Naive RGB→CMYK percentages (a starting point — proof with the printer's ICC profile)."""
    r, g, b = (v / 255 for v in hex_rgb(c))
    k = 1 - max(r, g, b)
    if k >= 1:
        return 0, 0, 0, 100
    return tuple(round(100 * v) for v in ((1 - r - k) / (1 - k), (1 - g - k) / (1 - k), (1 - b - k) / (1 - k), k))


def hsl(c: str) -> tuple[int, int, int]:
    r, g, b = (v / 255 for v in hex_rgb(c))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return round(h * 360), round(s * 100), round(l * 100)


def mix(a: str, b: str, t: float) -> str:
    ra, rb = hex_rgb(a), hex_rgb(b)
    return rgb_hex(tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(3)))


def name_hue(h: float, C: float = 0.1, L: float = 0.5) -> str:
    if C < 0.03:
        return "charcoal" if L < 0.3 else "grey" if L < 0.8 else "paper"
    names = [(20, "rose"), (45, "coral"), (70, "amber"), (100, "gold"), (130, "olive"), (160, "green"),
             (190, "teal"), (230, "cyan"), (262, "blue"), (290, "indigo"), (320, "violet"), (350, "magenta"), (361, "rose")]
    for lim, n in names:
        if h < lim:
            return n
    return "rose"


# ---- mood → base colour ----------------------------------------------------------------------
MOOD_HUES = [  # (keywords, oklch L, C, H)
    (["tech", "ai", "software", "digital", "startup", "innovative", "saas", "cyber"], 0.52, 0.2, 268),
    (["finance", "bank", "trust", "corporate", "consulting", "legal", "insurance", "professional", "reliable"], 0.36, 0.11, 255),
    (["health", "medical", "clinic", "pharma", "care", "dental", "wellness"], 0.58, 0.11, 190),
    (["eco", "green", "nature", "organic", "sustainab", "agri", "farm", "plant", "garden"], 0.5, 0.12, 150),
    (["food", "restaurant", "cafe", "coffee", "bakery", "kitchen", "delivery", "burger", "pizza"], 0.6, 0.18, 40),
    (["luxury", "premium", "jewel", "fashion", "elegant", "gold", "perfume", "boutique"], 0.28, 0.05, 30),
    (["beauty", "cosmetic", "skincare", "salon", "feminine", "spa", "bridal"], 0.62, 0.12, 5),
    (["kids", "toy", "playful", "fun", "school", "children"], 0.72, 0.17, 85),
    (["energy", "sport", "fitness", "gym", "bold", "dynamic", "powerful", "athlet"], 0.6, 0.22, 30),
    (["education", "academy", "learning", "course", "training", "university", "school", "institute"], 0.45, 0.14, 262),
    (["real estate", "property", "architecture", "construction", "develop", "build"], 0.33, 0.06, 60),
    (["travel", "tourism", "sea", "hotel", "beach", "resort", "nile"], 0.55, 0.12, 215),
    (["creative", "agency", "design", "art", "studio", "media", "production"], 0.55, 0.22, 330),
    (["heritage", "traditional", "craft", "cultural", "oriental", "egypt", "pharaon"], 0.45, 0.1, 55),
    (["calm", "minimal", "clean", "simple", "zen"], 0.4, 0.03, 250),
]


def base_from_brief(text: str) -> str:
    t = (text or "").lower()
    best, hits = None, 0
    for keys, L, C, H in MOOD_HUES:
        n = sum(1 for k in keys if k in t)
        if n > hits:
            best, hits = (L, C, H), n
    if not best:  # deterministic from text
        h = sum(ord(ch) for ch in t) % 360
        best = (0.5, 0.15, h)
    return from_oklch(*best)


HARMONIES = {
    "complementary": [180],
    "analogous": [-30, 30],
    "triadic": [120, 240],
    "split": [150, 210],
    "tetradic": [90, 180],
    "monochrome": [],
}


def build_palette(base: str, harmony: str = "auto", mood: str = "") -> dict:
    """A full brand palette from one base colour.

    Returns roles: primary, secondary, accent, ink (text), paper (background), surface, muted, line,
    on_primary/on_accent (best text colour on them), plus 50–900 ramps and WCAG-checked text pairs."""
    base = norm(base)
    L, C, H = to_oklch(base)
    mood = (mood or "").lower()
    if harmony == "auto":
        harmony = ("analogous" if any(k in mood for k in ("calm", "luxury", "elegant", "minimal", "nature", "organic"))
                   else "triadic" if any(k in mood for k in ("playful", "kids", "fun"))
                   else "split" if any(k in mood for k in ("creative", "bold", "energetic", "sport"))
                   else "complementary")
    offs = HARMONIES.get(harmony, [180])
    primary = base
    # secondary: deep, related tone (for large fields / dark backgrounds)
    sec_h = (H + (offs[0] * 0.5 if harmony == "analogous" else -18)) % 360
    secondary = from_oklch(min(0.3, max(0.2, L * 0.55)), max(0.04, C * 0.55), sec_h)
    # accent: the harmony partner, vivid and light enough to pop on dark and on paper
    acc_h = (H + (offs[-1] if offs else 0)) % 360
    acc_C = max(C, 0.14) if harmony != "monochrome" else C * 0.6
    accent = from_oklch(0.72 if harmony != "monochrome" else min(0.85, L + 0.25), acc_C, acc_h)
    if contrast(accent, primary) < 1.6:
        accent = from_oklch(0.8, acc_C, acc_h)
    # neutrals tinted with the primary hue
    ink = from_oklch(0.2, min(0.025, C * 0.25), H)
    paper = from_oklch(0.975, min(0.012, C * 0.1), (H + 40) % 360 if "warm" in mood or "heritage" in mood else H)
    if any(k in mood for k in ("luxury", "heritage", "traditional", "food", "craft", "warm", "cafe", "coffee")):
        paper = from_oklch(0.96, 0.018, 80)  # warm cream
    surface = from_oklch(0.93, min(0.02, C * 0.2), H)
    muted = ensure_contrast(from_oklch(0.5, min(0.03, C * 0.3), H), paper, 4.6)
    line = from_oklch(0.87, min(0.02, C * 0.2), H)
    roles = {"primary": primary, "secondary": secondary, "accent": accent, "ink": ink, "paper": paper,
             "surface": surface, "muted": muted, "line": line}
    roles["on_primary"] = text_on(primary, paper, ink)
    roles["on_secondary"] = text_on(secondary, paper, ink)
    roles["on_accent"] = text_on(accent, paper, ink)
    # primary usable as text on paper?
    roles["primary_text"] = ensure_contrast(primary, paper, 4.5)
    pairs = []
    for fg_n, bg_n in [("ink", "paper"), ("muted", "paper"), ("primary", "paper"), ("paper", "primary"),
                       ("paper", "secondary"), ("accent", "secondary"), ("ink", "accent"), ("paper", "ink"),
                       ("accent", "ink"), ("on_primary", "primary"), ("on_accent", "accent"), ("primary_text", "paper")]:
        r = contrast(roles[fg_n], roles[bg_n])
        pairs.append({"fg": fg_n, "bg": bg_n, "fg_hex": roles[fg_n], "bg_hex": roles[bg_n], "ratio": r,
                      "normal_text": grade(r), "large_text": grade(r, True)})
    return {"harmony": harmony, "roles": roles, "ramps": {"primary": scale(primary), "accent": scale(accent),
                                                          "neutral": scale(from_oklch(0.55, min(0.02, C * 0.2), H))},
            "pairs": pairs}
