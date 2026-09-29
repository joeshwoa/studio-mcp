"""AI department tools: image & video generation/editing with FREE backends only (paid = opt-in BYO key)."""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ...core import qc
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import backends as B
from . import byok, comfy, prompting
from .catalog import (BUNDLES, DEFAULT_SETUP, MFLUX_MODELS, MFLUX_UPSCALE, STYLES, gen_size, parse_size)
from .outputs import (before_after, detail_compare, labelled_sheet, out_dir, save_png, stem_for, write_sidecar)

DEPT = "ai"
VIDEO_T2V_PREF = ["ltxv-2b", "wan21-1.3b", "wan22-5b-q4"]
VIDEO_I2V_PREF = ["ltxv-2b", "wan22-5b-q4"]


# ------------------------------------------------------------------ helpers
def _seeds(seed: int, count: int) -> list[int]:
    base = seed if seed is not None and seed >= 0 else random.randint(1, 2**31 - 1)
    return [base + i for i in range(count)]


def _size(size: str) -> tuple[int, int]:
    try:
        return parse_size(size)
    except ValueError as e:
        raise ToolError(str(e))


def _load_image(path: str) -> Image.Image:
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"no such image: {p}")
    try:
        im = Image.open(p)
        im = ImageOps.exif_transpose(im)
        return im.convert("RGB")
    except Exception as e:
        raise ToolError(f"cannot read image {p.name}: {e}")


def _tmp_png(im: Image.Image, name: str = "img") -> Path:
    d = Path(tempfile.mkdtemp(prefix="studio-ai-"))
    p = d / f"{name}.png"
    im.save(p)
    return p


def _previews_dir(d: Path) -> Path:
    p = d / "_previews"
    p.mkdir(exist_ok=True)
    return p


def _finish_images(gens: list[B.Gen], target: tuple[int, int], pinfo: dict, user_prompt: str, project: str, out: str,
                   kind: str, tool_name: str, extra: dict, route_why: list[str]) -> Result:
    d = out_dir(project, kind, out)
    files, labels, warnings, per = [], [], [], []
    for g in gens:
        im = B.fit_cover(g.image, target)
        up = max(target[0] / g.image.width, target[1] / g.image.height)
        m, w = B.image_qc(im)
        meta = {"tool": tool_name, "prompt": user_prompt, "final_prompt": pinfo.get("prompt"),
                "negative": pinfo.get("negative", ""), "seed": g.seed, "backend": g.backend, "model": g.model,
                "licence": g.licence, "commercial_use": g.commercial, "size": list(target),
                "generated_size": list(g.gen_size), "resample_factor": round(up, 3), "steps": g.steps,
                "seconds": round(g.seconds, 1), "qc": m, "route": route_why, **extra, **g.extra}
        p, side = save_png(im, d, stem_for(user_prompt), meta)
        files += [str(p), str(side)]
        labels.append(f"seed {g.seed} · {g.backend} · {g.seconds:.0f}s")
        per.append({"file": str(p), "seed": g.seed, "qc": m})
        warnings += [f"{p.name}: {x}" for x in w]
        if up > 1.25:
            warnings.append(f"{p.name}: generated at {g.gen_size[0]}×{g.gen_size[1]} and enlarged ×{up:.2f} to "
                            f"{target[0]}×{target[1]} with Lanczos — run ai_upscale on the chosen take for print/large use")
        if g.extra.get("watermark"):
            warnings.append(f"{p.name}: Pollinations free tier stamps a small logo bottom-right — draft/moodboard use only "
                            "(a free POLLINATIONS_TOKEN or a local backend avoids it)")
        if g.backend == "pollinations" and g.extra.get("served_model") not in (None, "", g.extra.get("requested_model")):
            warnings.append(f"Pollinations served model '{g.extra['served_model']}' instead of the requested "
                            f"'{g.extra['requested_model']}' (anonymous tier) — expect draft quality")
    sheet = _previews_dir(d) / f"{Path(files[0]).stem}-sheet.png"
    sheet = sheet if not sheet.exists() else _previews_dir(d) / f"{Path(files[0]).stem}-sheet-{int(time.time())}.png"
    labelled_sheet([Path(f) for f in files[::2]], labels, sheet, title=f"{tool_name}: {user_prompt[:110]}")
    g0 = gens[0]
    res = Result(f"{len(gens)} image(s) {target[0]}×{target[1]} via {g0.backend} ({g0.model}); licence: {g0.licence} "
                 f"— commercial use: {g0.commercial}.", files=files, previews=[str(sheet)])
    res.warnings = list(dict.fromkeys(pinfo.get("warnings", []) + pinfo.get("notes", []) + warnings))
    res.data = {"images": per, "backend": g0.backend, "model": g0.model, "route": route_why,
                "final_prompt": pinfo.get("prompt"), "negative": pinfo.get("negative", "")}
    res.next_steps = ["LOOK at the contact sheet; pick the best seed (re-run with that seed + tweaks for variations)",
                      "ai_upscale the chosen image for large formats; add text/logos with the design tools (Arabic type included)"]
    return res


# ------------------------------------------------------------------ prompt
@tool(DEPT)
def ai_prompt_enhance(idea: str, target: str = "flux", style: str = "none", brand_colors: list[str] = [],
                      size: str = "1:1", negative: str = "", camera: str = "", audio: str = "", dialogue: str = "",
                      duration: float = 4.0, no_text: bool = False) -> Result:
    """Turn a short English idea into a strong, model-specific prompt (deterministic templates, no LLM).
    target: flux | sdxl | pollinations | hf | mflux (images), ltxv | wan (local video), veo (Google Flow brief with
    camera + AUDIO/dialogue). style: one of the studio presets (photoreal, cinematic, product, editorial, food,
    flat-vector, 3d-render, poster, social-ad, islamic-geometric, egyptian-heritage …). brand_colors: hex or names
    (hex is translated to colour words models understand). Returns prompt + negative + notes in data.
    Arabic ideas: there is no offline translator — YOU (the agent) should write the idea in English; keep Arabic copy
    for text overlays done by the design tools."""
    w, h = _size(size)
    warns = prompting.language_warnings(idea)
    try:
        if target in ("ltxv", "wan", "wan21", "wan22", "veo", "flow"):
            p = prompting.build_video_prompt(idea, target, style if style != "none" else "cinematic", camera, brand_colors,
                                             negative, audio, dialogue, duration, size)
        else:
            p = prompting.build_image_prompt(idea, target, style, brand_colors, w, h, negative, no_text)
    except ValueError as e:
        raise ToolError(str(e), f"styles: {sorted(STYLES)}")
    txt = f"Prompt for {target} ({p['family']}):\n{p['prompt']}"
    if p["negative"]:
        txt += f"\n\nNegative:\n{p['negative']}"
    return Result(txt, warnings=warns + p["notes"], data={**p, "target": target, "style": style},
                  next_steps=["pass `prompt` (and `negative`) to ai_generate_image / ai_generate_video with enhance=false"])


# ------------------------------------------------------------------ generate image
@tool(DEPT, network=True)
def ai_generate_image(prompt: str, negative: str = "", size: str = "1:1", count: int = 1, seed: int = -1,
                      style: str = "none", brand_colors: list[str] = [], backend: str = "auto", model: str = "",
                      steps: int = 0, enhance: bool = True, no_text: bool = False, project: str = "", out: str = "") -> Result:
    """Generate images from an English prompt with the best FREE backend available, and say which one ran.
    Router (backend='auto'): mflux on an Apple-Silicon Mac (fastest local) → ComfyUI if running with a model
    (FLUX-schnell GGUF / SDXL-Lightning) → Hugging Face free tier if HF_TOKEN is set → Pollinations (free, no key,
    draft quality + small watermark). size: preset (1:1, 4:5/1080x1350, 9:16/story, 16:9, youtube-thumbnail, og, a4…)
    or 'WxH'; output is exactly that size (generated at the model's native ~1 MP, then cover-cropped).
    count ≤ 8 (seeds seed, seed+1…). style/brand_colors shape the prompt (see ai_prompt_enhance); enhance=false sends
    your prompt verbatim. model: ComfyUI bundle id (flux-schnell-q4, sdxl-lightning…), mflux id (schnell-q4,
    z-image-turbo) or HF repo. Returns PNGs (prompt/seed embedded) + sidecar JSON each (prompt, seed, backend, model,
    licence) + a labelled contact sheet to LOOK at. Paid providers are never used here (see ai_generate_image_byok)."""
    if not 1 <= count <= 8:
        raise ToolError("count must be 1–8")
    target = _size(size)
    task_backend, why = B.route("t2i", backend, model)
    if task_backend not in ("mflux", "comfyui", "hf", "pollinations"):
        raise ToolError(f"unknown backend {backend!r}", "auto | mflux | comfyui | hf | pollinations (paid: ai_generate_image_byok)")
    seeds = _seeds(seed, count)
    # the prompt style depends on the model family that will run
    fam_target = "flux"
    bundle = ""
    if task_backend == "comfyui":
        bundle = B.pick_bundle(B.comfy_client(), "image", model if model in BUNDLES else "", B.IMAGE_PREFERENCE)
        fam_target = "sdxl" if BUNDLES[bundle].family.startswith("sdxl") else "flux"
    elif task_backend == "hf" and model and "xl" in model.lower():
        fam_target = "sdxl"
    try:
        pinfo = prompting.build_image_prompt(prompt, fam_target, style, brand_colors, *target, negative=negative,
                                             no_text=no_text, enhance=enhance)
    except ValueError as e:
        raise ToolError(str(e), f"styles: {sorted(STYLES)}")
    pinfo["warnings"] = prompting.language_warnings(prompt)
    gens: list[B.Gen] = []
    if task_backend == "mflux":
        mid = model if model in MFLUX_MODELS else "schnell-q4"
        w, h = gen_size(target, 1024 * 1024, 16)
        gens = B.mflux_generate(pinfo["prompt"], w, h, seeds, mid, steps)
    elif task_backend == "comfyui":
        gens = B.comfy_generate(pinfo["prompt"], pinfo["negative"], target, seeds, bundle, steps)
    elif task_backend == "hf":
        w, h = gen_size(target, 1024 * 1024, 16, max_side=1440)
        gens = [B.hf_generate(pinfo["prompt"], pinfo["negative"], w, h, s, model, steps) for s in seeds]
    else:
        w, h = gen_size(target, 1024 * 1024, 16, max_side=1440)
        for i, s in enumerate(seeds):
            if i:
                time.sleep(2)  # be polite to the free service
            gens.append(B.pollinations(pinfo["prompt"], w, h, s, model if model and "/" not in model else "flux"))
    return _finish_images(gens, target, pinfo, prompt, project, out, "ai-images", "ai_generate_image",
                          {"style": style, "brand_colors": brand_colors, "enhanced": enhance}, why)


@tool(DEPT, network=True, spends=True)
def ai_generate_image_byok(prompt: str, provider: str = "fal", size: str = "1:1", count: int = 1, seed: int = -1,
                           style: str = "none", brand_colors: list[str] = [], model: str = "", confirm: bool = False,
                           project: str = "", out: str = "") -> Result:
    """PAID, optional: generate with the user's OWN key at fal (FAL_KEY), Replicate (REPLICATE_API_TOKEN) or OpenAI
    (OPENAI_API_KEY). Only when the user explicitly wants a paid provider. confirm=false returns the price estimate
    and does nothing; call again with confirm=true after the user agrees. Same outputs as ai_generate_image."""
    p = byok.PROVIDERS.get(provider)
    if not p:
        raise ToolError(f"unknown provider {provider!r}", f"one of {sorted(byok.PROVIDERS)}")
    if not confirm:
        return Result(f"Would generate {count} image(s) with {provider} ({model or p['model']}) — {p['price']} each, "
                      f"billed to the user's {p['env']}. Nothing was spent.",
                      data={"provider": provider, "price": p["price"], "key_present": bool(os.environ.get(p["env"]))},
                      next_steps=["Ask the user; only with a clear yes call again with confirm=true",
                                  "Free alternative: ai_generate_image (backend auto)"])
    target = _size(size)
    pinfo = prompting.build_image_prompt(prompt, "flux", style, brand_colors, *target)
    pinfo["warnings"] = prompting.language_warnings(prompt)
    w, h = gen_size(target, 1024 * 1024, 16, max_side=1440)
    gens = [byok.generate(provider, pinfo["prompt"], w, h, s, model) for s in _seeds(seed, max(1, min(count, 8)))]
    r = _finish_images(gens, target, pinfo, prompt, project, out, "ai-images", "ai_generate_image_byok",
                       {"paid": True}, [f"paid provider {provider} requested explicitly"])
    r.warnings.insert(0, f"PAID generation on the user's {provider} account ({p['price']}).")
    return r


# ------------------------------------------------------------------ edit
def _working(im: Image.Image, area: int, mult: int = 8) -> tuple[Image.Image, float]:
    s = min(1.0, (area / (im.width * im.height)) ** 0.5)
    w = max(mult, int(im.width * s) // mult * mult)
    h = max(mult, int(im.height * s) // mult * mult)
    return (im.resize((w, h), Image.LANCZOS) if (w, h) != im.size else im), w / im.width


def _outpaint_pads(w: int, h: int, expand_to: str, expand_px: list[int]) -> tuple[int, int, int, int]:
    if expand_px:
        if len(expand_px) != 4:
            raise ToolError("expand_px must be [left, top, right, bottom]")
        return tuple(max(0, int(v)) for v in expand_px)  # type: ignore[return-value]
    if not expand_to:
        raise ToolError("outpaint needs expand_to (e.g. '9:16', '16:9', 'story') or expand_px [l,t,r,b]")
    tw, th = _size(expand_to)
    rt, r = tw / th, w / h
    if abs(rt - r) < 0.01:
        # same aspect: grow 25% on every side
        return (w // 4, h // 4, w // 4, h // 4)
    if rt > r:
        extra = round(h * rt) - w
        return (extra // 2, 0, extra - extra // 2, 0)
    extra = round(w / rt) - h
    return (0, extra // 2, 0, extra - extra // 2)


@tool(DEPT)
def ai_edit_image(image: str, prompt: str, mode: str = "img2img", mask: str = "", mask_box: list[int] = [],
                  strength: float = -1.0, expand_to: str = "", expand_px: list[int] = [], negative: str = "",
                  style: str = "none", seed: int = -1, backend: str = "auto", model: str = "",
                  project: str = "", out: str = "") -> Result:
    """Edit an existing image with AI (local, free). mode:
    'img2img' — restyle/re-imagine the whole picture (strength 0.2 subtle … 0.8 heavy; default 0.6);
    'inpaint' — repaint only the WHITE area of `mask` (PNG) or of `mask_box` [x, y, w, h] in image pixels, e.g. remove
    an object or change a garment (default strength 0.9; untouched pixels are copied back exactly);
    'outpaint' — extend the canvas: expand_to='9:16'/'16:9'/'story'… or expand_px [left, top, right, bottom]; the
    original pixels are kept at full resolution and only the new border is generated.
    Needs ComfyUI (comfyui_setup/comfyui_start); img2img can also run on mflux on the Mac. Prompt describes the
    RESULT (for inpaint: what should appear in the masked area). Returns the edited PNG + sidecar JSON + a
    before/after (and mask) preview sheet."""
    if mode not in ("img2img", "inpaint", "outpaint"):
        raise ToolError("mode must be img2img, inpaint or outpaint")
    src = _load_image(image)
    be, why = B.route(mode, backend, model)
    strength = strength if strength >= 0 else {"img2img": 0.6, "inpaint": 0.9, "outpaint": 1.0}[mode]
    if not 0 < strength <= 1:
        raise ToolError("strength must be in (0, 1]")
    sd = _seeds(seed, 1)[0]
    d = out_dir(project, "ai-edits", out)
    warns = prompting.language_warnings(prompt)

    # ---- mask
    mask_im = None
    if mode == "inpaint":
        if mask:
            mask_im = ImageOps.exif_transpose(Image.open(Path(mask).expanduser())).convert("L").resize(src.size)
        elif mask_box:
            if len(mask_box) != 4:
                raise ToolError("mask_box must be [x, y, width, height]")
            mask_im = Image.new("L", src.size, 0)
            x, y, bw, bh = mask_box
            ImageDraw.Draw(mask_im).rectangle([x, y, x + bw, y + bh], fill=255)
        else:
            raise ToolError("inpaint needs mask (PNG, white = repaint) or mask_box [x, y, w, h]")
        cover = sum(mask_im.histogram()[128:]) / (src.width * src.height)
        if cover < 0.002:
            raise ToolError("the mask is empty (nothing white to repaint)")
        if cover > 0.9:
            warns.append("mask covers >90% of the image — this is effectively a new image; consider img2img/generate")

    if be == "mflux":
        if mode != "img2img":
            raise ToolError("mflux here supports img2img only", "inpaint/outpaint need ComfyUI")
        work, s = _working(src, 1024 * 1024, 16)
        pinfo = prompting.build_image_prompt(prompt, "flux", style, None, *work.size, negative=negative)
        g = B.mflux_generate(pinfo["prompt"], work.width, work.height, [sd], "schnell-q4", 0,
                             str(_tmp_png(work)), 1 - strength)[0]
        result = g.image.resize(src.size, Image.LANCZOS)
        model_used, lic, com = g.model, g.licence, g.commercial
        secs = g.seconds
    else:
        c = B.comfy_client()
        if c is None:
            raise ToolError("ComfyUI is not running", "comfyui_start")
        bid = B.pick_bundle(c, "image", model, B.EDIT_PREFERENCE)
        bd = BUNDLES[bid]
        fam = "sdxl" if bd.family.startswith("sdxl") else "flux"
        area = bd.defaults["area"] if bd.family != "sdxl-turbo" else 768 * 768
        if mode == "outpaint":
            l0, t0, r0, b0 = _outpaint_pads(src.width, src.height, expand_to, expand_px)
            cw, ch = src.width + l0 + r0, src.height + t0 + b0
            s = min(1.0, (area * 1.15 / (cw * ch)) ** 0.5)
            work = src.resize((max(8, round(src.width * s) // 8 * 8), max(8, round(src.height * s) // 8 * 8)), Image.LANCZOS)
            s = work.width / src.width
            pads = [max(0, round(v * s / 8) * 8) for v in (l0, t0, r0, b0)]
        else:
            work, s = _working(src, area, 8)
        pinfo = prompting.build_image_prompt(prompt, fam, style, None, *work.size, negative=negative)
        params = {**B.comfy_params_for(bid), "prompt": pinfo["prompt"], "negative": pinfo["negative"], "seed": sd,
                  "steps": max(bd.defaults["steps"], 4 if mode != "img2img" else bd.defaults["steps"]),
                  "cfg": bd.defaults["cfg"], "sampler": bd.defaults["sampler"], "scheduler": bd.defaults["scheduler"],
                  "denoise": float(strength), "prefix": f"studio/{mode}", "image": c.upload_image(_tmp_png(work))}
        if mode == "inpaint":
            params["mask"] = c.upload_image(_tmp_png(mask_im.resize(work.size), "mask"))
            params["grow"] = 6
        if mode == "outpaint":
            params.update(left=pads[0], top=pads[1], right=pads[2], bottom=pads[3], feather=min(64, max(8, min(work.size) // 12)))
        wf = comfy.fill(comfy.load_template(mode), params, B.loader_for(bid))
        (meta, files, secs), = B.comfy_run_images([(wf, {"kind": mode, "seed": sd, "bundle": bid})])
        gen = Image.open(files[0]).convert("RGB")
        model_used, lic, com = bid, bd.licence, bd.commercial
        if mode == "img2img":
            result = gen.resize(src.size, Image.LANCZOS)
        elif mode == "inpaint":
            # composite at the ORIGINAL resolution: only the (feathered) mask area changes
            up = gen.resize(src.size, Image.LANCZOS)
            feather = mask_im.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(max(2, min(src.size) // 200)))
            result = Image.composite(up, src, feather)
        else:
            full = gen.resize((round(gen.width / s), round(gen.height / s)), Image.LANCZOS)
            ox, oy = round(pads[0] / s), round(pads[1] / s)
            m = Image.new("L", src.size, 255)
            fe = max(4, min(src.size) // 60)
            m = ImageOps.expand(Image.new("L", (src.width - 2 * fe, src.height - 2 * fe), 255), fe, 0).filter(
                ImageFilter.GaussianBlur(fe / 2)) if src.width > 4 * fe and src.height > 4 * fe else m
            full.paste(src, (ox, oy), m)
            result = full
    meta = {"tool": "ai_edit_image", "mode": mode, "source": str(Path(image).expanduser()), "prompt": prompt,
            "final_prompt": pinfo["prompt"], "negative": pinfo.get("negative", ""), "strength": strength, "seed": sd,
            "backend": be, "model": model_used, "licence": lic, "commercial_use": com, "size": list(result.size),
            "seconds": round(secs, 1), "route": why}
    qm, qw = B.image_qc(result)
    meta["qc"] = qm
    p, side = save_png(result, d, stem_for(prompt, mode), meta)
    prev = before_after(src, result, _previews_dir(d) / f"{p.stem}-compare.png", mask=mask_im)
    res = Result(f"{mode} done via {be} ({model_used}) → {result.width}×{result.height}. Licence {lic}; commercial: {com}.",
                 files=[str(p), str(side)], previews=[str(prev)], warnings=warns + pinfo.get("notes", []) + qw,
                 data={"route": why, "seed": sd, "qc": qm})
    res.next_steps = ["LOOK at the before/after sheet; if the edit is too weak raise strength, too wild lower it or change seed"]
    return res


# ------------------------------------------------------------------ upscale
@tool(DEPT)
def ai_upscale(image: str, scale: float = 2.0, model: str = "", backend: str = "auto", project: str = "", out: str = "") -> Result:
    """Enlarge an image with an AI upscaler that invents plausible fine detail (2×–4×; up to 8× with a final resample).
    Router: ComfyUI + Real-ESRGAN x4plus (BSD, commercial OK; or model='ultrasharp-4x' — crisper but NON-commercial)
    → mflux SeedVR2 on the Mac → plain Lanczos + unsharp as a clearly-labelled non-AI fallback.
    Returns the upscaled PNG + sidecar JSON + a 100%-crop detail comparison to LOOK at."""
    if not 1 < scale <= 8:
        raise ToolError("scale must be >1 and ≤8")
    src = _load_image(image)
    if max(src.size) * scale > 12000:
        raise ToolError(f"result would be {round(src.width * scale)}×{round(src.height * scale)} px — too large",
                        "use a smaller scale")
    be, why = B.route("upscale", backend)
    t0 = time.time()
    warns: list[str] = []
    if be == "comfyui":
        c = B.comfy_client()
        bid = B.pick_bundle(c, "upscale", model, B.UPSCALE_PREFERENCE)
        bd = BUNDLES[bid]
        params = {"image": c.upload_image(_tmp_png(src)), "upscale_model": bd.files[0].filename,
                  "rescale": round(scale / 4.0, 4), "prefix": "studio/upscale"}
        wf = comfy.fill(comfy.load_template("upscale"), params)
        (_, files, _), = B.comfy_run_images([(wf, {"kind": "upscale", "bundle": bid})])
        result = Image.open(files[0]).convert("RGB")
        model_used, lic, com = bid, bd.licence, bd.commercial
        if scale > 4:
            warns.append("model upscalers are 4×; the rest was a Lanczos resample")
    elif be == "mflux":
        tmp = Path(tempfile.mkdtemp())
        outp = tmp / "up.png"
        cmd = [MFLUX_UPSCALE["cli"], "--image-path", str(_tmp_png(src)), "--resolution", f"{scale:g}x", "--output", str(outp)]
        r = subprocess.run(cmd, capture_output=True, text=True, env=B.mflux_env(), timeout=3600)
        if r.returncode != 0 or not outp.exists():
            raise ToolError("mflux SeedVR2 upscale failed: " + (r.stderr or r.stdout)[-600:])
        result = Image.open(outp).convert("RGB")
        model_used, lic, com = "mflux:seedvr2-3b", MFLUX_UPSCALE["licence"], "yes — commercial use allowed"
    elif be == "lanczos":
        result = B.lanczos_upscale(src, scale)
        model_used, lic, com = "lanczos+unsharp (not AI)", "n/a", "yes"
        warns.append("NO AI upscaler available: this is a high-quality resample, it cannot add real detail. "
                     "comfyui_setup models=['realesrgan-x4'] (67 MB) + comfyui_start gives true AI upscaling.")
    else:
        raise ToolError(f"unknown backend {backend!r}", "auto | comfyui | mflux | lanczos")
    want = (round(src.width * scale), round(src.height * scale))
    if result.size != want:
        result = result.resize(want, Image.LANCZOS)
    d = out_dir(project, "ai-upscaled", out)
    qm, qw = B.image_qc(result)
    meta = {"tool": "ai_upscale", "source": str(Path(image).expanduser()), "scale": scale, "backend": be,
            "model": model_used, "licence": lic, "commercial_use": com, "size": list(result.size),
            "seconds": round(time.time() - t0, 1), "route": why, "qc": qm}
    p, side = save_png(result, d, f"{Path(image).stem}-x{scale:g}", meta)
    prev = detail_compare(src, result, _previews_dir(d) / f"{p.stem}-detail.png")
    return Result(f"Upscaled {src.width}×{src.height} → {result.width}×{result.height} via {be} ({model_used}); "
                  f"commercial: {com}.", files=[str(p), str(side)], previews=[str(prev)], warnings=warns + qw,
                  data={"route": why, "qc": qm})


# ------------------------------------------------------------------ video
def _video_frames(duration: float, fps: int, mult: int, cap: int) -> int:
    n = max(mult + 1, round(duration * fps / mult) * mult + 1)
    return min(n, cap)


def _handoff(prompt: str, image: str, duration: float, size: str, style: str, camera: str, audio: str, dialogue: str,
             negative: str, reason: str, project: str, out: str) -> Result:
    pinfo = prompting.build_video_prompt(prompt, "veo", style, camera, None, negative, audio, dialogue, duration, size)
    d = out_dir(project, "ai-video", out)
    brief = {"handoff_to": "flow-studio-director", "why": reason, "platform": "Google Flow (Veo 3.1 / Nano Banana)",
             "prompt": pinfo["prompt"], "aspect": size, "duration_s": duration, "start_image": image or None,
             "model_hint": "Veo 3.1 Fast for drafts, Veo 3.1 Quality for finals (uses Google AI Pro Flow credits)",
             "user_idea": prompt}
    from ...config import unique_path
    bp = unique_path(d, stem_for(prompt, "flow-brief"), ".json")
    bp.write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
    return Result(
        f"Hand-off: this video should be made in Google Flow by the flow-studio-director skill ({reason}). "
        "Nothing was generated here. Brief saved.", files=[str(bp)],
        warnings=pinfo["notes"] + ["Flow uses the user's Google AI Pro credits (already paid plan, not free-per-use)."],
        next_steps=["Invoke the flow-studio-director skill with data.handoff (prompt, aspect, duration, start image)",
                    "For a silent b-roll/loop without characters, ai_generate_video route='comfyui' runs locally for free"],
        data={"handoff": brief})


def _finish_video(job: dict, entry: dict, client: comfy.ComfyClient) -> Result:
    meta = job["meta"]
    d = Path(meta["out_dir"])
    tmp = Path(tempfile.mkdtemp(prefix="frames-"))
    files = sorted(client.download_outputs(entry, tmp), key=lambda p: p.name)
    pngs = [f for f in files if f.suffix.lower() == ".png"]
    if not pngs:
        raise ToolError("ComfyUI returned no frames")
    for i, f in enumerate(pngs):
        f.rename(tmp / f"f_{i:05d}.png")
    from ...config import unique_path
    mp4 = unique_path(d, meta["stem"], ".mp4")
    ff = shutil.which("ffmpeg")
    if not ff:
        raise ToolError("ffmpeg is needed to assemble the video", "brew install ffmpeg")
    subprocess.run([ff, "-y", "-v", "error", "-framerate", str(meta["fps"]), "-i", str(tmp / "f_%05d.png"),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(mp4)], check=True, capture_output=True, timeout=900)
    poster = mp4.with_name(mp4.stem + "-poster.png")
    shutil.copy(tmp / "f_00000.png", poster)
    info = qc.probe(mp4)
    sheet = qc.contact_sheet(mp4, _previews_dir(d) / f"{mp4.stem}-sheet.png", cols=4, rows=3)
    side = write_sidecar(mp4, {**meta, "frames": len(pngs), "probe": info, "prompt_id": job["prompt_id"],
                               "seconds": round(time.time() - job["submitted"], 1)})
    shutil.rmtree(tmp, ignore_errors=True)
    client.free()  # give the 16 GB back to the Mac
    b = BUNDLES[meta["bundle"]]
    warns = ["Silent clip (local models make no audio) — add music/VO with the audio department."]
    if tuple(meta["target"]) != (info.get("width"), info.get("height")):
        warns.append(f"Delivered at native {info.get('width')}×{info.get('height')} (same aspect as {meta['target']}); "
                     "upscale/reframe in the video department if a bigger master is needed.")
    return Result(f"Video {info.get('width')}×{info.get('height')} {info.get('duration', 0):.1f}s @{meta['fps']}fps via "
                  f"ComfyUI/{b.id}. Licence {b.licence}; commercial: {b.commercial}.",
                  files=[str(mp4), str(poster), str(side)], previews=[str(sheet)], warnings=warns,
                  data={"probe": info, "bundle": b.id, "seed": meta["seed"]},
                  next_steps=["LOOK at the contact sheet for flicker/warping; re-run with another seed if motion breaks"])


@tool(DEPT)
def ai_generate_video(prompt: str, image: str = "", duration: float = 4.0, size: str = "16:9", route: str = "auto",
                      model: str = "", seed: int = -1, quality: str = "draft", style: str = "cinematic", camera: str = "",
                      negative: str = "", audio: str = "", dialogue: str = "", wait: bool = True, timeout_min: int = 60,
                      project: str = "", out: str = "") -> Result:
    """Text→video or image→video (`image` = first frame). Routing:
    • cinematic video with characters, dialogue, speech, Arabic lines, storytelling or sound → HAND-OFF to the
      flow-studio-director skill (Google Flow, Veo 3.1): returns a ready brief, generates nothing here;
    • silent b-roll, loops, product/atmosphere shots → local ComfyUI, free: LTX-Video 2B distilled (fastest,
      ≈3–10 min per 4 s on a 16 GB Mac), Wan 2.1 1.3B (t2v) or Wan 2.2 5B GGUF (best, slow; quality='standard').
    route: auto | comfyui | flow. model: ltxv-2b | wan21-1.3b | wan22-5b-q4. Local output: H.264 MP4 at the model's
    native size (same aspect as `size`), poster frame, sidecar JSON and a contact sheet to LOOK at. Long renders:
    wait=false (or a timeout) returns a prompt_id — fetch later with comfyui_collect."""
    if route not in ("auto", "comfyui", "flow"):
        raise ToolError("route must be auto, comfyui or flow")
    if image:
        _load_image(image)
    if route == "flow":
        return _handoff(prompt, image, duration, size, style, camera, audio, dialogue, negative,
                        "Flow requested explicitly", project, out)
    if route == "auto":
        need, reason = prompting.wants_flow(prompt)
        if dialogue or audio:
            need, reason = True, "dialogue/audio requested — only Veo 3.1 generates sound"
        if need:
            return _handoff(prompt, image, duration, size, style, camera, audio, dialogue, negative, reason, project, out)
    c = B.comfy_client()
    avail = B.comfy_bundles(c, "video") if c else []
    if not avail or c is None:
        why = "ComfyUI not running" if c is None else "no local video model installed"
        if route == "comfyui":
            raise ToolError(f"cannot render locally: {why}", "comfyui_setup models=['ltxv-2b'] then comfyui_start")
        r = _handoff(prompt, image, duration, size, style, camera, audio, dialogue, negative,
                     f"{why}; Flow is the ready option", project, out)
        r.next_steps.append("Free local alternative: comfyui_setup models=['ltxv-2b'] (≈9.7 GB) + comfyui_start")
        return r
    prefs = VIDEO_I2V_PREF if image else VIDEO_T2V_PREF
    if quality == "standard":
        prefs = ["wan22-5b-q4"] + [p for p in prefs if p != "wan22-5b-q4"]
    if model:
        if model not in avail:
            raise ToolError(f"video model {model!r} not installed", f"installed: {avail}; comfyui_setup models=['{model}']")
        if image and BUNDLES[model].family == "wan21":
            raise ToolError("Wan 2.1 1.3B is text→video only", "use ltxv-2b or wan22-5b-q4 for image→video")
        bid = model
    else:
        cand = [p for p in prefs if p in avail]
        if not cand:
            raise ToolError(f"no installed model can do {'image' if image else 'text'}→video: {avail}",
                            "comfyui_setup models=['ltxv-2b']")
        bid = cand[0]
    b = BUNDLES[bid]
    dft = b.defaults
    target = _size(size)
    fam = b.family
    w, h = gen_size(target, dft["width"] * dft["height"], 32, max_side=1280)
    cap = 257 if fam == "ltxv" else 121
    length = _video_frames(duration, dft["fps"], dft["frame_multiple"], cap)
    pinfo = prompting.build_video_prompt(prompt, "ltxv" if fam == "ltxv" else "wan", style, camera, None, negative)
    sd = _seeds(seed, 1)[0]
    names = {f.folder: f.filename for f in b.files}
    enc = next(f.filename for f in b.files if f.folder == "text_encoders")
    params = {"prompt": pinfo["prompt"], "negative": pinfo["negative"], "seed": sd, "steps": dft["steps"],
              "cfg": dft["cfg"], "width": w, "height": h, "length": length, "prefix": "studio/video"}
    if fam == "ltxv":
        params.update(ckpt=names["checkpoints"], t5=enc, fps=float(dft["fps"]))
        tpl = "video_ltxv_i2v" if image else "video_ltxv_t2v"
    else:
        params.update(unet=names["diffusion_models"], umt5=enc, vae=names["vae"], shift=dft["shift"])
        tpl = "video_wan21_t2v" if fam == "wan21" else ("video_wan22_i2v" if image else "video_wan22_t2v")
    if image:
        src = _load_image(image)
        params["image"] = c.upload_image(_tmp_png(B.fit_cover(src, (w, h))))
    wf = comfy.fill(comfy.load_template(tpl), params)
    d = out_dir(project, "ai-video", out)
    meta = {"kind": "video", "tool": "ai_generate_video", "prompt": prompt, "final_prompt": pinfo["prompt"],
            "negative": pinfo["negative"], "seed": sd, "backend": "comfyui", "bundle": bid, "model": b.title,
            "licence": b.licence, "commercial_use": b.commercial, "fps": dft["fps"], "frames": length,
            "gen_size": [w, h], "target": list(target), "source_image": image or None, "out_dir": str(d),
            "stem": stem_for(prompt, "video"), "speed_estimate": b.speed_mac16}
    c, pid = comfy.submit(wf, meta, c)
    if not wait:
        return Result(f"Queued {length} frames {w}×{h} on ComfyUI/{bid} (prompt_id {pid}). Expected: {b.speed_mac16}.",
                      data={"prompt_id": pid}, next_steps=[f"comfyui_collect prompt_id='{pid}' when it is done"])
    try:
        entry = c.wait(pid, timeout=timeout_min * 60, poll=3)
    except comfy.JobTimeout:
        return Result(f"Still rendering after {timeout_min} min (prompt_id {pid}) — ComfyUI keeps going.",
                      data={"prompt_id": pid}, next_steps=[f"comfyui_collect prompt_id='{pid}' later"])
    return _finish_video(comfy.load_job(pid), entry, c)


# ------------------------------------------------------------------ ComfyUI management
def _setup_running() -> dict | None:
    f = comfy.setup_status_file()
    try:
        st = json.loads(f.read_text())
    except Exception:
        return None
    if st.get("state") == "running":
        try:
            os.kill(int(st.get("pid", 0)), 0)
            return st
        except Exception:
            return None
    return None


def _spawn_worker(plan: dict, name: str) -> Path:
    from ...config import sub
    pf = sub("logs") / f"{name}-plan.json"
    pf.write_text(json.dumps(plan, indent=1))
    src_root = str(Path(__file__).resolve().parents[3])
    env = {**os.environ, "PYTHONPATH": src_root + os.pathsep + os.environ.get("PYTHONPATH", "")}
    subprocess.Popen([sys.executable, "-m", "studio.depts.ai.setup_worker", str(pf)], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return pf


def _run_plan(plan: dict, confirm: bool, background: bool, what: str) -> Result:
    size = plan["download_bytes"]
    lines = [f"{i + 1}. {s['title']}" + (f"  → {' '.join(s['cmd'][:6])}{' …' if len(s['cmd']) > 6 else ''}" if s["kind"] == "cmd" else "")
             for i, s in enumerate(plan["steps"])]
    lic = [f"- {m}: {v['licence']} — commercial use: {v['commercial']}" for m, v in plan.get("licences", {}).items()]
    head = (f"{what}: {len(plan['steps'])} step(s), downloads ≈{size / 1e9:.1f} GB into {plan.get('models_root', '')} "
            f"(free there: {plan['free_bytes'] / 1e9:.0f} GB).")
    no_space = bool(plan["free_bytes"]) and size > plan["free_bytes"] * 0.95
    if not confirm:
        r = Result(head + "\n\nPlan:\n" + "\n".join(lines) + ("\n\nLicences:\n" + "\n".join(lic) if lic else ""),
                   data=plan, next_steps=["Show this to the user (sizes + licences); only if they agree, call again "
                                          "with confirm=true. It runs in the background — follow with comfyui_status."])
        if no_space:
            r.warnings.append("NOT enough free disk space for this plan — free space or pick fewer models.")
        return r
    if no_space:
        raise ToolError(head + " Not enough free disk space.", "free space on the studio drive or pick fewer models")
    if any(s["kind"] == "cmd" and s["cmd"][0] == "git" for s in plan["steps"]) and not shutil.which("git"):
        raise ToolError("git is required", "macOS: xcode-select --install")
    if _setup_running():
        raise ToolError("a setup is already running", "watch it with comfyui_status")
    if not plan["steps"]:
        return Result("Nothing to do — everything is already installed.")
    if background:
        pf = _spawn_worker(plan, "comfyui-setup" if "ComfyUI" in what else "mflux-setup")
        return Result(f"{head}\nStarted in the background. Progress: comfyui_status (log: {comfy.setup_log()}).",
                      files=[str(pf)], next_steps=["comfyui_status every few minutes; comfyui_start when it says done"])
    st = comfy.execute_plan(plan)
    r = Result(f"{what} {st['state']}: " + ", ".join(f"{s['title']}={s['state']}" for s in st["steps"]),
               data=st, ok=st["state"] == "done")
    if st["state"] != "done":
        r.warnings.append(f"see {comfy.setup_log()}")
    return r


@tool(DEPT, installs=True, network=True)
def comfyui_setup(models: list[str] = DEFAULT_SETUP, custom_nodes: list[str] = [], confirm: bool = False,
                  update: bool = False, background: bool = True) -> Result:
    """Install ComfyUI (free, local AI image/video server) into STUDIO_HOME/apps/ComfyUI with its own Python
    environment (PyTorch with Apple Metal on the Mac), the needed custom nodes (ComfyUI-GGUF), and download the chosen
    models from Hugging Face into STUDIO_HOME/models/comfyui (on the external SSD). models: any of
    flux-schnell-q4 (10.8 GB, default), flux-schnell-q5, sdxl-lightning (6.9 GB), sdxl-turbo, realesrgan-x4 (67 MB),
    ultrasharp-4x (non-commercial), ltxv-2b (video, 9.7 GB), wan21-1.3b, wan22-5b-q4 — shared files download once.
    confirm=false (default) only returns the plan with sizes and LICENCES — show it to the user first. confirm=true
    runs it in the background (resumable downloads); watch with comfyui_status. FLUX-dev is deliberately not offered
    (non-commercial licence) — schnell is Apache-2.0."""
    plan = comfy.plan_setup(list(models), list(custom_nodes), update, check_sizes=True)
    return _run_plan(plan, confirm, background, "ComfyUI setup")


@tool(DEPT, installs=True, network=True)
def mflux_setup(model: str = "schnell-q4", confirm: bool = False, background: bool = True) -> Result:
    """Apple-Silicon only: install mflux (MLX-native FLUX, the fastest local image generator on a Mac) into the studio's
    Python and pre-download a model into STUDIO_HOME/models/hf. model: schnell-q4 (≈9.6 GB, pre-quantised 4-bit,
    Apache-2.0, default), schnell-q8 (≈13 GB), z-image-turbo (≈20 GB download, quantised to 4-bit at load, Apache-2.0,
    excellent photorealism). confirm=false returns the plan and sizes only."""
    m = MFLUX_MODELS.get(model)
    if not m:
        raise ToolError(f"unknown mflux model {model!r}", f"one of {sorted(MFLUX_MODELS)}")
    steps = []
    if not B.mflux_installed():
        steps.append({"kind": "cmd", "title": "install mflux (pip)", "cmd": [sys.executable, "-m", "pip", "install", "-U", "mflux"]})
    hf = str(B.hf_home())
    if not B.mflux_cached(model):
        steps.append({"kind": "cmd", "title": f"download {m.repo} (≈{m.size / 1e9:.1f} GB) into {hf}",
                      "cmd": [sys.executable, "-c",
                              f"import os; os.environ['HF_HOME']={hf!r}; from huggingface_hub import snapshot_download; "
                              f"snapshot_download({m.repo!r})"]})
    plan = {"steps": steps, "download_bytes": m.size if not B.mflux_cached(model) else 0,
            "free_bytes": shutil.disk_usage(hf).free, "models_root": hf,
            "licences": {model: {"licence": m.licence, "commercial": m.commercial}}}
    if confirm and not B.IS_ARM_MAC:
        raise ToolError("mflux needs an Apple-Silicon Mac (MLX)", "on this machine use comfyui_setup instead")
    r = _run_plan(plan, confirm, background, "mflux setup")
    r.warnings.append(f"Speed on a 16 GB Mac: {m.speed}")
    return r


@tool(DEPT)
def comfyui_start(port: int = 8188, lowvram: bool = False, wait_s: int = 90) -> Result:
    """Start the local ComfyUI server installed by comfyui_setup (background process, logs in STUDIO_HOME/logs).
    lowvram=true trades speed for memory — use it on a 16 GB Mac for video or when you see out-of-memory errors.
    Returns the URL; does nothing if it is already running."""
    info = comfy.start_server(port, lowvram, wait_s)
    if info.get("already_running"):
        return Result(f"ComfyUI already running at {info['url']}.", data=info)
    if info.get("still_starting"):
        return Result(f"ComfyUI is starting at {info['url']} (first start compiles/loads — can take minutes).", data=info,
                      next_steps=["comfyui_status in a minute"])
    return Result(f"ComfyUI running at {info['url']} (pid {info['pid']}, up in {info['seconds']} s).", data=info,
                  next_steps=["ai_backends to see what the router will use"])


@tool(DEPT)
def comfyui_stop(free_only: bool = False) -> Result:
    """Stop the ComfyUI server the studio started (frees all its memory). free_only=true keeps it running but unloads
    models from RAM — handy on a 16 GB Mac before switching to video/other apps."""
    if free_only:
        c = B.comfy_client()
        if not c:
            return Result("ComfyUI is not running.")
        c.free()
        return Result("Asked ComfyUI to unload models and free memory.")
    info = comfy.stop_server()
    return Result("ComfyUI stopped." if info.get("stopped") else f"Nothing stopped: {info.get('reason')}.", data=info)


@tool(DEPT)
def comfyui_status() -> Result:
    """ComfyUI health: installed?, running (URL, device, memory, queue)?, which studio model bundles are present,
    and the progress of a running comfyui_setup/mflux_setup download."""
    cdir = comfy.comfy_dir()
    lines = [f"Install dir: {cdir} ({'installed' if (cdir / 'main.py').exists() else 'not installed'})",
             f"Models dir: {comfy.comfy_models_root()}"]
    data: dict = {"installed": (cdir / "main.py").exists()}
    c = B.comfy_client()
    if c:
        st = c.system_stats()
        dev = (st.get("devices") or [{}])[0]
        q = c.queue_state()
        lines.append(f"Running at {c.url}: ComfyUI {st.get('system', {}).get('comfyui_version')}, device {dev.get('type')} "
                     f"{dev.get('name', '')}, queue running={len(q.get('queue_running', []))} pending={len(q.get('queue_pending', []))}")
        data.update(running=True, url=c.url, system=st.get("system"), device=dev)
    else:
        lines.append(f"Not running at {comfy.base_url()} (comfyui_start)")
        data["running"] = False
    have = {bid: comfy.bundle_installed(b) for bid, b in BUNDLES.items()}
    live = B.comfy_bundles(c, "image") + B.comfy_bundles(c, "video") + B.comfy_bundles(c, "upscale") if c else []
    lines.append("Model bundles: " + ", ".join(f"{k}{'✓' if v or k in live else '·'}" for k, v in have.items()))
    data["bundles"] = {k: bool(v or k in live) for k, v in have.items()}
    try:
        sj = json.loads(comfy.setup_status_file().read_text())
        cur = [s for s in sj.get("steps", []) if s.get("state") in ("running", "failed")]
        lines.append(f"Last setup: {sj.get('state')}" + (f" — {cur[-1]['title']} {cur[-1].get('pct') or ''}"
                                                         f"{'%' if cur[-1].get('pct') else ''} {cur[-1].get('error', '')}" if cur else ""))
        data["setup"] = sj
    except Exception:
        pass
    return Result("\n".join(lines), data=data)


@tool(DEPT)
def comfyui_run_workflow(workflow: str, params: dict = {}, wait: bool = True, timeout_min: int = 30,
                         project: str = "", out: str = "") -> Result:
    """Run ANY ComfyUI workflow: a file exported with ComfyUI's 'Save (API)' or a studio template name (t2i, i2i,
    inpaint, outpaint, upscale, video_ltxv_t2v, …). String values written as "$name" are replaced from `params`
    (typed: numbers stay numbers); params named image/mask/start_image that are local file paths are uploaded
    automatically. The workflow is validated against the live server first (missing nodes/models are named).
    Returns every output image/video downloaded + a preview sheet."""
    p = Path(workflow).expanduser()
    if p.exists():
        wf = json.loads(p.read_text())
        if "nodes" in wf and isinstance(wf["nodes"], list):
            raise ToolError("this is ComfyUI's UI-format workflow", "in ComfyUI use Workflow → Export (API) and pass that file")
        tpl = {"nodes": wf.get("nodes", wf)}
    else:
        tpl = comfy.load_template(workflow)
    c = B.comfy_client()
    if not c:
        raise ToolError("ComfyUI is not running", "comfyui_start")
    prm = dict(params)
    for k in ("image", "mask", "start_image"):
        v = prm.get(k)
        if isinstance(v, str) and Path(v).expanduser().is_file():
            prm[k] = c.upload_image(Path(v).expanduser())
    missing = comfy.placeholders(tpl["nodes"]) - set(prm)
    if missing:
        raise ToolError(f"workflow needs params {sorted(missing)}")
    wf = comfy.fill(tpl, prm)
    d = out_dir(project, "comfyui", out)
    c, pid = comfy.submit(wf, {"kind": "generic", "out_dir": str(d), "workflow": str(workflow), "params": params}, c)
    if not wait:
        return Result(f"Queued (prompt_id {pid}).", data={"prompt_id": pid}, next_steps=[f"comfyui_collect prompt_id='{pid}'"])
    try:
        entry = c.wait(pid, timeout=timeout_min * 60, poll=2)
    except comfy.JobTimeout:
        return Result(f"Still running (prompt_id {pid}).", data={"prompt_id": pid}, next_steps=[f"comfyui_collect prompt_id='{pid}'"])
    return _finish_generic(comfy.load_job(pid), entry, c)


def _finish_generic(job: dict, entry: dict, c: comfy.ComfyClient) -> Result:
    d = Path(job["meta"]["out_dir"])
    tmp = Path(tempfile.mkdtemp())
    got = c.download_outputs(entry, tmp)
    from ...config import unique_path
    files, previews, imgs = [], [], []
    for f in got:
        dest = unique_path(d, f.stem, f.suffix)
        shutil.move(str(f), dest)
        files.append(str(dest))
        if dest.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            imgs.append(dest)
        elif dest.suffix.lower() in (".mp4", ".webm", ".gif", ".mov"):
            previews.append(str(qc.contact_sheet(dest, _previews_dir(d) / f"{dest.stem}-sheet.png")))
    if imgs:
        sheet = _previews_dir(d) / f"{imgs[0].stem}-outputs.png"
        labelled_sheet(imgs[:24], [i.name for i in imgs[:24]], sheet)
        previews.insert(0, str(sheet))
    side = write_sidecar(d / f"comfyui-{job['prompt_id'][:8]}.json", {**job["meta"], "prompt_id": job["prompt_id"], "outputs": files})
    return Result(f"Workflow finished: {len(files)} output file(s).", files=files + [str(side)], previews=previews,
                  warnings=[] if files else ["the workflow produced no downloadable outputs (no SaveImage node?)"])


@tool(DEPT)
def comfyui_collect(prompt_id: str, wait: bool = True, timeout_min: int = 60) -> Result:
    """Fetch the result of a long ComfyUI job started with wait=false or that timed out (ai_generate_video,
    comfyui_run_workflow): waits for it, downloads, assembles video, writes sidecar + previews."""
    job = comfy.load_job(prompt_id)
    c = comfy.ComfyClient(job.get("url"))
    if not c.alive():
        raise ToolError("ComfyUI is not running", "comfyui_start — note: a restarted server has lost unfinished jobs")
    try:
        entry = c.wait(prompt_id, timeout=(timeout_min * 60) if wait else 0.1, poll=3)
    except comfy.JobTimeout:
        q = c.queue_state()
        pos = [i for i, x in enumerate(q.get("queue_pending", [])) if len(x) > 1 and x[1] == prompt_id]
        running = any(len(x) > 1 and x[1] == prompt_id for x in q.get("queue_running", []))
        return Result(f"Job {prompt_id} not finished ({'running' if running else f'queued #{pos[0] + 1}' if pos else 'unknown'}).",
                      data={"prompt_id": prompt_id}, next_steps=["try comfyui_collect again later"])
    if job["meta"].get("kind") == "video":
        return _finish_video(job, entry, c)
    return _finish_generic(job, entry, c)


# ------------------------------------------------------------------ overview
@tool(DEPT, network=True)
def ai_backends(check_network: bool = True) -> Result:
    """Which AI backends are usable right now, what the automatic router will pick for each task (and why), installed
    models with licences, and honest speed expectations on a 16 GB Apple-Silicon Mac. Start here before generating."""
    rows = []
    c = B.comfy_client()
    rows.append({"backend": "mflux", "kind": "local (MLX, Apple Silicon)", "available": B.IS_ARM_MAC and B.mflux_installed(),
                 "detail": ("installed; models cached: " + ", ".join(k for k in MFLUX_MODELS if B.mflux_cached(k)) or "none")
                 if B.mflux_installed() else ("mflux_setup" if B.IS_ARM_MAC else "not an Apple-Silicon Mac")})
    rows.append({"backend": "comfyui", "kind": "local server", "available": bool(c),
                 "detail": (f"running at {c.url}; image={B.comfy_bundles(c, 'image')} video={B.comfy_bundles(c, 'video')} "
                            f"upscale={B.comfy_bundles(c, 'upscale')}") if c else
                 ("installed, not running → comfyui_start" if (comfy.comfy_dir() / "main.py").exists() else "comfyui_setup")})
    rows.append({"backend": "hf", "kind": "free cloud (HF_TOKEN, monthly free credits)", "available": bool(B.hf_token()),
                 "detail": "HF_TOKEN set" if B.hf_token() else "set HF_TOKEN (free account) to enable"})
    pol = None
    if check_network:
        import urllib.request
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    os.environ.get("STUDIO_POLLINATIONS_URL", "https://image.pollinations.ai") + "/models", headers=B.UA), timeout=8) as r:
                pol = r.read().decode()[:200]
        except Exception as e:
            pol = f"unreachable ({e})"
    rows.append({"backend": "pollinations", "kind": "free cloud, no key (draft quality, watermark)", "available": pol is None or "unreachable" not in pol,
                 "detail": f"models offered: {pol}" if pol else "not checked"})
    routes = {}
    for task in ("t2i", "img2img", "inpaint", "outpaint", "upscale"):
        try:
            routes[task] = B.route(task)
        except ToolError as e:
            routes[task] = ("unavailable", [str(e)])
    vid = B.comfy_bundles(c, "video") if c else []
    routes["video"] = ("comfyui" if vid else "flow hand-off",
                       [f"local models: {vid}" if vid else "no local video model",
                        "characters/dialogue/sound always → flow-studio-director (Veo 3.1)"])
    lines = ["Backends:"] + [f"  {'✓' if r['available'] else '·'} {r['backend']:12} {r['kind']} — {r['detail']}" for r in rows]
    lines += ["", "Router decisions now:"] + [f"  {t:9} → {b}   ({'; '.join(w)})" for t, (b, w) in routes.items()]
    lines += ["", "Model bundles (ComfyUI):"] + [
        f"  {b.id:16} {b.kind:7} {b.size / 1e9:5.1f} GB  {b.licence} — commercial: {b.commercial}. {b.speed_mac16}"
        for b in BUNDLES.values()]
    lines += ["", "mflux models:"] + [f"  {m.id:14} {m.size / 1e9:5.1f} GB  {m.licence}. {m.speed}" for m in MFLUX_MODELS.values()]
    return Result("\n".join(lines), data={"backends": rows, "routes": {k: {"backend": v[0], "why": v[1]} for k, v in routes.items()}},
                  warnings=["Speed figures are estimates for a 16 GB M-series Mac, not measured on this machine."])
