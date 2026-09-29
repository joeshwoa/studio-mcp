# Design department — graphic design & layout

Engine: HTML/CSS rendered by headless Chromium (Playwright) → PNG / JPG / WebP, **vector PDF** (with
bleed + crop marks for print), **SVG** (via poppler `pdftocairo`). Fonts are Google Fonts (OFL),
downloaded once into `STUDIO_HOME/cache/fonts` as static TTFs and embedded with `@font-face`, so a
design renders identically offline and on any Mac/Linux box.

## Tools

| tool | what it does |
|---|---|
| `design_create` | Template + JSON content spec (+ brand/style) → finished design at one or many sizes, with QC and an editable master |
| `design_carousel` | Multi-slide Instagram/LinkedIn carousel: consistent header, page counter, progress dots, swipe hint; PNG per slide + PDF |
| `design_resize` | Export set: re-flow an existing master to other size presets (real re-layout, not a crop) |
| `design_render_html` | Power tool: render any HTML/CSS you write (string, file or master folder), with font embedding, brand variables, fitting and QC |
| `design_catalog` | Templates (fields, sizes), size presets with safe zones, colour styles, Arabic+Latin font pairings |
| `design_fonts` | Search Google Fonts (Arabic filter, curated notes) and render a specimen PNG |
| `design_check` | QC a PNG/JPG or master: size vs preset, contrast, safe-zone + platform-UI overlay preview |
| `design_contrast` | WCAG ratio/grades for two colours + nearest passing foreground |

## Templates

`headline` · `photo` · `split` · `quote` · `stat` · `list` · `event` · `offer` · `editorial` (social, any size) ·
`thumbnail` (YouTube) · `banner` (LinkedIn/X/Facebook/YouTube covers, email header) · `poster` · `flyer` ·
`business_card` (front + back, bilingual) · `certificate` · `menu` (bilingual, dotted price leaders) ·
`infographic` (stats, steps timeline, bar chart) · `letterhead` · `avatar`.

Content markup: `*word*` = highlight colour, `==word==` = marker box, `\n` = line break. Arabic content
(detected from the headline, or `"lang": "ar"`) flips the whole layout to RTL.

```bash
studio design_create '{"template":"event","size":"ig_portrait,ig_story","style":"sunset",
  "content":{"eyebrow":"Free webinar","date":"14 OCT","headline":"AI for small businesses",
             "time":"7:00 PM Cairo","location":"Zoom","speaker":"Joshua George","cta":"Register"}}'
studio design_create '{"template":"business_card","brand":"cominde","content":{"name":"…","name_ar":"…","phone":"…"}}'
```

Themes: `brand=<slug>` (from the brand department) or `style` = studio, midnight, electric, sunset,
mint, mono, nile, rose; `mode` = light | dark | brand | accent | ink picks the background; `colors` /
`fonts` override single roles.

## Sizes

Social presets carry platform safe zones and UI overlays (IG/TikTok story bars, YouTube timestamp,
LinkedIn/X profile photo): content padding grows automatically to clear them and QC warns if text
still lands under UI. Print presets (A3/A4/A5/A6/Letter/DL/50×70/roll-up/business cards EU, EG 9×5,
US) are mm-based with 3 mm bleed, rendered at 300 dpi and as vector PDF + a crop-marks PDF.
`WxH` (px) and `WxHmm` work too. Full list: `studio design_catalog '{"what":"sizes"}'`.

## Typography & Arabic

Each design uses two role families, `sf-head` and `sf-body`, each built from a Latin face plus an Arabic
face restricted by `unicode-range` with a per-family `size-adjust` — so mixed Arabic/English lines use
the right font per script at matching optical size. Letter-spacing is forced to 0 on Arabic (tracking
breaks joining), line-height is increased for Arabic, and each text block gets its own `dir`.
Headlines use `text-wrap: balance`, body `text-wrap: pretty`.

## QC (measured, every render)

* **Text fitting** — every `[data-fit-box]` scales its `[data-fit]` text together (hierarchy kept) by
  binary search until nothing overflows; optional `data-fit-grow` enlarges when there's room.
  Warnings when text was shrunk a lot or can't fit at the minimum.
* **WCAG contrast** — text is hidden, the page re-rendered, and each text box's colour is compared to
  the real pixels behind it (works on photos/gradients); failures are listed with ratios.
* **Safe zones / UI overlays / outside canvas / tiny text / broken images / fonts not loaded.**
* A preview PNG (or contact sheet) is always returned — look at it.

## Editable master

`<name>.design/` = `index.html` (plain HTML/CSS, edit freely), `spec.json` (template + content → re-render
at any size with `design_resize`), `fonts/`, `assets/`. SVG export (poppler) opens in Inkscape.

## Dependencies

`playwright` (+ Chromium: `python -m playwright install chromium`; falls back to any installed
Playwright Chromium/Chrome if the versions don't match), `segno` (QR codes, optional), poppler
(`pdftocairo`, SVG export, optional: `brew install poppler`), Ghostscript (CMYK PDF, optional:
`brew install ghostscript`).

## Known limits

* The contrast check is a measurement of the rendered pixels (20th-percentile background), not a
  guarantee for every glyph; text with shadows is judged on the median.
* Templates adapt to any aspect ratio but very content-heavy templates (infographic) in a story
  frame will be flagged as not fitting — shorten or use `infographic_tall`.
* Chromium PDFs are RGB; use `cmyk=true` (Ghostscript) or let the print shop convert.
* No image generation here — pass photos/cut-outs from the photo/AI departments.
