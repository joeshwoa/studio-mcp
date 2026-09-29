"""Brand department: identity from a brief — palette (harmonies + WCAG pairs), Arabic+Latin type
pairing, logo concepts and a full SVG logo suite, voice & tone, a guidelines PDF, and a starter asset
set. Kits live in STUDIO_HOME/brands/<slug>/brand.json; every design tool accepts `brand=<slug>`."""
from __future__ import annotations

import html as _html
import json
import shutil
from pathlib import Path

from ...config import output_dir, slug as _slug, unique_path
from ...core import qc
from ...core.registry import tool
from ...core.result import Result, ToolError
from ..design import color as col
from ..design import fonts as F
from ..design.engine import with_browser
from . import kit as K
from . import logo as L


def _json(v, name):
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        if v[:1] in "[{":
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ToolError(f"{name} is not valid JSON: {e}")
        return v
    return v


def _list(v) -> list[str]:
    v = _json(v, "list")
    if not v:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
    return [str(x) for x in v]


def _concepts(kit: dict) -> list[L.Concept]:
    return [L.Concept(**c) for c in kit.get("logo", {}).get("concepts", [])]


def _choose(kit: dict, cid: str) -> dict:
    cs = _concepts(kit)
    if not cs:
        raise ToolError("this brand has no logo concepts yet", "run brand_logo_concepts first")
    c = next((x for x in cs if x.id == cid or x.id == f"c{cid}"), None)
    if not c:
        raise ToolError(f"no concept {cid!r}", "concepts: " + ", ".join(x.id for x in cs))
    suite = K.write_suite(kit, c)
    kit["logo"].update({"concept": c.to_dict(), **suite})
    return kit


def _previews_dir(kit: dict) -> Path:
    p = K.kit_dir(kit["slug"]) / "previews"
    p.mkdir(exist_ok=True)
    return p


@tool("brand", network=True)
def brand_create(name: str, brief: str = "", sector: str = "", audience: str = "", personality: list | str = "",
                 name_ar: str = "", tagline: str = "", tagline_ar: str = "", base_color: str = "", harmony: str = "auto",
                 font_pair: str = "", concepts: int = 4, choose: str = "c1", overwrite: bool = False) -> Result:
    """Create a complete brand kit from a brief: colour palette (harmony + tints + WCAG-checked text
    pairs), Arabic+Latin type pairing, 3–6 logo concepts (geometric mark + outlined wordmark, Arabic name
    shaped correctly) with the full SVG/PNG/ICO suite for one of them, and starter voice & tone notes.
    Saved to STUDIO_HOME/brands/<slug>/brand.json so every design tool can use brand=<slug>.
    Returns previews to LOOK at: concept sheet, palette, typography, logo suite.

    personality: e.g. "modern, friendly, trustworthy". base_color: optional hex/name (else derived from
    sector/personality). harmony: auto | complementary | analogous | triadic | split | tetradic | monochrome.
    font_pair: modern, corporate, friendly, playful, luxury, editorial, heritage, bold, geometric, creative,
    news, egypt-classic (see design_catalog pairs). choose: concept built into the suite (change later with
    brand_choose_logo). Logos are typographic/geometric — honest starting points a designer would refine."""
    slug = _slug(name)
    d = K.kit_dir(slug)
    if (d / "brand.json").exists() and not overwrite:
        raise ToolError(f"brand '{slug}' already exists", "use brand_update / brand_logo_concepts, or overwrite=true to replace it")
    pers = _list(personality)
    kit = K.make_kit(name, brief, sector, audience, pers, name_ar, tagline, tagline_ar, "", base_color, harmony, font_pair)
    pair = kit.pop("_pair")
    cs = L.concepts_for(name, " ".join([brief, sector, " ".join(pers)]), pair, max(1, min(6, concepts)))
    kit["logo"] = {"concepts": [c.to_dict() for c in cs]}
    kit["_dir"] = str(d)
    _choose(kit, choose)
    K.save(kit)
    kit["_dir"] = str(d)
    pv = _previews_dir(kit)
    sheets = [K.concepts_sheet(kit, cs, pv / "concepts.png"), K.palette_sheet(kit, pv / "palette.png"),
              K.type_sheet(kit, pv / "typography.png"), K.suite_sheet(kit, pv / "logo-suite.png")]
    c = kit["colors"]
    warn = []
    bad = [p for p in kit["palette"]["pairs"] if p["fg"] in ("ink", "muted", "on_primary") and p["normal_text"] == "fail"]
    if bad:
        warn.append("some core text pairs fail WCAG: " + ", ".join(f"{p['fg']}/{p['bg']} {p['ratio']}" for p in bad))
    return Result(
        f"Brand kit '{name}' ({slug}) created: primary {c['primary']}, secondary {c['secondary']}, accent {c['accent']} "
        f"({kit['palette']['harmony']}); type {kit['fonts']['head']}/{kit['fonts']['body']} + {kit['fonts']['head_ar']}/{kit['fonts']['body_ar']}; "
        f"{len(cs)} logo concepts, suite built for {choose}. Voice notes are rule-based starters — refine them.",
        files=[str(d / "brand.json"), str(d / "logo")] + [str(s) for s in sheets], previews=[str(s) for s in sheets],
        warnings=warn,
        next_steps=["LOOK at concepts.png and let the user pick; then brand_choose_logo {\"brand\": \"%s\", \"concept\": \"c2\"}" % slug,
                    "tweak with brand_update (colors/fonts/tagline/voice) or brand_logo_concepts for fresh ideas",
                    "brand_guidelines → PDF book; brand_apply → social/cover/card/letterhead/signature starter set",
                    "use it anywhere: design_create {..., \"brand\": \"%s\"}" % slug],
        data={"slug": slug, "colors": c, "fonts": kit["fonts"], "concepts": [x.to_dict() for x in cs],
              "logo_files": kit["logo"]["files"]})


@tool("brand", network=True)
def brand_logo_concepts(brand: str, count: int = 4, marks: list | str = "", fonts: list | str = "", variation: str = "") -> Result:
    """Generate a fresh set of logo concepts for a saved brand (keeps the current chosen logo until you
    call brand_choose_logo). marks: optional list from monogram_circle, monogram_square, monogram_letter,
    letter_split, hexagon, quarters, orbit, stack, petals, arch, spark, chevrons, leaf, wave.
    fonts: optional Latin families to try for the wordmark. variation: any text → different random seed.
    Returns the concept sheet to LOOK at."""
    kit = K.load(brand)
    pair = next((p for p in F.PAIRS if p["id"] == kit["fonts"].get("pair")), None) or F.pair_for(kit.get("personality", []))
    pair = {**pair, "head": kit["fonts"]["head"], "body": kit["fonts"]["body"], "head_ar": kit["fonts"]["head_ar"],
            "head_weight": kit["fonts"].get("head_weight", 700)}
    n = max(1, min(8, count))
    cs = L.concepts_for(kit["name"], " ".join([kit.get("brief", ""), kit.get("sector", "")] + kit.get("personality", [])),
                        pair, n, seed_extra=variation or str(len(kit["logo"].get("concepts", []))))
    mk = _list(marks)
    bad = [m for m in mk if m not in L.MARK_KINDS]
    if bad:
        raise ToolError(f"unknown mark(s) {bad}", "marks: " + ", ".join(L.MARK_KINDS))
    fs = _list(fonts)
    for i, c in enumerate(cs):
        if mk:
            c.mark = mk[i % len(mk)]
        if fs:
            c.font = F.canonical(fs[i % len(fs)])
            c.weight = F.nearest_weight(c.font, c.weight)
    kit["logo"]["concepts"] = [c.to_dict() for c in cs]
    K.save(kit)
    sheet = K.concepts_sheet(kit, cs, _previews_dir(kit) / f"concepts-{_slug(variation or 'new')}.png")
    return Result(f"{len(cs)} new concepts for {kit['name']}: " + "; ".join(f"{c.id} {c.mark}+{c.font}" for c in cs),
                  files=[str(sheet)], previews=[str(sheet)],
                  next_steps=[f"brand_choose_logo {{\"brand\": \"{kit['slug']}\", \"concept\": \"c1\"}}"],
                  data={"concepts": [c.to_dict() for c in cs]})


@tool("brand", network=True)
def brand_choose_logo(brand: str, concept: str = "c1", wordmark_font: str = "", case: str = "", tracking: float = -99,
                      mark: str = "", dot: str = "") -> Result:
    """Build the full logo suite for one concept (and optionally tweak it): horizontal + stacked lockups,
    icon, wordmark (+ Arabic wordmark), monogram — each in colour, reversed, mono black, mono white —
    plus app icon, favicon.svg/.ico and PNG sizes (16/32/48/180/192/512). Returns the suite sheet to LOOK at.
    Tweaks: wordmark_font (Google family), case (title|upper|lower), tracking (em, e.g. 0.1), mark (kind),
    dot ('yes'/'no' accent full stop)."""
    kit = K.load(brand)
    cs = _concepts(kit)
    c = next((x for x in cs if x.id in (concept, f"c{concept}")), None)
    if not c:
        raise ToolError(f"no concept {concept!r}", "concepts: " + ", ".join(x.id for x in cs))
    if wordmark_font:
        c.font = F.canonical(wordmark_font)
        c.weight = F.nearest_weight(c.font, c.weight)
    if case:
        c.case = case
    if tracking > -90:
        c.tracking = tracking
    if mark:
        if mark not in L.MARK_KINDS:
            raise ToolError(f"unknown mark {mark!r}", ", ".join(L.MARK_KINDS))
        c.mark = mark
    if dot:
        c.dot = dot.lower() in ("yes", "true", "1", "on")
    kit["logo"]["concepts"] = [c.to_dict() if x.id == c.id else x.to_dict() for x in cs]
    suite = K.write_suite(kit, c)
    kit["logo"].update({"concept": c.to_dict(), **suite})
    K.save(kit)
    sheet = K.suite_sheet(kit, _previews_dir(kit) / "logo-suite.png")
    d = Path(kit["_dir"])
    return Result(f"Logo suite for {kit['name']} built from {c.id} ({c.mark} + {c.font} {c.weight} {c.case}): "
                  f"{len(suite['files'])} SVGs, {len(suite['png'])} PNG/ICO files.",
                  files=[str(d / v) for v in suite["files"].values()] + [str(d / v) for v in suite["png"].values()],
                  previews=[str(sheet)], data={"files": suite["files"], "png": suite["png"]},
                  next_steps=["open any SVG in Inkscape to refine by hand (open_in_app)", "brand_guidelines to document it"])


@tool("brand")
def brand_palette(base_color: str = "", brief: str = "", harmony: str = "auto", brand: str = "", apply: bool = False,
                  colors: dict | str = "") -> Result:
    """Colour palette generator: from a base colour (hex/name) or a brief ('organic juice bar, fresh,
    friendly'), build primary/secondary/accent + tinted neutrals, 50–900 ramps and WCAG-checked text pairs
    (contrast ratio + AA/AAA). harmony: auto | complementary | analogous | triadic | split | tetradic |
    monochrome. With brand + apply=true it replaces that kit's colours (logos are rebuilt); colors
    {"accent": "#..."} overrides individual roles. Returns a palette sheet to LOOK at."""
    over = _json(colors, "colors") or {}
    kit = K.load(brand) if brand else None
    base = col.norm(base_color) if base_color else (kit["colors"]["primary"] if kit and not brief else col.base_from_brief(brief))
    mood = " ".join([brief] + ((kit.get("personality", []) + [kit.get("sector", "")]) if kit else []))
    pal = col.build_palette(base, harmony, mood)
    for k, v in over.items():
        if k in pal["roles"]:
            pal["roles"][k] = col.norm(v)
    if over:  # recompute derived text colours and pairs
        again = col.build_palette(pal["roles"]["primary"], pal["harmony"], mood)
        for k in ("secondary", "accent", "ink", "paper", "surface", "muted", "line"):
            again["roles"][k] = pal["roles"][k]
        r = again["roles"]
        r["on_primary"] = col.text_on(r["primary"], r["paper"], r["ink"])
        r["on_secondary"] = col.text_on(r["secondary"], r["paper"], r["ink"])
        r["on_accent"] = col.text_on(r["accent"], r["paper"], r["ink"])
        r["primary_text"] = col.ensure_contrast(r["primary"], r["paper"], 4.5)
        pal = col.build_palette(r["primary"], pal["harmony"], mood)
        pal["roles"].update({k: r[k] for k in r})
        pal["pairs"] = [dict(p, fg_hex=r[p["fg"]], bg_hex=r[p["bg"]], ratio=col.contrast(r[p["fg"]], r[p["bg"]]),
                             normal_text=col.grade(col.contrast(r[p["fg"]], r[p["bg"]])),
                             large_text=col.grade(col.contrast(r[p["fg"]], r[p["bg"]]), True)) for p in pal["pairs"]]
    tmp = kit or {"name": "Palette", "slug": "palette"}
    view = dict(tmp, colors=pal["roles"], palette={"harmony": pal["harmony"], "ramps": pal["ramps"], "pairs": pal["pairs"]})
    if kit and apply:
        kit["colors"], kit["palette"] = pal["roles"], {"harmony": pal["harmony"], "ramps": pal["ramps"], "pairs": pal["pairs"], "base": base}
        if kit["logo"].get("concept"):
            kit["logo"].update(K.write_suite(kit, L.Concept(**kit["logo"]["concept"])))
        K.save(kit)
        dest = _previews_dir(kit) / "palette.png"
    else:
        dest = unique_path(output_dir("", "brand"), "palette-" + base.strip("#"), "png")
    sheet = K.palette_sheet(view, dest)
    r = pal["roles"]
    return Result(f"Palette ({pal['harmony']}) from {base}: primary {r['primary']}, secondary {r['secondary']}, accent {r['accent']}, "
                  f"ink {r['ink']}, paper {r['paper']}." + (" Applied to the brand and logos rebuilt." if kit and apply else ""),
                  files=[str(sheet)], previews=[str(sheet)], data=pal)


@tool("brand", network=True)
def brand_type_pairing(moods: list | str = "", pair: str = "", head: str = "", body: str = "", head_ar: str = "",
                       body_ar: str = "", head_weight: int = 0, brand: str = "", apply: bool = False) -> Result:
    """Typography pairing for Arabic + Latin: pick a curated pair by mood words ('luxury, calm') or id, or
    set families yourself (any Google font). Renders a bilingual specimen (headline + body in both scripts)
    to LOOK at. With brand + apply=true it updates the kit (logos keep their wordmark font until
    brand_choose_logo)."""
    if pair:
        pr = next((p for p in F.PAIRS if p["id"] == pair), None)
        if not pr:
            raise ToolError(f"unknown pair {pair!r}", ", ".join(p["id"] for p in F.PAIRS))
        pr = dict(pr)
    elif moods:
        pr = F.pair_for(_list(moods))
    elif brand:
        kf = K.load(brand)["fonts"]
        pr = {"id": kf.get("pair", "custom"), **kf}
    else:
        pr = dict(F.PAIRS[0])
    for k, v in (("head", head), ("body", body), ("head_ar", head_ar), ("body_ar", body_ar)):
        if v:
            F.family_info(v)
            pr[k] = F.canonical(v)
    if head_weight:
        pr["head_weight"] = head_weight
    for k in ("head_ar", "body_ar"):
        if not F.is_arabic_family(pr[k]):
            raise ToolError(f"{pr[k]} has no Arabic glyphs", "choose an Arabic family: design_fonts {\"arabic\": true}")
    kit = K.load(brand) if brand else {"name": "Type pairing", "slug": "type", "tagline": "Clear words, strong identity",
                                        "tagline_ar": "كلمات واضحة وهوية قوية"}
    fonts = {"head": pr["head"], "body": pr["body"], "head_ar": pr["head_ar"], "body_ar": pr["body_ar"],
             "head_weight": F.nearest_weight(pr["head"], pr.get("head_weight", 700)), "pair": pr.get("id", "custom")}
    view = dict(kit, fonts=fonts)
    if brand and apply:
        kit["fonts"] = fonts
        K.save(kit)
        dest = _previews_dir(kit) / "typography.png"
    else:
        dest = unique_path(output_dir("", "brand"), "type-" + _slug(pr["head"] + "-" + pr["head_ar"]), "png")
    sheet = K.type_sheet(view, dest)
    return Result(f"Type pairing '{fonts['pair']}': headlines {fonts['head']} {fonts['head_weight']} / {fonts['head_ar']}, "
                  f"body {fonts['body']} / {fonts['body_ar']}." + (" Applied to the brand." if brand and apply else ""),
                  files=[str(sheet)], previews=[str(sheet)], data=fonts)


@tool("brand")
def brand_update(brand: str, changes: dict | str) -> Result:
    """Edit a saved brand kit: changes = {"tagline": "...", "tagline_ar": "...", "name_ar": "...",
    "colors": {"accent": "#FF6B4A"}, "fonts": {"head": "Sora"}, "voice": {...}, "personality": [...],
    "audience": "...", "brief": "..."}. Colour changes rebuild the logo suite. Returns the updated kit."""
    kit = K.load(brand)
    ch = _json(changes, "changes") or {}
    if not isinstance(ch, dict):
        raise ToolError("changes must be a JSON object")
    rebuild = False
    for k, v in ch.items():
        if k in ("colors", "fonts", "voice") and isinstance(v, dict):
            if k == "colors":
                v = {kk: col.norm(vv) for kk, vv in v.items()}
                rebuild = True
            if k == "fonts":
                for kk in ("head", "body", "head_ar", "body_ar"):
                    if kk in v:
                        F.family_info(v[kk])
                        v[kk] = F.canonical(v[kk])
            kit[k] = {**kit.get(k, {}), **v}
        elif k in ("name", "slug", "logo"):
            raise ToolError(f"'{k}' can't be changed here", "create a new brand for a new name; use brand_choose_logo for the logo")
        else:
            kit[k] = v
            rebuild = rebuild or k == "name_ar"
    if rebuild and kit.get("logo", {}).get("concept"):
        r = kit["colors"]
        r["on_primary"] = col.text_on(r["primary"], r["paper"], r["ink"])
        r["on_accent"] = col.text_on(r["accent"], r["paper"], r["ink"])
        kit["logo"].update(K.write_suite(kit, L.Concept(**kit["logo"]["concept"])))
    K.save(kit)
    kit["_dir"] = str(K.kit_dir(kit["slug"]))
    pv = [str(K.suite_sheet(kit, _previews_dir(kit) / "logo-suite.png"))] if rebuild else []
    return Result(f"Updated {kit['name']}: {', '.join(ch)}" + (" (logos rebuilt)" if rebuild else ""),
                  files=[str(Path(kit['_dir']) / 'brand.json')], previews=pv, data={k: kit.get(k) for k in ch})


@tool("brand")
def brand_show(brand: str = "") -> Result:
    """List saved brand kits (no argument) or show one kit: colours, fonts, voice, logo files and previews."""
    if not brand:
        rows = K.all_brands()
        return Result(f"{len(rows)} brand kit(s):\n" + "\n".join(f"- {r['slug']}: {r['name']} {r['name_ar']} ({r['sector']}) {r['updated']}"
                                                                for r in rows), data={"brands": rows})
    kit = K.load(brand)
    d = Path(kit["_dir"])
    pv = sorted(str(p) for p in (d / "previews").glob("*.png")) if (d / "previews").exists() else []
    c, f = kit["colors"], kit["fonts"]
    return Result(f"{kit['name']} {kit.get('name_ar', '')} — {kit.get('tagline', '')}\n"
                  f"colours: primary {c['primary']} · secondary {c['secondary']} · accent {c['accent']} · ink {c['ink']} · paper {c['paper']}\n"
                  f"fonts: {f['head']} / {f['body']} · {f['head_ar']} / {f['body_ar']}\n"
                  f"logo: {(kit['logo'].get('concept') or {}).get('id', '—')} · {len(kit['logo'].get('files', {}))} files in {d / 'logo'}",
                  files=[str(d / "brand.json")], previews=pv[:4], data={k: v for k, v in kit.items() if k != "_dir"})


@tool("brand", network=True)
def brand_guidelines(brand: str, applications: bool = True, project: str = "") -> Result:
    """Brand guidelines book as a multi-page vector PDF (A4 landscape): cover, contents, essence, voice &
    tone, logo, clear space & minimum sizes, variations, misuse, colour (HEX/RGB/CMYK), measured WCAG pairs,
    Arabic + Latin typography, applications (renders a few branded assets when applications=true), back
    cover. Returns the PDF, page PNGs and a contact sheet to LOOK at."""
    from . import guidelines as G
    kit = K.load(brand)
    if not kit.get("logo", {}).get("files"):
        raise ToolError("this brand has no logo suite yet", "brand_choose_logo first")
    d = Path(kit["_dir"])
    apps: list[Path] = []
    warns: list[str] = []
    if applications:
        from ..design import build
        tmp = d / "guidelines" / "_apps"
        tmp.mkdir(parents=True, exist_ok=True)
        for t, spec, size in _sample_assets(kit)[:6]:
            try:
                r = build(t, spec, size, brand=kit["slug"], out=str(tmp), name=f"g-{t}-{size}", fmt="png")
                apps += [Path(f) for f in r.files if f.endswith(".png")][:1]
            except ToolError as e:
                warns.append(f"application {t}: {e}")
    pdf, pngs, rep = G.build(kit, d / "guidelines", apps)
    sheet = d / "previews" / "guidelines-sheet.png"
    sheet.parent.mkdir(exist_ok=True)
    qc.sheet_of_images([str(p) for p in pngs], sheet, tile=520, cols=4)
    files = [str(pdf)] + [str(p) for p in pngs]
    if project:
        dst = unique_path(output_dir(project, "brand"), pdf.stem, "pdf")
        shutil.copy2(pdf, dst)
        files.append(str(dst))
    return Result(f"Brand guidelines for {kit['name']}: {len(pngs)} pages → {pdf.name}.", files=files, previews=[str(sheet)],
                  warnings=warns, next_steps=["LOOK at the sheet; tweak the kit (brand_update) and re-run to regenerate"])


def _sample_assets(kit: dict, contact: dict | None = None) -> list[tuple[str, dict, str]]:
    ct = contact or {}
    name, tag = kit["name"], kit.get("tagline") or kit["name"]
    ar = kit.get("language") in ("ar", "bilingual") and kit.get("tagline_ar")
    web = ct.get("website") or ct.get("url") or f"{kit['slug']}.com"
    handle = ct.get("handle") or "@" + kit["slug"].replace("-", "")
    return [
        ("headline", {"eyebrow": "Hello" if not ar else "أهلاً", "headline": tag if not ar else kit["tagline_ar"],
                      "subhead": (kit.get("tagline") if ar else kit.get("brief", "")[:110]), "cta": "Learn more" if not ar else "اعرف أكتر", "handle": handle}, "ig_square"),
        ("headline", {"eyebrow": "New", "headline": tag, "cta": "Swipe up", "handle": handle}, "ig_story"),
        ("quote", {"quote": kit.get("voice", {}).get("samples", {}).get("social_en", tag), "author": name, "role": web, "handle": handle}, "ig_portrait"),
        ("banner", {"headline": tag, "subhead": kit.get("tagline_ar", ""), "url": web}, "linkedin_cover"),
        ("business_card", {"name": ct.get("name") or "Your Name", "title": ct.get("title") or "Title",
                           "name_ar": ct.get("name_ar", ""), "title_ar": ct.get("title_ar", ""),
                           "phone": ct.get("phone") or "+20 100 000 0000", "email": ct.get("email") or f"hello@{web}",
                           "website": web}, "business_card"),
        ("letterhead", {"phone": ct.get("phone") or "+20 100 000 0000", "email": ct.get("email") or f"hello@{web}",
                        "website": web, "address": ct.get("address", ""), "date": "", "body": ""}, "a4"),
    ]


def _signature_html(kit: dict, ct: dict, logo_url: str) -> str:
    c, f = kit["colors"], kit["fonts"]
    E = _html.escape
    font = f"'{f['body']}', Arial, Helvetica, sans-serif"
    rows = "".join(f'<tr><td style="padding:1px 0;font:13px/1.5 {font};color:{c["muted"]}">{E(lbl)}&nbsp; '
                   f'<span style="color:{c["ink"]}">{E(v)}</span></td></tr>'
                   for lbl, v in (("T", ct.get("phone", "")), ("E", ct.get("email", "")), ("W", ct.get("website", ""))) if v)
    ar = (f'<tr><td dir="rtl" style="font:600 14px/1.6 \'{f["body_ar"]}\', Tahoma, Arial;color:{c["ink"]};text-align:left">'
          f'{E(ct.get("name_ar", ""))}{" · " + E(ct.get("title_ar", "")) if ct.get("title_ar") else ""}</td></tr>') if ct.get("name_ar") else ""
    return f"""<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:{font}">
<tr><td style="padding:0 18px 0 0;border-right:3px solid {c['accent']};vertical-align:middle">
<img src="{E(logo_url)}" alt="{E(kit['name'])}" width="72" height="72" style="display:block;border:0;width:72px;height:72px"></td>
<td style="padding:0 0 0 18px;vertical-align:middle"><table cellpadding="0" cellspacing="0" border="0">
<tr><td style="font:700 17px/1.3 {font};color:{c['ink']}">{E(ct.get('name', 'Your Name'))}</td></tr>
<tr><td style="font:600 13px/1.5 {font};color:{c['primary_text']};padding-bottom:4px">{E(ct.get('title', ''))}{' · ' + E(kit['name'])}</td></tr>
{ar}{rows}</table></td></tr></table>"""


@tool("brand", network=True)
def brand_apply(brand: str, contact: dict | str = "", assets: list | str = "", logo_url: str = "", project: str = "") -> Result:
    """Apply a brand: generate a ready-to-post starter set in the brand's colours, fonts and logo —
    social avatar, Instagram post + story + quote templates, LinkedIn/X/Facebook/YouTube covers, website
    share image, print-ready business card (front/back PDF with bleed + crop marks), A4 letterhead PDF,
    and an email signature (HTML + preview). Every item is an editable design master.
    contact: {"name", "title", "name_ar", "title_ar", "phone", "email", "website", "address", "handle"}.
    assets: subset of avatar, post, story, quote, linkedin_cover, x_header, fb_cover, youtube_banner,
    og_image, business_card, letterhead, email_signature (default all). logo_url: public URL of the logo
    for the email signature (email clients can't embed local files). Returns files + a contact sheet."""
    from ..design import build
    kit = K.load(brand)
    if not kit.get("logo", {}).get("files"):
        raise ToolError("this brand has no logo suite yet", "brand_choose_logo first")
    ct = _json(contact, "contact") or {}
    want = _list(assets) or ["avatar", "post", "story", "quote", "linkedin_cover", "x_header", "fb_cover", "youtube_banner",
                             "og_image", "business_card", "letterhead", "email_signature"]
    samples = {t + ":" + s: (t, sp, s) for t, sp, s in _sample_assets(kit, ct)}
    tag = kit.get("tagline") or kit["name"]
    web = ct.get("website") or f"{kit['slug']}.com"
    plan = {
        "avatar": ("avatar", {}, "avatar"),
        "post": samples["headline:ig_square"], "story": samples["headline:ig_story"], "quote": samples["quote:ig_portrait"],
        "linkedin_cover": samples["banner:linkedin_cover"],
        "x_header": ("banner", {"headline": tag, "url": web}, "x_header"),
        "fb_cover": ("banner", {"headline": tag, "subhead": kit.get("tagline_ar", ""), "url": web}, "fb_cover"),
        "youtube_banner": ("banner", {"headline": tag, "subhead": kit.get("tagline_ar", ""), "url": web}, "youtube_banner"),
        "og_image": ("headline", {"eyebrow": kit["name"], "headline": tag, "handle": web}, "og_image"),
        "business_card": samples["business_card:business_card"], "letterhead": samples["letterhead:a4"],
    }
    unknown = [a for a in want if a not in plan and a != "email_signature"]
    if unknown:
        raise ToolError(f"unknown asset(s) {unknown}", ", ".join(list(plan) + ["email_signature"]))
    out = output_dir(project or f"{kit['slug']}-starter-kit", "brand")
    res = Result("")
    shots: list[str] = []
    for a in want:
        if a == "email_signature":
            continue
        t, spec, size = plan[a]
        mode = "dark" if a in ("x_header", "youtube_banner") else ""
        r = build(t, spec, size, brand=kit["slug"], out=str(out), name=f"{kit['slug']}-{a}", mode=mode)
        res.files += r.files
        res.warnings += [f"[{a}] {w}" for w in r.warnings]
        shots += [f for f in r.files if f.endswith(".png")][:2]
    if "email_signature" in want:
        d = Path(kit["_dir"])
        icon_png = d / kit["logo"]["png"].get("icon", kit["logo"]["png"].get("icon_512", ""))
        local = out / f"{kit['slug']}-signature-logo.png"
        if icon_png.exists():
            shutil.copy2(icon_png, local)
        html = _signature_html(kit, {**ct, "website": web}, logo_url or local.name)
        sig = unique_path(out, f"{kit['slug']}-email-signature", "html")
        sig.write_text(f'<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:24px;background:#fff">{html}</body></html>',
                       encoding="utf-8")
        png = sig.with_suffix(".png")

        def job(browser):
            ctx = browser.new_context(viewport={"width": 900, "height": 300}, device_scale_factor=2)
            pg = ctx.new_page()
            pg.goto(sig.resolve().as_uri(), wait_until="load")
            try:
                pg.wait_for_function("[...document.images].every(i => i.complete && i.naturalWidth > 0)", timeout=5000)
            except Exception:
                pass
            pg.locator("table").first.screenshot(path=str(png))
            ctx.close()
        with_browser(job)
        res.files += [str(sig), str(png), str(local)]
        shots.append(str(png))
        if not logo_url:
            res.warnings.append("email signature uses a local logo file — upload it and pass logo_url (or edit the <img src>) before pasting into Gmail/Outlook")
    sheet = out / f"{kit['slug']}-starter-kit-sheet.png"
    qc.sheet_of_images(shots, sheet, tile=460, cols=4)
    res.summary = f"Starter kit for {kit['name']}: {len([a for a in want])} assets in {out}."
    res.previews = [str(sheet)]
    res.next_steps = ["LOOK at the sheet; replace placeholder contact details via contact={...}",
                      "edit any piece with design_create/design_resize using brand=" + kit["slug"]]
    return res
