"""Print & special-format templates: YouTube thumbnail, banners/covers, poster, flyer, business card,
certificate, menu, infographic, letterhead, avatar. Registered into templates.TEMPLATES."""
from __future__ import annotations

import html as _html
import math

from . import color as col
from .builder import Ctx, icon, page, plain
from .templates import CHROME_CSS, _chrome, _cta, _handle, template


def qr_svg(data: str, color: str = "currentColor") -> str | None:
    """Inline SVG QR code (needs the small pure-Python 'segno' package)."""
    try:
        import segno
    except ImportError:
        return None
    q = segno.make(data, error="m")
    m = q.matrix
    n = len(m)
    path = "".join(f"M{x},{y}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg class="qr" viewBox="-2 -2 {n + 4} {n + 4}" shape-rendering="crispEdges">'
            f'<path d="{path}" fill="{color}"/></svg>')


def _qr(c: Ctx) -> str:
    d = c.get("qr")
    if not d:
        return ""
    s = qr_svg(str(d))
    if not s:
        c.warnings.append("QR code skipped — install the 'segno' package (pip install segno)")
        return ""
    return f'<div class="qrbox">{s}</div>'


def _contacts(c: Ctx, keys=(("phone", "phone"), ("email", "mail"), ("website", "web"), ("url", "web"), ("address", "pin")),
              fit=None) -> str:
    rows, seen = [], set()
    for k, ic in keys:
        v = c.get(k)
        if v and ic + str(v) not in seen:
            seen.add(ic + str(v))
            rows.append(f'<div class="ct"><span class="ic">{icon(ic)}</span>{c.T(v, "body", fit=fit)}</div>')
    return "".join(rows)


# ============================================================================= YouTube thumbnail
@template("thumbnail", "YouTube thumbnail: 2–5 huge punchy words, marker highlight, subject cut-out, bold contrast.",
          "headline* (use ==word== for a marker box), kicker (small label), image (subject, ideally a transparent cut-out PNG), "
          "background (image), font=impact|theme", "youtube_thumbnail (also x_post, fb_post)", mode="dark", kind="video")
def t_thumbnail(c: Ctx):
    if not c.theme.kit and (c.get("font") or "impact") == "impact":
        c.theme.head, c.theme.head_ar, c.theme.head_weight = "Anton", "Lalezar", 400
    subj = c.img(c.get("image", "subject"), "subject")
    bgimg = c.img(c.get("background", "bg_image"), "bg")
    head = c.get("headline", "title")
    words = len(plain(head).split())
    if words > 7:
        c.warnings.append(f"thumbnail headline has {words} words — 2–5 words read best at small size")
    bg = f'<img class="fill bgi" src="{bgimg}">' if bgimg else '<div class="fill grad"></div>'
    body = f"""
{bg}<div class="fill shade"></div>
<div class="glow" data-decor></div>
{f'<img class="subj" src="{subj}">' if subj else ''}
<div class="frame">
  <div class="main grow fitbox {'with-subj' if subj else 'no-subj'}" data-fit-box="thumb">
    {c.T(c.get("kicker", "eyebrow"), "kick", fit=(c.fs(6), c.fs(3.5)))}
    {c.T(head, "head h1", fit=(c.fs(19), c.fs(8)))}
  </div>
</div>"""
    css = """
.tpl-thumbnail .bgi{width:100%;height:100%;object-fit:cover}
.tpl-thumbnail .grad{background:radial-gradient(80% 110% at 85% 50%,var(--primary) 0%,var(--secondary) 70%)}
.tpl-thumbnail .shade{background:linear-gradient(to var(--end),rgba(0,0,0,.72) 0%,rgba(0,0,0,.35) 55%,rgba(0,0,0,0) 80%)}
.tpl-thumbnail .glow{position:absolute;width:calc(var(--u)*85);height:calc(var(--u)*85);border-radius:50%;inset-inline-end:calc(var(--u)*-6);top:calc(var(--u)*-2);
  background:radial-gradient(circle,var(--accent) 0%,transparent 65%);opacity:.55}
.tpl-thumbnail .subj{position:absolute;bottom:0;inset-inline-end:0;height:100%;max-width:52%;object-fit:contain;object-position:bottom;
  filter:drop-shadow(0 0 calc(var(--u)*1.2) rgba(0,0,0,.6))}
.tpl-thumbnail .main{display:flex;flex-direction:column;justify-content:center;gap:calc(var(--u)*1.5);color:#fff}
.tpl-thumbnail .main.with-subj{max-width:60%}
.tpl-thumbnail .main.no-subj{max-width:82%}
.tpl-thumbnail .h1{line-height:1.06;letter-spacing:.005em;text-transform:uppercase;color:#fff;
  text-shadow:0 calc(var(--u)*.6) 0 rgba(0,0,0,.45),0 0 calc(var(--u)*3) rgba(0,0,0,.35)}
.tpl-thumbnail .h1.ar{line-height:1.25}
.tpl-thumbnail .h1 .hl{color:var(--accent)}
.tpl-thumbnail .h1 .mark{background:var(--accent);color:var(--on-accent);text-shadow:none;padding:0 .12em;border-radius:.08em;line-height:1.06;display:inline-block;transform:rotate(-1.5deg);margin:.04em 0}
.tpl-thumbnail .kick{align-self:flex-start;background:#fff;color:#111;font-family:'sf-body';font-weight:800;padding:.2em .55em;border-radius:.2em;
  text-transform:uppercase;letter-spacing:.02em;transform:rotate(-2deg)}
"""
    return [page(body, c, "tpl-thumbnail o-" + c.orient)], css


# ============================================================================= banner / cover
@template("banner", "Profile banner / cover (LinkedIn, X, Facebook, YouTube channel art, email header): logo, tagline, URL, "
          "brand shapes — content kept clear of the profile photo and inside the all-device safe area.",
          "headline* (tagline), subhead, logo, handle|url, image (optional right-side photo)", "linkedin_cover, x_header, fb_cover, youtube_banner, email_header", mode="brand")
def t_banner(c: Ctx):
    avoid_start = any(a["x"] < c.w * 0.3 for a in c.canvas.avoid)
    src = c.img(c.get("image"), "banner")
    dark = c.theme.vars(c.mode)["dark"] == "1"
    align = "end" if avoid_start else ("center" if c.canvas.name == "youtube_banner" else "start")
    body = f"""
<div class="deco dc-{align}" data-decor><i class="b1"></i><i class="b2"></i><i class="b3"></i></div>
{f'<div class="pic"><img src="{src}"></div>' if src else ''}
<div class="frame al-{align}">
  <div class="main grow fitbox" data-fit-box="banner">
    <div class="lg">{c.logo(dark)}</div>
    {c.T(c.get("headline", "title", "tagline"), "head h1", fit=(c.fs(14 if c.orient == "wide" else 11), c.fs(4)))}
    {c.T(c.get("subhead", "body"), "body sub", fit=(c.fs(5), c.fs(2.4)))}
    {c.T(_handle(c), "meta", fit=(c.fs(4.2), c.fs(2.2)))}
  </div>
</div>"""
    css = """
.tpl-banner .deco i{position:absolute;border-radius:50%}
.tpl-banner .b1{width:calc(var(--u)*160);height:calc(var(--u)*160);inset-inline-end:calc(var(--u)*-70);top:calc(var(--u)*-40);background:var(--ink);opacity:.06}
.tpl-banner .b2{width:calc(var(--u)*60);height:calc(var(--u)*60);inset-inline-end:calc(var(--u)*30);bottom:calc(var(--u)*-40);border:calc(var(--u)*5) solid var(--deco)}
.tpl-banner .b3{width:calc(var(--u)*9);height:calc(var(--u)*9);inset-inline-end:calc(var(--u)*18);top:calc(var(--u)*18);background:var(--deco)}
.tpl-banner .pic{position:absolute;top:0;bottom:0;inset-inline-end:0;width:38%;overflow:hidden;clip-path:polygon(18% 0,100% 0,100% 100%,0 100%)}
[dir=rtl] .tpl-banner .pic{clip-path:polygon(0 0,82% 0,100% 100%,0 100%)}
.tpl-banner .pic img{width:100%;height:100%;object-fit:cover}
.tpl-banner .dc-end .b1{inset-inline-end:auto;inset-inline-start:calc(var(--u)*-60)}
.tpl-banner .dc-end .b2{inset-inline-end:auto;inset-inline-start:calc(var(--u)*55);bottom:calc(var(--u)*-45)}
.tpl-banner .dc-end .b3{inset-inline-end:auto;inset-inline-start:calc(var(--u)*120);top:calc(var(--u)*14)}
.tpl-banner .main{display:flex;flex-direction:column;justify-content:center;gap:calc(var(--u)*1.8);max-width:60%}
.tpl-banner .al-end .main{margin-inline-start:auto;align-items:flex-end;--ta:var(--end)}
.tpl-banner .al-center .main{margin:0 auto;align-items:center;--ta:center;max-width:100%}
.tpl-banner .lg .logo{height:min(calc(var(--u)*10), calc((var(--H) - var(--pt) - var(--pb)) * .26));max-width:calc(var(--u)*70)}
.tpl-banner .al-end .lg .logo{object-position:var(--end) center}
.tpl-banner .al-center .lg .logo{object-position:center}
.tpl-banner .sub{color:var(--muted)}
.tpl-banner .meta{font-weight:700;color:var(--hl-text);letter-spacing:.02em}
"""
    return [page(body, c, "tpl-banner o-" + c.orient)], css


# ============================================================================= poster
@template("poster", "Event / campaign poster (A3, A4, 50×70, story): hero image, huge title, details grid, sponsors/logo, QR.",
          "eyebrow, headline*, subhead, image, date, time, location|venue, price, details[] ({label,value}), cta, url, qr (link), organizer",
          "a3, a4, a5, poster_50x70, ig_story", mode="dark", kind="print")
def t_poster(c: Ctx):
    src = c.img(c.get("image", "photo"), "hero")
    dark = c.theme.vars(c.mode)["dark"] == "1"
    det = list(c.get("details", default=[]) or [])
    for k, lab_en, lab_ar in (("date", "Date", "التاريخ"), ("time", "Time", "الوقت"), ("location", "Venue", "المكان"),
                              ("venue", "Venue", "المكان"), ("price", "Entry", "الدخول")):
        if c.get(k):
            det.append({"label": lab_ar if c.rtl else lab_en, "value": c.get(k)})
    cells = "".join(f'<div class="cell">{c.T(d.get("label", ""), "lab eyebrow")}{c.T(d.get("value", ""), "head val", fit=(c.fs(4.6), c.fs(2.4)))}</div>'
                    for d in det[:4])
    hero = f'<div class="hero"><img src="{src}"><i class="tint"></i></div>' if src else '<div class="hero shapes" data-decor><i class="s1"></i><i class="s2"></i><i class="s3"></i></div>'
    body = f"""
{hero}
<div class="frame">
  <div class="top row">{c.logo(True)}<div class="grow"></div>{c.T(c.get("eyebrow", "tag"), "pill eb")}</div>
  <div class="grow"></div>
  <div class="main fitbox" data-fit-box="poster">
    {c.T(c.get("headline", "title"), "head h1", fit=(c.fs(17), c.fs(6)))}
    {c.T(c.get("subhead", "body"), "body sub", fit=(c.fs(4.4), c.fs(2.6)))}
    {f'<div class="grid">{cells}</div>' if cells else ''}
  </div>
  <div class="foot row">
    <div class="fl">{_cta(c, c.get("cta"), c.fs(3.4))}{c.T(c.get("url", "handle"), "meta")}{c.T(c.get("organizer"), "meta muted")}</div>
    <div class="grow"></div>{_qr(c)}
  </div>
</div>"""
    css = """
.tpl-poster .hero{position:absolute;top:0;left:0;right:0;height:58%}
.tpl-poster .hero img{width:100%;height:100%;object-fit:cover}
.tpl-poster .hero .tint{position:absolute;inset:0;background:linear-gradient(to bottom,rgba(0,0,0,.35) 0%,rgba(0,0,0,0) 30%,var(--bg) 100%)}
.tpl-poster .shapes i{position:absolute;border-radius:50%}
.tpl-poster .shapes .s1{width:calc(var(--u)*110);height:calc(var(--u)*110);inset-inline-end:calc(var(--u)*-35);top:calc(var(--u)*-30);background:var(--primary)}
.tpl-poster .shapes .s2{width:calc(var(--u)*46);height:calc(var(--u)*46);inset-inline-start:calc(var(--u)*8);top:calc(var(--u)*30);background:var(--accent)}
.tpl-poster .shapes .s3{width:calc(var(--u)*60);height:calc(var(--u)*60);inset-inline-start:calc(var(--u)*-20);top:calc(var(--u)*-26);border:calc(var(--u)*3) solid var(--ink);opacity:.25}
.tpl-poster .main{display:flex;flex-direction:column;gap:calc(var(--u)*3);max-height:62%}
.tpl-poster .h1{line-height:.95;letter-spacing:-.03em}
.tpl-poster .h1.ar{line-height:1.25}
.tpl-poster .sub{color:var(--muted);max-width:90%}
.tpl-poster .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(0,1fr));gap:calc(var(--u)*3);border-top:2px solid var(--ink);padding-top:calc(var(--u)*2.6)}
.tpl-poster .cell{display:flex;flex-direction:column;gap:calc(var(--u)*.8)}
.tpl-poster .lab{font-size:calc(var(--u)*2.1);color:var(--hl-text)}
.tpl-poster .val{line-height:1.1;letter-spacing:-.01em}
.tpl-poster .foot{margin-top:calc(var(--u)*4);align-items:flex-end}
.tpl-poster .fl{display:flex;flex-direction:column;gap:calc(var(--u)*1.4);align-items:flex-start}
.tpl-poster .meta{font-size:calc(var(--u)*2.6);font-weight:600}
.tpl-poster .eb{font-size:calc(var(--u)*2.4);background:rgba(0,0,0,.25);color:#fff;border-color:transparent}
.tpl-poster .qrbox{background:#fff;padding:calc(var(--u)*1);border-radius:calc(var(--u)*1)}
.tpl-poster .qr{width:calc(var(--u)*17);height:calc(var(--u)*17);display:block;color:#000}
.tpl-poster .top .logo{height:calc(var(--u)*6)}
"""
    return [page(body, c, "tpl-poster o-" + c.orient)], css


# ============================================================================= flyer
@template("flyer", "Flyer / leaflet (A5, A4, DL): headline, image, features with checks, offer, contact band, QR.",
          "eyebrow, headline*, body, image, features[] (strings), offer, cta, phone, email, website, address, qr",
          "a5, a4, dl, letter, ig_portrait", mode="light", kind="print")
def t_flyer(c: Ctx):
    src = c.img(c.get("image", "photo"), "flyer")
    feats = c.get("features", "bullets", "points", default=[])
    fl = "".join(f'<li><span class="ck">{icon("check")}</span>{c.T(f, "body", fit=(c.fs(3.3), c.fs(2.2)))}</li>' for f in feats[:6])
    body = f"""
<div class="band" data-decor></div>
<div class="frame">
  <div class="top row">{c.logo(False)}<div class="grow"></div>{c.T(c.get("eyebrow", "tag"), "eyebrow ebc")}</div>
  <div class="main grow fitbox" data-fit-box="flyer">
    {c.T(c.get("headline", "title"), "head h1", fit=(c.fs(11), c.fs(4.5)))}
    {c.T(c.get("body", "subhead"), "body sub", fit=(c.fs(3.6), c.fs(2.3)))}
    {f'<div class="pic"><img src="{src}">{c.T(c.get("offer"), "head offer")}</div>' if src else (c.T(c.get("offer"), "head offer solo") if c.get("offer") else "")}
    {f'<ul class="feats">{fl}</ul>' if fl else ''}
    {_cta(c, c.get("cta"), c.fs(3.2))}
  </div>
  <div class="contact">
    <div class="cts">{_contacts(c, fit=(c.fs(2.7), c.fs(1.9)))}</div>{_qr(c)}
  </div>
</div>"""
    css = """
.tpl-flyer .band{position:absolute;left:0;right:0;bottom:0;height:calc(var(--pb) + var(--u)*15);background:var(--secondary)}
.tpl-flyer .main{display:flex;flex-direction:column;gap:calc(var(--u)*3);padding:calc(var(--u)*4) 0 calc(var(--u)*5)}
.tpl-flyer .h1{letter-spacing:-.03em}
.tpl-flyer .sub{color:var(--muted);max-width:92%}
.tpl-flyer .ebc{color:var(--hl-text);font-size:calc(var(--u)*2.4)}
.tpl-flyer .pic{position:relative;flex:1 1 auto;min-height:calc(var(--u)*30);border-radius:calc(var(--u)*3);overflow:hidden}
.tpl-flyer .pic img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.tpl-flyer .offer{position:absolute;inset-inline-end:calc(var(--u)*3);bottom:calc(var(--u)*3);background:var(--accent);color:var(--on-accent);
  padding:.35em .6em;border-radius:calc(var(--u)*1.5);font-size:calc(var(--u)*5);line-height:1}
.tpl-flyer .offer.solo{position:static;align-self:flex-start}
.tpl-flyer .feats{list-style:none;display:grid;grid-template-columns:1fr 1fr;gap:calc(var(--u)*1.8) calc(var(--u)*4)}
.tpl-flyer .feats li{display:flex;gap:calc(var(--u)*1.6);align-items:flex-start;font-weight:600}
.tpl-flyer .ck{flex:none;width:calc(var(--u)*4);height:calc(var(--u)*4);border-radius:50%;background:var(--primary);color:var(--on-primary);display:grid;place-items:center;margin-top:.1em}
.tpl-flyer .ck svg{width:62%;height:62%}
.tpl-flyer .cta{align-self:flex-start}
.tpl-flyer .contact{height:calc(var(--u)*15);display:flex;align-items:center;gap:calc(var(--u)*3);color:var(--paper)}
.tpl-flyer .cts{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:calc(var(--u)*1.2) calc(var(--u)*3)}
.tpl-flyer .ct{display:flex;align-items:center;gap:calc(var(--u)*1.2);min-width:0}
.tpl-flyer .ct .ic{width:calc(var(--u)*3);height:calc(var(--u)*3);flex:none;color:var(--accent)}
.tpl-flyer .ct .ic svg{width:100%;height:100%}
.tpl-flyer .qrbox{background:#fff;padding:calc(var(--u)*.8);border-radius:calc(var(--u)*1)}
.tpl-flyer .qr{width:calc(var(--u)*11);height:calc(var(--u)*11);display:block;color:#000}
.tpl-flyer .top .logo{height:calc(var(--u)*6.5)}
"""
    return [page(body, c, "tpl-flyer o-" + c.orient)], css


# ============================================================================= business card
@template("business_card", "Business card, front + back, print-ready (bleed + crop marks PDF). Bilingual when name_ar/title_ar given.",
          "name*, title, name_ar, title_ar, phone, email, website, address, tagline, qr, logo", "business_card (85×55), business_card_eg (90×50), business_card_us",
          mode="brand", kind="print")
def t_business_card(c: Ctx):
    kit = c.theme.kit or {}
    dark_front = c.theme.vars("brand")["dark"] == "1"
    front_logo = c.logo_src(dark_front, "stacked") or c.logo_src(dark_front, "horizontal")
    brand_name = c.get("brand_name") or kit.get("name") or c.get("company") or ""
    front_inner = (f'<img class="flogo" src="{front_logo}">' if front_logo else c.T(brand_name, "head fname"))
    front = f"""
<div class="deco" data-decor><i class="c1"></i></div>
<div class="frame fc">{front_inner}{c.T(c.get("tagline") or kit.get("tagline", ""), "body tag")}</div>"""
    fit = (c.fs(4.3), c.fs(3.7))
    en = f"""<div class="id">{c.T(c.get("name"), "head nm", fit=(c.fs(7), c.fs(5.4)))}{c.T(c.get("title", "role"), "body ti", fit=fit)}</div>
      <div class="cts">{_contacts(c, fit=fit)}</div>"""
    ar_block = ""
    if c.get("name_ar"):
        ar_block = (f'<div class="arb" dir="rtl">{c.T(c.get("name_ar"), "head nm", fit=(c.fs(7), c.fs(5.4)))}'
                    f'{c.T(c.get("title_ar"), "body ti", fit=fit)}{c.T(c.get("address_ar"), "body ad", fit=fit)}</div>')
    small_logo = c.logo_src(False, "icon") or c.logo_src(False, "horizontal")
    back = f"""
<i class="stripe" data-decor></i>
<div class="frame bc">
  <div class="brow grow fitbox" data-fit-box="card">
    <div class="enb">{en}</div>
    {ar_block or (f'<div class="side"><img class="slogo" src="{small_logo}">{_qr(c)}</div>' if small_logo or c.get("qr") else '')}
  </div>
</div>"""
    css = """
.tpl-bc-front .deco .c1{position:absolute;width:calc(var(--u)*120);height:calc(var(--u)*120);border-radius:50%;border:calc(var(--u)*1.2) solid var(--deco);opacity:.5;
  inset-inline-end:calc(var(--u)*-88);bottom:calc(var(--u)*-100)}
.tpl-bc-front .fc{align-items:center;justify-content:center;gap:calc(var(--u)*4);--ta:center}
.tpl-bc-front .flogo{max-height:46%;max-width:70%;object-fit:contain}
.tpl-bc-front .fname{font-size:calc(var(--u)*12)}
.tpl-bc-front .tag{font-size:calc(var(--u)*4.4);color:var(--muted);letter-spacing:.02em}
.tpl-bc-back .stripe{position:absolute;top:0;bottom:0;inset-inline-start:0;width:calc(var(--bleed) + var(--u)*3);background:var(--primary)}
.tpl-bc-back .bc{padding-inline-start:calc(var(--u)*4)}
.tpl-bc-back .brow{display:flex;gap:calc(var(--u)*6);align-items:stretch}
.tpl-bc-back .enb{flex:1;display:flex;flex-direction:column;justify-content:space-between;min-width:0}
.tpl-bc-back .arb{flex:0 1 44%;display:flex;flex-direction:column;gap:calc(var(--u)*1);border-inline-start:1px solid var(--line);padding-inline-start:calc(var(--u)*5);--ta:right}
.tpl-bc-back .nm{line-height:1.1;letter-spacing:-.01em}
.tpl-bc-back .ti{color:var(--hl-text);font-weight:600;margin-top:calc(var(--u)*1)}
.tpl-bc-back .ad{color:var(--muted);margin-top:auto}
.tpl-bc-back .cts{display:flex;flex-direction:column;gap:calc(var(--u)*1.4)}
.tpl-bc-back .ct{display:flex;align-items:center;gap:calc(var(--u)*2)}
.tpl-bc-back .ct .ic{width:calc(var(--u)*4.2);height:calc(var(--u)*4.2);flex:none;color:var(--hl)}
.tpl-bc-back .ct .ic svg{width:100%;height:100%}
.tpl-bc-back .side{display:flex;flex-direction:column;justify-content:space-between;align-items:flex-end}
.tpl-bc-back .slogo{height:calc(var(--u)*14);width:auto;max-width:calc(var(--u)*40);object-fit:contain}
.tpl-bc-back .qr{width:calc(var(--u)*22);height:calc(var(--u)*22);color:var(--ink)}
"""
    return [page(front, c, "tpl-bc-front", mode="brand"), page(back, c, "tpl-bc-back", mode="light")], css


# ============================================================================= certificate
def _seal(c: Ctx, text: str) -> str:
    t = _html.escape(text.upper() if not c.rtl else text)
    pts = []
    for i in range(48):
        r = 50 if i % 2 == 0 else 45.5
        a = math.pi * 2 * i / 48
        pts.append(f"{50 + r * math.cos(a):.2f},{50 + r * math.sin(a):.2f}")
    return (f'<svg class="seal" viewBox="0 0 100 100" data-decor><polygon points="{" ".join(pts)}" fill="var(--accent)"/>'
            f'<circle cx="50" cy="50" r="39" fill="none" stroke="var(--on-accent)" stroke-width=".7" opacity=".7"/>'
            f'<circle cx="50" cy="50" r="27" fill="none" stroke="var(--on-accent)" stroke-width=".5" opacity=".7"/>'
            f'<path id="sp" d="M50,50 m-33,0 a33,33 0 1,1 66,0 a33,33 0 1,1 -66,0" fill="none"/>'
            f'<text font-family="sf-body" font-weight="700" font-size="6.3" letter-spacing="1.2" fill="var(--on-accent)">'
            f'<textPath href="#sp" startOffset="0">{t} ★ {t} ★</textPath></text>'
            f'<path d="M50 38l3.5 7.2 7.9 1.1-5.7 5.6 1.3 7.9L50 56.1l-7 3.7 1.3-7.9-5.7-5.6 7.9-1.1z" fill="var(--on-accent)"/></svg>')


@template("certificate", "Certificate / diploma / award (A4 landscape): ornamental frame, script title, recipient, signatures, seal.",
          "title (e.g. 'Certificate'), subtitle (e.g. 'of Completion'), presented (line above name), recipient*, body, date, "
          "signatures[] ({name, role, signature?}), seal (seal text), logo, id", "a4_landscape, letter_landscape", mode="light", kind="print")
def t_certificate(c: Ctx):
    c.script_font = "Great Vibes"
    sigs = c.get("signatures", default=[]) or []
    sg = "".join(f'<div class="sig"><div class="sgn">{c.T(s.get("signature") or s.get("name", ""), "script")}</div>'
                 f'<i class="ln"></i>{c.T(s.get("name", ""), "body snm")}{c.T(s.get("role", ""), "body srl")}</div>' for s in sigs[:3])
    date = c.get("date")
    date_block = f'<div class="sig">{c.T(date, "body dt")}<i class="ln"></i>{c.T("التاريخ" if c.rtl else "Date", "body srl")}</div>' if date else ""
    title = c.get("title", default="شهادة" if c.rtl else "Certificate")
    sub = c.get("subtitle", default="تقدير" if c.rtl else "of Achievement")
    presented = c.get("presented", default="تُمنح هذه الشهادة إلى" if c.rtl else "This certificate is proudly presented to")
    corner = ('<svg viewBox="0 0 40 40" class="orn" data-decor><path d="M2 38V2h36" fill="none" stroke="currentColor" stroke-width="1.2"/>'
              '<path d="M7 38V7h31" fill="none" stroke="currentColor" stroke-width=".6"/><rect x="0" y="0" width="9" height="9" fill="currentColor" transform="rotate(45 4.5 4.5) translate(1 -5)"/>'
              '<circle cx="7" cy="7" r="2.2" fill="currentColor"/></svg>')
    body = f"""
<div class="border" data-decor></div><div class="border2" data-decor></div>
<div class="orns" data-decor><span class="tl">{corner}</span><span class="tr">{corner}</span><span class="bl">{corner}</span><span class="br">{corner}</span></div>
<div class="frame cf">
  <div class="lg">{c.logo(False)}</div>
  <div class="main grow fitbox" data-fit-box="cert">
    {c.T(title, "head ttl", fit=(c.fs(6.4), c.fs(4)))}
    {c.T(sub, "script sub", fit=(c.fs(8), c.fs(5)))}
    {c.T(presented, "body pres", fit=(c.fs(2.8), c.fs(2)))}
    {c.T(c.get("recipient", "name"), "head rec", fit=(c.fs(11), c.fs(5)))}
    <i class="rule" data-decor></i>
    {c.T(c.get("body", "description"), "body desc", fit=(c.fs(3), c.fs(2)))}
  </div>
  <div class="sigs">{date_block}{_seal(c, c.get("seal", default=(c.theme.name or "Award")))}{sg}</div>
  {c.T(c.get("id", "certificate_id"), "body cid")}
</div>"""
    css = """
.tpl-cert .border{position:absolute;inset:calc(var(--bleed) + var(--u)*4);border:calc(var(--u)*1.1) solid var(--primary)}
.tpl-cert .border2{position:absolute;inset:calc(var(--bleed) + var(--u)*6.2);border:1px solid var(--accent)}
.tpl-cert .orns span{position:absolute;width:calc(var(--u)*9);height:calc(var(--u)*9);color:var(--accent)}
.tpl-cert .orns .tl{top:calc(var(--bleed) + var(--u)*7.2);left:calc(var(--bleed) + var(--u)*7.2)}
.tpl-cert .orns .tr{top:calc(var(--bleed) + var(--u)*7.2);right:calc(var(--bleed) + var(--u)*7.2);transform:scaleX(-1)}
.tpl-cert .orns .bl{bottom:calc(var(--bleed) + var(--u)*7.2);left:calc(var(--bleed) + var(--u)*7.2);transform:scaleY(-1)}
.tpl-cert .orns .br{bottom:calc(var(--bleed) + var(--u)*7.2);right:calc(var(--bleed) + var(--u)*7.2);transform:scale(-1,-1)}
.tpl-cert .orn{width:100%;height:100%}
.tpl-cert .cf{top:calc(var(--bleed) + var(--u)*11);bottom:calc(var(--bleed) + var(--u)*10);left:calc(var(--bleed) + var(--u)*18);right:calc(var(--bleed) + var(--u)*18);align-items:center;--ta:center}
.tpl-cert .lg .logo{height:calc(var(--u)*7);object-position:center}
.tpl-cert .main{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:calc(var(--u)*1.2);width:100%}
.tpl-cert .ttl{text-transform:uppercase;letter-spacing:.28em;color:var(--primary);font-weight:700;line-height:1}
.tpl-cert .ttl.ar{letter-spacing:0}
.tpl-cert .script{font-family:'sf-script',cursive;font-weight:400;letter-spacing:0;line-height:1.1}
.tpl-cert .sub{color:var(--hl);margin-top:calc(var(--u)*-1)}
.tpl-cert .pres{text-transform:uppercase;letter-spacing:.16em;color:var(--muted);margin-top:calc(var(--u)*1.6);font-weight:600}
.tpl-cert .rec{font-weight:var(--hw);letter-spacing:-.01em;line-height:1.1;color:var(--ink)}
.tpl-cert .rule{display:block;width:calc(var(--u)*50);height:1.5px;background:var(--accent)}
.tpl-cert .desc{max-width:76%;color:var(--muted);line-height:1.55}
.tpl-cert .sigs{display:flex;align-items:flex-end;justify-content:space-between;width:100%;gap:calc(var(--u)*5)}
.tpl-cert .sig{flex:1;display:flex;flex-direction:column;align-items:center;--ta:center;max-width:calc(var(--u)*42)}
.tpl-cert .sgn{font-size:calc(var(--u)*5.4);color:var(--ink);height:calc(var(--u)*7);display:flex;align-items:flex-end}
.tpl-cert .dt{font-size:calc(var(--u)*2.8);font-weight:600;height:calc(var(--u)*5);display:flex;align-items:flex-end}
.tpl-cert .ln{display:block;width:100%;height:1px;background:var(--ink);margin:calc(var(--u)*1) 0}
.tpl-cert .snm{font-size:calc(var(--u)*2.6);font-weight:700}
.tpl-cert .srl{font-size:calc(var(--u)*2.2);color:var(--muted)}
.tpl-cert .seal{width:calc(var(--u)*19);height:calc(var(--u)*19);flex:none}
.tpl-cert .cid{font-size:calc(var(--u)*1.7);color:var(--muted);margin-top:calc(var(--u)*1.2)}
"""
    return [page(body, c, "tpl-cert o-" + c.orient)], css


# ============================================================================= menu
@template("menu", "Restaurant / café menu (A4, A3, DL, story): sections, items with dotted price leaders, bilingual names.",
          "title (restaurant), subtitle, sections[]* ({title, title_ar?, items: [{name, name_ar?, desc?, price}]}), currency, "
          "note, phone, address, website, hours", "a4, a3, dl, ig_story", mode="light", kind="print")
def t_menu(c: Ctx):
    secs = c.get("sections", default=[]) or []
    cur = c.get("currency", default="")
    n_items = sum(len(s.get("items", [])) for s in secs)
    cols = 2 if (c.orient in ("square", "landscape", "wide") or (n_items > 6 and c.w / c.h > 0.6)) else 1
    body_sz = 3.0 if cols == 2 else 3.4
    parts = []
    for s in secs:
        rows = []
        for it in s.get("items", []):
            price = str(it.get("price", ""))
            rows.append(f'<div class="it"><div class="ln1">{c.T(it.get("name", ""), "head nm", fit=(c.fs(body_sz * 1.15), c.fs(2)))}'
                        f'<i class="lead" data-decor></i>{c.T((price + (" " + cur if cur and price else "")).strip(), "head pr", fit=(c.fs(body_sz * 1.15), c.fs(2)))}</div>'
                        f'{c.T(it.get("name_ar", ""), "body nmar", fit=(c.fs(body_sz * 1.0), c.fs(1.9)))}'
                        f'{c.T(it.get("desc", ""), "body ds", fit=(c.fs(body_sz * 0.82), c.fs(1.7)))}</div>')
        st = s.get("title", "")
        parts.append(f'<section class="sec"><div class="sh">{c.T(st, "head stl", fit=(c.fs(4.8), c.fs(2.6)))}'
                     f'{c.T(s.get("title_ar", ""), "head star", fit=(c.fs(4.2), c.fs(2.4)))}</div>{"".join(rows)}</section>')
    foot = " · ".join(str(c.get(k)) for k in ("hours", "phone", "address", "website") if c.get(k))
    body = f"""
<div class="topband" data-decor></div>
<div class="frame">
  <div class="mh">
    <div class="lg">{c.logo(True)}</div>
    {c.T(c.get("title", "headline", default="Menu"), "head mt", fit=None)}
    {c.T(c.get("subtitle", "subhead"), "body ms")}
  </div>
  <div class="main grow fitbox cols-{cols}" data-fit-box="menu" data-fit-grow="1.5">{''.join(parts)}</div>
  <div class="mf">{c.T(c.get("note"), "body note")}{c.T(foot, "body fo")}</div>
</div>"""
    css = """
.tpl-menu .topband{position:absolute;top:0;left:0;right:0;height:calc(var(--pt) + var(--u)*25);background:var(--secondary)}
.tpl-menu .topband::after{content:"";position:absolute;left:0;right:0;bottom:calc(var(--u)*-1.5);height:calc(var(--u)*1.5);
  background:repeating-linear-gradient(90deg,var(--accent) 0 calc(var(--u)*3),transparent calc(var(--u)*3) calc(var(--u)*4.5))}
.tpl-menu .mh{height:calc(var(--u)*25);display:flex;flex-direction:column;justify-content:center;align-items:center;gap:calc(var(--u)*1);color:var(--paper);--ta:center}
.tpl-menu .mh .logo{height:calc(var(--u)*6);object-position:center}
.tpl-menu .mt{font-size:calc(var(--u)*9);line-height:1;letter-spacing:-.01em}
.tpl-menu .ms{font-size:calc(var(--u)*2.6);letter-spacing:.2em;text-transform:uppercase;opacity:.85}
.tpl-menu .main{padding-top:calc(var(--u)*7)}
.tpl-menu .main.cols-2{column-count:2;column-gap:calc(var(--u)*8)}
.tpl-menu .sec{break-inside:avoid;margin-bottom:calc(var(--u)*5)}
.tpl-menu .sh{display:flex;justify-content:space-between;align-items:baseline;border-bottom:2px solid var(--ink);padding-bottom:calc(var(--u)*1);margin-bottom:calc(var(--u)*2.2);gap:calc(var(--u)*2)}
.tpl-menu .stl{color:var(--hl-text);text-transform:uppercase;letter-spacing:.06em;line-height:1.1}
.tpl-menu .stl.ar{letter-spacing:0}
.tpl-menu .star{color:var(--hl-text);line-height:1.2}
.tpl-menu .it{margin-bottom:calc(var(--u)*2.2);break-inside:avoid}
.tpl-menu .ln1{display:flex;align-items:baseline;gap:calc(var(--u)*1.2)}
.tpl-menu .nm{font-weight:700;line-height:1.2;letter-spacing:0;flex:0 1 auto}
.tpl-menu .lead{flex:1 1 auto;min-width:calc(var(--u)*3);border-bottom:2px dotted var(--line);transform:translateY(-.3em)}
.tpl-menu .pr{font-weight:700;white-space:nowrap;letter-spacing:0;font-variant-numeric:tabular-nums}
.tpl-menu .nmar{color:var(--ink);opacity:.8;line-height:1.5}
.tpl-menu .ds{color:var(--muted);line-height:1.4;margin-top:calc(var(--u)*.3)}
.tpl-menu .mf{border-top:1px solid var(--line);padding-top:calc(var(--u)*2);display:flex;flex-direction:column;align-items:center;gap:calc(var(--u)*.6);--ta:center}
.tpl-menu .note{font-size:calc(var(--u)*2.3);color:var(--muted);font-style:italic}
.tpl-menu .fo{font-size:calc(var(--u)*2.5);font-weight:600}
"""
    return [page(body, c, "tpl-menu o-" + c.orient)], css


# ============================================================================= infographic
@template("infographic", "Infographic (tall 1080×2700 default, story, A3): title, key stats, numbered steps, bar chart, source.",
          "eyebrow, headline*, subhead, stats[] ({value,label}), steps[] ({title,text}), chart ({title, items:[{label,value}], unit}), "
          "source, handle", "infographic_tall (1080×2700), ig_story, a3, a4", mode="light")
def t_infographic(c: Ctx):
    k = 1.45 if c.h / c.w >= 2.2 else 0.9 if c.h / c.w >= 1.6 else 1.0
    fs = lambda n: c.fs(n * k)  # noqa: E731
    stats = c.get("stats", default=[]) or []
    steps = c.get("steps", default=[]) or []
    chart = c.get("chart", default={}) or {}
    st_html = "".join(f'<div class="st">{c.T(s.get("value", ""), "head sv", fit=(fs(11), fs(5)))}{c.T(s.get("label", ""), "body sl", fit=(fs(3.1), fs(2)))}</div>' for s in stats[:3])
    sp_html = "".join(f'<div class="sp"><span class="sn" data-decor>{i + 1}</span><div class="sb">{c.T(s.get("title", ""), "head stt", fit=(fs(4.6), fs(2.6)))}'
                      f'{c.T(s.get("text", ""), "body stx", fit=(fs(3.1), fs(2)))}</div></div>' for i, s in enumerate(steps[:6]))
    ch_html = ""
    items = chart.get("items", [])
    if items:
        mx = max(float(i.get("value", 0) or 0) for i in items) or 1
        unit = chart.get("unit", "")
        bl = []
        for i in items[:8]:
            v = float(i.get("value", 0) or 0)
            bl.append(f'<div class="bar">{c.T(i.get("label", ""), "body bl", fit=(fs(3), fs(2)))}'
                      f'<div class="track"><i style="width:{100 * v / mx:.1f}%"></i></div>'
                      f'{c.T(f"{v:g}{unit}", "head bv", fit=(fs(3.4), fs(2.2)))}</div>')
        bars = "".join(bl)
        ch_html = f'<div class="chart">{c.T(chart.get("title", ""), "head cht", fit=(fs(5.2), fs(3)))}{bars}</div>'
    body = f"""
<div class="ihead">
  <i class="circ" data-decor></i>
  <div class="top row">{c.logo(True)}<div class="grow"></div>{c.T(c.get("eyebrow", "tag"), "eyebrow eb")}</div>
  <div class="intro fitbox" data-fit-box="intro">{c.T(c.get("headline", "title"), "head h1", fit=(fs(10.5), fs(5)))}{c.T(c.get("subhead", "body"), "body sub", fit=(fs(3.6), fs(2.3)))}</div>
</div>
<div class="ibody">
  <div class="main grow fitbox" data-fit-box="info" data-fit-grow="1.35">
    {f'<div class="stats">{st_html}</div>' if st_html else ''}
    {f'<div class="steps">{sp_html}</div>' if sp_html else ''}
    {ch_html}
  </div>
  <div class="foot row">{c.T(c.get("source"), "meta muted")}<div class="grow"></div>{c.T(_handle(c), "meta")}</div>
</div>"""
    css = """
.tpl-info{display:flex;flex-direction:column}
.tpl-info .ihead{position:relative;overflow:hidden;background:var(--primary);color:var(--on-primary);
  padding:var(--pt) var(--pr) calc(var(--u)*8) var(--pl);display:flex;flex-direction:column;gap:calc(var(--u)*5);max-height:40%}
.tpl-info .circ{position:absolute;width:calc(var(--u)*34);height:calc(var(--u)*34);border-radius:50%;background:var(--accent);
  inset-inline-end:calc(var(--u)*-12);bottom:calc(var(--u)*-14)}
.tpl-info .eb{font-size:calc(var(--u)*2.5);color:var(--on-primary)}
.tpl-info .intro{position:relative;display:flex;flex-direction:column;gap:calc(var(--u)*2.5);max-width:86%;min-height:0}
.tpl-info .intro .sub{opacity:.9}
.tpl-info .ibody{flex:1;min-height:0;display:flex;flex-direction:column;padding:calc(var(--u)*7) var(--pr) var(--pb) var(--pl)}
.tpl-info .main{display:flex;flex-direction:column;justify-content:space-between;gap:calc(var(--u)*6)}
.tpl-info .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(0,1fr));gap:calc(var(--u)*3)}
.tpl-info .st{background:var(--surface);border-radius:calc(var(--u)*2.5);padding:calc(var(--u)*4) calc(var(--u)*3);display:flex;flex-direction:column;gap:calc(var(--u)*1)}
.tpl-info .sv{color:var(--hl);line-height:1;letter-spacing:-.03em}
.tpl-info .sl{color:var(--muted);line-height:1.3}
.tpl-info .steps{display:flex;flex-direction:column}
.tpl-info .sp{display:flex;gap:calc(var(--u)*4);padding-bottom:calc(var(--u)*4);position:relative}
.tpl-info .sp:not(:last-child)::before{content:"";position:absolute;inset-inline-start:calc(var(--u)*4 - 1px);top:calc(var(--u)*8);bottom:0;width:2px;background:var(--line)}
.tpl-info .sn{flex:none;width:calc(var(--u)*8);height:calc(var(--u)*8);border-radius:50%;background:var(--ink);color:var(--bg);display:grid;place-items:center;
  font-family:'sf-head';font-weight:var(--hw);font-size:calc(var(--u)*3.6)}
.tpl-info .sb{display:flex;flex-direction:column;gap:calc(var(--u)*.8);padding-top:calc(var(--u)*1.2);min-width:0}
.tpl-info .stx{color:var(--muted)}
.tpl-info .chart{display:flex;flex-direction:column;gap:calc(var(--u)*2.4);background:var(--surface);border-radius:calc(var(--u)*2.5);padding:calc(var(--u)*5)}
.tpl-info .bar{display:grid;grid-template-columns:30% 1fr auto;align-items:center;gap:calc(var(--u)*2.5)}
.tpl-info .track{height:calc(var(--u)*3.4);background:var(--line);border-radius:99px;overflow:hidden}
.tpl-info .track i{display:block;height:100%;background:var(--primary);border-radius:99px}
.tpl-info .bar:first-of-type .track i{background:var(--accent)}
.tpl-info .bv{min-width:calc(var(--u)*9);--ta:var(--end)}
.tpl-info .foot{border-top:1px solid var(--line);padding-top:calc(var(--u)*2.4);margin-top:calc(var(--u)*4)}
.tpl-info .meta{font-size:calc(var(--u)*2.4);font-weight:600}
"""
    return [page(body, c, "tpl-info o-" + c.orient)], css


# ============================================================================= letterhead
@template("letterhead", "Letterhead (A4/Letter): logo, contact block, accent rule, letter body, footer band.",
          "body (letter text, paragraphs separated by blank lines), date, recipient, subject, signoff, name, title, "
          "phone, email, website, address", "a4, letter", mode="light", kind="print")
def t_letterhead(c: Ctx):
    paras = [p for p in str(c.get("body", default="")).split("\n\n") if p.strip()]
    bf = (c.fs(1.95), c.fs(1.6))
    letter = "".join(c.T(p, "body para", fit=bf) for p in paras)
    body = f"""
<i class="corner" data-decor></i>
<div class="frame">
  <div class="lh row">{c.logo(False)}<div class="grow"></div><div class="cinfo">{_contacts(c, fit=None)}</div></div>
  <i class="rule" data-decor></i>
  <div class="main grow fitbox" data-fit-box="letter">
    {c.T(c.get("date"), "body para dt", fit=bf)}{c.T(c.get("recipient"), "body para", fit=bf)}
    {c.T(c.get("subject"), "body para subj", fit=bf)}
    {letter}
    {c.T(c.get("signoff"), "body para", fit=bf)}{c.T(c.get("name"), "body para snm", fit=bf)}{c.T(c.get("title"), "body para muted", fit=bf)}
  </div>
  <div class="lf">{c.T(c.get("footer") or (c.theme.kit or {}).get("tagline", ""), "body")}</div>
</div>"""
    css = """
.tpl-lh .corner{position:absolute;top:0;inset-inline-end:0;width:calc(var(--bleed) + var(--u)*22);height:calc(var(--bleed) + var(--u)*3);background:var(--accent)}
.tpl-lh .lh{align-items:flex-start;min-height:calc(var(--u)*10)}
.tpl-lh .lh .logo{height:calc(var(--u)*8)}
.tpl-lh .cinfo{display:flex;flex-direction:column;gap:calc(var(--u)*.6);font-size:calc(var(--u)*1.7);color:var(--muted);--ta:var(--end);align-items:flex-end}
.tpl-lh .ct{display:flex;align-items:center;gap:calc(var(--u)*.9);flex-direction:row-reverse}
.tpl-lh .ct .ic{width:calc(var(--u)*1.9);height:calc(var(--u)*1.9);color:var(--hl)}
.tpl-lh .ct .ic svg{width:100%;height:100%}
.tpl-lh .rule{display:block;height:2px;background:var(--primary);margin:calc(var(--u)*3) 0 calc(var(--u)*7)}
.tpl-lh .main{display:flex;flex-direction:column;gap:calc(var(--u)*2.2)}
.tpl-lh .para{line-height:1.6;max-width:92%}
.tpl-lh .subj{font-weight:700}
.tpl-lh .snm{font-weight:700;margin-top:calc(var(--u)*3)}
.tpl-lh .lf{border-top:1px solid var(--line);padding-top:calc(var(--u)*1.6);font-size:calc(var(--u)*1.7);color:var(--muted);--ta:center}
"""
    return [page(body, c, "tpl-lh o-" + c.orient)], css


# ============================================================================= avatar
@template("avatar", "Profile picture / app-store style avatar: brand icon or initials centred inside the circle crop.",
          "initials (optional), logo", "avatar (1080×1080), 512x512", mode="brand")
def t_avatar(c: Ctx):
    dark = c.theme.vars(c.mode)["dark"] == "1"
    src = c.logo_src(dark, "icon") or c.logo_src(dark, "monogram")
    kit = c.theme.kit or {}
    inner = f'<img class="ic" src="{src}">' if src else c.T(c.get("initials") or "".join(w[0] for w in str(kit.get("name") or c.get("brand_name") or "S").split()[:2]).upper(), "head ini")
    body = f'<div class="fill av">{inner}</div>'
    css = """
.tpl-avatar .av{display:grid;place-items:center;--ta:center}
.tpl-avatar .ic{width:52%;height:52%;object-fit:contain}
.tpl-avatar .ini{font-size:calc(var(--u)*34);letter-spacing:-.04em;line-height:1}
"""
    return [page(body, c, "tpl-avatar")], css
