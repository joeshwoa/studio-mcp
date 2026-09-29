# Photo department (Photoshop replacement)

Code: `src/studio/depts/photo/` · Tests: `tests/test_photo.py` · Outputs: `STUDIO_HOME/projects/<project>/photo/`
(previews in `_previews/`). Every tool takes `project` and `out`, never overwrites, and returns previews
(before/after sheets, checkerboard for transparency) that the agent must look at.

## Engines (all free)

| Engine | Used for | Install (macOS / Linux) |
|---|---|---|
| Pillow + NumPy (libraqm for Arabic shaping) | everything pixel-level, text, blend modes, PSD writer | in the venv (`brew install libraqm` if Pillow reports no raqm) |
| OpenCV (`opencv-python-headless`) | face detection (YuNet DNN), saliency helpers, inpainting, Poisson blending, perspective warp, denoise, straighten | `pip install opencv-python-headless` |
| rembg + onnxruntime | background removal | `pip install "rembg[cpu]"` |
| Real-ESRGAN ONNX via onnxruntime | AI upscaling | model fetched on first use |
| GIMP 2.10 or 3.x (headless) | the editable `.xcf` master with LIVE text layers | `brew install --cask gimp` / `apt install gimp` |

### Model files (downloaded once into `STUDIO_HOME/models`, sha256-checked)

GitHub release downloads (rembg's own source) are often blocked, so we fetch from Hugging Face mirrors:

| File | Source | Size | Notes |
|---|---|---|---|
| `models/rembg/<name>.onnx` | `huggingface.co/tomjackson2023/rembg` | 5–180 MB | byte-identical to rembg's releases (md5 matches rembg's own checksums). We set `U2NET_HOME` to this folder so rembg finds them (works with old and new rembg layouts). Models: isnet-general-use (default), u2net, u2netp, u2net_human_seg, silueta, isnet-anime. |
| `models/upscale/realesr-general-x4v3.onnx` | `huggingface.co/CoderViking/realesr-general-x4v3-onnx` | 5 MB | ONNX export of the official Real-ESRGAN weights (BSD-3). |
| `models/faces/face_detection_yunet_2023mar.onnx` | `huggingface.co/opencv/face_detection_yunet` | 230 KB | OpenCV 5 dropped the Haar cascades; YuNet is used (Haar remains a fallback on OpenCV 4). |

Override mirrors with `STUDIO_REMBG_MIRROR` (base URL) and `STUDIO_UPSCALE_URL`. Offline: copy the files into those folders.

Fonts: text uses `fonts.find_font` — local files, system fonts (fontconfig / macOS font folders), otherwise a free
download from Google Fonts into `STUDIO_HOME/fonts` which is also copied to the user font folder
(`~/Library/Fonts` on macOS, `~/.local/share/fonts/studio` on Linux) so GIMP/Inkscape render the same family.
Default: Inter for Latin, Cairo for Arabic (Cairo also has Latin). Weight matching is exact (e.g. 800 → ExtraBold file).

## Tools (20)

| Tool | What it does |
|---|---|
| `photo_info` | size, format, EXIF, ICC, exposure stats (clipping), detected faces (preview with boxes) |
| `photo_resize` | fit / fill (smart crop) / pad (colour, transparent or blurred photo) / exact; scale or long_edge |
| `photo_smart_crop` | any ratio (`9:16`, `4:5`, `story`, 2.39…) keeping faces whole with headroom + saliency; optional AI subject mask; preview shows the chosen box |
| `photo_crop` | box / margins / auto-trim uniform or transparent borders |
| `photo_rotate` | rotate, flip, crop-to-clean-rectangle, auto-straighten (Hough lines; returns 0 when there's no horizon/architecture) |
| `photo_canvas` | extend canvas to size/ratio/padding with colour, transparency or blurred extension |
| `photo_convert` | PNG/JPG/WebP/AVIF/TIFF/GIF, quality, max side, ICC → sRGB, strip/keep EXIF, reports size saving |
| `photo_batch` | run any single-image photo tool over a folder → contact sheet |
| `photo_adjust` | exposure, contrast, highlights/shadows/whites/blacks, saturation, vibrance (skin-protecting), temperature/tint, curves (monotone), clarity, sharpen, NL-means denoise, vignette, grain, fade |
| `photo_auto_enhance` | grey-world WB, levels, CLAHE when flat, midtone lift, vibrance, sharpen (strength) |
| `photo_look` | 14 film/cinematic grades; exports the look as a 33³ `.cube` (same look in Resolve/Premiere/ffmpeg); `look='all'` makes a picker sheet |
| `photo_apply_lut` | any 3D `.cube` LUT, trilinear, strength |
| `photo_remove_background` | transparent PNG + mask; guided-filter edge refinement (hair), adaptive where edges are low-contrast; colour-fringe decontamination; optional contact/drop shadow; crop to subject |
| `photo_replace_background` | colour / linear / radial gradient / photo backdrop; subject scaling & placement (people cut by the frame stay anchored to the bottom edge with headroom); contact shadow; light-match + light wrap |
| `photo_retouch` | remove objects/blemishes by mask or regions: `auto` = diffusion for tiny spots, content-aware patch search + Poisson seamless clone for larger areas, grain matching. Generative fill for big areas belongs to the ai department. |
| `photo_upscale` | 2×/3×/4× Real-ESRGAN general x4v3 (tiled, CPU; CoreML on Mac when onnxruntime has it) or plain Lanczos (labelled "not AI"); preview is a 1:1 detail crop vs bicubic |
| `photo_compose_layers` | **the "Photoshop file"**: JSON layer spec → layered **PSD** + **XCF** (live GIMP text layers) + flattened PNG/JPG; layers sheet preview; verify = GIMP re-renders the XCF and the mean difference to our PNG is reported |
| `photo_mockup` | built-in generated scenes `phone`, `poster`, `cards`; or corner-pin into any photo (4 points) with lighting/texture carried into prints or glare on screens |
| `photo_collage` | grid / hero / mosaic, smart-cropped cells, gaps, rounded corners, captions (Arabic OK) |
| `photo_compare` | before/after side-by-side, stacked, or split with a slider handle; Arabic labels OK |

## Layer spec (`photo_compose_layers`)

```json
{"width": 1080, "height": 1350, "background": "linear:#0b1020,#1e293b,90",
 "layers": [
  {"type": "image", "name": "Photo", "src": "/path/lake.jpg", "x": 0, "y": 0, "width": 1080, "height": 820},
  {"type": "shape", "name": "Accent", "shape": "rect", "x": 80, "y": 900, "width": 120, "height": 10, "radius": 5,
   "fill": {"type": "linear", "colors": ["#f59e0b", "#ef4444"], "angle": 0}},
  {"type": "text", "name": "العنوان", "text": "رحلة البحيرة — عرض خاص النهارده بس", "font": "Cairo", "weight": 800,
   "size": 84, "color": "#ffffff", "x": 80, "y": 930, "width": 920, "align": "right", "shadow": {"dy": 6, "blur": 18}},
  {"type": "fill", "name": "Warm glow", "fill": "radial:#f59e0b66,#00000000", "blend": "screen", "opacity": 0.6}]}
```

Layer types: `image` (fit cover/contain/stretch, smart crop, radius, rotate, remove_background), `text` (wrap box,
align, direction auto/rtl/ltr, line_height, letter_spacing (Latin only), stroke, uppercase), `shape`
(rect/ellipse/polygon/line, gradient fills, stroke), `fill` (colour or gradient). All: `opacity`, `blend` (17 PS modes),
`visible`, `shadow` (written as its own editable layer below).

What is editable where:
- **PSD** (own dependency-free writer, validated with psd-tools): named pixel layers (Unicode names), positions, blend
  modes, opacity, visibility, merged composite. Text is pixels — Photoshop's editable text needs Adobe's text-engine
  data, which only Photoshop writes.
- **XCF** (GIMP loads our PSD, then each text layer is replaced by a real GIMP text layer with the same font, size,
  colour, justification, RTL direction, line spacing). Verified: GIMP 2.10.36 (Script-Fu path, mean diff ≈4.8/255)
  and GIMP 3.2.6 (Python-Fu path, mean diff ≈3/255). An XCF saved by GIMP 3 may not open in GIMP 2.10.
  GIMP 2.10 mirrors left/right justification for RTL text — handled. Text stroke is not a GIMP text property.
- Set `STUDIO_GIMP=/path/to/gimp-console-3.x` to force a specific GIMP. On macOS the bundle
  (`/Applications/GIMP.app/Contents/MacOS/gimp-console*`) is detected automatically.

## Known limits
- Background removal: quality depends on the model; holes inside objects (a watch buckle) may stay filled.
- Retouch is classic (non-generative): great for spots/wires/small objects on texture; large removals need AI inpaint.
- Auto-straighten needs straight lines; it declines (0°) on organic scenes rather than guessing.
- Mockup scenes are procedurally drawn (no stock photos): clean studio look, not photoreal props.
- Upscaling invents plausible detail — check faces and text in the 1:1 preview.
