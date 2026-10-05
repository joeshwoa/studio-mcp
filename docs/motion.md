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
| `motion_templates` | Catalog (incl. the pro templates below). |

All templates: `brand=<slug>` (brand kit colours on a dark stage, Arabic+Latin fonts, logo/icon),
`size` (`16:9`, `9:16`, `1:1`, `4:5`, `4k`, `WxH` — layouts scale with the short side), `fps`, `colors`,
`fonts` (families or a pairing/mood word), `formats`.

## Measured here (Linux, 2 CPUs, software rendering)
- 1080p title card, 5 s / 150 frames: ~32 s end to end (≈0.15 s/frame capture). Lower third 4 s alpha
  (MOV + WebM): ~24 s. Heavier pages (many animated SVG paths) ≈0.2 s/frame.
- Determinism test: a 1 s WAAPI move sampled at frame 5 lands at exactly x=100 px.
- The Mac (8+ cores, GPU raster) will be several times faster; not measured here.


---

# Pro motion engines (2025 upgrade)

Code: `motion/compose.py` + `assets/motion/compose.js|css` (GSAP runtime), `motion/presets.py` (templates),
`motion/lottie.py`, `motion/three_d.py` + `assets/motion3d/scene.js`, `motion/blender.py` +
`assets/blender/studio_bpy.py`, `motion/remotion.py`, `motion/router.py`, `motion/libs.py`.
Tests: `tests/test_motion_pro.py` (`-m slow` renders templates, three.js, Blender/fallback).

## Which engine? (`motion_plan` does this for you)

| The job | Engine / tool | Why |
|---|---|---|
| Typography, titles, lower thirds, infographics, charts, UI/app promos, 2D logo animation, transitions | **`motion_compose`** (HTML + GSAP) — default | fastest, crisp vector text, 40 presets + any GSAP tween, Arabic shaped by Chromium, editable spec + HTML |
| A designer-made animation (.json Bodymovin / .lottie) | **`motion_lottie`** (lottie-web) — or a `lottie` layer in a comp | plays exactly as designed; brand recolour, retime, loops, alpha |
| 3D text/logo, product spin, device mockup, particles — fast | **`motion_3d`** (three.js / WebGL) | seconds per second; env reflections, soft shadows, ACES, alpha |
| Photoreal 3D: glass, caustics, optical DOF, physical light; render time OK | **`motion_blender`** (Blender Cycles/EEVEE) | true path tracing, real DOF + motion blur, editable `.blend`; falls back to three.js (clearly labelled) if Blender is missing |
| Existing React/Remotion project, data-driven batches | **`motion_remotion`** | reuses the team's code; Node + licence confirmation required |

## JS libraries (`libs.py`)
Downloaded once from cdn.jsdelivr.net (exact npm versions: gsap 3.15.0 incl. CustomEase, SplitText,
MorphSVG, DrawSVG, MotionPath, ScrambleText, Flip, Physics2D, Text, CustomWiggle/Bounce; lottie-web 5.13.0;
three 0.186.1 + addons SVGLoader, GLTFLoader, RoomEnvironment, RoundedBoxGeometry, EffectComposer/Unreal
bloom; d3 7.9.0) into `STUDIO_HOME/cache/jslibs`, sha256 recorded in `lock.json` on first download and
verified on every use (a changed file is re-fetched, a mismatch refused). Pages load them as `file://`
scripts / an import map → fully offline after the first run.
**Licences:** GSAP (all plugins) is free incl. commercial use under the GSAP standard "no charge" licence
since 2025 — the one restriction is building no-code visual animation builders that compete with Webflow
(not what we do). lottie-web MIT, three.js MIT, d3 ISC. Remotion: free for individuals/orgs ≤ 3 people,
otherwise a company licence (remotion.dev/docs/license) — `motion_remotion` refuses without `licence_ok=true`.

## Renderer additions (`engine.py`)
- Virtual clock now also steps **Lottie** (`lottie.getRegisteredAnimations()` → `goToAndStop(frame, true)`;
  per-animation `anim.__studio = {start, speed, loop}`), honours `<video data-start data-rate data-loop>`,
  and makes `Math.random` deterministic (seeded) — glitches/particles look identical on every render.
- **Motion blur**: `motion_blur=N` renders N sub-frames across a 180° shutter (centred) and averages them
  (premultiplied alpha) — After Effects' "samples per frame". Costs N× render time.
- **Post**: `post_frame` glow (bloom), chromatic aberration, vignette; in-page vignette, light leaks,
  letterbox; static grain at encode time.
- `times=[…]` renders only chosen instants (used by `preview=true`); contact sheets are now
  **timecoded** (mm:ss:ff + seconds) for every motion tool, and new tools add 2–3 full key frames.

## `motion_compose` spec
```json
{"size": "1920x1080", "fps": 30, "brand": "acme",
 "background": {"type": "animated"},            // colour token | gradient {colors, angle} | animated | grid | dots | image/video path
 "post": {"grain": 4, "vignette": 0.35, "light_leaks": true, "letterbox": 2.39, "glow": 0.3, "chroma": 0, "motion_blur": 0},
 "camera": "drift",                              // whole-comp camera
 "markers": {"drop": 2.4},                        // usable as times: "drop+0.2"
 "scenes": [
  {"duration": 3, "camera": {"preset": "push-in", "amount": 0.08, "punch": [1.2]}, "background": "background",
   "layers": [
    {"type": "text", "text": "Make it *move*", "size": 140, "weight": 900, "y": "45%", "align": "center",
     "emph_color": "accent", "glow": "primary",
     "enter": {"preset": "split-chars", "style": "rise", "stagger": 0.04},
     "animate": [{"preset": "shine", "at": 1.4}], "exit": "fade-out"},
    {"type": "chart", "chart": "bar", "data": [{"label": "Q1", "value": 12}], "highlight": 0,
     "annotations": [{"index": 0, "text": "+74%"}]}]},
  {"duration": 3, "transition": {"type": "whip", "duration": 0.6}, "layers": []}]}
```
- **Layer types**: text · counter (`value`, `prefix`, `suffix`, `decimals`, `digits: arabic`) · shape
  (rect, circle, ellipse, ring, line, star, polygon, heart, triangle, arrow, path+`viewBox`; fill = colour or
  gradient; stroke, dash, radius) · image · video (trim, speed, loop) · svg (file or markup; `brand:logo`,
  `brand:icon`, `brand:stacked`) · lottie (src, speed, loop, recolor) · icon (built-in set: check arrow star
  heart play bolt rocket globe pin cart clock calendar chart mail phone user bell plus quote spark download)
  · chart (bar, hbar, line, area, pie, donut; real values to scale; count-up labels; annotations) ·
  3d (`scene` = motion_3d options) · group (children timed relative to the group = precomp).
- **Transform**: `x`, `y` (px, "50%", "12u" = 1080p units, "vw/vh") of the `anchor` (center, top-left, …,
  or [ax, ay]), `w`, `h`, `scale`, `rotation`, `opacity`, `skew`, `z` (2.5D depth, parallax under camera
  moves), `blend`, `z_index`. **Timing**: `in`, `out` (seconds in the scene, or "out-0.5", markers).
- **Animation presets** (`enter`, `exit`, `animate:[…]`, `loop_anim`; every preset takes `at`, `duration`,
  `ease`, `stagger`; `-out` suffix = exit): fade · slide (`from` side, `distance`, "offscreen") · rise ·
  drop · scale · zoom · pop/scale-pop (overshoot) · blur · elastic · bounce · flip/flip-3d · spin · swing ·
  wipe/mask/clip-reveal (`from`) · iris · block (colour block reveal) · split-chars / split-words /
  split-lines (`style`: rise, drop, fade, pop, blur, rotate, slide, scale, punch, elastic; masked) ·
  typewriter (caret) · scramble · highlight (marker) · underline · shine · glitch · draw (SVG stroke
  draw-on, fills after) · morph (`to`: path or circle/star/heart/…) · motion-path (`path`: [[dx,dy]…] or SVG
  d, `auto_rotate`) · counter · loops: wiggle, shake, float, parallax, pulse, spin-loop, kenburns.
  Also raw `keyframes:[{t, x, y, scale, rotation, opacity, ease}]` and `tweens:[{target: L|F|A|C|selector,
  from, to, at, duration, ease, stagger}]` (any GSAP property).
- **Easing**: any GSAP ease (power1–4, expo, sine, circ, back.out(1.7), elastic.out(1,0.4), bounce,
  steps(n)), `cubic-bezier(a,b,c,d)` (CustomEase), CSS-style `easeOutExpo` names, and aliases smooth,
  snappy, soft, overshoot, spring, bouncy, anticipate, cinematic, whip, linear.
- **Transitions** (scene `transition`, overlapping the scenes): cut · crossfade · dip (colour) · wipe (edge
  bar) · slide · push · zoom · blur · iris · whip (directional SVG blur; add motion_blur for real streaks)
  · shape (skewed panels) · glitch. **Camera** (scene or comp): push-in, pull-out, zoom-in/out, pan-*,
  tilt-*, drift, orbit (3D rotateY), roll, handheld/shake, `punch:[t…]` beat bumps, or keyframes.
- **Arabic**: text direction auto-detected; Chromium shapes it; split-chars/typewriter/scramble fall back to
  words for RTL — letters are never separated, so joining is always intact. Charts flip RTL.
- Outputs: video(s), `-spec.json` (editable), `-source.html` (standalone GSAP page; re-render with
  `motion_render_html`), timecoded sheet + key frames. `preview=true` = 12 stills only (seconds).

## Pro templates (`motion_compose(template=…, fields={…})`)
kinetic_quote · stat_reveal · social_promo · app_promo · logo_sting_pro · lower_third_pro (alpha) ·
title_sequence · chart_story · map_route (own SVG map or stylised dots — no copyrighted map data) ·
countdown_pro · transition_pro (alpha; stripes, diagonal, iris, blocks, slide; cut at the midpoint).
Every template lays out for 16:9 and 9:16 inside the title-safe area; `motion_templates` lists the fields.

## `motion_lottie`
`.json` or `.lottie` (dotLottie v1/v2, embedded images inlined). Frame-exact, `speed`, `loops`,
`duration`, `fit`, alpha, `recolor_map {"#from": "#to"}` or `recolor_mode` brand | mono:#hex (fills,
strokes, gradients, text, solids; static and animated). `data.palette` lists the colours used;
`data.lottie.features` flags expressions/effects/mattes (lottie-web supports most; AE effects beyond
fills/strokes/blur are not supported by any Lottie player).

## `motion_3d` (three.js)
Presets title (text → HarfBuzz-shaped outlines → ExtrudeGeometry — correct Arabic) · logo (SVG extruded +
bevelled) · product (.glb turntable on a shadow catcher) · device (phone/laptop, `screen` image or video)
· particles · abstract. Materials plastic, metal, chrome, gold, glass (transmission), matte, clay, neon,
satin; RoomEnvironment PMREM + key/rim lights, rotating environment for gliding reflections, ACES tone
mapping, optional bloom. Cameras orbit, push-in, dolly, crane, static, turntable. Deterministic (pure
function of t, seeded). Measured here (software WebGL, 2 CPUs): ~0.6 s/frame at 720p.

## `motion_blender`
Presets title, logo, turntable, abstract → curved cyclorama, area-light studio, AgX, real DOF (f/2.8)
and motion blur; EEVEE or CYCLES (OIDN denoise); quality draft/preview/final; saves the `.blend` before
rendering. Text is shaped first (HarfBuzz → SVG → Blender SVG importer → extruded curve). Runs `blender -b
--factory-startup -P studio_bpy.py -- job.json`, or a Python with the `bpy` wheel via
`STUDIO_BLENDER_PYTHON=/path/to/python` (verified here with bpy 4.2: Cycles CPU 128 spp 640×360 ≈ 40 s/frame
on 2 shared cores). Without Blender it falls back to motion_3d and says **FALLBACK** in the summary.

## `motion_remotion`
`project_dir`, `composition`, `props`, codec h264/h265/vp9/prores (+alpha); requires `licence_ok=true`
and, if `node_modules` is missing, `install=true` (runs npm install). Node missing → MissingTool.
