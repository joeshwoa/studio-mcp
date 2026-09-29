"""Smart reframe: follow faces (YuNet via the photo department) — or the most salient region when no
face is visible — with a virtual camera operator: dead zone, eased moves, no drifting, hard re-frame at
scene cuts. Produces a crop path rendered by ffmpeg as a piecewise-linear crop expression."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ...core.deps import need
from ...core.result import ToolError
from . import _common as C


def analyse(src: Path, info: dict, sample_fps: float = 6.0, width: int = 640) -> dict:
    """Sample frames → face / saliency targets (normalised 0..1 x and y), scene cuts."""
    from ..photo.saliency import detect_faces, saliency_map
    W, H = info["width"], info["height"]
    sw = width
    sh = int(round(H * sw / W / 2)) * 2
    cmd = [need("ffmpeg"), "-v", "error", "-nostdin", "-i", str(src), "-vf", f"fps={sample_fps},scale={sw}:{sh}",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fb = sw * sh * 3
    ts, xs, ys, kinds, sizes, thumbs = [], [], [], [], [], []
    i = 0
    prev = None
    while True:
        buf = proc.stdout.read(fb)
        if len(buf) < fb:
            break
        a = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
        im = Image.fromarray(a)
        t = i / sample_fps
        faces = detect_faces(im)
        tgt = None
        if faces:
            # main subject: biggest face, but stick with the previous subject when it is still comparable
            big = max(faces, key=lambda f: f[2] * f[3])
            cand = [f for f in faces if f[2] * f[3] >= 0.55 * big[2] * big[3]]
            if prev is not None and len(cand) > 1:
                f = min(cand, key=lambda f: abs((f[0] + f[2] / 2) / sw - prev))
            else:
                f = big
            tgt = ((f[0] + f[2] / 2) / sw, (f[1] + f[3] * 0.45) / sh, "face", f[3] / sh)
        else:
            m = saliency_map(im, 96)
            hh, ww = m.shape
            m = np.clip(m - np.percentile(m, 70), 0, None)
            s = m.sum()
            if s > 1e-6:
                yy, xx = np.mgrid[0:hh, 0:ww]
                tgt = (float((m * xx).sum() / s / ww), float((m * yy).sum() / s / hh), "saliency", 0.0)
        if tgt:
            ts.append(t)
            xs.append(tgt[0])
            ys.append(tgt[1])
            kinds.append(tgt[2])
            sizes.append(tgt[3])
            prev = tgt[0] if tgt[2] == "face" else prev
        thumbs.append(np.asarray(im.convert("L").resize((32, 18)), np.float32))
        i += 1
    proc.wait()
    n = i
    cuts = []
    for k in range(1, len(thumbs)):
        d = float(np.abs(thumbs[k] - thumbs[k - 1]).mean())
        if d > 28:
            cuts.append(k / sample_fps)
    return {"t": ts, "x": xs, "y": ys, "kind": kinds, "size": sizes, "samples": n, "cuts": cuts, "fps": sample_fps}


def _smooth(vals: np.ndarray, sigma: float) -> np.ndarray:
    if len(vals) < 3 or sigma <= 0:
        return vals
    r = int(max(1, sigma * 3))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    p = np.pad(vals, r, mode="edge")
    return np.convolve(p, k, mode="valid")


def camera_path(an: dict, duration: float, crop_frac: float, deadzone: float = 0.1, smooth_s: float = 0.5,
                max_speed: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Crop-centre path (normalised x of the crop centre) on a regular 0.1 s grid.
    crop_frac = crop width / frame width. deadzone: fraction of the crop width the subject may move
    before the camera follows. max_speed: frame-widths per second."""
    step = 0.1
    grid = np.arange(0, max(step, duration) + 1e-9, step)
    lo, hi = crop_frac / 2, 1 - crop_frac / 2
    if not an["t"]:
        return grid, np.full(len(grid), 0.5)
    t = np.array(an["t"])
    x = np.array(an["x"])
    raw = np.interp(grid, t, x)
    bounds = [0.0] + [c for c in an["cuts"] if 0 < c < duration] + [duration + 1]
    out = np.empty_like(raw)
    for a, b in zip(bounds, bounds[1:]):
        idx = np.where((grid >= a) & (grid < b))[0]
        if not len(idx):
            continue
        seg = raw[idx]
        # median de-jitter
        if len(seg) >= 5:
            seg = np.array([np.median(seg[max(0, i - 2): i + 3]) for i in range(len(seg))])
        cam = np.empty_like(seg)
        c = float(np.clip(np.median(seg[: max(1, int(1 / step))]), lo, hi))   # start framed on the subject
        dz = deadzone * crop_frac
        vmax = max_speed * step
        for i, s in enumerate(seg):
            err = s - c
            if abs(err) > dz:
                move = (abs(err) - dz) * np.sign(err) * 0.35
                c += float(np.clip(move, -vmax, vmax))
            cam[i] = c
        out[idx] = _smooth(cam, smooth_s / step) if len(cam) > 3 else cam
    return grid, np.clip(out, lo, hi)


def simplify(t: np.ndarray, v: np.ndarray, tol: float) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker on the path → few keyframes (tol in the same units as v)."""
    pts = list(zip(t.tolist(), v.tolist()))
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ta, va = pts[a]
        tb, vb = pts[b]
        best, bi = 0.0, -1
        for i in range(a + 1, b):
            ti, vi = pts[i]
            vl = va + (vb - va) * (ti - ta) / (tb - ta) if tb > ta else va
            d = abs(vi - vl)
            if d > best:
                best, bi = d, i
        if best > tol and bi > 0:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def crop_expr(kf: list[tuple[float, float]], max_x: int) -> str:
    """Piecewise-linear x(t) as a flat sum of gated terms (no deep nesting)."""
    if len(kf) == 1:
        return str(int(kf[0][1]))
    terms = []
    for (ta, xa), (tb, xb) in zip(kf, kf[1:]):
        if tb - ta < 1e-6:
            continue
        slope = (xb - xa) / (tb - ta)
        terms.append(f"gte(t,{ta:.3f})*lt(t,{tb:.3f})*({xa:.2f}+{slope:.4f}*(t-{ta:.3f}))")
    terms.append(f"gte(t,{kf[-1][0]:.3f})*{kf[-1][1]:.2f}")
    return f"clip({'+'.join(terms)},0,{max_x})"


def plot(an: dict, grid: np.ndarray, path: np.ndarray, crop_frac: float, duration: float, dest: Path) -> Path:
    """Diagnostic: subject detections (dots) and the camera window (band) over time."""
    Wp, Hp = 900, 300
    im = Image.new("RGB", (Wp, Hp), (24, 24, 28))
    d = ImageDraw.Draw(im)
    X = lambda t: 40 + (Wp - 60) * t / max(duration, 1e-6)
    Y = lambda v: 20 + (Hp - 50) * v
    for c in an["cuts"]:
        d.line([X(c), 10, X(c), Hp - 25], fill=(90, 90, 110))
    band = [(X(t), Y(v - crop_frac / 2)) for t, v in zip(grid, path)] + [(X(t), Y(v + crop_frac / 2)) for t, v in zip(grid[::-1], path[::-1])]
    d.polygon(band, fill=(40, 70, 120))
    d.line([(X(t), Y(v)) for t, v in zip(grid, path)], fill=(120, 170, 255), width=2)
    for t, x, k in zip(an["t"], an["x"], an["kind"]):
        col = (255, 200, 60) if k == "face" else (150, 150, 150)
        d.ellipse([X(t) - 2, Y(x) - 2, X(t) + 2, Y(x) + 2], fill=col)
    d.text((10, Hp - 20), "vertical: source x (top=left edge)  yellow=face  grey=saliency  blue=crop window  lines=scene cuts",
           fill=(200, 200, 200))
    im.save(dest)
    return dest
