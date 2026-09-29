"""Background removal (rembg, local ONNX models), edge refinement, shadows, background replacement."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from ...core.deps import need
from ...core.registry import tool
from ...core.result import Result, ToolError
from ._common import (checkerboard, has_alpha, open_image, out_file, parse_color, preview, preview_dir,
                      side_by_side, stem_of, save_image)
from .models import REMBG_MODELS, rembg_home, rembg_model

_sessions: dict = {}


def _session(model: str):
    need("rembg")
    path = rembg_model(model)
    os.environ["U2NET_HOME"] = str(rembg_home())  # rembg looks for <U2NET_HOME>/<name>.onnx
    if model not in _sessions:
        from rembg import new_session
        try:
            _sessions[model] = new_session(model)
        except Exception as e:
            raise ToolError(f"rembg could not load {model} from {path}: {e}")
    return _sessions[model]


def _box(x: np.ndarray, r: int) -> np.ndarray:
    """Mean filter (same size): OpenCV when present, else integral image."""
    try:
        import cv2
        return cv2.boxFilter(x.astype(np.float32), -1, (2 * r + 1, 2 * r + 1), borderType=cv2.BORDER_REFLECT)
    except ImportError:
        pass
    h, w = x.shape
    ii = np.pad(x, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(h) - r, 0, h)
    y1 = np.clip(np.arange(h) + r + 1, 0, h)
    x0 = np.clip(np.arange(w) - r, 0, w)
    x1 = np.clip(np.arange(w) + r + 1, 0, w)
    s = ii[y1][:, x1] - ii[y0][:, x1] - ii[y1][:, x0] + ii[y0][:, x0]
    area = (y1 - y0)[:, None] * (x1 - x0)[None, :]
    return s / area


def guided_filter(guide: np.ndarray, src: np.ndarray, r: int = 8, eps: float = 1e-3) -> np.ndarray:
    """He et al. guided filter (grey guide) — snaps a soft mask to real image edges (hair, fur)."""
    mI, mp = _box(guide, r), _box(src, r)
    cov = _box(guide * src, r) - mI * mp
    var = _box(guide * guide, r) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return _box(a, r) * guide + _box(b, r)


def refine_mask(im: Image.Image, mask: Image.Image, edges: str = "soft") -> Image.Image:
    m = np.asarray(mask.convert("L"), np.float32) / 255
    if edges == "hard":
        m = (m > 0.5).astype(np.float32)
        return Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    # work at ≤1600 px for speed, then upsample
    g = np.asarray(im.convert("L"), np.float32) / 255
    scale = min(1.0, 1600 / max(g.shape))
    if scale < 1:
        size = (int(g.shape[1] * scale), int(g.shape[0] * scale))
        gs = np.asarray(im.convert("L").resize(size, Image.BILINEAR), np.float32) / 255
        ms = np.asarray(mask.convert("L").resize(size, Image.BILINEAR), np.float32) / 255
    else:
        gs, ms = g, m
    r = max(2, int(max(gs.shape) / 250))
    ref = guided_filter(gs, ms, r=r, eps=2e-3)
    # only allow changes near the edge; keep solid interior/exterior solid
    band = (ms > 0.02) & (ms < 0.98)
    band = (_box(band.astype(np.float32), r) > 0.001).astype(np.float32)
    out = ms * (1 - band) + np.clip(ref, 0, 1) * band
    # contrast curve on alpha: removes faint halo, keeps hair wisps
    # where the photo has little contrast across the edge (white product on white), the guided filter
    # only blurs — fall back to the model's own edge there
    gx = np.abs(np.diff(gs, axis=1, append=gs[:, -1:])) + np.abs(np.diff(gs, axis=0, append=gs[-1:, :]))
    edge_contrast = _box(gx, r * 2) * 6
    trust = np.clip(edge_contrast, 0, 1)
    out = ms * (1 - trust * band) + out * trust * band
    out = np.clip((out - 0.1) / 0.8, 0, 1)
    res = Image.fromarray((out * 255).astype(np.uint8))
    if scale < 1:
        res = res.resize(im.size, Image.LANCZOS)
    return res


def decontaminate(im: Image.Image, alpha: Image.Image, radius: int = 0) -> Image.Image:
    """Replace colours of semi-transparent edge pixels with nearby solid foreground colour (kills the
    old-background colour fringe)."""
    rgb = np.asarray(im.convert("RGB"), np.float32)
    a = np.asarray(alpha, np.float32) / 255
    radius = radius or max(3, int(max(a.shape) / 300))
    solid = (a > 0.95).astype(np.float32)
    def k(x):
        return _box(_box(x, radius), radius)
    num = np.stack([k(rgb[..., c] * solid) for c in range(3)], -1)
    den = k(solid)[..., None]
    fg = num / np.maximum(den, 1e-4)
    w = ((a > 0.02) & (a < 0.95))[..., None] & (den > 0.02)
    # blend towards the estimated foreground colour where alpha is partial
    t = np.clip(1 - a, 0, 1)[..., None] * 0.9
    out = np.where(w, rgb * (1 - t) + fg * t, rgb)
    res = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
    res.putalpha(alpha)
    return res


def subject_mask(im: Image.Image, model: str = "isnet-general-use", edges: str = "soft",
                 alpha_matting: bool = False) -> tuple[Image.Image, list[str]]:
    from rembg import remove
    sess = _session(model)
    work = im.convert("RGB")
    warns = []
    if alpha_matting:
        try:
            cut = remove(work, session=sess, alpha_matting=True, alpha_matting_foreground_threshold=240,
                         alpha_matting_background_threshold=10, alpha_matting_erode_size=10)
            return cut.getchannel("A"), warns
        except Exception as e:
            warns.append(f"alpha matting failed ({e}); used guided-filter edges")
    mask = remove(work, session=sess, only_mask=True)
    if edges != "raw":
        mask = refine_mask(work, mask, edges)
    cover = float((np.asarray(mask) > 128).mean())
    if cover < 0.01:
        warns.append("the model found almost no subject — try another model (u2net_human_seg for people, isnet-anime for art)")
    elif cover > 0.97:
        warns.append("mask covers nearly the whole image — the model could not separate a subject")
    return mask, warns


def add_shadow(cut: Image.Image, kind: str = "contact", opacity: float = 0.5, offset: tuple[int, int] | None = None,
               canvas_pad: int = 0) -> Image.Image:
    """Return an RGBA image (same size + pad) with the subject over its shadow."""
    a = cut.getchannel("A")
    W, H = cut.size
    pad = canvas_pad or int(max(W, H) * 0.08)
    out = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    bbox = a.point(lambda v: 255 if v > 30 else 0).getbbox()
    if not bbox:
        return cut
    if kind in ("drop", "both"):
        dx, dy = offset or (int(W * 0.015), int(H * 0.025))
        sh = Image.new("L", out.size, 0)
        sh.paste(a, (pad + dx, pad + dy))
        sh = sh.filter(ImageFilter.GaussianBlur(max(W, H) / 60)).point(lambda v: int(v * opacity * 0.8))
        layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
        layer.putalpha(sh)
        out.alpha_composite(layer)
    if kind in ("contact", "both"):
        l, t, r, b = bbox
        w = r - l
        sh = Image.new("L", out.size, 0)
        from PIL import ImageDraw
        d = ImageDraw.Draw(sh)
        cy = pad + b
        eh = max(6, int(w * 0.07))
        d.ellipse((pad + l + w * 0.04, cy - eh / 2, pad + r - w * 0.04, cy + eh / 2), fill=int(255 * opacity))
        sh = sh.filter(ImageFilter.GaussianBlur(max(3, eh * 0.6)))
        # tighter, darker core right under the object
        core = Image.new("L", out.size, 0)
        ImageDraw.Draw(core).ellipse((pad + l + w * 0.15, cy - eh / 4, pad + r - w * 0.15, cy + eh / 4), fill=int(255 * opacity))
        core = core.filter(ImageFilter.GaussianBlur(max(2, eh * 0.25)))
        sh = Image.fromarray(np.maximum(np.asarray(sh), np.asarray(core)))
        layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
        layer.putalpha(sh)
        out.alpha_composite(layer)
    out.alpha_composite(cut, (pad, pad))
    return out


@tool("photo")
def photo_remove_background(image: str, model: str = "isnet-general-use", edges: str = "soft",
                            clean_fringe: bool = True, shadow: str = "none", crop_to_subject: bool = False,
                            padding: int = 0, save_mask: bool = True, project: str = "", out: str = "") -> Result:
    """Remove the background → transparent PNG (local AI, no upload). model: isnet-general-use (best,
    default), u2net, u2net_human_seg (people), isnet-anime (art), silueta/u2netp (small, fast); the
    model (5–180 MB) downloads once from Hugging Face into STUDIO_HOME/models/rembg. edges: 'soft'
    (guided-filter refinement keeps hair), 'hard' (crisp product edges), 'matting' (pymatting, slow),
    'raw'. clean_fringe removes old-background colour on edges. shadow: none | contact | drop | both.
    crop_to_subject trims to the subject (+padding px). Also saves the mask (subject cut-out). Preview on
    a checkerboard."""
    src = open_image(image)
    mask, warns = subject_mask(src, model, "raw" if edges == "matting" else edges, alpha_matting=edges == "matting")
    cut = src.convert("RGB")
    if clean_fringe:
        cut = decontaminate(cut, mask)
    else:
        cut = cut.convert("RGBA")
        cut.putalpha(mask)
    if shadow != "none":
        cut = add_shadow(cut, shadow)
    if crop_to_subject:
        bb = cut.getchannel("A").point(lambda v: 255 if v > 6 else 0).getbbox()
        if bb:
            l, t, r, b = bb
            cut = cut.crop((max(0, l - padding), max(0, t - padding), min(cut.width, r + padding), min(cut.height, b + padding)))
    p = out_file(project, out, stem_of(image, "cutout"), ".png")
    save_image(cut, p)
    files = [str(p)]
    if save_mask:
        mp = out_file(project, out, stem_of(image, "mask"), ".png")
        mask.save(mp)
        files.append(str(mp))
    on_dark = Image.new("RGBA", cut.size, (40, 40, 44, 255))
    on_dark.alpha_composite(cut)
    prev = side_by_side(src, cut, preview_dir(p) / (p.stem + "-compare.png"), ("Original", "Cut-out"))
    prev2 = side_by_side(on_dark, Image.alpha_composite(Image.new("RGBA", cut.size, (255, 255, 255, 255)), cut),
                         preview_dir(p) / (p.stem + "-edges.png"), ("On dark (check fringe)", "On white"))
    cover = round(float((np.asarray(mask) > 128).mean()) * 100, 1)
    return Result(f"Background removed with {model} ({edges} edges): subject covers {cover}% of the frame.",
                  files=files, previews=[prev, prev2], warnings=warns,
                  data={"model": model, "subject_pct": cover, "size": list(cut.size)},
                  next_steps=["photo_replace_background to put it on a colour/gradient/photo",
                              "photo_compose_layers to build a layered PSD with it"])


def _background(spec: str, size: tuple[int, int]) -> Image.Image:
    W, H = size
    s = (spec or "#ffffff").strip()
    if s.startswith(("linear:", "gradient:", "radial:")):
        kind, _, cols = s.partition(":")
        parts = [c.strip() for c in cols.split(",") if c.strip()]
        angle = 90.0
        if parts and parts[-1].replace(".", "").replace("-", "").isdigit():
            angle = float(parts.pop())
        cs = [np.array(parse_color(c), np.float32) for c in parts] or [np.array((255, 255, 255, 255), np.float32)] * 2
        if len(cs) == 1:
            cs.append(cs[0])
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        if kind == "radial":
            t = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H * 0.45) / (H / 2)) ** 2) / 1.3
        else:
            a = np.radians(angle)
            t = ((xx - W / 2) * np.cos(a) + (yy - H / 2) * np.sin(a))
            t = (t - t.min()) / (t.max() - t.min() + 1e-6)
        t = np.clip(t, 0, 1)[..., None]
        n = len(cs) - 1
        idx = np.minimum((t * n).astype(int), n - 1)
        f = t * n - idx
        stack = np.stack(cs)
        arr = stack[idx[..., 0]] * (1 - f) + stack[idx[..., 0] + 1] * f
        # tiny dither against banding
        arr += np.random.default_rng(1).uniform(-0.6, 0.6, arr.shape)
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    p = Path(s).expanduser()
    if p.suffix and p.exists():
        return ImageOps.fit(open_image(str(p)).convert("RGBA"), (W, H), Image.LANCZOS)
    return Image.new("RGBA", (W, H), parse_color(s))


@tool("photo")
def photo_replace_background(image: str, background: str = "#f2f2f2", width: int = 0, height: int = 0,
                             subject_scale: float = 0.85, position: str = "center", shadow: str = "contact",
                             blur_background: float = 0.0, match_light: bool = True, model: str = "isnet-general-use",
                             format: str = "jpg", quality: int = 92, project: str = "", out: str = "") -> Result:
    """Put the subject on a new background: a colour ('#f5efe6'), a gradient ('linear:#1e3a8a,#60a5fa,90'
    or 'radial:#fff,#d1d5db'), or a photo path. Works on photos (removes the background first) or on
    transparent PNG cut-outs. width/height set the canvas (default: original size); subject_scale is the
    subject's max share of the canvas; position center|bottom|left|right; shadow contact|drop|both|none;
    blur_background (px) for photo backgrounds; match_light nudges the subject's colour toward the scene
    and adds a subtle light wrap. Returns the composite + before/after preview."""
    src = open_image(image)
    warns: list[str] = []
    if has_alpha(src):
        cut = src.convert("RGBA")
    else:
        mask, warns = subject_mask(src, model)
        cut = decontaminate(src.convert("RGB"), mask)
    bb = cut.getchannel("A").point(lambda v: 255 if v > 10 else 0).getbbox()
    if not bb:
        raise ToolError("no subject found to place")
    cut = cut.crop(bb)
    tol = 3
    touch_bottom = bb[3] >= src.height - tol
    touch_sides = bb[0] <= tol and bb[2] >= src.width - tol
    W, H = (width or src.width), (height or src.height)
    bg = _background(background, (W, H))
    if blur_background:
        bg = bg.filter(ImageFilter.GaussianBlur(blur_background))
    s = min(W * subject_scale / cut.width, H * subject_scale / cut.height)
    if touch_bottom:  # a person cropped by the frame: keep them anchored to the bottom edge
        s = min(W / cut.width, H * subject_scale / cut.height) if not touch_sides else W / cut.width
        position, shadow = "bottom_edge", "none"
    cut = cut.resize((max(1, int(cut.width * s)), max(1, int(cut.height * s))), Image.LANCZOS)
    if match_light:
        bga = np.asarray(bg.convert("RGB"), np.float32)
        mean_bg = bga.reshape(-1, 3).mean(0)
        rgb = np.asarray(cut.convert("RGB"), np.float32)
        a = np.asarray(cut.getchannel("A"), np.float32)[..., None] / 255
        tint = (mean_bg / (mean_bg.mean() + 1e-6) - 1) * 0.08
        rgb = rgb * (1 + tint)
        # light wrap: background colour bleeding onto the edge
        edge = np.clip(1 - np.asarray(cut.getchannel("A").filter(ImageFilter.GaussianBlur(max(1.5, cut.width / 400))), np.float32) / 255, 0, 1)[..., None]
        rgb = rgb * (1 - edge * 0.12) + mean_bg * edge * 0.12
        c2 = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
        c2.putalpha(cut.getchannel("A"))
        cut = c2
    pad = int(max(cut.size) * 0.08)
    comp_subject = add_shadow(cut, shadow, opacity=0.45, canvas_pad=pad) if shadow != "none" else cut
    if shadow == "none":
        pad = 0
    x = (W - cut.width) // 2
    y = (H - cut.height) // 2
    if position == "bottom":
        y = H - cut.height - int(H * 0.06)
    elif position == "bottom_edge":  # keep headroom; let the body run off the bottom edge
        y = H - cut.height if cut.height <= H * 0.94 else int(H * 0.06)
    elif position == "left":
        x = int(W * 0.06)
    elif position == "right":
        x = W - cut.width - int(W * 0.06)
    out_im = bg.copy()
    out_im.alpha_composite(comp_subject, (x - pad, y - pad)) if (x - pad >= 0 and y - pad >= 0) else \
        out_im.paste(comp_subject, (x - pad, y - pad), comp_subject)
    ext = "." + format.lower().replace("jpeg", "jpg").lstrip(".")
    fin = out_im if ext == ".png" else out_im.convert("RGB")
    p = out_file(project, out, stem_of(image, "new-bg"), ext)
    save_image(fin, p, quality=quality)
    prev = side_by_side(src, fin, preview_dir(p) / (p.stem + "-compare.png"))
    return Result(f"Placed subject on new background ({background[:40]}) at {W}×{H}.", files=[str(p)], previews=[prev],
                  warnings=warns, data={"size": [W, H]})
