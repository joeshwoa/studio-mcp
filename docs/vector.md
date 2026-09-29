# Vector department (Illustrator replacement)

Code: `src/studio/depts/vector/` · Tests: `tests/test_vector.py` · Outputs: `STUDIO_HOME/projects/<project>/vector/`.

## Engines (all free)

| Engine | Used for | Install (macOS / Linux) |
|---|---|---|
| hand-written SVG (ElementTree) | composing artwork, optimizing, recolouring | built in |
| librsvg `rsvg-convert` | PNG/PDF/EPS rendering, previews, visual diffs | `brew install librsvg` / `apt install librsvg2-bin` |
| Inkscape 1.x CLI (actions) | text → outlines, boolean ops, EPS, PDF with outlined text | `brew install --cask inkscape` / `apt install inkscape` |
| vtracer | raster → vector tracing | `pip install vtracer` |
| poppler `pdftoppm` (optional) | PDF preview | `brew install poppler` / `apt install poppler-utils` |

Fonts come from the shared resolver (`photo/fonts.py`): system fonts or free Google Fonts, installed into the user
font folder so rsvg, Inkscape and Illustrator/Figma on the same machine render the exact family and weight. PDFs
embed the fonts (checked with `pdffonts`).

## Tools (8)

| Tool | What it does |
|---|---|
| `vector_compose` | JSON spec → clean SVG + vector PDF + PNG; rect/circle/ellipse/line/polygon/polyline/path/text/image/group, linear/radial gradients (alpha stops), drop-shadow filters, transforms, Arabic RTL text with *visual* anchors (`end` = right edge at x), multi-line text; `outline_text` also writes a print-safe outlined copy |
| `vector_export` | SVG → PDF / EPS / PNG (by DPI or width) / SVG copy; `sizes=[…]` square PNGs; presets `icons` (16…1024), `favicon` (ico 16/32/48, png 16/32, apple-touch 180 opaque, android-chrome 192/512, icon.svg, site.webmanifest, `<head>` snippet), `app_icon` (iOS `AppIcon.appiconset` 1024 + legacy sizes + Contents.json, opaque; Android mipmap-* ic_launcher + round + 512 Play Store); padding/background/corner_radius shape the tile |
| `vector_text_to_outlines` | Inkscape `--export-text-to-path`; checks no `<text>` remains and the render is unchanged |
| `vector_optimize` | strips editor namespaces/metadata/comments, unused defs & ids, empty groups; rounds numbers; verifies visual diff ≈0 and reports size saving |
| `vector_boolean` | union / difference / intersection / exclusion / division / cut / combine by ids (Inkscape actions `select-by-id;object-to-path;path-…`); restores the bottom object's fill, which Inkscape drops for presentation attributes |
| `vector_trace` | vtracer with k-means colour quantisation (`colors` 2–16), mode filter cleanup, flat-background keying incl. anti-alias fringe, presets logo / illustration / sharp / pixel / photo, black-and-white sketch mode; reports paths and fidelity (render diff) |
| `vector_recolor` | exact mapping, or palette by luminance rank (keeps contrast hierarchy) or nearest Lab colour; handles attributes, style=, `<style>` CSS, gradient stops |
| `vector_info` | size, counts, top-level ids, colours, fonts, live text, rasters |

## Spec (`vector_compose`)

```json
{"width": 1200, "height": 630, "background": {"type": "linear", "colors": ["#0f172a", "#1e3a8a"], "angle": 0},
 "elements": [
  {"type": "group", "id": "logo", "transform": "translate(90 120)", "elements": [
    {"type": "rect", "width": 120, "height": 120, "rx": 28,
     "fill": {"type": "linear", "colors": ["#f59e0b", "#ef4444"], "angle": 45}, "shadow": {"dy": 10, "blur": 20}},
    {"type": "path", "d": "M30 88 L60 30 L90 88 Z", "fill": "#ffffff"}]},
  {"type": "text", "text": "Cominde Studio", "x": 90, "y": 340, "font": "Inter", "weight": 800, "size": 84, "fill": "#fff"},
  {"type": "text", "text": "استوديو تصميم بالذكاء الاصطناعي", "x": 1110, "y": 440, "anchor": "end",
   "font": "Cairo", "weight": 700, "size": 56, "fill": "#fde68a"}]}
```

## Known limits
- Inkscape actions differ slightly between 1.2 (tested here) and 1.3/1.4 (Mac Homebrew); the used actions exist in all 1.2+.
- Nested SVG in icon tiles is embedded as a data-URI `<image>` (renders in rsvg/browsers/Inkscape; for hand editing, edit the source SVG).
- Tracing photos gives a posterised illustration, not a faithful photo; logos with gradients trace as bands (raise `colors`).
- Arabic `letter_spacing` is ignored on purpose (it breaks letter joining).
