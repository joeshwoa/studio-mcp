"""Turns (template, content spec, brand/theme, canvas) into a self-contained design master folder:

    <name>.design/
      index.html      the editable design (HTML/CSS, fonts + assets referenced relatively)
      spec.json       template + content + brand + size → re-render at any size (design_resize)
      fonts/          the exact TTFs used (OFL Google Fonts)
      assets/         copied images/logos
"""
from __future__ import annotations

import html as _html
import json
import re
import shutil
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ...config import brands_dir, slug as _slug
from ...core.result import ToolError
from . import color as col
from . import fonts as F
from .engine import Canvas, studio_js

AR_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
LAT_RE = re.compile(r"[A-Za-z]")


def has_ar(s: str) -> bool:
    return bool(AR_RE.search(s or ""))


def is_ar(s: str) -> bool:
    """Mostly Arabic (more Arabic letters than Latin)."""
    return len(AR_RE.findall(s or "")) >= max(1, len(LAT_RE.findall(s or "")))


# ---- themes --------------------------------------------------------------------------------------

STYLES: dict[str, dict] = {
    "studio": {"primary": "#123F33", "secondary": "#0B2620", "accent": "#FF6B4A", "ink": "#16181A", "paper": "#F4EFE6",
               "pair": "modern", "desc": "deep green, warm cream, coral — confident and warm"},
    "midnight": {"primary": "#0E1E3F", "secondary": "#081226", "accent": "#E8B64C", "ink": "#121521", "paper": "#F6F3EC",
                 "pair": "luxury", "desc": "navy and gold — premium, formal"},
    "electric": {"primary": "#3A2BFF", "secondary": "#120D4D", "accent": "#C8FF3D", "ink": "#0B0B12", "paper": "#F3F3F0",
                 "pair": "creative", "desc": "electric blue and lime — tech, youth, launch"},
    "sunset": {"primary": "#E4572E", "secondary": "#2A1B33", "accent": "#FFC857", "ink": "#1D1420", "paper": "#FFF6EC",
               "pair": "bold", "desc": "orange, plum and sun yellow — energetic, food, events"},
    "mint": {"primary": "#0F766E", "secondary": "#083B37", "accent": "#FDBA4D", "ink": "#10201F", "paper": "#F1F7F5",
             "pair": "friendly", "desc": "teal and warm amber — health, education, friendly"},
    "mono": {"primary": "#111111", "secondary": "#000000", "accent": "#FF3B30", "ink": "#111111", "paper": "#F5F5F2",
             "pair": "editorial", "desc": "black/white with a red signal — editorial, fashion"},
    "nile": {"primary": "#0C4A6E", "secondary": "#062A40", "accent": "#D9A441", "ink": "#0F1B24", "paper": "#F6F0E4",
             "pair": "egypt-classic", "desc": "Nile blue and desert gold — Egyptian, heritage-modern"},
    "rose": {"primary": "#7A1F3D", "secondary": "#3D0F1F", "accent": "#F4A7B9", "ink": "#23121A", "paper": "#FBF5F2",
             "pair": "luxury", "desc": "wine and blush — beauty, fashion, wedding"},
}


def load_brand(brand) -> dict | None:
    """Brand kit dict from a slug/name, a folder, a brand.json path, or an inline dict. Relative file
    paths inside the kit are resolved against the kit folder."""
    if not brand:
        return None
    if isinstance(brand, dict):
        return brand
    p = Path(str(brand)).expanduser()
    cands = [p / "brand.json", p] if p.suffix != ".json" else [p]
    cands += [brands_dir() / _slug(str(brand)) / "brand.json"]
    for c in cands:
        if c.is_file():
            kit = json.loads(c.read_text(encoding="utf-8"))
            kit["_dir"] = str(c.parent)
            return kit
    known = sorted(d.name for d in brands_dir().iterdir() if (d / "brand.json").exists())
    raise ToolError(f"brand {brand!r} not found", f"saved brands: {', '.join(known) or 'none yet — create one with brand_create'}")


def brand_file(kit: dict, key: str) -> Path | None:
    files = (kit.get("logo") or {}).get("files") or {}
    v = files.get(key)
    if not v:
        return None
    p = Path(v)
    if not p.is_absolute():
        p = Path(kit.get("_dir", ".")) / p
    return p if p.exists() else None


@dataclass
class Theme:
    colors: dict
    head: str
    body: str
    head_ar: str
    body_ar: str
    head_weight: int = 700
    script: str = ""
    kit: dict | None = None
    name: str = ""

    def vars(self, mode: str) -> dict:
        c = self.colors
        paper, ink, primary, accent, secondary = c["paper"], c["ink"], c["primary"], c["accent"], c["secondary"]
        if mode == "dark":
            bg, fg = secondary, paper
        elif mode == "brand":
            bg, fg = primary, col.text_on(primary, paper, ink)
        elif mode == "accent":
            bg, fg = accent, col.text_on(accent, paper, ink)
        elif mode == "ink":
            bg, fg = ink, paper
        else:
            bg, fg = paper, ink
        dark_bg = col.luminance(bg) < 0.2
        muted = col.ensure_contrast(col.mix(fg, bg, 0.3), bg, 4.6)
        # emphasis colour: accent → primary → fg, whichever reads on this background
        hl = next((x for x in ([accent, primary] if dark_bg else [primary, accent]) if col.contrast(x, bg) >= 3.0 and x != bg), None)
        hl = hl or (col.ensure_contrast(accent, bg, 3.2) if col.contrast(accent, bg) > 1.3 else fg)
        cta_bg = next((x for x in ([accent, fg] if mode != "accent" else [ink, primary]) if col.contrast(x, bg) >= 1.8), fg)
        cta_fg = col.text_on(cta_bg, paper, ink)
        surface = col.mix(bg, fg, 0.07)
        deco = accent if col.contrast(accent, bg) >= 1.4 else (primary if col.contrast(primary, bg) >= 1.4 else fg)
        mark_bg = accent if col.contrast(accent, bg) >= 1.3 else primary
        hl_text = hl if col.contrast(hl, bg) >= 4.6 else col.ensure_contrast(hl, bg, 4.6)
        return {"bg": bg, "ink": fg, "muted": muted, "hl": hl, "hl-text": hl_text, "cta-bg": cta_bg, "cta-fg": cta_fg, "surface": surface,
                "line": col.mix(bg, fg, 0.18), "deco": deco, "primary": primary, "accent": accent,
                "secondary": secondary, "paper": paper, "ink0": ink, "mark-bg": mark_bg,
                "mark-fg": col.text_on(mark_bg, paper, ink), "on-primary": col.text_on(primary, paper, ink),
                "on-accent": col.text_on(accent, paper, ink), "dark": "1" if dark_bg else "0"}


def make_theme(brand=None, style: str = "", colors: dict | None = None, fonts: dict | None = None) -> Theme:
    kit = load_brand(brand) if brand else None
    if kit:
        k = kit.get("colors", {})
        base = {r: k.get(r) for r in ("primary", "secondary", "accent", "ink", "paper")}
        kf = kit.get("fonts", {})
        th = Theme(colors=base, head=kf.get("head", "Inter"), body=kf.get("body", "Inter"),
                   head_ar=kf.get("head_ar", "Cairo"), body_ar=kf.get("body_ar", "Cairo"),
                   head_weight=int(kf.get("head_weight", 700)), kit=kit, name=kit.get("name", ""))
    else:
        st = STYLES.get((style or "studio").lower())
        if not st:
            raise ToolError(f"unknown style {style!r}", "styles: " + ", ".join(STYLES))
        pair = next(p for p in F.PAIRS if p["id"] == st["pair"])
        th = Theme(colors={r: st[r] for r in ("primary", "secondary", "accent", "ink", "paper")},
                   head=pair["head"], body=pair["body"], head_ar=pair["head_ar"], body_ar=pair["body_ar"],
                   head_weight=pair.get("head_weight", 700), name=style or "studio")
    for kk, v in (colors or {}).items():
        if kk in th.colors and v:
            th.colors[kk] = col.norm(v)
    for kk, v in (fonts or {}).items():
        if hasattr(th, kk) and v:
            setattr(th, kk, v if kk != "head_weight" else int(v))
    for r in ("primary", "secondary", "accent", "ink", "paper"):
        th.colors[r] = col.norm(th.colors.get(r) or STYLES["studio"][r])
    return th


# ---- context used by templates -------------------------------------------------------------------

def _inline(text: str) -> str:
    s = _html.escape(str(text))
    s = re.sub(r"==(.+?)==", r'<span class="mark">\1</span>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r'<em class="hl">\1</em>', s)
    return s.replace("\n", "<br>")


def plain(text: str) -> str:
    return re.sub(r"==|\*", "", str(text or ""))


@dataclass
class Ctx:
    canvas: Canvas
    spec: dict
    theme: Theme
    master: Path
    mode: str = "light"
    warnings: list = field(default_factory=list)
    chrome: dict | None = None   # carousel: {"index": i, "total": n}
    page_mode: str | None = None

    def __post_init__(self):
        c = self.canvas
        self.w, self.h = c.w, c.h
        a = c.w / c.h
        self.u0 = min(c.w, c.h) / 100
        # type unit: landscape canvases get proportionally larger type (short side alone is too small)
        self.u = self.u0 * (min(1.3, a ** 0.5) if a > 1.15 else 1.0)
        self.aspect = a
        self.orient = ("wide" if a >= 2.2 else "landscape" if a > 1.15 else "square" if a >= 0.87 else
                       "portrait" if a >= 0.62 else "tall")
        lang = (self.spec.get("lang") or "auto").lower()
        if lang == "auto":
            probe = next((str(self.spec[k]) for k in ("headline", "title", "quote", "name", "recipient") if self.spec.get(k)), "") or \
                " ".join(str(self.spec.get(k, "")) for k in ("body", "subhead"))
            lang = "ar" if has_ar(probe) and is_ar(probe) else "en"
        self.lang = lang
        self.rtl = lang == "ar"
        self.start, self.end = ("right", "left") if self.rtl else ("left", "right")
        b = c.bleed_px
        base = self.u0 * (6.5 if not c.print_ else 7.5) * (0.8 if self.orient == "wide" else 1)
        s = c.safe or {}
        self.pad = {k: b + max(s.get(k, 0), base) for k in ("top", "right", "bottom", "left")}
        # keep content clear of platform UI overlays (avatars, timestamps): grow the padding on the side
        # that costs the least area
        for a in c.avoid:
            m = self.u0 * 3
            W, H = c.w, c.h
            opts = []
            if a["x"] < W * 0.4:
                opts.append(("left", a["x"] + a["w"] + m, (a["x"] + a["w"] + m) * H))
            if a["x"] + a["w"] > W * 0.6:
                opts.append(("right", W - a["x"] + m, (W - a["x"] + m) * H))
            if a["y"] + a["h"] > H * 0.6:
                opts.append(("bottom", H - a["y"] + m, (H - a["y"] + m) * W))
            if a["y"] < H * 0.4:
                opts.append(("top", a["y"] + a["h"] + m, (a["y"] + a["h"] + m) * W))
            if opts:
                side, val, _ = min(opts, key=lambda o: o[2])
                self.pad[side] = max(self.pad[side], b + val)
        (self.master / "assets").mkdir(parents=True, exist_ok=True)

    # typography scale in px, relative to the canvas
    def fs(self, n: float) -> float:
        k = {"wide": 1.05, "landscape": 1.0, "square": 1.0, "portrait": 1.0, "tall": 1.0}[self.orient]
        return round(n * self.u * k, 2)

    def T(self, text, cls: str = "", tag: str = "div", fit: tuple | None = None, attrs: str = "") -> str:
        if text is None or str(text).strip() == "":
            return ""
        t = str(text)
        ar = has_ar(t) and is_ar(t)
        classes = ("t " + cls + (" ar" if ar else " lat")).strip()
        a = f' dir="{"rtl" if ar else "ltr"}" lang="{"ar" if ar else "en"}"'
        if fit:
            mx, mn = fit
            a += f' data-fit data-max="{mx}" data-min="{mn}" style="font-size:{mx}px"'
        return f'<{tag} class="{classes}"{a} {attrs}>{_inline(t)}</{tag}>'

    def get(self, *keys, default=""):
        for k in keys:
            v = self.spec.get(k)
            if v not in (None, "", []):
                return v
        return default

    def img(self, src, name: str = "") -> str | None:
        """Copy/download an image into the master's assets → relative path (or None + warning)."""
        if not src:
            return None
        src = str(src)
        dest_dir = self.master / "assets"
        try:
            if src.startswith(("http://", "https://")):
                ext = Path(src.split("?")[0]).suffix[:5] or ".jpg"
                d = dest_dir / f"{name or 'img'}-{abs(hash(src)) % 10**8}{ext}"
                if not d.exists():
                    req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0 studio-mcp"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        d.write_bytes(r.read())
            else:
                p = Path(src).expanduser()
                if not p.exists():
                    self.warnings.append(f"image not found: {src}")
                    return None
                d = dest_dir / p.name
                if not d.exists():
                    shutil.copy2(p, d)
            return f"assets/{d.name}"
        except Exception as e:
            self.warnings.append(f"could not load image {src}: {e}")
            return None

    def logo_src(self, on_dark: bool, kind: str = "horizontal") -> str | None:
        """Logo file for this background: spec.logo(_light/_dark) or the brand kit's variant."""
        s = self.spec
        explicit = s.get("logo_dark" if on_dark else "logo_light") or s.get("logo")
        if explicit:
            return self.img(explicit, "logo")
        kit = self.theme.kit
        if kit:
            order = ([f"{kind}_reversed", f"{kind}", "horizontal_reversed", "horizontal"] if on_dark else
                     [kind, "horizontal", "primary"])
            for k in order:
                p = brand_file(kit, k)
                if p:
                    return self.img(str(p), "logo")
        return None

    def logo(self, on_dark: bool, cls: str = "logo", kind: str = "horizontal") -> str:
        src = self.logo_src(on_dark, kind)
        if src:
            return f'<img class="{cls}" src="{src}" alt="logo">'
        name = self.spec.get("brand_name") or (self.theme.kit or {}).get("name") or ""
        if self.rtl and (self.theme.kit or {}).get("name_ar"):
            name = self.theme.kit["name_ar"]
        return self.T(name, cls + " logo-text") if name else ""


# ---- document ------------------------------------------------------------------------------------

BASE_CSS = r"""
*{box-sizing:border-box;margin:0;padding:0}
html,body{margin:0;padding:0;background:#fff}
body{width:var(--TW)}
.page{position:relative;width:var(--TW);height:var(--TH);overflow:hidden;background:var(--bg);color:var(--ink);
  font-family:'sf-body',sans-serif;font-size:calc(var(--u)*3);line-height:1.4;
  -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;font-kerning:normal;
  font-feature-settings:"kern" 1,"liga" 1,"calt" 1;--ta:var(--start)}
.t{text-align:var(--ta);overflow-wrap:normal;word-break:normal;hyphens:manual}
.head{font-family:'sf-head',sans-serif;font-weight:var(--hw);line-height:1.02;letter-spacing:-0.02em;text-wrap:balance}
.body{font-family:'sf-body',sans-serif;line-height:1.45;text-wrap:pretty}
.ar{letter-spacing:0!important;text-transform:none!important}
.head.ar{line-height:1.3}
.body.ar{line-height:1.7}
.hl{font-style:normal;color:var(--hl)}
.mark{background:var(--mark-bg);color:var(--mark-fg);padding:0 .14em;box-decoration-break:clone;-webkit-box-decoration-break:clone;border-radius:.06em}
em{font-style:normal}
strong{font-weight:800}
img{display:block}
.fill{position:absolute;inset:0}
.frame{position:absolute;top:var(--pt);right:var(--pr);bottom:var(--pb);left:var(--pl);display:flex;flex-direction:column}
.grow{flex:1 1 auto;min-height:0}
.fitbox{min-height:0;overflow:hidden}
.row{display:flex;align-items:center}
.eyebrow{font-family:'sf-body',sans-serif;font-weight:700;letter-spacing:.14em;text-transform:uppercase}
.logo{height:calc(var(--u)*6.6);width:auto;max-width:calc(var(--u)*40);object-fit:contain;object-position:var(--start) center}
.logo-text{font-family:'sf-head',sans-serif;font-weight:var(--hw);font-size:calc(var(--u)*3.4);letter-spacing:-.01em;line-height:1.1}
.cta{display:inline-flex;align-items:center;gap:.5em;background:var(--cta-bg);color:var(--cta-fg);font-family:'sf-body',sans-serif;font-weight:700;
  border-radius:999px;padding:.72em 1.35em;line-height:1.1;white-space:nowrap}
.cta .t{text-align:center}
.cta svg{width:1em;height:1em;flex:none}
[dir=rtl] .cta svg.arrow{transform:scaleX(-1)}
.pill{display:inline-block;border:1.5px solid currentColor;border-radius:999px;padding:.38em .9em;line-height:1.15;font-weight:700}
.muted{color:var(--muted)}
.chrome{display:flex;justify-content:space-between;align-items:center;font-family:'sf-body',sans-serif;font-weight:600;color:var(--muted);font-size:calc(var(--u)*2.5)}
.dots{display:flex;gap:calc(var(--u)*.9);align-items:center}
.dots i{display:block;width:calc(var(--u)*1.1);height:calc(var(--u)*1.1);border-radius:50%;background:var(--line)}
.dots i.on{background:var(--ink);width:calc(var(--u)*3.4);border-radius:99px}
.grain{position:absolute;inset:0;pointer-events:none;opacity:.07;mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")}
"""

ICONS = {
    "arrow": '<svg class="arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    "calendar": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4.5" width="18" height="16" rx="3"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/></svg>',
    "clock": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    "pin": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 21s-7-6.3-7-11.5a7 7 0 0 1 14 0C19 14.7 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>',
    "phone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M5 3.5h3.5l2 5-2.5 1.5a11 11 0 0 0 6 6l1.5-2.5 5 2V19a2 2 0 0 1-2 2A17 17 0 0 1 3 5.5a2 2 0 0 1 2-2z"/></svg>',
    "mail": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 6.5 12 13l8.5-6.5"/></svg>',
    "web": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.8 3 2.8 15 0 18M12 3c-2.8 3-2.8 15 0 18"/></svg>',
    "check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    "star": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.8l2.8 6 6.5.7-4.9 4.4 1.4 6.4L12 17l-5.8 3.3 1.4-6.4L2.7 9.5l6.5-.7z"/></svg>',
    "user": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
    "swipe": '<svg class="arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15M14 6l6 6-6 6"/></svg>',
}


def icon(name: str) -> str:
    return ICONS.get(name, "")


def fonts_css(theme: Theme, master: Path, head_weights=(400, 500, 600, 700, 800, 900), body_weights=(400, 500, 700),
              script: str = "") -> tuple[str, list[str]]:
    """@font-face CSS referencing fonts/ inside the master (files copied there). Returns (css, checks)."""
    fdir = master / "fonts"
    used: list[Path] = []

    def url_for(p: Path) -> str:
        used.append(p)
        return "fonts/" + p.name

    hw = sorted({theme.head_weight, *[w for w in head_weights if w >= 400]})
    css = [F.role_css("sf-head", theme.head, theme.head_ar, list(hw), url_for),
           F.role_css("sf-body", theme.body, theme.body_ar, list(body_weights), url_for)]
    if script:
        css.append(F.role_css("sf-script", script, "Aref Ruqaa", [400], url_for))
    F.copy_into(used, fdir)
    checks = [f"{theme.head_weight} 20px sf-head", "400 20px sf-body"]
    return "\n".join(css), checks


def document(pages: list[str], css: str, ctx: Ctx, title: str = "design", script_font: str = "") -> tuple[str, list[str]]:
    c = ctx.canvas
    v = ctx.theme.vars(ctx.mode)
    fcss, checks = fonts_css(ctx.theme, ctx.master, script=script_font)
    root = [f"--TW:{c.tw:.2f}px", f"--TH:{c.th:.2f}px", f"--W:{c.w:.2f}px", f"--H:{c.h:.2f}px", f"--u:{ctx.u:.4f}px",
            f"--bleed:{c.bleed_px:.2f}px", f"--pt:{ctx.pad['top']:.1f}px", f"--pr:{ctx.pad['right']:.1f}px",
            f"--pb:{ctx.pad['bottom']:.1f}px", f"--pl:{ctx.pad['left']:.1f}px", f"--start:{ctx.start}", f"--end:{ctx.end}",
            f"--hw:{ctx.theme.head_weight}"]
    root += [f"--{k}:{val}" for k, val in v.items()]
    dir_ = "rtl" if ctx.rtl else "ltr"
    doc = f"""<!doctype html>
<html lang="{ctx.lang}" dir="{dir_}">
<head>
<meta charset="utf-8">
<title>{_html.escape(title)}</title>
<!-- studio-mcp design master: edit freely; fonts/ and assets/ are relative. Re-export with design_render_html. -->
<style>
{fcss}
:root{{{';'.join(root)}}}
{BASE_CSS}
{css}
</style>
</head>
<body>
{chr(10).join(pages)}
<script>
{studio_js()}
</script>
</body>
</html>"""
    return doc, checks


def page(inner: str, ctx: Ctx, cls: str = "", mode: str | None = None, style: str = "") -> str:
    """One .page. Its colour variables are written inline for its own mode, so pages with different
    modes (carousel slides, business-card front/back) can live in one document."""
    m = mode or ctx.mode
    v = ctx.theme.vars(m)
    st = ";".join(f"--{k}:{val}" for k, val in v.items()) + (";" + style if style else "")
    dir_ = "rtl" if ctx.rtl else "ltr"
    return f'<section class="page {cls}" dir="{dir_}" data-mode="{m}" style="{st}">{inner}</section>'
