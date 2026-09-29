# AI department: image and video generation with free tools

Code: `src/studio/depts/ai/` (tools.py, backends.py, comfy.py, catalog.py, prompting.py, outputs.py, byok.py,
setup_worker.py). ComfyUI workflow templates: `src/studio/assets/comfy/*.json`. Tests: `tests/test_ai.py`.

## Tools

| tool | what it does |
|---|---|
| `ai_backends` | Start here. Shows which backends work right now, which one the router will pick for each task and why, the model catalogue with licences, and speed estimates for a 16 GB Mac. |
| `ai_prompt_enhance` | Turns a short idea into a prompt shaped for one model (flux, sdxl, ltxv, wan, or veo for Flow). Uses fixed templates, not an LLM. |
| `ai_generate_image` | Text to image with the best free backend available. Returns PNGs at the exact size you asked for, a sidecar JSON for each and a labelled contact sheet. |
| `ai_edit_image` | `img2img`, `inpaint` (with a mask PNG or `mask_box`) or `outpaint` (`expand_to='9:16'` or pixel values). Pixels you did not ask to change stay exactly as they were. Shows a before/after preview. |
| `ai_upscale` | 2× to 8×. Uses Real-ESRGAN in ComfyUI, or SeedVR2 through mflux, or falls back to Lanczos (labelled as not AI). Includes a 100% detail comparison. |
| `ai_generate_video` | Text or image to video. Runs locally on ComfyUI (LTX-Video 2B, Wan 2.1 1.3B or Wan 2.2 5B), **or hands off to the `flow-studio-director` skill** (Veo 3.1 in Google Flow) when the video has characters, dialogue, speech, Arabic lines or cinematic storytelling. The hand-off writes a brief JSON and generates nothing here. |
| `comfyui_setup` (installs) | Clones ComfyUI and ComfyUI-GGUF, creates a venv, installs torch (Metal on the Mac) and downloads the chosen model bundles into `STUDIO_HOME/models/comfyui`. The dry run lists sizes and licences. `confirm=true` runs it in the background with resumable downloads. |
| `mflux_setup` (installs) | Apple Silicon only. Runs pip install mflux and pre-downloads a model into `STUDIO_HOME/models/hf`. |
| `comfyui_start` / `comfyui_stop` / `comfyui_status` | Start or stop the server, which runs detached with logs in `STUDIO_HOME/logs`. `stop free_only=true` unloads the models. `comfyui_status` shows health, installed bundles and setup progress. |
| `comfyui_run_workflow` | Runs any workflow exported with ComfyUI's "Export (API)", or a studio template, with `$placeholders` filled in. It uploads local images and validates the workflow before queueing. |
| `comfyui_collect` | Picks up a long job that was started with `wait=false` or that timed out. |
| `ai_generate_image_byok` (spends) | Paid, opt-in: fal, Replicate or OpenAI with the user's own key. `confirm=false` shows the price and spends nothing. The automatic router never uses these. |

## Router (backend="auto")

| task | order tried |
|---|---|
| text → image | **mflux** (Apple Silicon, model cached) → **ComfyUI** (running with an image bundle) → **Hugging Face** free tier (only if `HF_TOKEN` is set) → **Pollinations** (no key) |
| edit (img2img / inpaint / outpaint) | ComfyUI → mflux (img2img only). Otherwise it stops with a clear error, because no free cloud API does mask editing without a key. |
| upscale | ComfyUI + Real-ESRGAN → mflux SeedVR2 → Lanczos with unsharp (warns that this is not AI) |
| video | Characters, dialogue, sound or Arabic → Flow hand-off. Otherwise ComfyUI if a video bundle is installed. With no local model it falls back to the Flow hand-off. |

Every result names the backend and model it used, the licence and the commercial-use verdict. It also records the route explanation in the sidecar JSON.

## Model bundles

All sizes were checked live on Hugging Face in September 2026.

| id | size | licence / commercial use | 16 GB Mac estimate (not measured) |
|---|---|---|---|
| flux-schnell-q4 (default) | 10.8 GB | Apache-2.0, yes | 60–150 s per 1024² image |
| flux-schnell-q5 | 12.2 GB | Apache-2.0, yes | 70–170 s |
| sdxl-lightning | 6.9 GB | OpenRAIL++, yes | 15–40 s; best for editing |
| sdxl-turbo | 6.9 GB | Stability Community, only if revenue is under US$1M | 3–8 s at 512² (drafts) |
| realesrgan-x4 (default) | 67 MB | BSD-3, yes | 5–20 s |
| ultrasharp-4x | 67 MB | CC BY-NC-SA, **no** | 5–20 s |
| ltxv-2b | 9.7 GB | LTXV Open Weights, yes if revenue is under US$10M | 3–10 min per 4 s clip at 768×512 |
| wan21-1.3b | 7.2 GB | Apache-2.0, yes | 10–25 min per 2 s clip at 832×480 |
| wan22-5b-q4 | 9.0 GB | Apache-2.0, yes | 20–45 min per 2 s clip (overnight batches) |

Decisions:
- **GGUF, not fp8.** Apple's MPS backend has no float8 support, so fp8 checkpoints either fail or get upcast.
- **FLUX-dev is not offered** because its licence is non-commercial. FLUX-schnell is Apache-2.0.
- **The FLUX VAE comes from the ungated Comfy-Org repackage.** BFL's own repo is gated.
- **Shared text encoders download once:** the T5 GGUF is used by both FLUX and LTX, and UMT5 by both Wan models.
- **Local video is saved as PNG frames.** The studio then assembles an H.264 MP4 with ffmpeg, which keeps it independent of ComfyUI's changing video-save nodes.

mflux (MLX): `schnell-q4` (pre-quantised, 9.6 GB, ungated), `schnell-q8` and `z-image-turbo` (Apache-2.0, very good
photorealism, quantised to 4-bit when it loads). The command is built for the mflux 0.20 CLI:
```
mflux-generate --model mflux-community/flux-1-schnell-mflux-q4 --base-model schnell \
  --prompt … --width … --height … --steps 4 --seed S1 S2 --output …/img_{seed}.png [--image init.png 0.40] [--low-ram]
```
`HF_HOME` is pointed at `STUDIO_HOME/models/hf` so the weights live on the SSD. Set `STUDIO_MFLUX_LOW_RAM=1` if the Mac runs short of memory.

## Honest limits of the free cloud backends
- **Pollinations anonymous tier (tested live on 2026-09-26/29):** it serves the **sana** model even when you ask for flux, returns images smaller than requested (about 684×864 for a 912×1152 request), and **stamps a "pollinations.ai" logo** despite `nologo=true`.
  - The tool reads the served model from EXIF, records the real generated size and warns about both.
  - Use it for drafts and moodboards only. A free `POLLINATIONS_TOKEN` is sent if you set one.
- **Hugging Face Inference:** it spends your monthly free credits. HTTP 402 is reported as "credits used up" together with the free alternatives, and the tool never switches on paid billing.

## Arabic
The models only understand English captions, and there is no offline machine translation. **The agent writes the English prompt itself.** Prompts containing Arabic, or Arabic in quotes, trigger a warning. Arabic text for an image should be added afterwards with the design department (which does proper RTL shaping), and Arabic *dialogue* in video goes to the Flow hand-off, since Veo 3.1 speaks it.

## Outputs
Files are written to `projects/<project>/ai-images | ai-edits | ai-upscaled | ai-video | comfyui/`. Nothing is ever overwritten.
- Each PNG carries `studio:*` tEXt chunks and has a sidecar `.json` with: prompt, final_prompt, negative, seed, backend, model, licence, commercial_use, size, generated_size, resample_factor, steps, seconds, QC metrics and the route.
- Previews go in `_previews/` next to the outputs: labelled contact sheets, before/after comparisons, 100% detail crops, and video contact sheets.
- QC flags flat or blank images (for example safety-filter black frames), images that are too dark or too bright, blown highlights, softness, and any upscale beyond 1.25×.

## Environment variables
- `COMFYUI_URL`, `COMFYUI_DIR`
- `HF_TOKEN`, `STUDIO_HF_ENDPOINT` (the model mirror)
- `POLLINATIONS_TOKEN`
- `FAL_KEY`, `REPLICATE_API_TOKEN`, `OPENAI_API_KEY` (paid only)
- `STUDIO_MFLUX_LOW_RAM`
- Test overrides: `STUDIO_POLLINATIONS_URL`, `STUDIO_HF_ROUTER`, `STUDIO_<PROVIDER>_URL`, `COMFYUI_TEST_URL`

## Verified here and still to verify on the Mac
**Verified on Linux, CPU only:**
- A real ComfyUI 0.37.0 with ComfyUI-GGUF was installed, started and stopped with the studio tools.
- Every template was validated against that server's live `/object_info`.
- Real Real-ESRGAN ×2 upscales ran end to end through the tools: upload, queue, poll, download.
- The download and resume path was exercised.
- Pollinations was tested live.
- The rest of the chain was tested against fake ComfyUI, Hugging Face, Pollinations and fal servers.

**To verify on the Mac:**
- Actual FLUX, SDXL, LTX and Wan generation quality and speed on MPS.
- The mflux runs.
- Whether the Wan fp16 1.3B model produces black frames on MPS. If it does, `comfyui_start lowvram=true` or a GGUF Wan is the fix.
