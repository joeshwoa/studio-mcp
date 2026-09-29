"""Adjustments: exposure/contrast/… (photo_adjust), auto enhance, film & cinematic looks (also
exported as .cube LUTs usable in Resolve/Premiere/Kdenlive), and applying any .cube LUT."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ...core.registry import tool
from ...core.result import Result, ToolError
from ._common import (before_after_preview, from_float, has_alpha, image_stats, luma, open_image, out_file,
                      save_image, stats_warnings, stem_of, to_float)
from .basic import _ext_for


# ============================================================================ colour math (pure, per-pixel)
def _srgb_to_lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _lin_to_srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def exposure(a, stops):
    return _lin_to_srgb(_srgb_to_lin(a) * (2.0 ** stops)) if stops else a


def contrast(a, amount):
    """amount -1..1 : S-curve around mid grey for +, flattening towards grey for −."""
    if not amount:
        return a
    x = np.clip(a, 0, 1)
    if amount < 0:
        return x + (0.5 - x) * (-amount) * 0.6
    k = 1 + amount * 1.6
    s = 1 / (1 + np.exp(-(x - 0.5) * 6 * k))
    s0, s1 = 1 / (1 + np.exp(3 * k)), 1 / (1 + np.exp(-3 * k))
    s = (s - s0) / (s1 - s0)
    # blend: small amounts stay gentle
    return x + (s - x) * min(1.0, amount * 1.5)


def shadows_highlights(a, shadows=0.0, highlights=0.0, whites=0.0, blacks=0.0):
    if not (shadows or highlights or whites or blacks):
        return a
    y = luma(a)[..., None]
    out = a
    if shadows:
        m = (1 - np.clip(y / 0.5, 0, 1)) ** 2
        out = out + m * shadows * 0.35 * (1 - out) if shadows > 0 else out * (1 + m * shadows * 0.5)
    if highlights:
        m = np.clip((y - 0.5) / 0.5, 0, 1) ** 1.5
        out = out + m * highlights * 0.3 * out if highlights < 0 else out + m * highlights * 0.25 * (1 - out)
    if whites:
        out = out * (1 + whites * 0.15)
    if blacks:
        out = out + blacks * 0.06 * (1 - out) if blacks > 0 else (out - (-blacks) * 0.06) / (1 - (-blacks) * 0.06)
    return out


def saturation(a, amount):
    if not amount:
        return a
    y = luma(a)[..., None]
    return y + (a - y) * (1 + amount)


def vibrance(a, amount):
    if not amount:
        return a
    mx, mn = a.max(-1, keepdims=True), a.min(-1, keepdims=True)
    sat = mx - mn
    # protect skin tones (r>g>b) a little
    skin = ((a[..., 0:1] > a[..., 1:2]) & (a[..., 1:2] > a[..., 2:3])).astype(np.float32) * 0.4
    w = amount * (1 - sat) * (1 - skin)
    y = luma(a)[..., None]
    return y + (a - y) * (1 + w)


def temperature(a, temp=0.0, tint=0.0):
    """temp -1 (cool) .. +1 (warm); tint -1 (green) .. +1 (magenta)."""
    if not (temp or tint):
        return a
    g = np.array([1 + 0.18 * temp + 0.05 * tint, 1 - 0.12 * tint, 1 - 0.22 * temp + 0.05 * tint], np.float32)
    lin = _srgb_to_lin(np.clip(a, 0, 1)) * g
    y0 = luma(_srgb_to_lin(np.clip(a, 0, 1)))[..., None]
    y1 = luma(lin)[..., None]
    lin = lin * (y0 / (y1 + 1e-6))
    return _lin_to_srgb(lin)


def _pchip(xs, ys, x):
    """Monotone cubic interpolation (Fritsch–Carlson)."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    h = np.diff(xs)
    d = np.diff(ys) / h
    m = np.zeros_like(ys)
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, len(xs) - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    x = np.clip(x, xs[0], xs[-1])
    i = np.clip(np.searchsorted(xs, x) - 1, 0, len(xs) - 2)
    t = (x - xs[i]) / h[i]
    h00, h10, h01, h11 = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t, -2 * t**3 + 3 * t**2, t**3 - t**2
    return h00 * ys[i] + h10 * h[i] * m[i] + h01 * ys[i + 1] + h11 * h[i] * m[i + 1]


def curve_lut(points) -> np.ndarray:
    pts = sorted([(float(p[0]), float(p[1])) for p in points])
    if pts[0][0] > 0:
        pts.insert(0, (0.0, 0.0))
    if pts[-1][0] < 255:
        pts.append((255.0, 255.0))
    xs, ys = zip(*pts)
    return np.clip(_pchip(np.array(xs) / 255, np.array(ys) / 255, np.linspace(0, 1, 1024)), 0, 1).astype(np.float32)


def apply_curves(a, curves):
    """curves: [[x,y],…] for RGB, or {'rgb':[…], 'r':[…], 'g':[…], 'b':[…]} (0–255 points)."""
    if not curves:
        return a
    if isinstance(curves, list):
        curves = {"rgb": curves}
    out = np.clip(a, 0, 1)
    for key, idx in (("rgb", None), ("r", 0), ("g", 1), ("b", 2)):
        if key in curves and curves[key]:
            lut = curve_lut(curves[key])
            if idx is None:
                out = lut[(out * 1023).astype(np.int32)]
            else:
                out = out.copy()
                out[..., idx] = lut[(out[..., idx] * 1023).astype(np.int32)]
    return out


def split_tone(a, shadow_rgb=(0, 0, 0), highlight_rgb=(0, 0, 0), balance=0.0):
    y = luma(np.clip(a, 0, 1))[..., None]
    ms = np.clip(1 - y * 2 + balance, 0, 1) ** 1.5
    mh = np.clip(y * 2 - 1 + balance, 0, 1) ** 1.5
    return a + ms * np.array(shadow_rgb, np.float32) + mh * np.array(highlight_rgb, np.float32)


def fade(a, amount):
    return a * (1 - amount * 0.12) + amount * 0.08 if amount else a


def channel_mix_bw(a, r=0.3, g=0.59, b=0.11):
    y = a[..., 0:1] * r + a[..., 1:2] * g + a[..., 2:3] * b
    return np.repeat(y, 3, axis=-1)


# ============================================================================ spatial ops
def sharpen(im: Image.Image, amount: float, radius: float = 1.2) -> Image.Image:
    if amount <= 0:
        return im
    return im.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 150), threshold=2))


def clarity(a: np.ndarray, amount: float) -> np.ndarray:
    if not amount:
        return a
    y = luma(a)
    r = max(4, int(min(a.shape[:2]) / 60))
    blur = np.asarray(Image.fromarray((np.clip(y, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r)), np.float32) / 255
    detail = y - blur
    mid = 1 - np.abs(y - 0.5) * 2  # mostly in midtones
    return a + (detail * amount * 1.2 * np.clip(mid, 0.2, 1))[..., None]


def denoise(im: Image.Image, strength: float) -> Image.Image:
    if strength <= 0:
        return im
    try:
        import cv2
        arr = np.asarray(im.convert("RGB"))[:, :, ::-1]
        h = 3 + strength * 9
        out = cv2.fastNlMeansDenoisingColored(np.ascontiguousarray(arr), None, h, h, 7, 21)
        return Image.fromarray(out[:, :, ::-1])
    except ImportError:
        return im.filter(ImageFilter.MedianFilter(3))


def vignette(a: np.ndarray, amount: float, feather: float = 0.6) -> np.ndarray:
    if not amount:
        return a
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
    m = np.clip((d - (1 - feather) * 0.7) / (feather + 1e-6), 0, 1) ** 2
    return a * (1 - amount * 0.75 * m)[..., None] if amount > 0 else a + (1 - a) * (-amount) * 0.6 * m[..., None]


def grain(a: np.ndarray, amount: float, seed: int = 7) -> np.ndarray:
    if not amount:
        return a
    rng = np.random.default_rng(seed)
    h, w = a.shape[:2]
    n = rng.normal(0, 1, (h // 2 + 1, w // 2 + 1)).astype(np.float32)
    n = np.asarray(Image.fromarray(((n * 0.25 + 0.5).clip(0, 1) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32) / 255 - 0.5
    y = luma(a)
    return a + (n * amount * 0.16 * (1 - np.abs(y - 0.5)))[..., None]


# ============================================================================ looks → LUTs
def _look(name: str):
    L = {
        "teal_orange": lambda a: apply_curves(split_tone(contrast(vibrance(a, 0.15), 0.25), (-0.06, 0.02, 0.07), (0.07, 0.025, -0.06), 0.0),
                                              {"b": [[0, 18], [128, 124], [255, 238]]}),
        "film_portra": lambda a: saturation(apply_curves(temperature(a, 0.18, 0.04),
                                                         {"rgb": [[0, 14], [64, 66], [190, 196], [255, 246]], "g": [[0, 6], [255, 252]]}), -0.12),
        "film_fuji": lambda a: apply_curves(saturation(temperature(a, -0.08, -0.08), 0.05),
                                            {"rgb": [[0, 10], [70, 62], [180, 190], [255, 250]], "g": [[40, 46], [210, 214]], "b": [[0, 20], [255, 240]]}),
        "kodachrome": lambda a: saturation(contrast(temperature(a, 0.1, 0.0), 0.3), 0.25),
        "bleach_bypass": lambda a: contrast(saturation(a, -0.55), 0.45),
        "noir": lambda a: apply_curves(contrast(channel_mix_bw(a, 0.5, 0.4, 0.1), 0.55), [[0, 6], [255, 250]]),
        "bw_soft": lambda a: apply_curves(channel_mix_bw(a, 0.3, 0.55, 0.15), [[0, 28], [128, 128], [255, 238]]),
        "vintage_warm": lambda a: apply_curves(saturation(temperature(a, 0.3, 0.05), -0.25),
                                               {"rgb": [[0, 34], [128, 132], [255, 232]], "b": [[0, 30], [255, 205]]}),
        "moody_matte": lambda a: apply_curves(split_tone(saturation(contrast(a, 0.15), -0.3), (-0.02, 0.01, 0.05), (0.02, 0.01, -0.01)),
                                              [[0, 30], [60, 55], [200, 205], [255, 240]]),
        "cross_process": lambda a: apply_curves(a, {"r": [[0, 0], [64, 50], [192, 215], [255, 255]], "g": [[0, 0], [64, 55], [192, 210], [255, 255]],
                                                    "b": [[0, 40], [255, 200]]}),
        "golden_hour": lambda a: apply_curves(vibrance(temperature(a, 0.4, 0.08), 0.2), [[0, 8], [128, 136], [255, 252]]),
        "clean_bright": lambda a: vibrance(contrast(exposure(a, 0.25), 0.1), 0.25),
        "blockbuster_cool": lambda a: contrast(split_tone(temperature(a, -0.25, 0.0), (-0.04, 0.0, 0.06), (0.04, 0.02, -0.02)), 0.3),
        "sepia": lambda a: np.clip(channel_mix_bw(a) * np.array([1.07, 0.95, 0.78], np.float32), 0, 1),
    }
    if name not in L:
        raise ToolError(f"unknown look {name!r}", "one of: " + ", ".join(sorted(L)))
    return L[name]


LOOKS = {"teal_orange": "Hollywood teal shadows / orange skin & highlights", "film_portra": "warm, soft, pastel portrait film",
         "film_fuji": "cool-green shadows, clean highlights (Fuji-ish)", "kodachrome": "saturated warm classic slide film",
         "bleach_bypass": "desaturated, high-contrast gritty", "noir": "high-contrast black & white (red filter)",
         "bw_soft": "matte, soft black & white", "vintage_warm": "faded warm 70s print", "moody_matte": "lifted blacks, muted, cool shadows",
         "cross_process": "cross-processed yellow/cyan", "golden_hour": "warm sunset glow",
         "clean_bright": "bright, airy commercial", "blockbuster_cool": "cool, contrasty action look", "sepia": "sepia toned"}


def build_lut(fn, size: int = 33) -> np.ndarray:
    r = np.linspace(0, 1, size, dtype=np.float32)
    b, g, rr = np.meshgrid(r, r, r, indexing="ij")  # .cube order: R fastest
    grid = np.stack([rr, g, b], -1).reshape(-1, 1, 3)
    return np.clip(fn(grid), 0, 1).reshape(size, size, size, 3)  # [b][g][r]


def write_cube(lut: np.ndarray, path: Path, title: str) -> Path:
    n = lut.shape[0]
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    flat = lut.reshape(-1, 3)
    lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in flat]
    path.write_text("\n".join(lines) + "\n")
    return path


def read_cube(path: str) -> tuple[np.ndarray, list[float], list[float]]:
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"LUT not found: {p}")
    size, dmin, dmax, vals = 0, [0.0] * 3, [1.0] * 3, []
    for line in p.read_text(errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("TITLE"):
            continue
        if s.startswith("LUT_3D_SIZE"):
            size = int(s.split()[1])
        elif s.startswith("LUT_1D_SIZE"):
            raise ToolError("1D LUTs are not supported", "export a 3D .cube LUT")
        elif s.startswith("DOMAIN_MIN"):
            dmin = [float(x) for x in s.split()[1:4]]
        elif s.startswith("DOMAIN_MAX"):
            dmax = [float(x) for x in s.split()[1:4]]
        elif s[0].isdigit() or s[0] in "-.":
            vals.append([float(x) for x in s.split()[:3]])
    if not size or len(vals) != size ** 3:
        raise ToolError(f"{p.name} is not a valid 3D .cube LUT (size {size}, {len(vals)} entries)")
    return np.array(vals, np.float32).reshape(size, size, size, 3), dmin, dmax


def apply_lut_arr(a: np.ndarray, lut: np.ndarray, dmin=(0, 0, 0), dmax=(1, 1, 1)) -> np.ndarray:
    """Trilinear 3D LUT lookup; lut indexed [b][g][r]. Processes in row chunks to keep memory low."""
    n = lut.shape[0]
    out = np.empty_like(a)
    dmin, dmax = np.array(dmin, np.float32), np.array(dmax, np.float32)
    step = max(1, 2_000_000 // max(1, a.shape[1]))
    for y0 in range(0, a.shape[0], step):
        c = np.clip((a[y0:y0 + step] - dmin) / (dmax - dmin), 0, 1) * (n - 1)
        i0 = np.floor(c).astype(np.int32)
        i0 = np.minimum(i0, n - 2)
        f = c - i0
        r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
        acc = 0
        for db in (0, 1):
            wb = fb if db else 1 - fb
            for dg in (0, 1):
                wg = fg if dg else 1 - fg
                for dr in (0, 1):
                    wr = fr if dr else 1 - fr
                    acc = acc + lut[b0 + db, g0 + dg, r0 + dr] * (wb * wg * wr)
        out[y0:y0 + step] = acc
    return out


# ============================================================================ tools
def _finalize(im: Image.Image, src: Image.Image, image: str, suffix: str, project: str, out: str, format: str,
              quality: int, summary: str) -> Result:
    ext = _ext_for(image, format)
    if has_alpha(im) and ext in (".jpg", ".jpeg"):
        ext = ".png"
    p = out_file(project, out, stem_of(image, suffix), ext)
    save_image(im, p, quality=quality)
    before, after = image_stats(src), image_stats(im)
    res = Result(summary, files=[str(p)], previews=[before_after_preview(src, im, p)],
                 warnings=stats_warnings(after, before), data={"before": before, "after": after, "path": str(p)})
    return res


def _alpha(src):
    return src.getchannel("A") if has_alpha(src) else None


@tool("photo")
def photo_adjust(image: str, exposure_stops: float = 0.0, contrast_amount: float = 0.0, highlights: float = 0.0,
                 shadows: float = 0.0, whites: float = 0.0, blacks: float = 0.0, saturation_amount: float = 0.0,
                 vibrance_amount: float = 0.0, temperature_amount: float = 0.0, tint: float = 0.0, curves: dict = {},
                 clarity_amount: float = 0.0, sharpen_amount: float = 0.0, denoise_strength: float = 0.0,
                 vignette_amount: float = 0.0, grain_amount: float = 0.0, fade_amount: float = 0.0,
                 format: str = "", quality: int = 92, project: str = "", out: str = "") -> Result:
    """Lightroom-style manual adjustments in one pass (all default 0 = unchanged). Ranges: exposure_stops
    −3…+3; contrast/highlights/shadows/whites/blacks/saturation/vibrance/clarity −1…+1; temperature
    −1 cool…+1 warm; tint −1 green…+1 magenta; curves {'rgb':[[0,0],[64,50],[192,210],[255,255]],
    'r':…,'g':…,'b':…} (0–255 points, smooth monotone); sharpen 0…2; denoise 0…1 (OpenCV NL-means);
    vignette −1…+1; grain 0…1; fade 0…1 (matte blacks). Returns the image + before/after preview and
    clipping stats."""
    src = open_image(image)
    im = denoise(src.convert("RGB"), denoise_strength) if denoise_strength else src.convert("RGB")
    a = to_float(im)
    a = exposure(a, exposure_stops)
    a = temperature(a, temperature_amount, tint)
    a = shadows_highlights(a, shadows, highlights, whites, blacks)
    a = contrast(np.clip(a, 0, 1), contrast_amount)
    a = clarity(a, clarity_amount)
    a = apply_curves(np.clip(a, 0, 1), curves)
    a = vibrance(a, vibrance_amount)
    a = saturation(a, saturation_amount)
    a = fade(a, fade_amount)
    a = vignette(a, vignette_amount)
    a = grain(a, grain_amount)
    out_im = from_float(a)
    out_im = sharpen(out_im, sharpen_amount)
    if _alpha(src) is not None:
        out_im.putalpha(_alpha(src))
    used = {k: v for k, v in dict(exposure=exposure_stops, contrast=contrast_amount, highlights=highlights, shadows=shadows,
                                  whites=whites, blacks=blacks, saturation=saturation_amount, vibrance=vibrance_amount,
                                  temperature=temperature_amount, tint=tint, clarity=clarity_amount, sharpen=sharpen_amount,
                                  denoise=denoise_strength, vignette=vignette_amount, grain=grain_amount, fade=fade_amount).items() if v}
    if curves:
        used["curves"] = "custom"
    if not used:
        raise ToolError("no adjustment given", "set at least one of exposure_stops, contrast_amount, … (see help)")
    return _finalize(out_im, src, image, "adjusted", project, out, format, quality,
                     "Adjusted: " + ", ".join(f"{k} {v:+g}" if isinstance(v, (int, float)) else f"{k}" for k, v in used.items()))


@tool("photo")
def photo_auto_enhance(image: str, strength: float = 1.0, white_balance: bool = True, local_contrast: bool = True,
                       format: str = "", quality: int = 92, project: str = "", out: str = "") -> Result:
    """One-click enhance: neutralises colour casts (robust grey-world white balance), sets black/white
    points from the histogram, adds local contrast (CLAHE on lightness) when the photo is flat, a touch of
    vibrance and output sharpening. strength 0…1.5 scales everything. Before/after preview + stats."""
    src = open_image(image)
    a = to_float(src)
    orig = a.copy()
    notes = []
    if white_balance:
        y = luma(a)
        m = (y > np.percentile(y, 5)) & (y < np.percentile(y, 97))
        means = a[m].mean(0) if m.any() else a.reshape(-1, 3).mean(0)
        g = means.mean() / (means + 1e-6)
        g = 1 + (np.clip(g, 0.8, 1.25) - 1) * 0.7
        cast = float(np.abs(g - 1).max())
        if cast > 0.02:
            a = a * g
            notes.append(f"white balance (cast {cast * 100:.0f}%)")
    y = luma(np.clip(a, 0, 1))
    lo, hi = np.percentile(y, 0.4), np.percentile(y, 99.6)
    if hi - lo < 0.97:
        lo, hi = min(lo, 0.08), max(hi, 0.9)
        a = (a - lo) / (hi - lo)
        notes.append(f"levels {lo:.2f}–{hi:.2f}")
    a = np.clip(a, 0, 1)
    std = float(luma(a).std())
    if local_contrast and std < 0.26:
        try:
            import cv2
            lab = cv2.cvtColor((a * 255).astype(np.uint8), cv2.COLOR_RGB2LAB)
            clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8))
            L = clahe.apply(lab[..., 0])
            lab[..., 0] = (lab[..., 0] * 0.45 + L * 0.55).astype(np.uint8)
            a = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB).astype(np.float32) / 255
            notes.append("local contrast")
        except ImportError:
            a = contrast(a, 0.15)
    mid = float(np.median(luma(a)))
    if mid < 0.35:
        gamma = np.log(0.42) / np.log(max(mid, 0.05))
        a = a ** max(0.6, gamma)
        notes.append("lifted midtones")
    a = vibrance(a, 0.18)
    a = orig + (np.clip(a, 0, 1) - orig) * strength
    im = sharpen(from_float(a), 0.35 * strength)
    if _alpha(src) is not None:
        im.putalpha(_alpha(src))
    return _finalize(im, src, image, "enhanced", project, out, format, quality,
                     "Auto-enhanced: " + (", ".join(notes + ["vibrance", "sharpen"])))


@tool("photo")
def photo_look(image: str = "", look: str = "teal_orange", strength: float = 1.0, grain_amount: float = 0.0,
               vignette_amount: float = 0.0, export_cube: bool = False, format: str = "", quality: int = 92,
               project: str = "", out: str = "") -> Result:
    """Film / cinematic colour grade presets: teal_orange, film_portra, film_fuji, kodachrome,
    bleach_bypass, noir, bw_soft, vintage_warm, moody_matte, cross_process, golden_hour, clean_bright,
    blockbuster_cool, sepia. strength 0…1 blends with the original; optional grain/vignette.
    export_cube=true also writes the look as a 33³ .cube LUT (use it in Resolve, Premiere, Kdenlive,
    ffmpeg lut3d or photo_apply_lut); with no image it only writes the .cube. look='all' renders a
    contact sheet of every look on your image so the user can pick."""
    if look == "all":
        if not image:
            raise ToolError("look='all' needs an image to preview on")
        src = open_image(image)
        th = src.convert("RGB").copy()
        th.thumbnail((360, 360))
        from ._common import labelled
        tiles = [labelled(th, "original")]
        for name in LOOKS:
            lut = build_lut(_look(name), 25)
            tiles.append(labelled(from_float(apply_lut_arr(to_float(th), lut)), name))
        cols = 5
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (th.width + 8) + 8, rows * (th.height + 8) + 8), (24, 24, 26))
        for i, t in enumerate(tiles):
            sheet.paste(t, (8 + (i % cols) * (th.width + 8), 8 + (i // cols) * (th.height + 8)))
        p = out_file(project, out, stem_of(image, "looks-sheet"), ".jpg")
        save_image(sheet, p, 90)
        return Result(f"Preview of all {len(LOOKS)} looks on {Path(image).name}.", files=[str(p)], previews=[str(p)],
                      data={"looks": LOOKS}, next_steps=["photo_look with the chosen look name"])
    fn = _look(look)
    lut = build_lut(fn, 33)
    files, prevs = [], []
    res = None
    if image:
        src = open_image(image)
        a = to_float(src)
        g = apply_lut_arr(a, lut)
        g = a + (g - a) * strength
        g = vignette(g, vignette_amount)
        g = grain(g, grain_amount)
        im = from_float(g)
        if _alpha(src) is not None:
            im.putalpha(_alpha(src))
        res = _finalize(im, src, image, look.replace("_", "-"), project, out, format, quality,
                        f"Applied look '{look}' ({LOOKS[look]}) at {strength:.0%}.")
    if export_cube or not image:
        cp = out_file(project, out, f"look-{look.replace('_', '-')}", ".cube")
        write_cube(lut, cp, f"studio {look}")
        files.append(str(cp))
        if res is None:
            res = Result(f"Wrote LUT {cp.name} for look '{look}'.", files=files)
        else:
            res.files += files
        res.next_steps.append(f"ffmpeg -i in.mp4 -vf lut3d='{cp}' out.mp4  (same look on video)")
    return res


@tool("photo")
def photo_apply_lut(image: str, lut: str, strength: float = 1.0, format: str = "", quality: int = 92,
                    project: str = "", out: str = "") -> Result:
    """Apply a 3D .cube LUT (from Resolve, a LUT pack, or photo_look export_cube) with trilinear
    interpolation; strength 0…1 blends with the original. Before/after preview."""
    src = open_image(image)
    table, dmin, dmax = read_cube(lut)
    a = to_float(src)
    g = apply_lut_arr(a, table, dmin, dmax)
    g = a + (g - a) * strength
    im = from_float(g)
    if _alpha(src) is not None:
        im.putalpha(_alpha(src))
    return _finalize(im, src, image, Path(lut).stem, project, out, format, quality,
                     f"Applied LUT {Path(lut).name} ({table.shape[0]}³) at {strength:.0%}.")
