"""Retouch (object/blemish removal by mask, OpenCV inpainting) and AI upscaling (Real-ESRGAN ONNX)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ...core.deps import need
from ...core.registry import tool
from ...core.result import Result, ToolError
from ._common import before_after_preview, has_alpha, open_image, out_file, save_image, stem_of
from .basic import _ext_for
from .models import UPSCALE_MODELS, upscale_model


def _regions_mask(size, regions: list) -> Image.Image:
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for r in regions:
        t = r.get("type", "circle" if "r" in r else "rect")
        if t == "circle":
            x, y, rad = r["x"], r["y"], r.get("r", 10)
            d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=255)
        elif t == "rect":
            if "box" in r:
                d.rectangle(tuple(r["box"]), fill=255)
            else:
                d.rectangle((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]), fill=255)
        elif t in ("polygon", "stroke", "line"):
            pts = [tuple(p) for p in r["points"]]
            if t == "polygon":
                d.polygon(pts, fill=255)
            else:
                d.line(pts, fill=255, width=int(r.get("width", 12)), joint="curve")
        else:
            raise ToolError(f"unknown region type {t!r}", "circle {x,y,r} | rect {x,y,w,h} or {box} | polygon {points} | stroke {points,width}")
    return m


@tool("photo")
def photo_retouch(image: str, mask: str = "", regions: list[dict] = [], method: str = "auto", grow: int = 4,
                  match_grain: bool = True, format: str = "", quality: int = 92, project: str = "", out: str = "") -> Result:
    """Remove objects, blemishes, dust, wires or text by painting a mask — offline OpenCV inpainting
    method 'auto' (default: diffusion for tiny spots, content-aware patch copy + Poisson seamless
    blending for larger areas), 'patch', 'telea' or 'ns' (pure diffusion), with grain matching so the patch doesn't look plastic. Give `mask` (white =
    remove, any size — it is scaled) or `regions`: [{'type':'circle','x':..,'y':..,'r':..},
    {'type':'rect','x','y','w','h'}, {'type':'polygon','points':[[x,y],…]}, {'type':'stroke','points':…,
    'width':..}]. Best for small/medium areas on textured backgrounds; for big objects or areas that need
    new content (a person, a building), use the ai department's generative inpaint instead. Returns
    the result + before/after preview with the mask outlined."""
    need("opencv")
    import cv2
    src = open_image(image)
    rgb = src.convert("RGB")
    if mask:
        m = open_image(mask).convert("L").resize(src.size, Image.NEAREST)
        m = m.point(lambda v: 255 if v > 127 else 0)
    elif regions:
        m = _regions_mask(src.size, regions)
    else:
        raise ToolError("give a mask image or regions to remove")
    if grow:
        m = m.filter(ImageFilter.MaxFilter(grow * 2 + 1))
    area = float((np.asarray(m) > 0).mean())
    if area == 0:
        raise ToolError("the mask is empty")
    arr = np.asarray(rgb)[:, :, ::-1].copy()
    mk = np.asarray(m)
    rad = int(max(3, min(15, np.sqrt(area * src.width * src.height) / 20)))
    flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
    res_bgr = cv2.inpaint(arr, mk, rad, flag)
    used = method
    if method in ("auto", "patch"):
        n, lab = cv2.connectedComponents((mk > 0).astype(np.uint8))
        used = "telea"
        for i in range(1, n):
            comp = (lab == i).astype(np.uint8) * 255
            if method == "auto" and comp.sum() / 255 < 400:  # tiny spot: diffusion is perfect
                continue
            filled = _patch_fill(res_bgr, comp)
            if filled is not None:
                sel = cv2.dilate(comp, np.ones((5, 5), np.uint8)) > 0
                res_bgr[sel] = filled[sel]
                used = "patch+seamless clone"
    res = res_bgr[:, :, ::-1].astype(np.float32)
    if match_grain:
        g = np.asarray(rgb.convert("L"), np.float32)
        hp = g - cv2.GaussianBlur(g, (0, 0), 2)
        ring = cv2.dilate(mk, np.ones((25, 25), np.uint8)) > 0
        ring &= mk == 0
        sd = float(hp[ring].std()) if ring.any() else 0.0
        noise = np.random.default_rng(3).normal(0, sd * 0.9, g.shape).astype(np.float32)
        noise = cv2.GaussianBlur(noise, (0, 0), 0.7)
        res += (noise * (mk > 0))[..., None]
    soft = cv2.GaussianBlur(mk.astype(np.float32) / 255, (0, 0), 1.5)[..., None]
    outa = np.asarray(rgb, np.float32) * (1 - soft) + res * soft
    im = Image.fromarray(np.clip(outa, 0, 255).astype(np.uint8))
    if has_alpha(src):
        im.putalpha(src.getchannel("A"))
    ext = _ext_for(image, format)
    p = out_file(project, out, stem_of(image, "retouched"), ext if not has_alpha(im) or ext == ".png" else ".png")
    save_image(im, p, quality=quality)
    before = rgb.copy()
    edge = m.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))
    before.paste((255, 40, 40), mask=edge)
    warns = []
    if area > 0.08:
        warns.append(f"mask covers {area * 100:.0f}% of the image — classic inpainting smears large areas; "
                     "use the ai department's generative inpaint for big removals")
    return Result(f"Retouched {area * 100:.2f}% of the image ({used}).", files=[str(p)],
                  previews=[before_after_preview(before, im, p, ("Mask (red)", "Retouched"))], warnings=warns,
                  data={"mask_pct": round(area * 100, 3)},
                  next_steps=["zoom-check the preview; widen `grow` if edges of the object remain"])


def _patch_fill(img: np.ndarray, mk: np.ndarray) -> np.ndarray | None:
    """Content-aware-lite: find the offset whose surroundings best match the hole's border, copy that
    patch in and blend it with Poisson seamless cloning. img BGR uint8, mk uint8 (one component)."""
    import cv2
    H, W = mk.shape
    ys, xs = np.nonzero(mk)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    bw, bh = x1 - x0, y1 - y0
    ringw = max(4, int(max(bw, bh) * 0.25))
    dil = cv2.dilate(mk, np.ones((2 * ringw + 1, 2 * ringw + 1), np.uint8))
    ring = (dil > 0) & (mk == 0)
    ry, rx = np.nonzero(ring)
    if not len(ry):
        return None
    sc = min(1.0, 400 / max(H, W))
    small = cv2.resize(img, (max(1, int(W * sc)), max(1, int(H * sc))), interpolation=cv2.INTER_AREA).astype(np.float32)
    sry, srx = (ry * sc).astype(int), (rx * sc).astype(int)
    ref = small[sry, srx]
    best, best_cost = None, 1e18
    reach = int(max(bw, bh) * 4) + 20

    def cost(dx, dy):
        if not (0 <= x0 - ringw + dx and x1 + ringw + dx <= W and 0 <= y0 - ringw + dy and y1 + ringw + dy <= H):
            return 1e18
        # the source patch must not overlap the hole (or its border ring)
        if np.any(dil[np.clip(ys[::7] + dy, 0, H - 1), np.clip(xs[::7] + dx, 0, W - 1)]):
            return 1e18
        cand = small[np.clip(((ry + dy) * sc).astype(int), 0, small.shape[0] - 1), np.clip(((rx + dx) * sc).astype(int), 0, small.shape[1] - 1)]
        return float(((cand - ref) ** 2).sum()) * (1 + 0.15 * np.hypot(dx, dy) / reach)

    step = max(2, max(bw, bh) // 6)
    for dy in range(-reach, reach + 1, step):
        for dx in range(-reach, reach + 1, step):
            if dx == 0 and dy == 0:
                continue
            c = cost(dx, dy)
            if c < best_cost:
                best_cost, best = c, (dx, dy)
    if best is None:
        return None
    for st in (step // 2, step // 4, 1):  # refine
        if st < 1:
            continue
        bx, by = best
        for dy in (-st, 0, st):
            for dx in (-st, 0, st):
                c = cost(bx + dx, by + dy)
                if c < best_cost:
                    best_cost, best = c, (bx + dx, by + dy)
    dx, dy = best
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    src = cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)
    cm = cv2.dilate(mk, np.ones((5, 5), np.uint8))
    ys2, xs2 = np.nonzero(cm)
    cx = (xs2.min() + xs2.max() + 1) // 2
    cy = (ys2.min() + ys2.max() + 1) // 2
    try:
        return cv2.seamlessClone(src, img, cm, (int(cx), int(cy)), cv2.NORMAL_CLONE)
    except cv2.error:
        return None


# ----------------------------------------------------------------------------- upscale
_ort_sessions: dict = {}


def _sr_session(name: str):
    if name not in _ort_sessions:
        try:
            import onnxruntime as ort
        except ImportError:
            raise ToolError("upscaling needs onnxruntime", "pip install onnxruntime (comes with rembg[cpu])")
        path = upscale_model(name)
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        prov = [p for p in ("CoreMLExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
        try:
            _ort_sessions[name] = ort.InferenceSession(str(path), so, providers=prov)
        except Exception:
            _ort_sessions[name] = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
    return _ort_sessions[name]


def sr_x4(rgb: Image.Image, tile: int = 192, pad: int = 12) -> Image.Image:
    sess = _sr_session("realesr-general-x4v3")
    a = np.asarray(rgb.convert("RGB"), np.float32) / 255
    H, W = a.shape[:2]
    out = np.zeros((H * 4, W * 4, 3), np.float32)
    for y in range(0, H, tile):
        for x in range(0, W, tile):
            y0, x0 = max(0, y - pad), max(0, x - pad)
            y1, x1 = min(H, y + tile + pad), min(W, x + tile + pad)
            inp = a[y0:y1, x0:x1].transpose(2, 0, 1)[None]
            res = sess.run(None, {sess.get_inputs()[0].name: inp})[0][0].transpose(1, 2, 0)
            ty, tx = (y - y0) * 4, (x - x0) * 4
            th, tw = (min(y + tile, H) - y) * 4, (min(x + tile, W) - x) * 4
            out[y * 4:y * 4 + th, x * 4:x * 4 + tw] = res[ty:ty + th, tx:tx + tw]
    return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8))


@tool("photo")
def photo_upscale(image: str, scale: int = 4, model: str = "realesr-general-x4v3", detail_blend: float = 0.0,
                  format: str = "", quality: int = 92, project: str = "", out: str = "") -> Result:
    """Enlarge a photo 2×/3×/4× with AI super-resolution, offline: Real-ESRGAN 'realesr-general-x4v3'
    (compact GAN model, BSD-3, 5 MB ONNX fetched once from Hugging Face) run tiled on CPU (CoreML on
    Mac when available). It restores edges and texture and removes JPEG blocking — it does invent fine
    detail, so check faces/text in the preview. model='lanczos' = classic resampling + light sharpening
    (no AI). detail_blend 0…1 mixes some Lanczos back in for a more natural look. Transparent images keep
    their alpha (upscaled with Lanczos)."""
    src = open_image(image)
    if scale not in (2, 3, 4):
        raise ToolError("scale must be 2, 3 or 4")
    W, H = src.size
    if W * H * scale * scale > 80e6:
        raise ToolError(f"{W}×{H} ×{scale} would be {W * scale}×{H * scale} (>80 MP)",
                        "downscale first or use a smaller scale")
    target = (W * scale, H * scale)
    lz = src.convert("RGB").resize(target, Image.LANCZOS)
    warns = []
    if model == "lanczos":
        im = lz.filter(ImageFilter.UnsharpMask(1.5, 60, 2))
        label = "Lanczos resampling + unsharp mask (not AI)"
    elif model in UPSCALE_MODELS:
        sr = sr_x4(src.convert("RGB"))
        im = sr if scale == 4 else sr.resize(target, Image.LANCZOS)
        if detail_blend:
            im = Image.blend(im, lz, float(detail_blend))
        label = f"AI super-resolution ({UPSCALE_MODELS[model][3]})"
    else:
        raise ToolError(f"unknown model {model!r}", "realesr-general-x4v3 | lanczos")
    if has_alpha(src):
        im.putalpha(src.getchannel("A").resize(target, Image.LANCZOS))
    ext = _ext_for(image, format)
    p = out_file(project, out, stem_of(image, f"x{scale}"), ext)
    save_image(im, p, quality=quality)
    # compare a 1:1 detail crop at the centre (what the upscale actually changed)
    cw, ch = min(400, target[0]), min(400, target[1])
    cx, cy = target[0] // 2 - cw // 2, target[1] // 2 - ch // 2
    naive = src.convert("RGB").resize(target, Image.BICUBIC).crop((cx, cy, cx + cw, cy + ch))
    prev = before_after_preview(naive, im.crop((cx, cy, cx + cw, cy + ch)), p, ("Bicubic (detail 1:1)", "Upscaled (detail 1:1)"))
    return Result(f"Upscaled {W}×{H} → {target[0]}×{target[1]} with {label}.", files=[str(p)], previews=[prev],
                  warnings=warns, data={"method": label, "size": list(target)})
