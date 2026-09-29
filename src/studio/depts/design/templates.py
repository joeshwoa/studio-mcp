"""Professional template library. Each template is a function Ctx → (pages: list[str], css: str).

Layouts are built on the canvas unit --u (1% of the short side) so one design reflows cleanly to
every size preset. Text sits in [data-fit-box] regions and is auto-fitted in the browser (all
text in a region scales together, so hierarchy is kept). *word* = highlight colour,
==word== = marker box, \\n = line break. Arabic flips the layout (RTL) automatically."""
from __future__ import annotations

import html as _html
import math

from . import color as col
from .builder import Ctx, has_ar, icon, is_ar, page, plain

TEMPLATES: dict[str, dict] = {}


def template(name: str, desc: str, fields: str, sizes: str, mode: str = "light", kind: str = "social"):
    def wrap(fn):
        TEMPLATES[name] = {"fn": fn, "desc": desc, "fields": fields, "sizes": sizes, "mode": mode, "kind": kind}
        return fn
    return wrap


def _cta(c: Ctx, text: str, size: float, arrow: bool = True) -> str:
    if not text:
        return ""
    return f'<div class="cta" style="font-size:{size}px">{c.T(text, "")}{icon("arrow") if arrow else ""}</div>'


def _chrome(c: Ctx, dark: bool) -> str:
    """Carousel footer: page counter + progress dots + swipe hint (only inside a carousel)."""
    ch = c.chrome
    if not ch:
        return ""
    i, n = ch["index"], ch["total"]
    dots = "".join(f'<i class="{"on" if k == i else ""}"></i>' for k in range(n))
    swipe = "" if i == n - 1 else f'<span class="swipe">{c.T(ch.get("swipe") or ("اسحب" if c.rtl else "Swipe"), "")}{icon("swipe")}</span>'
    return f'<div class="chrome"><span class="pnum">{i + 1:02d} / {n:02d}</span><div class="dots">{dots}</div>{swipe or "<span></span>"}</div>'


def _handle(c: Ctx) -> str:
    return c.get("handle", "url", "website")


CHROME_CSS = """
.chrome .swipe{display:inline-flex;align-items:center;gap:.45em}
.chrome .swipe svg{width:1.2em;height:1.2em}
.chrome .pnum{font-variant-numeric:tabular-nums;letter-spacing:.04em}
"""


# ============================================================================= social: headline
@template("headline", "Bold statement / announcement: eyebrow, big headline, subhead, CTA, logo. The workhorse.",
          "eyebrow, headline*, subhead, cta, handle|url, logo", "any social size, banner, story", mode="brand")
def t_headline(c: Ctx):
    o = c.orient
    dark = c.theme.vars(c.mode)["dark"] == "1"
    H = {"wide": 13, "landscape": 10.5, "square": 12, "portrait": 12.5, "tall": 13}[o]
    eyebrow, head, sub = c.get("eyebrow", "kicker", "tag"), c.get("headline", "title"), c.get("subhead", "body", "text")
    top_end = c.get("date", "category")
    foot = _handle(c)
    logo = c.logo(dark)
    body = f"""
<div class="deco" data-decor><i class="d1"></i><i class="d2"></i><i class="dots-grid"></i></div>
<div class="frame">
  <div class="top row">{logo}<div class="grow"></div>{c.T(top_end, "muted meta")}</div>
  <div class="main grow fitbox" data-fit-box="headline">
    <div class="eb">{('<i class="rule"></i>' if eyebrow else '')}{c.T(eyebrow, "eyebrow", fit=(c.fs(2.7), c.fs(1.9)))}</div>
    {c.T(head, "head h1", fit=(c.fs(H), c.fs(4.2)))}
    {c.T(sub, "body sub", fit=(c.fs(3.5), c.fs(2.3)))}
  </div>
  <div class="bottom row">{_cta(c, c.get("cta"), c.fs(3.1))}<div class="grow"></div>{c.T(foot if c.get("cta") else "", "muted meta")}</div>
  {_chrome(c, dark)}
</div>"""
    if not c.get("cta") and foot:
        body = body.replace('<div class="bottom row"><div class="grow"></div>', f'<div class="bottom row">{c.T(foot, "meta muted")}<div class="grow"></div>')
    css = """
.tpl-headline .deco .d1{position:absolute;width:calc(var(--u)*76);height:calc(var(--u)*76);border-radius:50%;
  inset-inline-end:calc(var(--u)*-22);top:calc(var(--u)*-34);border:calc(var(--u)*2.2) solid var(--ink);opacity:.1}
.tpl-headline .deco .d2{position:absolute;width:calc(var(--u)*7);height:calc(var(--u)*7);border-radius:50%;
  inset-inline-end:calc(var(--u)*48.5);top:calc(var(--u)*13.5);background:var(--deco)}
.tpl-headline .deco .dots-grid{display:none}
.tpl-headline .main{display:flex;flex-direction:column;justify-content:flex-end;gap:calc(var(--u)*2.6);padding-bottom:calc(var(--u)*5)}
.tpl-headline .eb{display:flex;align-items:center;gap:calc(var(--u)*1.6);color:var(--hl-text)}
.tpl-headline .eb .rule{display:block;width:calc(var(--u)*6);height:calc(var(--u)*.55);background:currentColor;border-radius:2px}
.tpl-headline .h1{max-width:94%}
.tpl-headline .sub{max-width:78%;color:var(--muted)}
.tpl-headline .meta{font-size:calc(var(--u)*2.6);font-weight:600}
.tpl-headline .top{min-height:calc(var(--u)*6)}
.tpl-headline .bottom{min-height:calc(var(--u)*7)}
.tpl-headline .chrome{margin-top:calc(var(--u)*3.2)}
.o-tall .tpl-headline .main,.tpl-headline.o-tall .main{justify-content:center}
.tpl-headline.o-wide .main{justify-content:center;padding-bottom:0;gap:calc(var(--u)*2.2)}
.tpl-headline.o-wide .h1{max-width:66%}
.tpl-headline.o-wide .sub{max-width:60%}
.tpl-headline.o-wide .deco .d1{width:calc(var(--u)*150);height:calc(var(--u)*150);top:calc(var(--u)*-40);inset-inline-end:calc(var(--u)*-50)}
.tpl-headline.o-wide .deco .d2{top:calc(var(--u)*70);inset-inline-end:calc(var(--u)*80)}
.tpl-headline.o-landscape .h1{max-width:80%}
"""
    return [page(body, c, f"tpl-headline o-{o}")], css + CHROME_CSS


# ============================================================================= social: photo
@template("photo", "Full-bleed photo with a legible scrim and headline — lifestyle, product, news, real estate.",
          "image*, eyebrow|tag, headline*, subhead, cta, handle, text_position=bottom|top|center, image_focus", "any social size", mode="dark")
def t_photo(c: Ctx):
    o = c.orient
    src = c.img(c.get("image", "photo"), "photo")
    pos = (c.get("text_position") or "bottom").lower()
    focus = c.get("image_focus") or "center"
    bg = (f'<img class="fill ph" src="{src}" style="object-fit:cover;width:100%;height:100%;object-position:{focus}">' if src else
          '<div class="fill ph placeholder"></div>')
    if not src:
        c.warnings.append("photo template without an image — used an abstract brand gradient placeholder")
    H = {"wide": 10, "landscape": 9.5, "square": 10.5, "portrait": 11, "tall": 11.5}[o]
    tag = c.get("eyebrow", "tag", "kicker")
    body = f"""
{bg}
<div class="fill scrim scrim-{pos}"></div>
<div class="frame">
  <div class="top row">{c.logo(True)}<div class="grow"></div>{c.T(c.get("date", "category"), "meta")}</div>
  <div class="main grow fitbox pos-{pos}" data-fit-box="headline">
    {f'<div class="tag">{c.T(tag, "eyebrow", fit=(c.fs(2.5), c.fs(1.8)))}</div>' if tag else ''}
    {c.T(c.get("headline", "title"), "head h1", fit=(c.fs(H), c.fs(4)))}
    {c.T(c.get("subhead", "body"), "body sub", fit=(c.fs(3.3), c.fs(2.2)))}
  </div>
  <div class="bottom row">{_cta(c, c.get("cta"), c.fs(3))}<div class="grow"></div>{c.T(_handle(c), "meta")}</div>
  {_chrome(c, True)}
</div>"""
    css = """
.tpl-photo{--ink:#fff;--muted:rgba(255,255,255,.84);--line:rgba(255,255,255,.35);color:#fff}
.tpl-photo .placeholder{background:radial-gradient(120% 90% at 80% 10%,var(--accent) 0%,transparent 55%),linear-gradient(160deg,var(--primary),var(--secondary))}
.tpl-photo .scrim-bottom{background:linear-gradient(to top,rgba(0,0,0,.86) 0%,rgba(0,0,0,.55) 38%,rgba(0,0,0,0) 68%),linear-gradient(to bottom,rgba(0,0,0,.45) 0%,rgba(0,0,0,0) 22%)}
.tpl-photo .scrim-top{background:linear-gradient(to bottom,rgba(0,0,0,.86) 0%,rgba(0,0,0,.5) 40%,rgba(0,0,0,0) 70%),linear-gradient(to top,rgba(0,0,0,.5) 0%,rgba(0,0,0,0) 22%)}
.tpl-photo .scrim-center{background:rgba(0,0,0,.5)}
.tpl-photo .main{display:flex;flex-direction:column;gap:calc(var(--u)*2.2);padding:calc(var(--u)*3) 0}
.tpl-photo .main.pos-bottom{justify-content:flex-end}
.tpl-photo .main.pos-top{justify-content:flex-start}
.tpl-photo .main.pos-center{justify-content:center;--ta:center;align-items:center}
.tpl-photo .h1{max-width:92%;text-shadow:0 2px 24px rgba(0,0,0,.25)}
.tpl-photo .sub{max-width:80%;color:var(--muted)}
.tpl-photo .tag{align-self:flex-start;background:var(--accent);color:var(--on-accent);padding:.55em 1em;border-radius:999px;line-height:1}
.tpl-photo .main.pos-center .tag{align-self:center}
.tpl-photo .tag .t{font-size:inherit}
.tpl-photo .meta{font-size:calc(var(--u)*2.6);font-weight:600;color:var(--muted)}
.tpl-photo .top,.tpl-photo .bottom{min-height:calc(var(--u)*6)}
.tpl-photo .chrome{margin-top:calc(var(--u)*3)}
.tpl-photo.o-wide .h1{max-width:60%}
"""
    return [page(body, c, f"tpl-photo o-{o}")], css + CHROME_CSS


# ============================================================================= social: split
@template("split", "Image on one side, message on the other — product launch, course, service, hiring.",
          "image*, eyebrow, headline*, body, bullets[], cta, handle, image_side=start|end", "any social size, banners, flyers", mode="light")
def t_split(c: Ctx):
    o = c.orient
    src = c.img(c.get("image", "photo"), "photo")
    side = (c.get("image_side") or "end").lower()
    horiz = o in ("landscape", "wide") or (o == "square" and c.get("layout") == "horizontal")
    img = (f'<img src="{src}" style="width:100%;height:100%;object-fit:cover;object-position:{c.get("image_focus") or "center"}">' if src
           else '<div class="placeholder"></div>')
    if not src:
        c.warnings.append("split template without an image — used an abstract brand panel")
    bullets = c.get("bullets", "points", default=[])
    bl = "".join(f'<li><span class="ck">{icon("check")}</span>{c.T(b, "body", fit=(c.fs(3.1), c.fs(2)))}</li>' for b in bullets[:5])
    H = 10.5 if horiz else 9.2
    text = f"""
<div class="txt">
  <div class="top row">{c.logo(False)}</div>
  <div class="main grow fitbox" data-fit-box="copy">
    {c.T(c.get("eyebrow", "kicker", "tag"), "eyebrow hl-c", fit=(c.fs(2.5), c.fs(1.8)))}
    {c.T(c.get("headline", "title"), "head h1", fit=(c.fs(H), c.fs(3.6)))}
    {c.T(c.get("body", "subhead"), "body sub", fit=(c.fs(3.2), c.fs(2.1)))}
    {f'<ul class="bl">{bl}</ul>' if bl else ''}
  </div>
  <div class="bottom row">{_cta(c, c.get("cta"), c.fs(2.9))}<div class="grow"></div>{c.T(_handle(c), "meta muted")}</div>
  {_chrome(c, False)}
</div>"""
    body = f'<div class="wrap {"h" if horiz else "v"} img-{side}"><div class="pic">{img}<i class="blob" data-decor></i></div>{text}</div>'
    css = """
.tpl-split .wrap{position:absolute;inset:0;display:flex}
.tpl-split .wrap.h{flex-direction:row}
.tpl-split .wrap.h.img-start{flex-direction:row-reverse}
.tpl-split .wrap.v{flex-direction:column-reverse}
.tpl-split .wrap.v.img-start{flex-direction:column}
.tpl-split .pic{position:relative;overflow:hidden;background:var(--surface)}
.tpl-split .wrap.h .pic{width:48%;height:100%}
.tpl-split .wrap.v .pic{height:46%;width:100%}
.tpl-split .placeholder{position:absolute;inset:0;background:radial-gradient(90% 80% at 30% 30%,var(--accent),transparent 60%),linear-gradient(135deg,var(--primary),var(--secondary))}
.tpl-split .blob{position:absolute;width:calc(var(--u)*12);height:calc(var(--u)*12);background:var(--accent);border-radius:50%;
  inset-inline-end:calc(var(--u)*-6);top:calc(50% - var(--u)*6)}
.tpl-split .wrap.h.img-start .blob{inset-inline-end:auto;inset-inline-start:calc(var(--u)*-6)}
.tpl-split .wrap.v .blob{inset-inline-start:auto;inset-inline-end:calc(var(--u)*8);bottom:calc(var(--u)*-7)}
.tpl-split .wrap.v.img-start .blob{bottom:auto;top:auto;bottom:calc(var(--u)*-7)}
.tpl-split .txt{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;position:relative}
.tpl-split .wrap.h .txt{padding:var(--pt) calc(var(--u)*5) var(--pb) calc(var(--u)*5)}
.tpl-split .wrap.h.img-end .txt{padding-inline-start:var(--pl)}
.tpl-split .wrap.h.img-start .txt{padding-inline-end:var(--pr)}
.tpl-split .wrap.v .txt{padding:calc(var(--u)*6) var(--pr) var(--pb) var(--pl)}
.tpl-split .wrap.v.img-end .txt{padding-top:var(--pt)}
.tpl-split .main{display:flex;flex-direction:column;justify-content:center;gap:calc(var(--u)*2.2);padding:calc(var(--u)*2) 0}
.tpl-split .hl-c{color:var(--hl-text)}
.tpl-split .sub{color:var(--muted)}
.tpl-split .bl{list-style:none;display:flex;flex-direction:column;gap:calc(var(--u)*1.3);margin-top:calc(var(--u)*.6)}
.tpl-split .bl li{display:flex;align-items:flex-start;gap:calc(var(--u)*1.5)}
.tpl-split .ck{flex:none;width:calc(var(--u)*3.6);height:calc(var(--u)*3.6);border-radius:50%;background:var(--accent);color:var(--on-accent);display:grid;place-items:center;margin-top:.1em}
.tpl-split .ck svg{width:62%;height:62%}
.tpl-split .meta{font-size:calc(var(--u)*2.5);font-weight:600}
.tpl-split .top{min-height:calc(var(--u)*5.5)}
.tpl-split .chrome{margin-top:calc(var(--u)*2.4)}
"""
    return [page(body, c, f"tpl-split o-{o}")], css + CHROME_CSS


# ============================================================================= social: quote
@template("quote", "Testimonial / quote card with big quote mark, author, photo and optional stars.",
          "quote*, author, role, avatar(image), rating(1-5), handle", "any social size", mode="light")
def t_quote(c: Ctx):
    o = c.orient
    q = c.get("quote", "headline", "text")
    av = c.img(c.get("avatar", "image", "photo"), "avatar")
    rating = int(c.get("rating", default=0) or 0)
    stars = f'<div class="stars">{"".join(icon("star") for _ in range(max(0, min(5, rating))))}</div>' if rating else ""
    qmark = "”" if not c.rtl else "”"
    L = len(plain(q))
    Q = 7.4 if L < 90 else 6.2 if L < 160 else 5.2
    who = f"""<div class="who row">{f'<img class="av" src="{av}">' if av else ''}
      <div class="nm">{c.T(c.get("author", "name"), "body name")}{c.T(c.get("role", "title_role", "company"), "body role muted")}</div></div>"""
    body = f"""
<div class="bgq" data-decor>{qmark}</div>
<div class="frame">
  <div class="top row">{c.logo(c.theme.vars(c.mode)['dark'] == '1')}<div class="grow"></div>{stars}</div>
  <div class="main grow fitbox" data-fit-box="quote">
    <div class="qm" data-decor>“</div>
    {c.T(q, "head qt", fit=(c.fs(Q), c.fs(3)))}
  </div>
  <div class="bottom">{who}<div class="grow"></div>{c.T(_handle(c), "meta muted")}</div>
  {_chrome(c, False)}
</div>"""
    css = """
.tpl-quote .bgq{position:absolute;font-family:Georgia,'Times New Roman',serif;font-size:calc(var(--u)*120);line-height:1;color:var(--deco);opacity:.08;
  inset-inline-end:calc(var(--u)*-4);bottom:calc(var(--u)*-62);pointer-events:none}
.tpl-quote .main{display:flex;flex-direction:column;justify-content:center;gap:calc(var(--u)*1)}
.tpl-quote .qm{font-family:Georgia,'Times New Roman',serif;font-weight:700;font-size:calc(var(--u)*22);line-height:.62;height:calc(var(--u)*11);color:var(--hl)}
.tpl-quote .qt{font-weight:600;line-height:1.16;letter-spacing:-.015em}
.tpl-quote .qt.ar{line-height:1.5}
.tpl-quote .bottom{display:flex;align-items:center;gap:calc(var(--u)*2)}
.tpl-quote .who{gap:calc(var(--u)*2.2)}
.tpl-quote .av{width:calc(var(--u)*10);height:calc(var(--u)*10);border-radius:50%;object-fit:cover;border:calc(var(--u)*.5) solid var(--accent)}
.tpl-quote .name{font-weight:700;font-size:calc(var(--u)*3.2);line-height:1.2}
.tpl-quote .role{font-size:calc(var(--u)*2.6);line-height:1.3}
.tpl-quote .nm{display:flex;flex-direction:column;gap:calc(var(--u)*.3)}
.tpl-quote .stars{display:flex;gap:calc(var(--u)*.6);color:var(--accent)}
.tpl-quote .stars svg{width:calc(var(--u)*3.6);height:calc(var(--u)*3.6)}
.tpl-quote .meta{font-size:calc(var(--u)*2.5);font-weight:600}
.tpl-quote .top{min-height:calc(var(--u)*6)}
.tpl-quote .chrome{margin-top:calc(var(--u)*3)}
"""
    return [page(body, c, f"tpl-quote o-{o}")], css + CHROME_CSS


# ============================================================================= social: stat
@template("stat", "One big number that tells the story (percent, money, count) with label and source.",
          "stat* (e.g. '87%'), label*, body, source, eyebrow, handle", "any social size", mode="dark")
def t_stat(c: Ctx):
    o = c.orient
    stat = str(c.get("stat", "value", "number", "headline"))
    pct = None
    try:
        s2 = stat.replace("٪", "%").strip()
        if s2.endswith("%"):
            pct = max(0.0, min(100.0, float(s2[:-1].translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))))
    except ValueError:
        pct = None
    ring = ""
    if pct is not None:
        r = 45
        circ = 2 * math.pi * r
        ring = (f'<svg class="ring" viewBox="0 0 100 100" data-decor><circle cx="50" cy="50" r="{r}" fill="none" stroke="var(--line)" stroke-width="3"/>'
                f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="var(--accent)" stroke-width="3" stroke-linecap="round" '
                f'stroke-dasharray="{circ * pct / 100:.2f} {circ:.2f}" transform="rotate(-90 50 50)"/></svg>')
    big = 30 if len(stat) <= 4 else 24 if len(stat) <= 6 else 17
    if o in ("landscape", "wide"):
        big *= 0.9
    body = f"""
{ring}
<div class="frame">
  <div class="top row">{c.logo(c.theme.vars(c.mode)['dark'] == '1')}<div class="grow"></div>{c.T(c.get("eyebrow", "tag"), "eyebrow muted", fit=None)}</div>
  <div class="main grow fitbox" data-fit-box="stat">
    <div class="num-wrap">{c.T(stat, "head num", fit=(c.fs(big), c.fs(10)))}</div>
    <div class="bar" data-decor></div>
    {c.T(c.get("label", "title"), "head lbl", fit=(c.fs(6), c.fs(3)))}
    {c.T(c.get("body", "subhead"), "body sub", fit=(c.fs(3.2), c.fs(2.1)))}
  </div>
  <div class="bottom row">{c.T(c.get("source"), "meta muted src")}<div class="grow"></div>{c.T(_handle(c), "meta muted")}</div>
  {_chrome(c, True)}
</div>"""
    css = """
.tpl-stat .ring{position:absolute;width:calc(var(--u)*30);height:calc(var(--u)*30);inset-inline-end:var(--pr);top:calc(var(--pt) + var(--u)*9)}
.tpl-stat .main{display:flex;flex-direction:column;justify-content:flex-end;gap:calc(var(--u)*2);padding-bottom:calc(var(--u)*4)}
.tpl-stat .num{color:var(--hl);line-height:.9;letter-spacing:-.045em;font-variant-numeric:lining-nums}
.tpl-stat .num.ar{line-height:1.1}
.tpl-stat .bar{width:calc(var(--u)*12);height:calc(var(--u)*.8);background:var(--ink);border-radius:9px;margin:calc(var(--u)*1) 0}
.tpl-stat .lbl{max-width:88%;line-height:1.08}
.tpl-stat .sub{max-width:80%;color:var(--muted)}
.tpl-stat .eyebrow{font-size:calc(var(--u)*2.4)}
.tpl-stat .meta{font-size:calc(var(--u)*2.4);font-weight:500}
.tpl-stat .top{min-height:calc(var(--u)*6)}
.tpl-stat .chrome{margin-top:calc(var(--u)*3)}
.tpl-stat.o-wide .ring,.tpl-stat.o-landscape .ring{width:calc(var(--u)*34);height:calc(var(--u)*34);top:calc(50% - var(--u)*17)}
"""
    return [page(body, c, f"tpl-stat o-{o}")], css + CHROME_CSS


# ============================================================================= social: list
@template("list", "Numbered tips / steps / reasons — educational posts and carousel pages.",
          "eyebrow, headline*, items[]* (strings or {title,text}), cta, handle", "any social size", mode="light")
def t_list(c: Ctx):
    o = c.orient
    items = c.get("items", "bullets", "points", "steps", default=[])
    n = len(items)
    rows = []
    for i, it in enumerate(items[:7]):
        if isinstance(it, dict):
            tt, tx = it.get("title", ""), it.get("text", "")
        else:
            tt, tx = str(it), ""
        num = f"{i + 1:02d}" if not c.rtl else f"{i + 1:02d}"
        rows.append(f'<li><span class="n" data-decor>{num}</span><div class="it">{c.T(tt, "head itt", fit=(c.fs(4.4 if n <= 4 else 3.7), c.fs(2.3)))}'
                    f'{c.T(tx, "body itx", fit=(c.fs(2.9), c.fs(1.9)))}</div></li>')
    body = f"""
<div class="frame">
  <div class="top row">{c.logo(c.theme.vars(c.mode)['dark'] == '1')}<div class="grow"></div>{c.T(c.get("eyebrow", "tag"), "eyebrow hl-c")}</div>
  <div class="main grow fitbox" data-fit-box="list">
    {c.T(c.get("headline", "title"), "head h1", fit=(c.fs(9.5), c.fs(3.6)))}
    <ol class="items">{''.join(rows)}</ol>
  </div>
  <div class="bottom row">{_cta(c, c.get("cta"), c.fs(2.8))}<div class="grow"></div>{c.T(_handle(c), "meta muted")}</div>
  {_chrome(c, False)}
</div>"""
    css = """
.tpl-list .main{display:flex;flex-direction:column;justify-content:center;gap:calc(var(--u)*4)}
.tpl-list .items{list-style:none;display:flex;flex-direction:column;gap:0}
.tpl-list li{display:flex;gap:calc(var(--u)*3);align-items:baseline;padding:calc(var(--u)*2.1) 0;border-top:1.5px solid var(--line)}
.tpl-list li:last-child{border-bottom:1.5px solid var(--line)}
.tpl-list .n{flex:none;font-family:'sf-head';font-weight:var(--hw);color:var(--hl);font-size:calc(var(--u)*3.3);min-width:calc(var(--u)*6);font-variant-numeric:tabular-nums}
.tpl-list .it{display:flex;flex-direction:column;gap:calc(var(--u)*.7);min-width:0}
.tpl-list .itt{line-height:1.15;letter-spacing:-.01em}
.tpl-list .itx{color:var(--muted)}
.tpl-list .hl-c{color:var(--hl-text);font-size:calc(var(--u)*2.4)}
.tpl-list .meta{font-size:calc(var(--u)*2.5);font-weight:600}
.tpl-list .top{min-height:calc(var(--u)*6)}
.tpl-list .chrome{margin-top:calc(var(--u)*2.6)}
"""
    return [page(body, c, f"tpl-list o-{o}")], css + CHROME_CSS


# ============================================================================= social: event
@template("event", "Event / webinar / launch announcement with date block, details, speaker and CTA.",
          "eyebrow, headline*, date* (e.g. '12 OCT' or {day,month}), time, location, speaker, speaker_role, image(speaker photo), body, cta, url",
          "any social size, story, poster", mode="brand")
def t_event(c: Ctx):
    o = c.orient
    d = c.get("date", default="")
    if isinstance(d, dict):
        day, mon = str(d.get("day", "")), str(d.get("month", ""))
    else:
        parts = str(d).split()
        day = next((p for p in parts if any(ch.isdigit() for ch in p)), parts[0] if parts else "")
        mon = " ".join(p for p in parts if p != day)
    ph = c.img(c.get("image", "speaker_photo", "photo"), "speaker")
    details = []
    for key, ic in (("time", "clock"), ("location", "pin"), ("venue", "pin"), ("price", "star")):
        v = c.get(key)
        if v:
            details.append(f'<div class="dt"><span class="ic">{icon(ic)}</span>{c.T(v, "body", fit=(c.fs(3), c.fs(2)))}</div>')
    spk = ""
    if c.get("speaker"):
        ph_html = f'<img src="{ph}">' if ph else ""
        spk = (f'<div class="spk">{ph_html}<div>{c.T(c.get("speaker"), "body sn", fit=(c.fs(3.1), c.fs(2)))}'
               f'{c.T(c.get("speaker_role"), "body sr", fit=(c.fs(2.5), c.fs(1.8)))}</div></div>')
    body = f"""
<div class="deco" data-decor><i class="a1"></i><i class="a2"></i></div>
<div class="frame">
  <div class="top row">{c.logo(c.theme.vars(c.mode)['dark'] == '1')}<div class="grow"></div>{c.T(c.get("eyebrow", "tag"), "pill ebp")}</div>
  <div class="main grow fitbox" data-fit-box="event">
    <div class="date">{c.T(day, "head day", fit=(c.fs(11), c.fs(6)))}{c.T(mon, "body mon", fit=(c.fs(3.1), c.fs(2)))}</div>
    {c.T(c.get("headline", "title"), "head h1", fit=(c.fs(9.6), c.fs(3.6)))}
    {c.T(c.get("body", "subhead"), "body sub", fit=(c.fs(3.1), c.fs(2)))}
    <div class="dts">{''.join(details)}</div>
    {spk}
  </div>
  <div class="bottom row">{_cta(c, c.get("cta"), c.fs(2.9))}<div class="grow"></div>{c.T(c.get("url", "handle"), "meta")}</div>
  {_chrome(c, True)}
</div>"""
    css = """
.tpl-event .deco .a1{position:absolute;width:calc(var(--u)*70);height:calc(var(--u)*70);border:calc(var(--u)*9) solid var(--deco);border-radius:50%;
  inset-inline-end:calc(var(--u)*-28);top:calc(var(--u)*-26);opacity:.9}
.tpl-event .deco .a2{position:absolute;width:calc(var(--u)*22);height:calc(var(--u)*22);background:var(--ink);opacity:.07;border-radius:50%;
  inset-inline-end:calc(var(--u)*30);top:calc(var(--u)*32)}
.tpl-event .main{display:flex;flex-direction:column;justify-content:flex-end;gap:calc(var(--u)*2.3);padding-bottom:calc(var(--u)*3)}
.tpl-event .date{display:inline-flex;flex-direction:column;align-self:flex-start;background:var(--cta-bg);color:var(--cta-fg);border-radius:calc(var(--u)*2.2);
  padding:calc(var(--u)*1.6) calc(var(--u)*2.6) calc(var(--u)*1.8);--ta:center;min-width:calc(var(--u)*17)}
.tpl-event .day{line-height:.95;letter-spacing:-.03em;font-variant-numeric:lining-nums}
.tpl-event .mon{font-weight:700;letter-spacing:.12em;text-transform:uppercase;line-height:1.1}
.tpl-event .h1{max-width:92%}
.tpl-event .sub{color:var(--muted);max-width:82%}
.tpl-event .dts{display:flex;flex-wrap:wrap;gap:calc(var(--u)*1.2) calc(var(--u)*4)}
.tpl-event .dt{display:flex;align-items:center;gap:calc(var(--u)*1.2);font-weight:600}
.tpl-event .ic{width:calc(var(--u)*3.4);height:calc(var(--u)*3.4);color:var(--hl);flex:none}
.tpl-event .ic svg{width:100%;height:100%}
.tpl-event .spk{display:flex;align-items:center;gap:calc(var(--u)*2);margin-top:calc(var(--u)*.8);padding-top:calc(var(--u)*2.2);border-top:1.5px solid var(--line)}
.tpl-event .spk img{width:calc(var(--u)*9);height:calc(var(--u)*9);border-radius:50%;object-fit:cover}
.tpl-event .sn{font-weight:700}
.tpl-event .sr{color:var(--muted)}
.tpl-event .ebp{font-size:calc(var(--u)*2.3)}
.tpl-event .meta{font-size:calc(var(--u)*2.6);font-weight:600;color:var(--muted)}
.tpl-event .top{min-height:calc(var(--u)*6)}
.tpl-event .chrome{margin-top:calc(var(--u)*3)}
.tpl-event.o-wide .h1,.tpl-event.o-landscape .h1{max-width:70%}
"""
    return [page(body, c, f"tpl-event o-{o}")], css + CHROME_CSS


# ============================================================================= social: offer
@template("offer", "Sale / promo / product offer: big discount or price, product image, badge, CTA.",
          "eyebrow, offer* (e.g. '50%' or 'OFF'), offer_label, headline (product), price, old_price, currency, image (product, ideally transparent PNG), badge, cta, terms, handle",
          "any social size, story", mode="accent")
def t_offer(c: Ctx):
    o = c.orient
    src = c.img(c.get("image", "product"), "product")
    offer = str(c.get("offer", "discount", default=""))
    price, old = c.get("price"), c.get("old_price")
    cur = c.get("currency")
    badge = c.get("badge")
    horiz = o in ("landscape", "wide", "square")
    pr = ""
    if price:
        pr = (f'<div class="price">{c.T(f"{price}", "head pv", fit=(c.fs(9), c.fs(4)))}'
              f'{c.T(cur, "body pc", fit=(c.fs(3.2), c.fs(2)))}{c.T(old, "body old", fit=(c.fs(3.2), c.fs(2)))}</div>')
    visual = (f'<div class="vis"><i class="disc" data-decor></i><img src="{src}"></div>' if src else
              '<div class="vis"><i class="disc" data-decor></i></div>')
    body = f"""
<div class="frame {'h' if horiz else 'v'}">
  <div class="top row">{c.logo(c.theme.vars(c.mode)['dark'] == '1')}<div class="grow"></div>{c.T(c.get("eyebrow", "tag"), "eyebrow")}</div>
  <div class="mid grow">
    {visual}
    <div class="main fitbox" data-fit-box="offer">
      {c.T(offer, "head big", fit=(c.fs(22 if len(offer) <= 4 else 15), c.fs(8)))}
      {c.T(c.get("offer_label"), "head ol", fit=(c.fs(5.4), c.fs(3)))}
      {c.T(c.get("headline", "title", "product_name"), "body pn", fit=(c.fs(4), c.fs(2.4)))}
      {pr}
    </div>
    {f'<div class="badge" data-decor>{c.T(badge, "head")}</div>' if badge else ''}
  </div>
  <div class="bottom row">{_cta(c, c.get("cta"), c.fs(3))}<div class="grow"></div>{c.T(c.get("terms") or _handle(c), "meta")}</div>
  {_chrome(c, False)}
</div>"""
    css = """
.tpl-offer .mid{position:relative;display:flex;min-height:0}
.tpl-offer .frame.v .mid{flex-direction:column}
.tpl-offer .frame.h .mid{flex-direction:row-reverse;align-items:center}
.tpl-offer .vis{position:relative;flex:1 1 50%;min-height:0;align-self:stretch;display:grid;place-items:center}
.tpl-offer .vis img{position:absolute;inset:4% 0;width:100%;height:92%;object-fit:contain;filter:drop-shadow(0 calc(var(--u)*2) calc(var(--u)*3) rgba(0,0,0,.28))}
.tpl-offer .disc{position:absolute;width:min(100%,calc(var(--u)*62));aspect-ratio:1;border-radius:50%;background:var(--primary);opacity:1}
.tpl-offer .main{display:flex;flex-direction:column;justify-content:center;gap:calc(var(--u)*1.2);flex:0 1 auto}
.tpl-offer .frame.h .main{flex:1 1 50%;height:100%}
.tpl-offer .frame.v .main{max-height:50%;flex:0 0 auto}
.tpl-offer .frame.v .vis{flex:1 1 50%}
.tpl-offer .frame.h .vis{height:100%}
.tpl-offer .big{line-height:.86;letter-spacing:-.05em;color:var(--ink)}
.tpl-offer .big.ar{line-height:1.1}
.tpl-offer .ol{text-transform:uppercase;letter-spacing:.02em;line-height:1}
.tpl-offer .pn{font-weight:600;opacity:.9}
.tpl-offer .price{display:flex;align-items:baseline;gap:calc(var(--u)*1.2);flex-wrap:wrap}
.tpl-offer .pv{line-height:1;letter-spacing:-.03em}
.tpl-offer .pc{font-weight:700}
.tpl-offer .old{text-decoration:line-through;opacity:.62}
.tpl-offer .badge{position:absolute;inset-inline-end:calc(var(--u)*1);top:calc(var(--u)*1);width:calc(var(--u)*17);height:calc(var(--u)*17);border-radius:50%;
  background:var(--ink0);color:var(--paper);display:grid;place-items:center;transform:rotate(-12deg);font-size:calc(var(--u)*3.4);padding:calc(var(--u)*2);--ta:center;line-height:1}
.tpl-offer .eyebrow{font-size:calc(var(--u)*2.4)}
.tpl-offer .meta{font-size:calc(var(--u)*2.3);font-weight:500;opacity:.85;max-width:60%}
.tpl-offer .top{min-height:calc(var(--u)*6)}
.tpl-offer .bottom{margin-top:calc(var(--u)*2)}
.tpl-offer .chrome{margin-top:calc(var(--u)*2.6)}
"""
    return [page(body, c, f"tpl-offer o-{o}")], css + CHROME_CSS


# ============================================================================= social: editorial
@template("editorial", "Minimal magazine layout: rules, issue line, serif-feel headline, body — thought leadership, articles.",
          "eyebrow (section), issue, headline*, body, author, handle", "any social size, poster", mode="light")
def t_editorial(c: Ctx):
    o = c.orient
    body = f"""
<div class="frame">
  <div class="mast row">{c.T(c.get("eyebrow", "section", "tag"), "eyebrow")}<div class="grow"></div>{c.T(c.get("issue", "date"), "eyebrow muted")}</div>
  <div class="main grow fitbox" data-fit-box="editorial">
    {c.T(c.get("headline", "title"), "head h1", fit=(c.fs(12), c.fs(4)))}
    <div class="sep" data-decor></div>
    {c.T(c.get("body", "subhead"), "body sub", fit=(c.fs(3.9), c.fs(2.2)))}
    {c.T(("— " + c.get("author")) if c.get("author") else "", "body by", fit=(c.fs(2.8), c.fs(2)))}
  </div>
  <div class="foot row">{c.logo(c.theme.vars(c.mode)['dark'] == '1')}<div class="grow"></div>{c.T(_handle(c), "meta muted")}</div>
  {_chrome(c, False)}
</div>"""
    css = """
.tpl-editorial .mast{border-bottom:2px solid var(--ink);padding-bottom:calc(var(--u)*1.6);font-size:calc(var(--u)*2.3)}
.tpl-editorial .mast .eyebrow{font-size:calc(var(--u)*2.3)}
.tpl-editorial .main{display:flex;flex-direction:column;justify-content:flex-end;padding-bottom:calc(var(--u)*6);gap:calc(var(--u)*3)}
.tpl-editorial .h1{font-weight:var(--hw);letter-spacing:-.025em;line-height:1.0}
.tpl-editorial .sep{width:calc(var(--u)*10);height:calc(var(--u)*.7);background:var(--hl)}
.tpl-editorial .sub{max-width:88%;color:var(--muted)}
.tpl-editorial .by{font-weight:600}
.tpl-editorial .foot{border-top:1px solid var(--line);padding-top:calc(var(--u)*2);min-height:calc(var(--u)*8)}
.tpl-editorial .meta{font-size:calc(var(--u)*2.4);font-weight:600}
.tpl-editorial .chrome{margin-top:calc(var(--u)*2.4)}
.tpl-editorial.o-wide .sub,.tpl-editorial.o-landscape .sub{max-width:70%;column-count:1}
"""
    return [page(body, c, f"tpl-editorial o-{o}")], css + CHROME_CSS
