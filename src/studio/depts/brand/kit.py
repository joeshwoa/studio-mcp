"""Brand kit model: brief → palette + type pairing + voice + logo concepts, saved as
STUDIO_HOME/brands/<slug>/brand.json (the file every other tool reads via `brand=<slug>`).

brand.json (paths relative to the kit folder):
{
  "name", "name_ar", "slug", "tagline", "tagline_ar", "brief", "sector", "audience", "personality": [...],
  "language": "en|ar|bilingual",
  "colors": {primary, secondary, accent, ink, paper, surface, muted, line, on_primary, on_accent, ...},
  "palette": {"harmony", "ramps": {...}, "pairs": [{fg,bg,ratio,normal_text,large_text}]},
  "fonts": {"head", "body", "head_ar", "body_ar", "head_weight", "pair"},
  "logo": {"concept": {...}, "concepts": [...], "files": {"horizontal": "logo/…svg", ...}, "png": {...}},
  "voice": {"summary", "traits": [...], "do": [...], "dont": [...], "words_we_use": [...], "samples": {...}},
  "version": 1, "updated": iso-date
}
"""
from __future__ import annotations

import html as _html
import json
from datetime import date
from pathlib import Path

from PIL import Image

from ...config import brands_dir, slug as _slug
from ...core.result import ToolError
from ..design import color as col
from ..design import fonts as F
from ..design.engine import with_browser
from . import logo as L

# ------------------------------------------------------------------------------------------ voice
TRAITS = {
    "friendly": ("Warm, not sugary", "We talk like a helpful friend: first person, short sentences, plain words.",
                 "Say 'you' and 'we'", "Stack exclamation marks or emojis"),
    "professional": ("Confident, not cold", "Clear claims backed by specifics; no jargon we wouldn't say out loud.",
                     "Lead with the outcome, then the proof", "Hide behind buzzwords"),
    "modern": ("Fresh, not trendy", "Current language, clean structure, no slang that will date in a year.",
               "Use short headlines and active verbs", "Chase every meme"),
    "tech": ("Precise and human", "Explain the technology through what it does for people.",
             "Use concrete numbers and examples", "Assume the reader knows the acronyms"),
    "luxury": ("Quiet confidence", "Fewer words, chosen carefully; let the product carry the weight.",
               "Use sensory, specific detail", "Shout discounts or use ALL CAPS urgency"),
    "playful": ("Fun, never silly", "A wink in the copy, a surprising word, but the message stays clear.",
                "Play with rhythm and wordplay", "Joke about the customer's problem"),
    "bold": ("Direct and energetic", "Strong verbs, short lines, a clear call to action every time.",
             "Make one clear promise", "Hedge with 'maybe' and 'kind of'"),
    "trustworthy": ("Honest and steady", "We say what we do, show how, and never overpromise.",
                    "Be specific about process and guarantees", "Use fear to sell"),
    "heritage": ("Proud and generous", "We honour where we come from and invite people in.",
                 "Tell origin stories and name real places", "Sound museum-dusty"),
    "caring": ("Kind and reassuring", "Calm, patient language that lowers stress.",
               "Acknowledge feelings, then guide", "Rush or blame"),
    "creative": ("Curious and original", "Unexpected angles, fresh metaphors, a point of view.",
                 "Show the idea, not the adjectives", "Explain the joke"),
    "educational": ("Clear teacher", "Break things into steps; one idea per sentence.",
                    "Use examples and numbered steps", "Talk down to learners"),
}
ALIASES = {"warm": "friendly", "approachable": "friendly", "fun": "playful", "young": "playful", "premium": "luxury",
           "elegant": "luxury", "corporate": "professional", "serious": "professional", "innovative": "tech",
           "smart": "tech", "energetic": "bold", "confident": "bold", "reliable": "trustworthy", "authentic": "heritage",
           "traditional": "heritage", "kind": "caring", "health": "caring", "artsy": "creative", "education": "educational",
           "clean": "modern", "minimal": "modern"}


def voice_for(name: str, personality: list[str], audience: str, sector: str, tagline: str = "") -> dict:
    keys = []
    for p in personality:
        k = ALIASES.get(p.lower().strip(), p.lower().strip())
        if k in TRAITS and k not in keys:
            keys.append(k)
    for fallback in ("modern", "friendly", "professional"):
        if len(keys) >= 3:
            break
        if fallback not in keys:
            keys.append(fallback)
    traits = [{"trait": TRAITS[k][0], "means": TRAITS[k][1]} for k in keys[:4]]
    do = [TRAITS[k][2] for k in keys[:4]] + ["Write Arabic in natural Egyptian dialect for social, Modern Standard Arabic for formal documents",
                                              "Keep one message per post"]
    dont = [TRAITS[k][3] for k in keys[:4]] + ["Mix three languages in one line", "Use more than one exclamation mark"]
    summary = (f"{name} sounds {', '.join(t['trait'].split(',')[0].lower() for t in traits[:3])}. "
               f"We speak to {audience or 'our audience'} as {('peers' if 'professional' in keys else 'friends')}"
               f"{', in ' + sector if sector else ''}.")
    samples = {
        "headline_en": tagline or f"{name}: made for {audience.split(',')[0] if audience else 'you'}.",
        "social_en": "New this week — and we think you'll love it. Tap the link to see how it works.",
        "social_ar_eg": "جديد الأسبوع ده — وإحنا متأكدين إنه هيعجبك. دوس على اللينك وشوف بنفسك.",
        "cta_en": "Get started", "cta_ar_eg": "ابدأ دلوقتي",
        "formal_ar": "يسعدنا تقديم خدماتنا لكم، ونتطلع إلى التعاون معكم.",
    }
    return {"summary": summary, "traits": traits, "do": do, "dont": dont, "samples": samples,
            "note": "Starter voice notes generated from the brief — review and rewrite in your own words."}


# ------------------------------------------------------------------------------------------ io

def kit_dir(slug: str) -> Path:
    d = brands_dir() / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(kit: dict) -> Path:
    kit = {k: v for k, v in kit.items() if not k.startswith("_")}
    kit["updated"] = date.today().isoformat()
    p = kit_dir(kit["slug"]) / "brand.json"
    if p.exists():  # keep one backup of the previous version
        (p.parent / "brand.prev.json").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    p.write_text(json.dumps(kit, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(brand: str) -> dict:
    from ..design.builder import load_brand
    kit = load_brand(brand)
    if not kit:
        raise ToolError("no brand given")
    return kit


def all_brands() -> list[dict]:
    out = []
    for d in sorted(brands_dir().iterdir()):
        p = d / "brand.json"
        if p.exists():
            try:
                k = json.loads(p.read_text(encoding="utf-8"))
                out.append({"slug": k.get("slug", d.name), "name": k.get("name"), "name_ar": k.get("name_ar", ""),
                            "sector": k.get("sector", ""), "updated": k.get("updated", ""), "path": str(p)})
            except Exception:
                continue
    return out


# ------------------------------------------------------------------------------------------ rendering

def svgs_to_png(jobs: list[tuple[Path, Path, int, str | None]]) -> list[Path]:
    """Rasterise SVGs with Chromium: (svg, png, width_px, background or None). Height follows the viewBox."""
    def job(browser):
        ctx = browser.new_context(device_scale_factor=1)
        page = ctx.new_page()
        done = []
        for svg, png, w, bg in jobs:
            txt = Path(svg).read_text(encoding="utf-8")
            import re
            m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', txt)
            vw, vh = (float(m.group(1)), float(m.group(2))) if m else (100, 100)
            h = max(1, round(w * vh / vw))
            page.set_viewport_size({"width": int(w), "height": int(h)})
            import base64
            uri = "data:image/svg+xml;base64," + base64.b64encode(txt.encode("utf-8")).decode()
            page.set_content(f'<html><body style="margin:0;background:{bg or "transparent"}">'
                             f'<img src="{uri}" style="display:block;width:{w}px;height:{h}px"></body></html>')
            page.wait_for_function("[...document.images].every(i => i.complete && i.naturalWidth > 0)", timeout=10000)
            page.screenshot(path=str(png), omit_background=bg is None, clip={"x": 0, "y": 0, "width": int(w), "height": int(h)})
            done.append(Path(png))
        ctx.close()
        return done
    return with_browser(job)


def write_suite(kit: dict, concept: L.Concept) -> dict:
    """Write every logo SVG + PNG/ICO sizes for a concept into <kit>/logo/. Returns files map (relative)."""
    d = kit_dir(kit["slug"])
    ld = d / "logo"
    ld.mkdir(exist_ok=True)
    for old in ld.glob("*"):
        old.unlink()
    svgs = L.build_svgs(concept, kit["name"], kit.get("name_ar", ""), kit.get("initials") or L.initials_of(kit["name"]),
                        kit["colors"])
    files = {}
    for k, s in svgs.items():
        p = ld / f"{kit['slug']}-{k.replace('_', '-')}.svg"
        p.write_text(s, encoding="utf-8")
        files[k] = f"logo/{p.name}"
    # PNGs: lockups at 2000 px, icons at app sizes
    jobs = []
    for k in ("horizontal", "horizontal_reversed", "stacked", "stacked_reversed", "icon", "icon_reversed", "wordmark",
              "horizontal_mono_black", "horizontal_mono_white"):
        if k in svgs:
            jobs.append((d / files[k], ld / f"{kit['slug']}-{k.replace('_', '-')}.png", 2000 if "icon" not in k else 1024, None))
    sizes = {"favicon-16": 16, "favicon-32": 32, "favicon-48": 48, "apple-touch-icon": 180, "icon-192": 192, "icon-512": 512}
    for n, s in sizes.items():
        jobs.append((d / files["favicon" if s <= 48 else "app_icon"], ld / f"{n}.png", s, None))
    pngs = svgs_to_png(jobs)
    png_map = {p.stem.replace(f"{kit['slug']}-", "").replace("-", "_"): f"logo/{p.name}" for p in pngs}
    ico = ld / "favicon.ico"
    Image.open(ld / "favicon-48.png").save(ico, sizes=[(16, 16), (32, 32), (48, 48)])
    png_map["favicon_ico"] = f"logo/{ico.name}"
    return {"files": files, "png": png_map}


def sheet_html(title: str, blocks: list[str], kit: dict, width: int = 1800) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#EDEBE7;font-family:'Inter',Helvetica,Arial,sans-serif;color:#1c1c1c}}
.page{{width:{width}px;padding:48px;box-sizing:border-box;background:#EDEBE7}}
h1{{font:700 30px/1.2 Helvetica,Arial,sans-serif;margin:0 0 28px;letter-spacing:-.01em}}
.grid{{display:grid;gap:22px}}
.card{{background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 1px 0 rgba(0,0,0,.05)}}
.lab{{font:600 15px/1.3 Helvetica,Arial,sans-serif;color:#555;padding:14px 20px;border-top:1px solid #eee}}
.lab b{{color:#111}}
</style></head><body><section class="page"><h1>{_html.escape(title)}</h1>{''.join(blocks)}</section></body></html>"""


def render_sheet(html: str, dest: Path, width: int = 1800) -> Path:
    tmp = dest.with_suffix(".html")
    tmp.write_text(html, encoding="utf-8")

    def job(browser):
        ctx = browser.new_context(viewport={"width": width, "height": 800})
        pg = ctx.new_page()
        pg.goto(tmp.resolve().as_uri(), wait_until="load")
        pg.wait_for_timeout(150)
        pg.locator(".page").screenshot(path=str(dest))
        ctx.close()
    with_browser(job)
    tmp.unlink(missing_ok=True)
    return dest


def concepts_sheet(kit: dict, concepts: list[L.Concept], dest: Path) -> Path:
    """One card per concept: horizontal lockup on paper, reversed on the dark brand colour, app icon."""
    tmpd = dest.parent / "_concepts"
    tmpd.mkdir(exist_ok=True)
    c = kit["colors"]
    blocks = []
    for cc in concepts:
        s = L.build_svgs(cc, kit["name"], kit.get("name_ar", ""), kit.get("initials") or L.initials_of(kit["name"]), c)
        paths = {}
        for k in ("horizontal", "horizontal_reversed", "app_icon", "stacked"):
            p = tmpd / f"{cc.id}-{k}.svg"
            p.write_text(s[k], encoding="utf-8")
            paths[k] = p.resolve().as_uri()
        blocks.append(f"""<div class="card"><div style="display:grid;grid-template-columns:1.25fr 1.25fr .8fr .9fr;height:300px">
 <div style="background:{c['paper']};display:grid;place-items:center;padding:36px"><img src="{paths['horizontal']}" style="max-width:100%;max-height:150px"></div>
 <div style="background:{c['secondary']};display:grid;place-items:center;padding:36px"><img src="{paths['horizontal_reversed']}" style="max-width:100%;max-height:150px"></div>
 <div style="background:#fff;display:grid;place-items:center;padding:30px"><img src="{paths['stacked']}" style="max-width:100%;max-height:220px"></div>
 <div style="background:#F4F4F2;display:grid;place-items:center"><img src="{paths['app_icon']}" style="width:150px;height:150px;filter:drop-shadow(0 8px 18px rgba(0,0,0,.18))"></div>
</div><div class="lab"><b>Concept {cc.id[1:]}</b> — mark: {cc.mark.replace('_', ' ')} · wordmark: {cc.font} {cc.weight} {cc.case}{' + accent dot' if cc.dot else ''} · Arabic: {cc.font_ar}</div></div>""")
    html = sheet_html(f"{kit['name']} — logo concepts (choose one: brand_choose_logo)", [f'<div class="grid">{"".join(blocks)}</div>'], kit)
    out = render_sheet(html, dest)
    for p in tmpd.glob("*"):
        p.unlink()
    tmpd.rmdir()
    return out


def palette_sheet(kit: dict, dest: Path) -> Path:
    c = kit["colors"]
    roles = [("primary", "Primary"), ("secondary", "Secondary"), ("accent", "Accent"), ("ink", "Ink / text"),
             ("paper", "Paper / background"), ("surface", "Surface"), ("muted", "Muted text"), ("line", "Lines")]
    sw = []
    for r, label in roles:
        h = c[r]
        rgb = col.hex_rgb(h)
        cm = col.cmyk(h)
        fg = col.text_on(h)
        sw.append(f"""<div class="card"><div style="background:{h};height:220px;padding:22px;box-sizing:border-box;color:{fg};
 font:700 22px Helvetica,Arial">{label}</div><div class="lab"><b>{h.upper()}</b><br>RGB {rgb[0]} {rgb[1]} {rgb[2]} · CMYK {cm[0]} {cm[1]} {cm[2]} {cm[3]}</div></div>""")
    pairs = []
    for p in kit["palette"]["pairs"]:
        ok = p["normal_text"] != "fail"
        pairs.append(f"""<div style="background:{p['bg_hex']};color:{p['fg_hex']};border-radius:14px;padding:18px 20px;display:flex;justify-content:space-between;align-items:center">
 <span style="font:700 26px Helvetica,Arial">Aa عربي</span><span style="font:600 15px Helvetica,Arial">{p['fg']} on {p['bg']} · {p['ratio']}:1 · {p['normal_text']}{'' if ok else ' (large ' + p['large_text'] + ')'}</span></div>""")
    ramps = []
    for name, ramp in kit["palette"]["ramps"].items():
        ramps.append('<div style="display:flex;border-radius:12px;overflow:hidden">' + "".join(
            f'<div style="flex:1;height:64px;background:{v};color:{col.text_on(v)};font:600 12px Helvetica;padding:8px">{k}<br>{v}</div>'
            for k, v in ramp.items()) + "</div>")
    html = sheet_html(f"{kit['name']} — colour palette ({kit['palette']['harmony']})", [
        f'<div class="grid" style="grid-template-columns:repeat(4,1fr)">{"".join(sw)}</div>',
        f'<h1 style="margin-top:36px">Tints & shades</h1><div class="grid">{"".join(ramps)}</div>',
        f'<h1 style="margin-top:36px">Text pairs (WCAG 2.x)</h1><div class="grid" style="grid-template-columns:repeat(2,1fr)">{"".join(pairs)}</div>'], kit)
    return render_sheet(html, dest)


def type_sheet(kit: dict, dest: Path) -> Path:
    f = kit["fonts"]
    fams = [(f["head"], [f.get("head_weight", 700)]), (f["body"], [400, 700]), (f["head_ar"], [700]), (f["body_ar"], [400, 700])]
    css = []
    for fam, ws in fams:
        got = F.ensure(fam, ws + [400])
        for w, p in got.items():
            css.append(f"@font-face{{font-family:'{fam}';font-weight:{w};src:url('{p.resolve().as_uri()}')}}")
    name, name_ar = kit["name"], kit.get("name_ar") or "هوية بصرية واضحة"
    hw = f.get("head_weight", 700)
    block = f"""<style>{''.join(css)}</style>
<div class="grid" style="grid-template-columns:1fr 1fr">
 <div class="card" style="padding:40px"><div class="lab" style="border:0;padding:0 0 12px">Latin headline — <b>{f['head']} {hw}</b></div>
  <div style="font:{hw} 84px/1.02 '{f['head']}';letter-spacing:-.02em">{_html.escape(kit.get('tagline') or name)}</div>
  <div class="lab" style="border:0;padding:28px 0 12px">Latin body — <b>{f['body']} 400/700</b></div>
  <div style="font:400 24px/1.5 '{f['body']}'">{_html.escape(kit.get('brief') or 'Good typography is invisible: it lets the message speak.')[:220]}</div></div>
 <div class="card" style="padding:40px;direction:rtl;text-align:right"><div class="lab" style="border:0;padding:0 0 12px;direction:ltr;text-align:left">Arabic headline — <b>{f['head_ar']}</b></div>
  <div style="font:700 80px/1.3 '{f['head_ar']}'">{_html.escape(kit.get('tagline_ar') or name_ar)}</div>
  <div class="lab" style="border:0;padding:28px 0 12px;direction:ltr;text-align:left">Arabic body — <b>{f['body_ar']} 400/700</b></div>
  <div style="font:400 26px/1.8 '{f['body_ar']}'">الخط الجيد لا يلفت الانتباه لنفسه، بل يترك الرسالة تتكلم. نص تجريبي يوضح شكل الفقرات العربية مع English words داخل السطر.</div></div>
</div>"""
    return render_sheet(sheet_html(f"{kit['name']} — typography", [block], kit), dest)


def suite_sheet(kit: dict, dest: Path) -> Path:
    d = Path(kit.get("_dir") or kit_dir(kit["slug"]))
    files = kit["logo"]["files"]
    c = kit["colors"]
    items = [("horizontal", c["paper"]), ("horizontal_reversed", c["secondary"]), ("stacked", "#ffffff"),
             ("stacked_reversed", c["primary"]), ("icon", c["paper"]), ("icon_reversed", c["secondary"]),
             ("horizontal_mono_black", "#ffffff"), ("horizontal_mono_white", "#111111"), ("wordmark", c["paper"]),
             ("monogram", "#ffffff"), ("app_icon", "#F1F1EF"), ("wordmark_ar", c["paper"])]
    cards = []
    for k, bg in items:
        if k in files:
            cards.append(f'<div class="card"><div style="background:{bg};height:260px;display:grid;place-items:center;padding:34px">'
                         f'<img src="{(d / files[k]).resolve().as_uri()}" style="max-width:100%;max-height:190px"></div>'
                         f'<div class="lab"><b>{k.replace("_", " ")}</b> · {Path(files[k]).name}</div></div>')
    html = sheet_html(f"{kit['name']} — logo suite", [f'<div class="grid" style="grid-template-columns:repeat(4,1fr)">{"".join(cards)}</div>'], kit)
    return render_sheet(html, dest)


# ------------------------------------------------------------------------------------------ creation

def make_kit(name: str, brief: str = "", sector: str = "", audience: str = "", personality: list[str] | None = None,
             name_ar: str = "", tagline: str = "", tagline_ar: str = "", language: str = "", base_color: str = "",
             harmony: str = "auto", pair: str = "", fonts: dict | None = None, slug: str = "") -> dict:
    personality = [p.strip() for p in (personality or []) if p.strip()]
    text = " ".join([brief, sector, audience, " ".join(personality)])
    base = col.norm(base_color) if base_color else col.base_from_brief(text)
    pal = col.build_palette(base, harmony, text)
    pr = next((p for p in F.PAIRS if p["id"] == pair), None) if pair else None
    pr = dict(pr) if pr else F.pair_for(personality + sector.split() + brief.lower().split())
    fnt = {"head": pr["head"], "body": pr["body"], "head_ar": pr["head_ar"], "body_ar": pr["body_ar"],
           "head_weight": pr.get("head_weight", 700), "pair": pr["id"]}
    for k, v in (fonts or {}).items():
        if v:
            fnt[k] = v if k != "head_weight" else int(v)
    lang = language or ("bilingual" if name_ar else "en")
    return {"name": name, "name_ar": name_ar, "slug": slug or _slug(name), "tagline": tagline, "tagline_ar": tagline_ar,
            "brief": brief, "sector": sector, "audience": audience, "personality": personality, "language": lang,
            "initials": L.initials_of(name),
            "colors": pal["roles"], "palette": {"harmony": pal["harmony"], "ramps": pal["ramps"], "pairs": pal["pairs"], "base": base},
            "fonts": fnt, "voice": voice_for(name, personality, audience, sector, tagline), "logo": {}, "version": 1,
            "_pair": pr}
