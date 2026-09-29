"""Brand guidelines book: multi-page A4-landscape HTML → vector PDF + page PNGs."""
from __future__ import annotations

import html as _html
from datetime import date
from pathlib import Path

from ..design import color as col
from ..design import fonts as F
from ..design.engine import MM, Canvas, render, studio_js

E = _html.escape


def _u(p: Path) -> str:
    return p.resolve().as_uri()


def build(kit: dict, out_dir: Path, applications: list[Path] | None = None) -> tuple[Path, list[Path], dict]:
    d = Path(kit["_dir"])
    c = kit["colors"]
    f = kit["fonts"]
    files = kit["logo"]["files"]
    lg = {k: _u(d / v) for k, v in files.items()}
    name, name_ar = kit["name"], kit.get("name_ar", "")
    fontdir = out_dir / "fonts"
    used: list[Path] = []

    def url_for(p):
        used.append(p)
        return "fonts/" + p.name
    hw = f.get("head_weight", 700)
    css_fonts = [F.role_css("sf-head", f["head"], f["head_ar"], sorted({400, 700, hw}), url_for),
                 F.role_css("sf-body", f["body"], f["body_ar"], [400, 500, 700], url_for)]
    F.copy_into(used, fontdir)
    ink, paper, prim, sec, acc = c["ink"], c["paper"], c["primary"], c["secondary"], c["accent"]
    on_sec = col.text_on(sec, paper, ink)
    pages: list[str] = []
    n_total = [0]

    def pg(inner: str, bg: str = paper, fg: str = ink, section: str = "", cls: str = "") -> None:
        n_total[0] += 1
        num = n_total[0]
        foot = (f'<div class="pf"><span>{E(name)} — Brand guidelines</span><span>{E(section)}</span><span>{num:02d}</span></div>'
                if section else "")
        pages.append(f'<section class="page {cls}" style="background:{bg};color:{fg}">{inner}{foot}</section>')

    # 1 cover
    pg(f"""<div class="cover"><img class="cl" src="{lg.get('horizontal_reversed', lg.get('horizontal'))}">
<div class="ct"><div class="k">Brand guidelines</div><div class="big">{E(kit.get('tagline') or name)}</div>
{f'<div class="big ar" dir="rtl">{E(kit.get("tagline_ar") or name_ar)}</div>' if (kit.get('tagline_ar') or name_ar) else ''}
<div class="meta">Version {kit.get('version', 1)} · {date.today().strftime('%B %Y')}</div></div>
<div class="shape s1"></div><div class="shape s2"></div></div>""", bg=sec, fg=on_sec)
    # 2 contents
    toc = ["Brand essence", "Voice & tone", "Logo", "Clear space & sizes", "Logo variations", "Logo misuse",
           "Colour", "Colour accessibility", "Typography", "Applications"]
    pg(f"""<div class="two"><div><div class="k">Contents</div><h1>Everything you need to use {E(name)} consistently.</h1></div>
<ol class="toc">{''.join(f'<li><span>{i + 1:02d}</span>{t}</li>' for i, t in enumerate(toc))}</ol></div>""", section="Contents")
    # 3 essence
    pers = "".join(f'<span class="chip">{E(p)}</span>' for p in kit.get("personality", []))
    pg(f"""<div class="k">01 · Brand essence</div><div class="two"><div><h1>{E(kit.get('tagline') or name)}</h1>
<p class="lead">{E(kit.get('brief') or '')}</p></div><div class="facts">
<div><div class="k2">Sector</div><p>{E(kit.get('sector') or '—')}</p></div>
<div><div class="k2">Audience</div><p>{E(kit.get('audience') or '—')}</p></div>
<div><div class="k2">Personality</div><p>{pers or '—'}</p></div>
{f'<div><div class="k2">الاسم بالعربي</div><p class="ar" dir="rtl">{E(name_ar)}</p></div>' if name_ar else ''}</div></div>""", section="Brand essence")
    # 4 voice
    v = kit.get("voice", {})
    traits = "".join(f'<div class="trait"><h3>{E(t["trait"])}</h3><p>{E(t["means"])}</p></div>' for t in v.get("traits", []))
    sm = v.get("samples", {})
    pg(f"""<div class="k">02 · Voice & tone</div><p class="lead">{E(v.get('summary', ''))}</p><div class="traits">{traits}</div>
<div class="two dd"><div><div class="k2 ok">Do</div><ul>{''.join(f'<li>{E(x)}</li>' for x in v.get('do', []))}</ul></div>
<div><div class="k2 no">Don't</div><ul>{''.join(f'<li>{E(x)}</li>' for x in v.get('dont', []))}</ul></div></div>
<div class="samples"><div><div class="k2">Social (EN)</div><p>{E(sm.get('social_en', ''))}</p></div>
<div dir="rtl" class="ar"><div class="k2">سوشيال (مصري)</div><p>{E(sm.get('social_ar_eg', ''))}</p></div></div>""", section="Voice & tone")
    # 5 logo
    pg(f"""<div class="k">03 · Logo</div><div class="logohero"><img src="{lg['horizontal']}"></div>
<div class="three"><div class="card"><img src="{lg['stacked']}"><p>Stacked — square spaces, profile images, merchandise.</p></div>
<div class="card"><img src="{lg['icon']}"><p>Icon — app icon, favicon, avatars, small sizes.</p></div>
<div class="card"><img src="{lg['wordmark']}"><p>Wordmark — when the name must be read first.</p></div></div>""", section="Logo")
    # 6 clear space
    pg(f"""<div class="k">04 · Clear space & minimum size</div><div class="two"><div class="cs"><div class="csbox"><img src="{lg['horizontal']}"></div></div>
<div><h2>Give it room.</h2><p>Keep clear space around the logo equal to the height of the icon's <b>x</b> — about a third of the icon height.
Nothing (text, edges, other logos) enters that area.</p>
<div class="mins"><div><img src="{lg['horizontal']}" style="width:{30 * MM:.0f}px"><span>Horizontal: min 30 mm / 120 px wide</span></div>
<div><img src="{lg['icon']}" style="width:{8 * MM:.0f}px"><span>Icon: min 8 mm / 24 px</span></div></div></div></div>""", section="Clear space")
    # 7 variations
    var = [("horizontal", paper, "Full colour on paper"), ("horizontal_reversed", sec, "Reversed on the dark brand colour"),
           ("horizontal_reversed", prim, "Reversed on primary"), ("horizontal_mono_black", "#ffffff", "One colour — black"),
           ("horizontal_mono_white", "#111111", "One colour — white"), ("app_icon", "#EEEDEA", "App icon / favicon")]
    pg(f"""<div class="k">05 · Logo variations</div><div class="vars">{''.join(
        f'<div class="var"><div class="vb" style="background:{bg}"><img src="{lg.get(k, lg["horizontal"])}" style="{"max-height:48%" if k == "app_icon" else ""}"></div><p>{t}</p></div>'
        for k, bg, t in var)}</div>""", section="Variations")
    # 8 misuse
    mis = [("transform:scaleX(1.45)", "Don't stretch or squash"), ("filter:hue-rotate(140deg)", "Don't recolour"),
           ("transform:rotate(-14deg)", "Don't rotate"), ("filter:drop-shadow(6px 8px 3px rgba(0,0,0,.55))", "Don't add effects"),
           ("", "Don't place on busy images"), ("opacity:.35", "Don't lower contrast")]
    cells = []
    for i, (st, t) in enumerate(mis):
        bg = "background:linear-gradient(135deg,#e96,#69c 40%,#fc3 70%,#396)" if i == 4 else f"background:{paper}"
        cells.append(f'<div class="var"><div class="vb" style="{bg}"><img src="{lg["horizontal"]}" style="{st}"><i class="x"></i></div><p>{t}</p></div>')
    pg(f'<div class="k">06 · Logo misuse</div><div class="vars">{"".join(cells)}</div>', section="Misuse")
    # 9 colour
    roles = [("primary", "Primary", 30), ("secondary", "Secondary", 25), ("accent", "Accent", 10), ("ink", "Ink", 15), ("paper", "Paper", 20)]
    sw = "".join(f"""<div class="sw" style="flex:{w}"><div class="swc" style="background:{c[r]};color:{col.text_on(c[r])}"><b>{lab}</b><span>{w}%</span></div>
<div class="swt"><b>{c[r].upper()}</b><br>RGB {' '.join(map(str, col.hex_rgb(c[r])))}<br>CMYK {' '.join(map(str, col.cmyk(c[r])))}</div></div>""" for r, lab, w in roles)
    pg(f"""<div class="k">07 · Colour</div><h2>Our palette — {kit['palette']['harmony']} harmony</h2><div class="sws">{sw}</div>
<p class="note">Widths show the proportion of use. CMYK values are a starting point — proof with your printer's profile.</p>""", section="Colour")
    # 10 accessibility
    pr = "".join(f"""<div class="pair" style="background:{p['bg_hex']};color:{p['fg_hex']}"><span class="aa">Aa عربي</span>
<span>{p['fg']} / {p['bg']}<br><b>{p['ratio']}:1 · {p['normal_text']}</b></span></div>""" for p in kit["palette"]["pairs"][:10])
    pg(f"""<div class="k">08 · Colour accessibility</div><h2>Text colour pairs, measured (WCAG 2.x).</h2>
<p>AA needs 4.5:1 for body text and 3:1 for large headlines. Pairs marked <b>fail</b> are for decoration only.</p><div class="pairs">{pr}</div>""", section="Accessibility")
    # 11 typography
    pg(f"""<div class="k">09 · Typography</div><div class="two"><div>
<div class="k2">Headlines · {E(f['head'])} {hw}</div><div class="spec head">Aa Bb Cc 123</div>
<div class="k2">Body · {E(f['body'])}</div><p class="body-s">The quick brown fox jumps over the lazy dog. Clear, generous line spacing and one or two weights keep everything readable.</p>
<div class="scale"><div class="h1s">Headline 1</div><div class="h2s">Headline 2</div><div class="bs">Body text 16 / 24</div><div class="cs2">CAPTION · LABEL</div></div></div>
<div dir="rtl" class="ar"><div class="k2">العناوين · {E(f['head_ar'])}</div><div class="spec head">أبجد هوز ١٢٣</div>
<div class="k2">النصوص · {E(f['body_ar'])}</div><p class="body-s">الخط الجيد لا يلفت الانتباه لنفسه، بل يترك الرسالة تتكلم بوضوح. استخدم وزنين فقط ومسافات مريحة بين السطور.</p></div></div>""", section="Typography")
    # 12 applications
    apps = applications or []
    if apps:
        pg(f"""<div class="k">10 · Applications</div><div class="apps">{''.join(f'<div class="app"><img src="{_u(a)}"></div>' for a in apps[:6])}</div>""",
           section="Applications")
    # back cover
    pg(f"""<div class="cover back"><img class="cl" src="{lg.get('stacked_reversed', lg['horizontal'])}"><div class="meta">{E(kit.get('tagline') or '')}</div></div>""",
       bg=prim, fg=col.text_on(prim, paper, ink))

    css = f"""{chr(10).join(css_fonts)}
*{{box-sizing:border-box;margin:0;padding:0}} html,body{{margin:0}}
.page{{position:relative;width:{297 * MM:.2f}px;height:{210 * MM:.2f}px;overflow:hidden;padding:{16 * MM:.1f}px {18 * MM:.1f}px;
 font-family:'sf-body';font-size:13.5px;line-height:1.55;-webkit-font-smoothing:antialiased}}
h1{{font:{hw} 42px/1.08 'sf-head';letter-spacing:-.02em;margin:10px 0 18px;text-wrap:balance}}
h2{{font:{hw} 26px/1.15 'sf-head';letter-spacing:-.01em;margin:6px 0 14px}}
h3{{font:700 16px/1.3 'sf-head';margin-bottom:6px}}
p{{margin-bottom:10px;text-wrap:pretty}} .lead{{font-size:18px;line-height:1.5;max-width:560px}}
.k{{font:700 11px/1 'sf-body';letter-spacing:.18em;text-transform:uppercase;color:{c['primary_text'] if col.contrast(c['primary_text'], paper) >= 4.5 else ink};margin-bottom:14px}}
.k2{{font:700 10.5px/1 'sf-body';letter-spacing:.14em;text-transform:uppercase;color:{c['muted']};margin:14px 0 8px}}
.ar{{font-family:'sf-body';line-height:1.8}} .ar .k2{{letter-spacing:0}}
.pf{{position:absolute;left:{18 * MM:.1f}px;right:{18 * MM:.1f}px;bottom:{8 * MM:.1f}px;display:flex;justify-content:space-between;font-size:9.5px;color:{c['muted']};letter-spacing:.04em}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:start}}
.three{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:22px}}
.card{{background:#fff;border-radius:12px;padding:20px;height:170px;display:flex;flex-direction:column;justify-content:space-between}}
.card img{{max-height:84px;max-width:80%;object-fit:contain;object-position:left center}} .card p{{font-size:11.5px;color:{c['muted']};margin:0}}
.cover{{position:absolute;inset:0;padding:{20 * MM:.1f}px;display:flex;flex-direction:column;justify-content:space-between}}
.cover .cl{{height:64px;width:auto;align-self:flex-start;position:relative;z-index:2}}
.cover .ct{{position:relative;z-index:2}} .cover .k{{color:{acc if col.contrast(acc, sec) >= 3 else on_sec}}}
.cover .big{{font:{hw} 58px/1.02 'sf-head';letter-spacing:-.025em;max-width:70%;text-wrap:balance}}
.cover .big.ar{{font-size:40px;line-height:1.4;margin-top:8px;letter-spacing:0}}
.cover .meta{{margin-top:18px;opacity:.75;font-size:12px}}
.cover .shape{{position:absolute;border-radius:50%}}
.cover .s1{{width:{150 * MM:.0f}px;height:{150 * MM:.0f}px;right:-{45 * MM:.0f}px;top:-{50 * MM:.0f}px;background:{prim};opacity:.9}}
.cover .s2{{width:{36 * MM:.0f}px;height:{36 * MM:.0f}px;right:{70 * MM:.0f}px;top:{78 * MM:.0f}px;background:{acc}}}
.back{{align-items:center;justify-content:center;gap:20px}} .back .cl{{height:170px;align-self:center}}
.toc{{list-style:none;columns:1;font:600 17px/1 'sf-head'}} .toc li{{display:flex;gap:18px;padding:11px 0;border-bottom:1px solid {c['line']}}}
.toc span{{color:{c['muted']};font-variant-numeric:tabular-nums;width:26px}}
.facts>div{{border-top:1px solid {c['line']};padding-top:4px}}
.chip{{display:inline-block;border:1px solid {ink};border-radius:99px;padding:3px 11px;margin:0 6px 6px 0;font-size:12px;font-weight:600}}
.traits{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:14px 0}}
.trait{{background:#fff;border-radius:12px;padding:16px}} .trait p{{font-size:12px;color:{c['muted']};margin:0}}
.dd ul{{padding-left:18px;font-size:12.5px}} .ok{{color:#1a7f37}} .no{{color:#c62828}}
.samples{{display:grid;grid-template-columns:1fr 1fr;gap:30px;margin-top:10px;font-size:14px}}
.logohero{{background:#fff;border-radius:14px;height:{88 * MM:.0f}px;display:grid;place-items:center}} .logohero img{{max-height:46%;max-width:66%}}
.cs{{display:grid;place-items:center;height:{120 * MM:.0f}px;background:#fff;border-radius:14px}}
.csbox{{outline:2px dashed {acc};outline-offset:26px;padding:0;background:repeating-linear-gradient(45deg,transparent 0 6px,rgba(0,0,0,.03) 6px 12px)}}
.csbox img{{height:{22 * MM:.0f}px;display:block}}
.mins{{display:flex;flex-direction:column;gap:16px;margin-top:18px}} .mins div{{display:flex;align-items:center;gap:16px;font-size:12px;color:{c['muted']}}}
.vars{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:6px}}
.var p{{font-size:11.5px;color:{c['muted']};margin-top:6px}}
.vb{{position:relative;height:{50 * MM:.0f}px;border-radius:12px;display:grid;place-items:center;overflow:hidden;border:1px solid rgba(0,0,0,.06)}}
.vb img{{max-width:62%;max-height:40%}}
.x{{position:absolute;right:10px;top:10px;width:26px;height:26px;border-radius:50%;background:#c62828}}
.x::before,.x::after{{content:"";position:absolute;left:6px;top:12px;width:14px;height:2.5px;background:#fff;transform:rotate(45deg)}} .x::after{{transform:rotate(-45deg)}}
.sws{{display:flex;gap:12px;margin-top:16px;height:{105 * MM:.0f}px}} .sw{{display:flex;flex-direction:column;min-width:0}}
.swc{{flex:1;border-radius:12px;padding:14px;display:flex;flex-direction:column;justify-content:space-between;border:1px solid rgba(0,0,0,.06)}}
.swc b{{font-size:15px}} .swc span{{font-size:22px;font-weight:700}}
.swt{{font-size:11px;line-height:1.5;margin-top:8px}} .note{{font-size:11px;color:{c['muted']};margin-top:14px}}
.pairs{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:10px}}
.pair{{border-radius:10px;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;font-size:11px;border:1px solid rgba(0,0,0,.06)}}
.pair .aa{{font:700 22px 'sf-head'}}
.spec{{font-size:64px;line-height:1.15;letter-spacing:-.02em}} .spec.head{{font-family:'sf-head';font-weight:{hw}}} .ar .spec{{letter-spacing:0;line-height:1.5}}
.body-s{{font-size:15px;max-width:440px}}
.scale{{margin-top:16px;border-top:1px solid {c['line']};padding-top:12px}} .h1s{{font:{hw} 34px/1.2 'sf-head'}} .h2s{{font:{hw} 22px/1.3 'sf-head'}}
.bs{{font-size:14px}} .cs2{{font-size:10px;letter-spacing:.16em;font-weight:700;color:{c['muted']}}}
.apps{{display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:1fr 1fr;gap:14px;height:{150 * MM:.0f}px}}
.app{{background:#fff;border-radius:12px;overflow:hidden;padding:12px;min-height:0;position:relative}} .app img{{position:absolute;inset:12px;width:calc(100% - 24px);height:calc(100% - 24px);object-fit:contain;filter:drop-shadow(0 4px 12px rgba(0,0,0,.14))}}
"""
    html = f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>{"".join(pages)}<script>{studio_js()}</script></body></html>'
    out_dir.mkdir(parents=True, exist_ok=True)
    idx = out_dir / "index.html"
    idx.write_text(html, encoding="utf-8")
    cv = Canvas(297 * MM, 210 * MM, "a4_landscape", print_=True, bleed_mm=0, dpi=110)
    n = len(pages)
    pdf = out_dir.parent / f"{kit['slug']}-brand-guidelines.pdf"
    k = 2
    while pdf.exists():
        pdf = out_dir.parent / f"{kit['slug']}-brand-guidelines-{k}.pdf"
        k += 1
    pngs = [out_dir / f"page-{i + 1:02d}.png" for i in range(n)]
    rep = render(idx, cv, {"pdf": pdf, "png": pngs}, pages=n, qc=False)
    return pdf, pngs, rep
