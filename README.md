# studio-mcp — a free creative team for AI agents

One MCP server (and a plain CLI) that lets Claude, Cursor, Antigravity or any
MCP client do the work of a whole content-creation team — using **only free
and open-source tools**, all driven headlessly by the AI:

| Department | Replaces | Engines (all free) | Tools |
|---|---|---|---|
| **design** | Canva / InDesign layouts | HTML/CSS → headless Chromium (Playwright), Google Fonts | social posts, carousels, stories, thumbnails, posters, flyers, business cards, certificates, menus, infographics — Arabic RTL, print PDF with bleed |
| **brand** | branding agency | own logo/palette/type engine + Chromium | brand kit from a brief: logo concepts & full suite, palette (WCAG), Arabic+Latin type, guidelines PDF, starter asset set |
| **photo** | Photoshop / Lightroom | Pillow, OpenCV, ImageMagick, rembg, Real-ESRGAN, **GIMP** (batch) | edits & looks, LUTs, background removal/replacement, retouch, upscale, **layered PSD + XCF** masters, mockups, collages |
| **vector** | Illustrator | SVG, **Inkscape** CLI, vtracer | compose, export (PDF/EPS/PNG, icon/favicon/app-icon sets), outline text, boolean ops, trace, recolour |
| **video** | Premiere / Resolve | ffmpeg, **MLT/Kdenlive**, PySceneDetect, OpenTimelineIO | JSON timeline → MP4 **and an editable .kdenlive project**, transitions, J/L cuts, titles, auto-captions (reels style, Arabic), silence cuts, face-following reframe, grading/LUTs, stabilise, speed, ducked music, platform exports; **pro:** auto-edit from a brief (VO + footage/stock b-roll + music on the beat + captions + titles), Descript-style transcript editing with Arabic/English filler removal, scene detection & take selection, beat detection & cut-to-beat, colour match / auto colour → .cube LUTs, broadcast/social QC, FCPXML / OTIO / EDL export for Resolve, Premiere, FCP |
| **motion** | After Effects / C4D | GSAP (all plugins) + Lottie + three.js frame-exact in Chromium, **Blender** (Cycles/EEVEE), Remotion (licence-gated) | `motion_plan` picks the engine; `motion_compose` (JSON scene spec → GSAP: 40 animation presets, 12 transitions, camera moves, charts, Lottie/3D layers, motion blur, grain/glow/light leaks); 11 pro templates (kinetic quote, social promo, app promo, logo sting, title sequence, chart story, map route…); Lottie render/recolour; 3D titles & logos (Arabic too), product turntables, device mockups; plus title cards, lower thirds, logo reveals, captions, CTAs, countdowns, transitions (alpha) |
| **stock** | Envato / Artgrid | official free APIs: Pexels, Pixabay, Unsplash, Coverr, Freesound, Jamendo (free keys) + Openverse, Wikimedia Commons, Internet Archive, NASA, Iconify (no key) | one search across every free stock site (video, photo, illustration, vector, music, SFX, icons), labelled contact sheets, verified downloads with licence sidecars, auto CREDITS for the description |
| **audio** | Audition / Audacity | faster-whisper, edge-tts, Piper, noisereduce, Pedalboard, Demucs, **Audacity** (mod-script-pipe) | transcription (Egyptian Arabic + English), voiceover (Egyptian voices), studio voice clean-up, mastering, ducked mixes, stems, procedural music & SFX, free-licence library search |
| **ai** | Midjourney / Runway | **ComfyUI** (FLUX-schnell, SDXL, LTX-Video, Wan), mflux (Apple Silicon), Pollinations & HF free tiers | generate/edit/upscale images, local video, prompt engineering; cinematic AI video hands off to Google Flow (flow-studio-director) |

Every tool returns file paths **plus preview images** (contact sheets, before/after,
safe-zone overlays, spectrograms) so the agent checks its own work, and measured
facts (loudness, sizes, contrast) instead of claims. Where a free desktop app
exists, the tool also writes that app's editable file — so a human can take over.

## Install

```bash
pip install "studio-mcp[all] @ git+https://github.com/joeshwoa/studio-mcp"
python -m playwright install chromium
studio doctor                       # what's installed, how to add the rest
studio studio_install '{"group":"apps"}'          # plan only; add "confirm":true to install
```
Apps (macOS): `brew install ffmpeg mlt librsvg imagemagick sox` and
`brew install --cask gimp inkscape kdenlive audacity blender krita` (all optional,
installed on demand). Outputs and AI models live in `STUDIO_HOME` (defaults to the
external SSD when mounted).

## Use

```bash
studio list                         # 132 tools by department
studio help design_create
studio design_create @brief.json    # args as JSON or @file
studio-mcp                          # MCP server over stdio
```
MCP client config: `{"command": "studio-mcp"}` (use the venv's absolute path).

## Honest limits

Local AI generation quality/speed must be checked on your Mac (models run there,
not in CI). Offline music is a procedural bed, not a composer. Egyptian-Arabic
transcription is good but not perfect — listen before publishing captions. Logos
are clean geometric/typographic systems; a human should choose and refine them.
See `docs/<department>.md` for each department's details and limits.

MIT licence. Built by Joshua George.
