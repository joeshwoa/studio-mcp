"""Where the subject is: faces (OpenCV Haar, bundled with opencv) + spectral-residual
saliency + optional AI subject mask. Used by smart crop, resize(fill) and collage."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def _cv2():
    try:
        import cv2  # noqa
        try:
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:
            pass
        return cv2
    except ImportError:
        return None


def detect_faces(im: Image.Image) -> list[tuple[int, int, int, int]]:
    """Face boxes (x, y, w, h) in image pixels: OpenCV YuNet DNN detector (tiny ONNX, fetched once),
    falling back to Haar cascades on OpenCV builds that still ship them. Empty when OpenCV is missing."""
    cv2 = _cv2()
    if cv2 is None:
        return []
    rgb = im.convert("RGB")
    scale = 1.0
    if max(rgb.size) > 1280:
        scale = 1280 / max(rgb.size)
        rgb = rgb.resize((int(rgb.width * scale), int(rgb.height * scale)), Image.BILINEAR)
    boxes = []
    if hasattr(cv2, "FaceDetectorYN"):
        from .models import face_model
        mp = face_model()
        if mp:
            try:
                bgr = np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])
                det = cv2.FaceDetectorYN.create(str(mp), "", (bgr.shape[1], bgr.shape[0]), 0.7, 0.3, 50)
                _, faces = det.detect(bgr)
                for f in (faces if faces is not None else []):
                    x, y, w, h = f[:4]
                    boxes.append((int(x / scale), int(y / scale), int(w / scale), int(h / scale)))
            except Exception:
                boxes = []
    if not boxes and hasattr(cv2, "CascadeClassifier"):
        g = cv2.equalizeHist(np.asarray(rgb.convert("L")))
        for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
            try:
                cc = cv2.CascadeClassifier(cv2.data.haarcascades + name)
                minsz = max(24, int(min(g.shape) * 0.06))
                found = cc.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(minsz, minsz))
                for (x, y, w, h) in (found if len(found) else []):
                    boxes.append((int(x / scale), int(y / scale), int(w / scale), int(h / scale)))
            except Exception:
                continue
    out: list[tuple[int, int, int, int]] = []
    for b in sorted(boxes, key=lambda b: -b[2] * b[3]):
        if all(_iou(b, o) < 0.3 for o in out):
            out.append(b)
    return out


def _iou(a, b) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    return inter / float(a[2] * a[3] + b[2] * b[3] - inter + 1e-9)


def spectral_saliency(im: Image.Image, size: int = 128) -> np.ndarray:
    """Spectral residual saliency (Hou & Zhang 2007) + local contrast, normalised 0..1, at `size`."""
    w, h = im.size
    sc = size / max(w, h)
    sm = im.convert("RGB").resize((max(8, int(w * sc)), max(8, int(h * sc))), Image.BILINEAR)
    lab = np.asarray(sm.convert("LAB"), dtype=np.float32)
    total = np.zeros(lab.shape[:2], np.float32)
    for c in range(3):
        ch = lab[..., c]
        F = np.fft.fft2(ch)
        logA = np.log(np.abs(F) + 1e-8)
        ph = np.angle(F)
        k = np.ones((3, 3), np.float32) / 9
        pad = np.pad(logA, 1, mode="edge")
        avg = sum(pad[i:i + logA.shape[0], j:j + logA.shape[1]] * k[i, j] for i in range(3) for j in range(3))
        sal = np.abs(np.fft.ifft2(np.exp(logA - avg + 1j * ph))) ** 2
        total += sal
    s = Image.fromarray((total / (total.max() + 1e-9) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
    s = np.asarray(s, np.float32) / 255.0
    # colour distinctness from the mean (helps big uniform subjects)
    mean = lab.reshape(-1, 3).mean(0)
    dist = np.sqrt(((lab - mean) ** 2).sum(-1))
    dist = np.asarray(Image.fromarray((dist / (dist.max() + 1e-9) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(4)), np.float32) / 255
    out = 0.6 * s + 0.4 * dist
    return out / (out.max() + 1e-9)


def saliency_map(im: Image.Image, size: int = 160, faces: list | None = None, subject_mask: Image.Image | None = None) -> np.ndarray:
    """Combined importance map at `size` long side."""
    sal = spectral_saliency(im, size)
    h, w = sal.shape
    yy, xx = np.mgrid[0:h, 0:w]
    center = np.exp(-(((xx - w / 2) / (w * 0.6)) ** 2 + ((yy - h / 2) / (h * 0.6)) ** 2))
    m = sal * (0.7 + 0.3 * center)
    if subject_mask is not None:
        sm = np.asarray(subject_mask.convert("L").resize((w, h), Image.BILINEAR), np.float32) / 255
        m = 0.35 * m + 0.65 * sm
    sx, sy = w / im.width, h / im.height
    for (x, y, fw, fh) in faces or []:
        x0, y0 = int(x * sx), int(y * sy)
        x1, y1 = int((x + fw) * sx) + 1, int((y + fh) * sy) + 1
        m[max(0, y0):y1, max(0, x0):x1] += 3.0
    return m / (m.max() + 1e-9)


def best_crop(im: Image.Image, ratio: float, zoom: float = 1.0, faces: list | None = None,
              subject_mask: Image.Image | None = None) -> tuple[tuple[int, int, int, int], dict]:
    """Best crop box (l, t, r, b) of aspect `ratio` keeping the salient subject (faces fully inside,
    headroom above faces). zoom>1 crops tighter."""
    W, H = im.size
    faces = detect_faces(im) if faces is None else faces
    smap = saliency_map(im, 200, faces, subject_mask)
    sh, sw = smap.shape
    # largest window of this ratio, then zoom
    if W / H > ratio:
        ch, cw = H, H * ratio
    else:
        cw, ch = W, W / ratio
    cw, ch = cw / max(zoom, 1.0), ch / max(zoom, 1.0)
    cw, ch = int(round(cw)), int(round(ch))
    ii = np.pad(smap.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    kx, ky = sw / W, sh / H
    wx, wy = max(1, int(cw * kx)), max(1, int(ch * ky))
    best, best_score = (0, 0), -1e9
    total = smap.sum() + 1e-9
    stepx = max(1, (sw - wx) // 60 or 1)
    stepy = max(1, (sh - wy) // 60 or 1)
    fcs = [(x * kx, y * ky, fw * kx, fh * ky) for x, y, fw, fh in faces]
    for y0 in range(0, max(1, sh - wy + 1), stepy):
        for x0 in range(0, max(1, sw - wx + 1), stepx):
            s = ii[y0 + wy, x0 + wx] - ii[y0, x0 + wx] - ii[y0 + wy, x0] + ii[y0, x0]
            score = s / total
            for (fx, fy, fw, fh) in fcs:
                inside = fx - fw * 0.2 >= x0 and fy - fh * 0.35 >= y0 and fx + fw * 1.2 <= x0 + wx and fy + fh * 1.2 <= y0 + wy
                if not inside:
                    score -= 0.5
                else:
                    # eyes near the upper third
                    eye_y = (fy + fh * 0.4 - y0) / wy
                    score -= 0.15 * abs(eye_y - 0.36) if wy > fh * 2.2 else 0
                    score -= 0.3 * abs((fx + fw / 2 - x0) / wx - 0.5)
            if score > best_score:
                best_score, best = score, (x0, y0)
    l = int(best[0] / kx)
    t = int(best[1] / ky)
    if faces:
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        if fh * 1.4 > ch or fw > cw:  # face can't fit with headroom: centre on the face (eyes a bit above middle)
            l = int(fx + fw / 2 - cw / 2)
            t = int(fy + fh * 0.45 - ch * 0.45)
    l = max(0, min(W - cw, l))
    t = max(0, min(H - ch, t))
    info = {"faces": len(faces), "saliency_kept": round(float(best_score), 3)}
    return (l, t, l + cw, t + ch), info
