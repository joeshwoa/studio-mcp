"""Image backends (all free by default) + the router that picks the best AVAILABLE one.

  mflux        local, Apple-Silicon native (MLX) — fastest local path on the Mac
  comfyui      local server, most flexible (edit/inpaint/outpaint/upscale/video)
  hf           Hugging Face Inference free tier (HF_TOKEN; monthly free credits, rate-limited)
  pollinations free cloud, no key — DRAFT quality, watermark, model chosen by the service
Paid BYO-key providers (fal / replicate / openai) live in byok.py behind spends=True.
"""
from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ...config import models_dir
from ...core.result import ToolError
from . import comfy
from .catalog import BUNDLES, MFLUX_MODELS, MFLUX_UPSCALE, gen_size

IS_ARM_MAC = platform.system() == "Darwin" and platform.machine() == "arm64"
UA = {"User-Agent": "studio-mcp/0.1 (+free creative studio)"}

IMAGE_PREFERENCE = ["flux-schnell-q4", "flux-schnell-q5", "sdxl-lightning", "sdxl-turbo"]
EDIT_PREFERENCE = ["sdxl-lightning", "flux-schnell-q4", "flux-schnell-q5", "sdxl-turbo"]
UPSCALE_PREFERENCE = ["realesrgan-x4", "ultrasharp-4x"]


@dataclass
class Gen:
    image: Image.Image
    seed: int
    backend: str
    model: str
    licence: str
    commercial: str
    gen_size: tuple[int, int]
    steps: int | None = None
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


# ============================================================== availability
def hf_home() -> Path:
    p = models_dir() / "hf"
    p.mkdir(parents=True, exist_ok=True)
    return p


def mflux_installed() -> bool:
    return shutil.which("mflux-generate") is not None


def mflux_cached(model_id: str) -> bool:
    m = MFLUX_MODELS[model_id]
    return (hf_home() / "hub" / ("models--" + m.repo.replace("/", "--"))).exists()


def comfy_client() -> comfy.ComfyClient | None:
    c = comfy.ComfyClient()
    return c if c.alive() else None


def live_model_files(c: comfy.ComfyClient) -> set[str]:
    """Every model filename the running ComfyUI can see (from its loader nodes)."""
    names: set[str] = set()
    try:
        info = c.object_info()
    except Exception:
        return names
    for ct, node in info.items():
        if "Loader" not in ct:
            continue
        for grp in ("required", "optional"):
            for k, spec in (node.get("input", {}).get(grp) or {}).items():
                if "name" not in k:
                    continue
                opts = comfy._spec_options(spec) or []
                names.update(o for o in opts if isinstance(o, str))
    return names


def comfy_bundles(c: comfy.ComfyClient | None, kind: str) -> list[str]:
    """Bundles usable right now: every file visible to the live server (or on the studio drive)."""
    live = live_model_files(c) if c else set()
    ok = []
    for bid, b in BUNDLES.items():
        if b.kind != kind:
            continue
        if all(f.filename in live for f in b.files) or (not live and comfy.bundle_installed(b)):
            ok.append(bid)
    return ok


def hf_token() -> str:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""


# ============================================================== router
def route(task: str, backend: str = "auto", model: str = "") -> tuple[str, list[str]]:
    """→ (backend, explanation lines). task: t2i | img2img | inpaint | outpaint | upscale."""
    why: list[str] = []
    c = comfy_client()
    if backend != "auto":
        return backend, [f"backend '{backend}' requested explicitly"]
    if task == "t2i":
        mid = model if model in MFLUX_MODELS else "schnell-q4"
        if IS_ARM_MAC and mflux_installed() and mflux_cached(mid):
            return "mflux", [f"mflux + {mid} is installed on this Apple-Silicon Mac — fastest local option"]
        why.append("mflux: " + ("not an Apple-Silicon Mac" if not IS_ARM_MAC else
                                "not installed (mflux_setup)" if not mflux_installed() else f"model {mid} not downloaded yet (mflux_setup)"))
        if c and comfy_bundles(c, "image"):
            return "comfyui", why + [f"ComfyUI running with {', '.join(comfy_bundles(c, 'image'))}"]
        why.append("comfyui: " + ("no image model installed (comfyui_setup)" if c else "not running (comfyui_start / comfyui_setup)"))
        if hf_token():
            return "hf", why + ["HF_TOKEN set → Hugging Face free-tier inference (FLUX.1-schnell)"]
        why.append("hf: no HF_TOKEN")
        return "pollinations", why + ["falling back to Pollinations (free, no key; draft quality + watermark)"]
    if task in ("img2img", "inpaint", "outpaint"):
        if c and comfy_bundles(c, "image"):
            return "comfyui", [f"ComfyUI running with {', '.join(comfy_bundles(c, 'image'))}"]
        why.append("comfyui: " + ("no image model installed" if c else "not running"))
        if task == "img2img" and IS_ARM_MAC and mflux_installed() and mflux_cached("schnell-q4"):
            return "mflux", why + ["mflux img2img (FLUX-schnell) on this Mac"]
        raise ToolError(f"No free local backend can do {task} right now: " + "; ".join(why),
                        "comfyui_setup (dry run shows sizes) then comfyui_start. Free cloud APIs don't offer "
                        "mask-based editing without a paid key.")
    if task == "upscale":
        if c and comfy_bundles(c, "upscale"):
            return "comfyui", [f"ComfyUI running with {', '.join(comfy_bundles(c, 'upscale'))}"]
        why.append("comfyui: " + ("no upscale model (comfyui_setup models=['realesrgan-x4'])" if c else "not running"))
        if IS_ARM_MAC and shutil.which(MFLUX_UPSCALE["cli"]) and \
                (hf_home() / "hub" / ("models--" + MFLUX_UPSCALE["repo"].replace("/", "--"))).exists():
            return "mflux", why + ["mflux SeedVR2 upscaler"]
        return "lanczos", why + ["no AI upscaler available → high-quality Lanczos + unsharp (NOT AI detail)"]
    raise ToolError(f"unknown task {task}")


# ============================================================== pollinations
def pollinations(prompt: str, w: int, h: int, seed: int, model: str = "flux", timeout: float = 180) -> Gen:
    base = os.environ.get("STUDIO_POLLINATIONS_URL", "https://image.pollinations.ai").rstrip("/")
    q = {"width": w, "height": h, "seed": seed, "model": model or "flux", "nologo": "true", "private": "true",
         "enhance": "false", "safe": "false"}
    url = f"{base}/prompt/{urllib.parse.quote(prompt[:1800], safe='')}?{urllib.parse.urlencode(q)}"
    headers = dict(UA)
    tok = os.environ.get("POLLINATIONS_TOKEN", "")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    t0 = time.time()
    data = b""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 3:
                time.sleep(6 * (attempt + 1))
                continue
            raise ToolError(f"Pollinations returned HTTP {e.code}", "rate-limited or down: wait a minute, or use another backend")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ToolError(f"Pollinations not reachable ({getattr(e, 'reason', e)})", "check the internet connection")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ToolError("Pollinations did not return an image: " + data[:200].decode("utf-8", "replace"))
    served = model
    try:
        make = im.getexif().get(271)  # EXIF Make — Pollinations writes the model that actually ran
        if make:
            served = str(make)
    except Exception:
        pass
    return Gen(im.convert("RGB"), seed, "pollinations", f"pollinations:{served}", "Pollinations terms (free tier)",
               "check pollinations.ai terms; free tier adds a watermark", im.size, None, time.time() - t0,
               {"requested_model": model, "served_model": served, "watermark": not bool(tok), "requested_size": [w, h]})


# ============================================================== Hugging Face
def hf_generate(prompt: str, negative: str, w: int, h: int, seed: int, model: str = "", steps: int = 0,
                timeout: float = 180) -> Gen:
    tok = hf_token()
    if not tok:
        raise ToolError("Hugging Face backend needs HF_TOKEN (free account → Settings → Access Tokens, 'read' scope)")
    model = model or "black-forest-labs/FLUX.1-schnell"
    base = os.environ.get("STUDIO_HF_ROUTER", "https://router.huggingface.co").rstrip("/")
    params = {"width": w, "height": h, "seed": seed}
    if steps:
        params["num_inference_steps"] = steps
    elif "schnell" in model.lower():
        params["num_inference_steps"] = 4
    if negative:
        params["negative_prompt"] = negative
    body = json.dumps({"inputs": prompt, "parameters": params}).encode()
    headers = {**UA, "Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": "image/png"}
    t0 = time.time()
    for attempt in range(4):
        req = urllib.request.Request(f"{base}/hf-inference/models/{model}", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code == 503 and attempt < 3:        # model loading
                time.sleep(10)
                continue
            if e.code == 402:
                raise ToolError("Hugging Face free inference credits are used up for this month",
                                "use backend=pollinations/comfyui/mflux (free) — do NOT enable paid billing without the user's OK")
            if e.code == 429 and attempt < 3:
                time.sleep(15)
                continue
            raise ToolError(f"Hugging Face inference error {e.code}: {msg}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ToolError(f"Hugging Face not reachable ({getattr(e, 'reason', e)})")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ToolError("Hugging Face did not return an image: " + data[:200].decode("utf-8", "replace"))
    lic = "Apache-2.0" if "schnell" in model.lower() else "see model card"
    return Gen(im.convert("RGB"), seed, "hf", model, lic, "yes" if lic == "Apache-2.0" else "check the model card",
               im.size, params.get("num_inference_steps"), time.time() - t0)


# ============================================================== mflux
def mflux_cmd(model_id: str, prompt: str, w: int, h: int, seeds: list[int], out_pattern: str, steps: int = 0,
              image: str = "", strength: float = 0.4, low_ram: bool = False) -> list[str]:
    m = MFLUX_MODELS.get(model_id)
    if m is None:
        raise ToolError(f"unknown mflux model {model_id!r}", f"one of {sorted(MFLUX_MODELS)}")
    cmd = [m.cli, "--model", m.model_arg]
    if m.base_model:
        cmd += ["--base-model", m.base_model]
    if m.quantize:
        cmd += ["--quantize", str(m.quantize)]
    cmd += ["--prompt", prompt, "--width", str(w), "--height", str(h), "--steps", str(steps or m.steps),
            "--seed", *[str(s) for s in seeds], "--output", out_pattern]
    if image:
        cmd += ["--image", image, f"{strength:.2f}"]
    if low_ram:
        cmd.append("--low-ram")
    return cmd


def mflux_env() -> dict:
    return {**os.environ, "HF_HOME": str(hf_home()), "MFLUX_CACHE_DIR": str(models_dir() / "mflux-cache")}


def mflux_generate(prompt: str, w: int, h: int, seeds: list[int], model_id: str = "schnell-q4", steps: int = 0,
                   image: str = "", strength: float = 0.4) -> list[Gen]:
    if not IS_ARM_MAC:
        raise ToolError("mflux runs only on Apple-Silicon Macs (MLX)", "use backend=comfyui, hf or pollinations here")
    if not mflux_installed():
        raise ToolError("mflux is not installed", "mflux_setup (dry run shows the download size)")
    m = MFLUX_MODELS[model_id]
    tmp = Path(tempfile.mkdtemp(prefix="mflux-"))
    cmd = mflux_cmd(model_id, prompt, w, h, seeds, str(tmp / "img_{seed}.png"), steps, image, strength,
                    low_ram=os.environ.get("STUDIO_MFLUX_LOW_RAM") == "1")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, env=mflux_env(), timeout=3600)
    if r.returncode != 0:
        raise ToolError("mflux failed: " + (r.stderr or r.stdout)[-800:],
                        "on 16 GB try STUDIO_MFLUX_LOW_RAM=1 or a smaller size")
    dt = (time.time() - t0) / max(1, len(seeds))
    gens = []
    for s in seeds:
        p = tmp / f"img_{s}.png"
        if not p.exists():
            raise ToolError(f"mflux produced no file for seed {s}: {r.stdout[-300:]}")
        gens.append(Gen(Image.open(p).convert("RGB"), s, "mflux", f"mflux:{m.id}", m.licence, m.commercial, (w, h),
                        steps or m.steps, dt, {"cmd": cmd}))
    shutil.rmtree(tmp, ignore_errors=True)
    return gens


# ============================================================== ComfyUI image jobs
def comfy_params_for(bundle_id: str) -> dict:
    b = BUNDLES[bundle_id]
    names = {f.folder: f.filename for f in b.files}
    if b.family == "flux":
        fs = {f.filename: f for f in b.files}
        t5 = next(n for n in fs if n.startswith("t5"))
        clip = next(n for n in fs if n.startswith("clip_l"))
        return {"unet": names["diffusion_models"], "t5": t5, "clip_l": clip, "vae": names["vae"],
                "latent_class": "EmptySD3LatentImage"}
    if b.family in ("sdxl", "sdxl-turbo"):
        return {"ckpt": names["checkpoints"], "latent_class": "EmptyLatentImage"}
    raise ToolError(f"{bundle_id} is not an image model")


def pick_bundle(c: comfy.ComfyClient | None, kind: str, model: str, prefs: list[str]) -> str:
    if model:
        if model not in BUNDLES or BUNDLES[model].kind != kind:
            raise ToolError(f"unknown {kind} model {model!r}", f"one of {[b for b in BUNDLES if BUNDLES[b].kind == kind]}")
        return model
    avail = comfy_bundles(c, kind)
    for p in prefs:
        if p in avail:
            return p
    raise ToolError(f"ComfyUI has no {kind} model installed", f"comfyui_setup models=['{prefs[0]}'] (dry run first)")


def loader_for(bundle_id: str) -> dict:
    fam = BUNDLES[bundle_id].family
    return comfy.load_template("loader_flux_gguf" if fam == "flux" else "loader_checkpoint")


def comfy_run_images(workflows: list[tuple[dict, dict]], timeout: float = 1800) -> list[tuple[dict, list[Path], float]]:
    """Queue several workflows, wait for all; → [(meta, downloaded files, seconds)]."""
    c = comfy_client()
    if c is None:
        raise ToolError("ComfyUI is not running", "comfyui_start (after comfyui_setup)")
    subs = []
    for wf, meta in workflows:
        _, pid = comfy.submit(wf, meta, c)
        subs.append((pid, meta, time.time()))
    out = []
    tmpd = Path(tempfile.mkdtemp(prefix="comfy-"))
    for pid, meta, t0 in subs:
        entry = c.wait(pid, timeout=timeout)
        files = c.download_outputs(entry, tmpd / pid)
        if not files:
            raise ToolError("ComfyUI finished but returned no images")
        out.append((meta, files, time.time() - t0))
    return out


def comfy_generate(prompt: str, negative: str, target: tuple[int, int], seeds: list[int], model: str = "",
                   steps: int = 0) -> list[Gen]:
    c = comfy_client()
    bid = pick_bundle(c, "image", model, IMAGE_PREFERENCE)
    b = BUNDLES[bid]
    d = b.defaults
    w, h = gen_size(target, d["area"], d["multiple"])
    tpl, loader = comfy.load_template("t2i"), loader_for(bid)
    jobs = []
    for s in seeds:
        params = {**comfy_params_for(bid), "prompt": prompt, "negative": negative or "", "seed": s,
                  "steps": steps or d["steps"], "cfg": d["cfg"], "sampler": d["sampler"], "scheduler": d["scheduler"],
                  "width": w, "height": h, "batch": 1, "prefix": "studio/t2i"}
        jobs.append((comfy.fill(tpl, params, loader), {"kind": "t2i", "seed": s, "bundle": bid}))
    res = comfy_run_images(jobs)
    return [Gen(Image.open(files[0]).convert("RGB"), meta["seed"], "comfyui", bid, b.licence, b.commercial, (w, h),
                steps or d["steps"], dt) for meta, files, dt in res]


# ============================================================== post-processing
def fit_cover(im: Image.Image, target: tuple[int, int]) -> Image.Image:
    """Scale to cover the target and centre-crop to the exact size (Lanczos)."""
    tw, th = target
    if im.size == (tw, th):
        return im
    s = max(tw / im.width, th / im.height)
    nw, nh = max(tw, round(im.width * s)), max(th, round(im.height * s))
    im2 = im.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - tw) // 2, (nh - th) // 2
    return im2.crop((x, y, x + tw, y + th))


def lanczos_upscale(im: Image.Image, scale: float) -> Image.Image:
    from PIL import ImageFilter
    out = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))


def image_qc(im: Image.Image) -> tuple[dict, list[str]]:
    import numpy as np
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    lum = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    m = {"mean_luma": round(float(lum.mean()), 1), "contrast_std": round(float(lum.std()), 1),
         "clipped_black_pct": round(float((lum < 3).mean() * 100), 2),
         "clipped_white_pct": round(float((lum > 252).mean() * 100), 2)}
    # sharpness: variance of a Laplacian-ish second difference
    g = lum
    lap = (g[1:-1, 2:] + g[1:-1, :-2] + g[2:, 1:-1] + g[:-2, 1:-1] - 4 * g[1:-1, 1:-1])
    m["sharpness"] = round(float(lap.var()), 1)
    w = []
    if m["contrast_std"] < 4:
        w.append("image is almost flat/blank — likely a safety-filter black image or a failed generation")
    elif m["mean_luma"] < 25:
        w.append("image is very dark")
    elif m["mean_luma"] > 235:
        w.append("image is very bright / washed out")
    if m["clipped_white_pct"] > 25:
        w.append(f"{m['clipped_white_pct']}% blown highlights")
    if m["sharpness"] < 15 and m["contrast_std"] >= 4:
        w.append("image looks soft/blurry (low high-frequency detail)")
    return m, w
