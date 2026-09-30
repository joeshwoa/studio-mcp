# Brand department — identity

Creates a brand kit from a brief and saves it as `STUDIO_HOME/brands/<slug>/brand.json` (+ `logo/`,
`previews/`, `guidelines/`). Every design tool accepts `brand=<slug>` and picks up colours, fonts and
the right logo variant for each background automatically.

## Tools

| tool | what it does |
|---|---|
| `brand_create` | Brief → palette, type pairing, 3–6 logo concepts, full logo suite for one, voice & tone notes; previews |
| `brand_logo_concepts` | Fresh concept set (optionally choose marks / wordmark fonts) |
| `brand_choose_logo` | Build the full suite for a concept, with tweaks (font, case, tracking, mark, accent dot) |
| `brand_palette` | Palette from a base colour or brief (harmonies, 50–900 ramps, WCAG pairs); can apply to a kit |
| `brand_type_pairing` | Arabic + Latin pairing by mood/id/families, bilingual specimen; can apply to a kit |
| `brand_update` | Edit kit fields (tagline, colours, fonts, voice…); colour changes rebuild the logos |
| `brand_show` | List kits / show one |
| `brand_guidelines` | Multi-page A4 landscape guidelines PDF (+ page PNGs) |
| `brand_apply` | Starter set: avatar, IG post/story/quote, LinkedIn/X/Facebook/YouTube covers, OG image, print business card, letterhead, email signature |

```bash
studio brand_create '{"name":"Cominde","name_ar":"كوميندي","brief":"AI-enabled mobile startup for Egyptian small businesses",
  "sector":"tech startup","personality":"modern, friendly, innovative","tagline":"Smart apps for real businesses"}'
studio brand_choose_logo '{"brand":"cominde","concept":"c2"}'
studio brand_guidelines '{"brand":"cominde"}'
studio brand_apply '{"brand":"cominde","contact":{"name":"Joshua George","title":"Founder","phone":"+20 …"}}'
```

## How it works

* **Palette** — built in OKLCH (perceptually even): primary from the base colour (or from sector /
  personality keywords), a deep secondary, a harmony accent (complementary, analogous, triadic, split,
  tetradic, monochrome), hue-tinted neutrals (ink, paper, surface, muted, line), 50–900 ramps. Text
  colours are pushed until they pass WCAG AA; all key pairs are measured and graded.
* **Type** — 12 curated Arabic + Latin pairings keyed by mood (modern, corporate, friendly, playful,
  luxury, editorial, heritage, bold, geometric, creative, news, egypt-classic) using Cairo, Tajawal,
  Almarai, IBM Plex Sans Arabic, Noto Kufi/Naskh, Readex Pro, El Messiri, Amiri, Alexandria, Changa,
  Reem Kufi, Aref Ruqaa, Baloo Bhaijaan 2… with Latin partners. Any Google font can be set.
* **Reading the brief** — `brand/brief.py` matches whole words (so "Cairo" is not "ai") against 17
  sectors (coffee, food, tech, finance, health, wellness, eco, education, kids, property, travel, luxury,
  heritage, sport, creative, logistics, community; Arabic keywords too). The sector gives a curated
  palette family (3–4 per sector; the brand name seeds which one, mood words like warm/calm/premium
  re-rank them), the type pairings that suit it and its subject marks. `sector=` forces it,
  `base_color=` overrides the palette. The chosen direction is saved in `brand.json → direction`.
* **Logos** — wordmarks are shaped with HarfBuzz (correct Arabic joining, kerning, ligatures) and
  outlined with fontTools, so SVGs contain paths only (no font dependency). Marks are geometric
  constructions on a 100-unit grid (monograms in circle/square/hexagon, letter-split, quarters,
  orbit, stack, petals, arch, spark, chevrons, leaf, wave, star8, sun) plus subject marks (cup, bean,
  bowl, drop, roof, book, shield, bars, bolt, heart, plus, pyramid, bubble, pin, sprout), chosen from
  the brief's sector — every concept set mixes subject marks with one lettermark, knock-outs
  via SVG masks so every variant stays transparent. Suite: horizontal & stacked lockups (bilingual
  when `name_ar` is set), icon, wordmark, Arabic wordmark, monogram — each in colour / reversed /
  mono black / mono white — plus app icon, favicon.svg/.ico and PNGs 16/32/48/180/192/512 + 2000 px lockups.
* **Voice** — rule-based starter notes from the personality words (traits, do/don't, sample lines in
  English and Egyptian Arabic). Clearly labelled as a starting point.
* **Guidelines PDF** — cover, contents, essence, voice & tone, logo, clear space & minimum sizes,
  variations, misuse, colour (HEX/RGB/CMYK), measured contrast pairs, typography (both scripts),
  applications (rendered from the design templates), back cover.

## Dependencies

`uharfbuzz`, `fonttools`, `brotli` (logo outlines), `playwright` (rendering), `segno` (QR, optional).

## Known limits

* Logos are typographic/geometric systems — clean and professional, but a designer (or the user)
  should pick and refine; they are not illustrative or hand-drawn marks. Check trademark availability.
* CMYK values are naive conversions — proof with the printer's ICC profile.
* Email signature logo must be hosted (pass `logo_url`); local file paths don't work in mail clients.
* Voice notes and sample copy are generated from rules, not from research into the audience.
