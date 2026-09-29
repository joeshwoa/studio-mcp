# Motion department (After Effects replacement)

Code: `src/studio/depts/motion/` (`engine.py` renderer, `__init__.py` tools) · templates:
`src/studio/assets/motion/` (`kit.js`/`kit.css` shared helpers + one HTML file per template) ·
tests: `tests/test_motion.py` (`-m slow` renders every template/style at 320×180).

## How it renders (deterministic)
Headless Chromium (Playwright) loads the page with a **virtual clock** injected before any script:
`performance.now`, `Date`, `requestAnimationFrame`, `setTimeout/Interval`, every Web Animation / CSS
animation, GSAP timelines and `<video>` elements are driven to the exact frame time before each
screenshot (CDP `captureScreenshot`, transparent background for alpha). A slow machine produces the same
frames as a fast one. Frames → ffmpeg: **MP4** (H.264), **MOV ProRes 4444 with alpha**
(yuva444p12le), **WebM VP9 alpha** (alpha_mode=1), GIF, or the PNG sequence.

- Template scripts run only after the web fonts are loaded (`window.__ready` gate), so text fitting
  measures the real font.
- Templates render in parallel pages (contiguous frame chunks; `min(4, cores/2)` workers). The power tool
  `motion_render_html` defaults to 1 worker so stateful rAF/canvas loops stay exact.
- Film grain is a *static* ffmpeg noise texture added at encode time: in-browser SVG noise was 5×
  slower to capture and temporal grain made files 25× bigger.
- Fonts: Google Fonts via the design dept (`design/fonts.py`), one CSS family per role with the Arabic
  face on Arabic code points. Arabic text is animated per WORD (never per letter), so joining is kept.
- Every output: contact sheet (alpha shown over a checkerboard) to LOOK at, QC data (blank frames, alpha
  coverage, motion), the editable `-source.html`.

| Tool | Styles / notes |
|---|---|
| `motion_title_card` | bold · minimal (letter blur-in, word-level for Arabic) · split (colour panels). `*word*` = accent. Opaque or alpha. |
| `motion_lower_third` | modern (bar + wiping panels) · glass (card + initials/photo badge) · line. Title-safe, higher on 9:16, auto right-aligned for Arabic. Alpha. |
| `motion_logo_reveal` | draw (SVG strokes draw on → fills) · pop (spring + rings + particles) · mask (iris + ring + light sweep). SVG or PNG, or `brand=<slug>` uses the kit's reversed lockup + tagline. |
| `motion_kinetic_text` | punch (beats punch in, accent highlight boxes) · stack (lines stack, earlier ones dim) · type (typewriter; Arabic types by word). Beats = new lines or `|`. |
| `motion_captions` | pop · karaoke · clean animated caption overlay (alpha) from SRT/segments or exact `words` (e.g. audio_transcribe `data.words`). For burned captions use `video_captions` (libass, much faster). |
| `motion_cta` | subscribe (cursor click → Subscribed, bell, like) · follow (avatar/handle, + → ✓) · link ("link in bio" + bouncing arrow). |
| `motion_countdown` | ring · bold, Latin or Arabic digits, optional end word. |
| `motion_transition` | diagonal · circle · bars · split shape wipes (alpha); frame fully covered at `data.cut_at`. |
| `motion_counter` | 1–4 counting stats with prefix/suffix, per-item decimals, progress underline. |
| `motion_infographic` | bars · columns · donut — values drawn to scale, counting labels, Arabic labels flip the chart RTL. |
| `motion_render_html` | Render ANY HTML/CSS/JS/GSAP/canvas animation frame-exactly (hooks: `window.__duration`, `window.__ready`, `window.__onseek(t)`). |
| `motion_templates` | Catalog. |

All templates: `brand=<slug>` (brand kit colours on a dark stage, Arabic+Latin fonts, logo/icon),
`size` (`16:9`, `9:16`, `1:1`, `4:5`, `4k`, `WxH` — layouts scale with the short side), `fps`, `colors`,
`fonts` (families or a pairing/mood word), `formats`.

## Measured here (Linux, 2 CPUs, software rendering)
- 1080p title card, 5 s / 150 frames: ~32 s end to end (≈0.15 s/frame capture). Lower third 4 s alpha
  (MOV + WebM): ~24 s. Heavier pages (many animated SVG paths) ≈0.2 s/frame.
- Determinism test: a 1 s WAAPI move sampled at frame 5 lands at exactly x=100 px.
- The Mac (8+ cores, GPU raster) will be several times faster; not measured here.
