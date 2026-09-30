"""Design department: graphic design & layout — social posts, carousels, stories, thumbnails, banners,
posters, flyers, business cards, certificates, menus, infographics.

Engine: HTML/CSS in headless Chromium (Playwright) → PNG/JPG/WebP, vector PDF (bleed + crop marks),
SVG (via poppler). Every design is saved as an editable master folder (<name>.design/: index.html,
spec.json, fonts/, assets/) that can be re-rendered at any size."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ...config import output_dir, slug, unique_path
from ...core import qc
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import color as col
from . import fonts as F
from . import presets as P
from .builder import STYLES, Ctx, document, load_brand, make_theme, plain
from .engine import Canvas, MM, contrast_summary, pdf_to_cmyk, pdf_to_svg, qc_warnings, render
from .templates import TEMPLATES
from . import templates_print  # noqa: F401  (registers print templates)

DEFAULT_SIZE = {"thumbnail": "youtube_thumbnail", "banner": "linkedin_cover", "poster": "a3", "flyer": "a5",
                "business_card": "business_card", "certificate": "a4_landscape", "menu": "a4",
                "infographic": "infographic_tall", "letterhead": "a4", "avatar": "avatar"}


def _parse(v, name: str):
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return {} if name != "slides" else []
        try:
            return json.loads(v)
        except json.JSONDecodeError as e:
            raise ToolError(f"{name} is not valid JSON: {e}")
    return v


def _sizes(size) -> list[str]:
    if isinstance(size, list):
        return [str(s) for s in size if str(s).strip()]
    return [s.strip() for s in str(size or "").split(",") if s.strip()]


def _canvas(size: str, bleed_mm: float) -> Canvas:
    if size == "infographic_tall":
        return P.canvas("1080x2700")
    return P.canvas(size, None if bleed_mm < 0 else bleed_mm)


def _formats(fmt: str, cv: Canvas) -> list[str]:
    fs = [f.strip().lower().lstrip(".") for f in (fmt or "").split(",") if f.strip()]
    if not fs or fs == ["auto"]:
        fs = ["pdf", "png"] if cv.print_ else ["png"]
    bad = set(fs) - {"png", "jpg", "jpeg", "webp", "pdf", "svg"}
    if bad:
        raise ToolError(f"unknown format(s) {sorted(bad)}", "png, jpg, webp, pdf, svg")
    return ["jpg" if f == "jpeg" else f for f in fs]


def _export(master: Path, html: Path, cv: Canvas, fmts: list[str], n_pages: int, checks: list[str],
            crop_marks: bool, cmyk: bool, label: str) -> tuple[list[Path], dict, list[str]]:
    """Render index.html of a master to the requested formats next to the master folder."""
    out = master.parent
    stem = master.name[:-len(".design")] if master.name.endswith(".design") else master.name
    tag = cv.name if cv.name != "custom" else f"{int(cv.w)}x{int(cv.h)}"
    base = f"{stem}-{slug(tag)}" if not stem.endswith(slug(tag)) else stem
    outputs: dict = {}
    files: list[Path] = []
    warns: list[str] = []

    def paths(ext):
        if n_pages == 1:
            return [unique_path(out, base, ext)]
        first = unique_path(out, f"{base}-p1", ext)
        suffix = first.stem[len(f"{base}-p1"):]
        return [out / f"{base}-p{i + 1}{suffix}.{ext}" for i in range(n_pages)]

    want_png = "png" in fmts
    outputs["png"] = paths("png") if want_png else None
    for f in ("jpg", "webp"):
        if f in fmts:
            outputs[f] = paths(f)
    pdf = None
    if "pdf" in fmts or "svg" in fmts:
        pdf = unique_path(out, base, "pdf")
        outputs["pdf"] = pdf
    if crop_marks and cv.print_:
        outputs["pdf_marks"] = unique_path(out, base + "-print-cropmarks", "pdf")
    rep = render(html, cv, outputs, pages=n_pages, fonts_check=checks,
                 min_text_px=(7 * 96 / 72 if cv.print_ else max(14, min(cv.w, cv.h) * 0.017)), label=label)
    for k in ("png", "jpg", "webp"):
        files += [Path(p) for p in rep.get(k, [])]
    if pdf and pdf.exists():
        if "pdf" in fmts:
            files.append(pdf)
        if "svg" in fmts:
            svgs = []
            for i in range(n_pages):
                s = pdf_to_svg(pdf, unique_path(out, base + (f"-p{i + 1}" if n_pages > 1 else ""), "svg"), i + 1)
                if s:
                    svgs.append(s)
            if svgs:
                files += svgs
            else:
                warns.append("SVG export needs poppler's pdftocairo (brew install poppler / apt install poppler-utils)")
            if "pdf" not in fmts:
                pdf.unlink(missing_ok=True)
    if outputs.get("pdf_marks") and Path(outputs["pdf_marks"]).exists():
        files.append(Path(outputs["pdf_marks"]))
    if cmyk and pdf and pdf.exists():
        c = pdf_to_cmyk(pdf, unique_path(out, base + "-cmyk", "pdf"))
        if c:
            files.append(c)
        else:
            warns.append("CMYK conversion needs Ghostscript (brew install ghostscript) — the RGB PDF is still fine for most print shops")
    return files, rep, warns


def _previews(files: list[Path], master: Path, n_pages: int) -> list[str]:
    pngs = [f for f in files if f.suffix == ".png"]
    if not pngs:
        tmp = [f for f in files if f.suffix in (".jpg", ".webp")]
        pngs = tmp
    if not pngs:
        pdfs = [f for f in files if f.suffix == ".pdf"]
        if pdfs:
            pv = master / "preview.png"
            qc.image_preview(pdfs[0], pv, 1200)
            return [str(pv)]
        return []
    if len(pngs) == 1:
        pv = master / "preview.png"
        qc.image_preview(pngs[0], pv, 1200)
        return [str(pv)]
    sheet = master / "preview-sheet.png"
    qc.sheet_of_images([str(p) for p in pngs], sheet, tile=420, cols=min(4, len(pngs)))
    return [str(sheet)]


def build(template: str, spec: dict, size: str, brand=None, style: str = "", mode: str = "", colors=None, fonts=None,
          project: str = "", out: str = "", name: str = "", fmt: str = "auto", bleed_mm: float = -1,
          crop_marks: bool = True, cmyk: bool = False, slides: list | None = None, kind: str = "design") -> Result:
    """Core builder shared by design_create / design_carousel / design_resize / brand_apply."""
    if template not in TEMPLATES and not slides:
        raise ToolError(f"unknown template {template!r}", "templates: " + ", ".join(sorted(TEMPLATES)))
    cv = _canvas(size, bleed_mm)
    theme = make_theme(brand, style, colors, fonts)
    tdef = TEMPLATES.get(template) or TEMPLATES["headline"]
    odir = Path(out).expanduser() if out else output_dir(project, kind)
    odir.mkdir(parents=True, exist_ok=True)
    stem = slug(name or plain(spec.get("headline") or spec.get("title") or spec.get("name") or template), 40)
    master = unique_path(odir, stem, ".design")
    master.mkdir(parents=True)
    pages_html: list[str] = []
    css_parts: list[str] = []
    warnings: list[str] = []
    script_font = ""
    items = slides if slides else [dict(spec, template=template)]
    doc_ctx = None
    for i, sd in enumerate(items):
        sd = dict(sd)
        tname = sd.pop("template", None) or template
        if tname not in TEMPLATES:
            raise ToolError(f"slide {i + 1}: unknown template {tname!r}")
        t = TEMPLATES[tname]
        merged = {**(spec if slides else {}), **sd}
        m = sd.pop("mode", None) or mode or t["mode"]
        ctx = Ctx(cv, merged, theme, master, mode=m,
                  chrome={"index": i, "total": len(items), "swipe": spec.get("swipe")} if slides and len(items) > 1 else None)
        import re as _re
        for req in _re.findall(r"(\w+)\*", t["fields"]):
            if merged.get(req) in (None, "", []):
                warnings.append(f"{'slide ' + str(i + 1) + ': ' if slides else ''}template '{tname}' expects '{req}' "
                                f"(fields: {t['fields']}) — the layout has an empty slot for it")
        pgs, css = t["fn"](ctx)
        pages_html += pgs
        css_parts.append(css)
        warnings += ctx.warnings
        script_font = script_font or getattr(ctx, "script_font", "")
        doc_ctx = doc_ctx or ctx
    doc_ctx.mode = (items[0].get("mode") or mode or TEMPLATES[items[0].get("template") or template]["mode"])
    css = "\n".join(dict.fromkeys(css_parts))
    html, checks = document(pages_html, css, doc_ctx, title=stem, script_font=script_font)
    idx = master / "index.html"
    idx.write_text(html, encoding="utf-8")
    meta = {"template": template, "spec": spec, "slides": slides, "size": size, "brand": brand if isinstance(brand, (str, type(None))) else "inline",
            "style": style, "mode": mode, "colors": colors, "fonts": fonts, "bleed_mm": bleed_mm, "lang": doc_ctx.lang,
            "canvas": {"w": cv.w, "h": cv.h, "print": cv.print_, "bleed_mm": cv.bleed_mm, "name": cv.name}}
    (master / "spec.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    fmts = _formats(fmt, cv)
    files, rep, w2 = _export(master, idx, cv, fmts, len(pages_html), checks, crop_marks, cmyk, label=stem)
    warnings += w2 + qc_warnings(rep)
    previews = _previews(files, master, len(pages_html))
    res = Result(
        f"{'Carousel' if slides else template.replace('_', ' ').capitalize() + ' design'} "
        f"'{stem}' — {len(pages_html)} page(s), {cv.name} "
        f"({'%g×%g mm' % cv.mm() + (f' + {cv.bleed_mm:g} mm bleed' if cv.bleed_mm else '') if cv.print_ else '%d×%d px' % (cv.w, cv.h)}), "
        f"template '{template}', {'brand ' + theme.name if theme.kit else 'style ' + theme.name}, mode {doc_ctx.mode}, "
        f"lang {doc_ctx.lang}.",
        files=[str(f) for f in files] + [str(master)], previews=previews, warnings=list(dict.fromkeys(warnings)),
        data={"master": str(master), "fit": rep.get("fit"), "contrast": contrast_summary(rep),
              "fonts": {"head": f"{theme.head} / {theme.head_ar}", "body": f"{theme.body} / {theme.body_ar}"},
              "safe_zone": cv.safe, "pages": len(pages_html)})
    res.next_steps = ["LOOK at the preview; fix any warning (shorter copy, other mode/style) and re-run",
                      f"other sizes: design_resize {{\"master\": \"{master}\", \"sizes\": \"ig_story,linkedin_post\"}}",
                      "hand-edit: open index.html in the master folder (HTML/CSS) and re-export with design_render_html"]
    if cv.print_:
        res.next_steps.append("send the *-print-cropmarks.pdf (or the plain PDF, it includes bleed) to the printer")
    return res


# ------------------------------------------------------------------------------------------------ tools

@tool("design", network=True)
def design_create(template: str = "headline", content: dict | str = "", size: str = "", brand: str = "",
                  style: str = "", mode: str = "", colors: dict | str = "", fonts: dict | str = "",
                  formats: str = "auto", bleed_mm: float = -1, crop_marks: bool = True, cmyk: bool = False,
                  name: str = "", project: str = "", out: str = "") -> Result:
    """Make a finished, agency-quality design from a professional template + a JSON content spec —
    social posts, stories, thumbnails, banners, posters, flyers, business cards, certificates, menus,
    infographics, letterheads. Returns exported files (PNG/JPG/WebP/PDF/SVG), an editable master folder
    and a preview to LOOK at, plus measured QC (text fit, WCAG contrast, safe zones).

    template: see design_catalog (headline, photo, split, quote, stat, list, event, offer, editorial,
    thumbnail, banner, poster, flyer, business_card, certificate, menu, infographic, letterhead, avatar).
    content: {"headline": "Grow *faster*", "eyebrow": "...", "subhead": "...", "cta": "...", "image": "path|url",
    "handle": "@you", "lang": "auto|ar|en", "leave_bottom": 0.3 (keep the lower 30% empty — for reels
that get burned-in captions), ...} — *word* = highlight colour, ==word== = marker, \\n = break.
    Arabic content flips the layout to RTL automatically. size: preset (ig_square, ig_portrait, ig_story,
    youtube_thumbnail, linkedin_cover, a4, a3, business_card…), 'WxH' px or 'WxHmm'; several comma-separated
    = one design exported at each size. brand: saved brand kit slug (brand_create) — applies its colours,
    fonts and logo. style (no brand): studio, midnight, electric, sunset, mint, mono, nile, rose.
    mode: light | dark | brand | accent | ink (background choice). formats: png,jpg,webp,pdf,svg ('auto' =
    png for screen; pdf+png for print). Print sizes get 3 mm bleed + a crop-marks PDF; cmyk=true converts
    with Ghostscript when installed. Fonts are Google Fonts downloaded once and cached."""
    spec = _parse(content, "content")
    if not isinstance(spec, dict):
        raise ToolError("content must be a JSON object")
    colors, fonts = _parse(colors, "colors"), _parse(fonts, "fonts")
    sizes = _sizes(size) or [DEFAULT_SIZE.get(template, "ig_square")]
    results = [build(template, spec, s, brand or None, style, mode, colors, fonts, project, out, name, formats,
                     bleed_mm, crop_marks, cmyk) for s in sizes]
    if len(results) == 1:
        return results[0]
    res = Result(f"'{template}' exported at {len(sizes)} sizes: {', '.join(sizes)}.")
    for r in results:
        res.files += r.files
        res.previews += r.previews
        res.warnings += [f"[{s}] {w}" for w in r.warnings for s in [r.summary.split(', ')[1] if ', ' in r.summary else '']]
        res.data.setdefault("masters", []).append(r.data.get("master"))
    res.next_steps = results[0].next_steps[:1]
    return res


@tool("design", network=True)
def design_carousel(slides: list | str, size: str = "ig_portrait", brand: str = "", style: str = "", mode: str = "",
                    handle: str = "", colors: dict | str = "", fonts: dict | str = "", formats: str = "png,pdf",
                    name: str = "", project: str = "", out: str = "") -> Result:
    """Multi-slide carousel (Instagram / LinkedIn) with one consistent look: shared header/logo, page
    counter, progress dots and a swipe hint. Returns one PNG per slide + a PDF (LinkedIn document post),
    an editable master and a contact sheet to LOOK at.

    slides: [{"template": "headline", "headline": "..."}, {"template": "list", "headline": "...", "items": [...]},
    {"template": "stat", "stat": "73%", "label": "..."}, {"template": "quote", ...}, {"template": "headline",
    "headline": "Follow for more", "cta": "Save this post", "mode": "accent"}]. Any design template works per slide;
    per-slide "mode" varies the background. 3–10 slides read best; keep ≤ 25 words per slide."""
    sl = _parse(slides, "slides")
    if not isinstance(sl, list) or not sl:
        raise ToolError("slides must be a non-empty JSON list of slide objects")
    shared = {"handle": handle} if handle else {}
    return build(sl[0].get("template", "headline"), shared, size, brand or None, style, mode,
                 _parse(colors, "colors"), _parse(fonts, "fonts"), project, out,
                 name or plain(sl[0].get("headline") or "carousel"), formats, slides=sl)


@tool("design", network=True)
def design_resize(master: str, sizes: str = "ig_square,ig_portrait,ig_story", formats: str = "auto",
                  project: str = "", out: str = "") -> Result:
    """Export set: re-flow an existing design master (a .design folder made by design_create /
    design_carousel) to other size presets — the template re-lays out for each aspect ratio (not a crop).
    Returns files for every size and previews to LOOK at. For hand-written HTML masters use
    design_render_html with sizes instead."""
    m = Path(master).expanduser()
    if m.name == "spec.json":
        m = m.parent
    sp = m / "spec.json"
    if not sp.exists():
        raise ToolError(f"{m} is not a design master (no spec.json)", "pass the .design folder returned by design_create")
    meta = json.loads(sp.read_text(encoding="utf-8"))
    if meta.get("template") == "__html__":
        return design_render_html(html=str(m / "index.html"), sizes=sizes, formats=formats, project=project, out=out or str(m.parent))
    res = Result(f"Resized '{m.name}' to: {sizes}")
    for s in _sizes(sizes):
        r = build(meta["template"], meta.get("spec") or {}, s, meta.get("brand"), meta.get("style") or "",
                  meta.get("mode") or "", meta.get("colors"), meta.get("fonts"), project,
                  out or str(m.parent), m.name.replace(".design", ""), formats, slides=meta.get("slides"))
        res.files += r.files
        res.previews += r.previews
        res.warnings += [f"[{s}] {w}" for w in r.warnings]
    return res


@tool("design", network=True)
def design_render_html(html: str, size: str = "1080x1080", sizes: str = "", formats: str = "png", fonts: list | str = "",
                       brand: str = "", pages: int = 0, name: str = "", project: str = "", out: str = "",
                       bleed_mm: float = 0) -> Result:
    """Power tool: render ANY HTML/CSS you write (a string or a path to an .html file / master folder) with
    headless Chromium to PNG/JPG/WebP/PDF/SVG — pixel-exact, real web typography, Arabic shaping and RTL.
    Returns files, a preview to LOOK at and QC (overflow, contrast, missing fonts/images).

    Conventions (all optional): wrap each page/artboard in <section class="page"> (sized by the tool to
    `size`); fonts: ["Cairo", "Inter:400,800"] → downloaded once and embedded as @font-face; use them by
    name in CSS. brand: a kit slug → CSS variables --primary --secondary --accent --ink --paper and families
    'sf-head'/'sf-body'. Text fitting: put text in a [data-fit-box] container with fixed size and give text
    elements data-fit data-max="120" data-min="40" (px). A full-size .page gets var(--u) = 1% of the short side.
    sizes: comma list to render the same responsive HTML at several sizes. Print: size 'a4' etc. →
    vector PDF with bleed_mm bleed (design to the bleed-inclusive page)."""
    src = html.strip()
    p = Path(src).expanduser() if len(src) < 1024 and "\n" not in src and "<" not in src else None
    if p and p.is_dir():
        p = p / "index.html"
    if p and not p.exists():
        raise ToolError(f"no such file: {p}")
    raw = p.read_text(encoding="utf-8") if p else src
    size_list = _sizes(sizes) or [size]
    fams = _parse(fonts, "fonts") if isinstance(fonts, str) and fonts.strip().startswith("[") else \
        ([f.strip() for f in fonts.split("|")] if isinstance(fonts, str) and fonts.strip() else (fonts or []))
    odir = Path(out).expanduser() if out else output_dir(project, "design")
    stem = slug(name or (p.parent.name.replace(".design", "") if p and p.name == "index.html" else p.stem if p else "html"), 40)
    res = Result("")
    for sz in size_list:
        cv = _canvas(sz, bleed_mm if bleed_mm > 0 else 0)
        if cv.print_ and bleed_mm <= 0:
            cv.bleed_mm = 0
        master = unique_path(odir, stem, ".design")
        master.mkdir(parents=True)
        (master / "assets").mkdir()
        if p:  # copy sibling assets so relative references keep working
            for sub in ("assets", "fonts", "images", "img"):
                if (p.parent / sub).is_dir():
                    shutil.copytree(p.parent / sub, master / sub, dirs_exist_ok=True)
        head = []
        checks = []
        used = []
        for fam in fams:
            fam, _, ws = str(fam).partition(":")
            weights = [int(w) for w in re.split(r"[,; ]+", ws) if w.strip().isdigit()] or [400, 700]
            got = F.ensure(fam.strip(), weights)
            for w in sorted(set(weights)):
                fp = got[w]
                used.append(fp)
                head.append(f"@font-face{{font-family:'{F.canonical(fam.strip())}';font-weight:{w};font-display:block;"
                            f"src:url('fonts/{fp.name}') format('truetype')}}")
            checks.append(f"{weights[0]} 20px '{F.canonical(fam.strip())}'")
        if used:
            F.copy_into(used, master / "fonts")
        if brand:
            th = make_theme(brand)
            ctx_css = []
            used2 = []

            def url_for(pp):
                used2.append(pp)
                return "fonts/" + pp.name
            ctx_css.append(F.role_css("sf-head", th.head, th.head_ar, sorted({400, 700, th.head_weight}), url_for))
            ctx_css.append(F.role_css("sf-body", th.body, th.body_ar, [400, 500, 700], url_for))
            F.copy_into(used2, master / "fonts")
            v = th.vars("light")
            ctx_css.append(":root{" + ";".join(f"--{k}:{val}" for k, val in th.colors.items()) +
                           f";--on-primary:{v['on-primary']};--on-accent:{v['on-accent']}}}")
            head += ctx_css
        u = min(cv.w, cv.h) / 100
        base = (f"html,body{{margin:0;padding:0}} :root{{--u:{u:.4f}px;--W:{cv.tw:.2f}px;--H:{cv.th:.2f}px}}"
                f" .page{{position:relative;width:{cv.tw:.2f}px;height:{cv.th:.2f}px;overflow:hidden}}")
        inject = f"<style>\n{chr(10).join(head)}\n{base}\n</style>"
        doc = raw
        if "<head" in doc.lower():
            doc = re.sub(r"(<head[^>]*>)", lambda m_: m_.group(1) + "\n<meta charset=\"utf-8\">\n" + inject, doc, count=1, flags=re.I)
        else:
            doc = f"<!doctype html><html><head><meta charset=\"utf-8\">{inject}</head><body>{doc}</body></html>"
        if 'class="page' not in doc and "class='page" not in doc:
            doc = re.sub(r"(<body[^>]*>)", r'\1<section class="page">', doc, count=1, flags=re.I)
            doc = re.sub(r"(</body>)", r"</section>\1", doc, count=1, flags=re.I)
        idx = master / "index.html"
        idx.write_text(doc, encoding="utf-8")
        (master / "spec.json").write_text(json.dumps({"template": "__html__", "size": sz, "fonts": fams, "brand": brand},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
        n = pages or max(1, len(re.findall(r"class=[\"'][^\"']*\bpage\b", doc)))
        fl, rep, w2 = _export(master, idx, cv, _formats(formats, cv), n, checks, cv.print_ and cv.bleed_mm > 0, False, stem)
        res.files += [str(f) for f in fl] + [str(master)]
        res.previews += _previews(fl, master, n)
        res.warnings += w2 + qc_warnings(rep)
        res.data.setdefault("reports", []).append({"size": sz, "fit": rep.get("fit"), "contrast": contrast_summary(rep),
                                                   "pages": rep.get("pages")})
    res.summary = f"Rendered HTML '{stem}' at {', '.join(size_list)} → {', '.join(sorted({Path(f).suffix for f in res.files if Path(f).suffix}))}."
    res.next_steps = ["LOOK at the preview; edit index.html in the master folder and re-run on that folder to iterate"]
    return res


@tool("design")
def design_catalog(what: str = "all") -> Result:
    """List what the design department can make: templates (with their content fields and best sizes),
    size presets (with platform safe zones), colour styles and Arabic+Latin font pairings.
    what: all | templates | sizes | styles | pairs."""
    lines, data = [], {}
    if what in ("all", "templates"):
        lines.append("TEMPLATES (design_create template=…):")
        for k, t in TEMPLATES.items():
            lines.append(f"  {k:14} {t['desc']}\n{'':17}fields: {t['fields']}\n{'':17}sizes: {t['sizes']} · default mode: {t['mode']}")
        data["templates"] = {k: {kk: v for kk, v in t.items() if kk != "fn"} for k, t in TEMPLATES.items()}
    if what in ("all", "sizes"):
        lines.append("\nSIZES (size=…; also 'WxH' px or 'WxHmm'):")
        for r in P.catalog():
            lines.append(f"  {r['name']:22} {r['size']:28} {r['use']}" + (f" — {r['note']}" if r.get("note") else ""))
        data["sizes"] = P.catalog()
    if what in ("all", "styles"):
        lines.append("\nSTYLES (style=…, when no brand kit):")
        for k, s in STYLES.items():
            lines.append(f"  {k:10} {s['desc']}  [{s['primary']} {s['accent']} {s['paper']}] fonts: {s['pair']}")
    if what in ("all", "pairs"):
        lines.append("\nFONT PAIRINGS (Latin head/body · Arabic head/body):")
        for p in F.PAIRS:
            lines.append(f"  {p['id']:14} {p['head']} / {p['body']} · {p['head_ar']} / {p['body_ar']}  — {', '.join(p['moods'][:5])}")
        data["pairs"] = F.PAIRS
    lines.append("\nModes: light · dark · brand (primary bg) · accent · ink.  Markup: *highlight*  ==marker==  \\n")
    return Result("\n".join(lines), data=data)


@tool("design", network=True)
def design_fonts(query: str = "", arabic: bool = False, specimen: str = "", sample: str = "",
                 project: str = "", out: str = "") -> Result:
    """Search Google Fonts (free, OFL) and optionally render a specimen PNG of one family so you can
    LOOK before choosing. query filters by name/category/notes ('kufi', 'serif', 'display'); arabic=true
    lists only Arabic-capable families (curated best ones first). specimen='Cairo' downloads it (cached in
    STUDIO_HOME/cache/fonts) and renders weights with Arabic + Latin sample text (sample overrides)."""
    rows = F.search(query, True if arabic else None, limit=40)
    lines = [f"{r['family']:24} {r['category'] or '':12} w{','.join(map(str, r['weights'] or []))}"
             f"{'  AR' if r['arabic'] else ''}{'  ★ ' + r['note'] if r['note'] else ''}" for r in rows]
    res = Result(f"{len(rows)} font(s){' for ' + repr(query) if query else ''}:\n" + "\n".join(lines), data={"fonts": rows})
    if specimen:
        fam = F.canonical(specimen)
        ws = F.family_info(fam)["weights"] or [400]
        ws = [w for w in ws if w in (300, 400, 500, 700, 800, 900)] or ws[:4]
        txt_ar = "صمّم هويتك بثقة — ٢٠٢٦" if F.is_arabic_family(fam) else ""
        s = sample or "The quick brown fox · 0123456789"
        rows_html = "".join(
            f"<div class='r'><span class='w'>{w}</span><div style=\"font:{w} 64px/1.25 '{fam}'\">{s}</div>"
            + (f"<div dir='rtl' style=\"font:{w} 64px/1.5 '{fam}';text-align:right\">{txt_ar}</div>" if txt_ar else "") + "</div>"
            for w in ws[:6])
        html = (f"<section class='page' style='background:#f6f3ee;color:#16181a;padding:60px;font-family:sans-serif'>"
                f"<div style=\"font:700 28px '{fam}';letter-spacing:.1em;text-transform:uppercase;color:#888\">{fam}</div>"
                f"<style>.r{{border-top:1px solid #ddd;padding:18px 0}} .w{{font:600 16px sans-serif;color:#999}}</style>{rows_html}</section>")
        h = 160 + len(ws[:6]) * (230 if txt_ar else 130)
        r = design_render_html(html=html, size=f"1600x{h}", fonts=[f"{fam}:{','.join(map(str, ws[:6]))}"],
                               name=f"specimen-{fam}", project=project, out=out)
        res.files += r.files
        res.previews += r.previews
        res.warnings += [w for w in r.warnings if "contrast" not in w]
    return res


@tool("design")
def design_check(path: str, size: str = "", show_safe_zone: bool = True) -> Result:
    """QC any finished image or design master before publishing: size vs the platform preset, WCAG
    contrast of text (masters only), text outside safe zones, and a preview with the platform's safe zone
    and UI overlays drawn on top so you can LOOK at what the app will cover. path: PNG/JPG or .design folder."""
    from PIL import Image, ImageDraw
    p = Path(path).expanduser()
    warnings: list[str] = []
    data: dict = {}
    if p.is_dir() or p.name == "index.html":
        m = p if p.is_dir() else p.parent
        meta = json.loads((m / "spec.json").read_text()) if (m / "spec.json").exists() else {}
        sz = size or meta.get("size") or "1080x1080"
        cv = _canvas(sz, -1)
        tmp = m / "qc-check.png"
        rep = render(m / "index.html", cv, {"png": [tmp] if True else None}, qc=True)
        warnings += qc_warnings(rep)
        data["contrast"] = rep.get("contrast")
        img_path = Path(rep["png"][0]) if rep.get("png") else tmp
    else:
        if not p.exists():
            raise ToolError(f"no such file: {p}")
        img_path = p
        im0 = Image.open(p)
        sz = size
        if not sz:
            match = [k for k, v in P.SOCIAL.items() if v["size"] == im0.size]
            sz = match[0] if match else f"{im0.width}x{im0.height}"
        cv = _canvas(sz, -1)
        if (round(cv.tw), round(cv.th)) != im0.size and not cv.print_:
            warnings.append(f"image is {im0.width}×{im0.height} but {sz} expects {round(cv.w)}×{round(cv.h)}")
        data["size"] = im0.size
    im = Image.open(img_path).convert("RGBA")
    sx, sy = im.width / cv.tw, im.height / cv.th
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    if show_safe_zone and cv.safe:
        s = cv.safe
        b = cv.bleed_px
        box = ((b + s["left"]) * sx, (b + s["top"]) * sy, im.width - (b + s["right"]) * sx, im.height - (b + s["bottom"]) * sy)
        for rect in [(0, 0, im.width, box[1]), (0, box[3], im.width, im.height), (0, box[1], box[0], box[3]), (box[2], box[1], im.width, box[3])]:
            d.rectangle(rect, fill=(255, 0, 80, 70))
        d.rectangle(box, outline=(255, 0, 80, 255), width=max(2, int(im.width / 400)))
        for a in cv.avoid:
            d.rectangle((a["x"] * sx, a["y"] * sy, (a["x"] + a["w"]) * sx, (a["y"] + a["h"]) * sy), fill=(255, 160, 0, 120),
                        outline=(255, 160, 0, 255), width=3)
        if cv.bleed_px:
            d.rectangle((b * sx, b * sy, im.width - b * sx, im.height - b * sy), outline=(0, 160, 255, 255), width=2)
    out_p = unique_path(img_path.parent, img_path.stem + "-safezone", "png")
    Image.alpha_composite(im, ov).convert("RGB").save(out_p)
    pv = qc.image_preview(out_p, out_p.with_name(out_p.stem + "-preview.png"), 1200)
    return Result(f"Checked {p.name} against {cv.name}: {len(warnings)} issue(s). Pink = outside the safe zone"
                  f"{', orange = platform UI' if cv.avoid else ''}{', blue = trim line' if cv.bleed_px else ''}.",
                  files=[str(out_p)], previews=[str(pv)], warnings=warnings, data=data)


@tool("design")
def design_contrast(foreground: str, background: str) -> Result:
    """WCAG 2.x contrast ratio of two colours (hex or name) with AA/AAA grades for normal and large text,
    plus the nearest passing variant of the foreground if it fails. Fast — no rendering."""
    fg, bg = col.norm(foreground), col.norm(background)
    r = col.contrast(fg, bg)
    fix = col.ensure_contrast(fg, bg, 4.5)
    return Result(f"{fg} on {bg}: {r}:1 — normal text {col.grade(r)}, large text {col.grade(r, True)}"
                  + ("" if r >= 4.5 else f"; nearest AA foreground: {fix} ({col.contrast(fix, bg)}:1)"),
                  data={"ratio": r, "normal": col.grade(r), "large": col.grade(r, True), "fix": fix})
