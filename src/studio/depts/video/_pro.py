"""Shared helpers for the pro video tools (no @tool here): raw frame/audio readers over ffmpeg pipes,
frame metrics (scene score, sharpness, shake), labelled preview sheets and table images."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterator

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...core.deps import need
from ...core.result import ToolError

_FONTS: dict = {}


def font(size: int, bold: bool = False, text: str = "") -> ImageFont.ImageFont:
    """A real TrueType face for preview labels (photo.fonts when available, else DejaVu)."""
    key = (size, bold, any("؀" <= c <= "ۿ" for c in text))
    if key in _FONTS:
        return _FONTS[key]
    f = None
    try:
        from ..photo.fonts import pil_font
        f = pil_font("", size, 700 if bold else 400, text=text)[0]
    except Exception:
        f = None
    if f is None:
        for cand in (("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                     ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
                     ("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf")):
            try:
                f = ImageFont.truetype(cand, size)
                break
            except Exception:
                continue
    f = f or ImageFont.load_default()
    _FONTS[key] = f
    return f


def tc(t: float, fps: float = 0.0) -> str:
    """mm:ss.cc (or hh:mm:ss.cc) for labels."""
    t = max(0.0, float(t))
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}" if h else f"{m:02d}:{s:05.2f}"


def smpte(t: float, fps: float) -> str:
    """HH:MM:SS:FF (non-drop) for EDLs."""
    fr = int(round(max(0.0, t) * fps))
    f = int(round(fps))
    return f"{fr // (3600 * f):02d}:{fr // (60 * f) % 60:02d}:{fr // f % 60:02d}:{fr % f:02d}"


# ─────────────────────────── raw readers ───────────────────────────

def frames(path: str | Path, w: int, h: int, *, fps: float = 0.0, start: float = 0.0, dur: float = 0.0,
           gray: bool = False) -> Iterator[np.ndarray]:
    """Decode frames scaled to w×h (uint8 arrays) through an ffmpeg pipe — low memory, any length."""
    vf = f"scale={w}:{h}:flags=area"
    if fps:
        vf = f"fps={fps},{vf}"
    args = [need("ffmpeg"), "-v", "error", "-nostdin"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if dur:
        args += ["-t", f"{dur:.3f}"]
    pix, ch = ("gray", 1) if gray else ("rgb24", 3)
    args += ["-an", "-vf", vf, "-pix_fmt", pix, "-f", "rawvideo", "-"]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = w * h * ch
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            a = np.frombuffer(b, np.uint8)
            yield a.reshape(h, w) if gray else a.reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def audio_mono(path: str | Path, sr: int = 16000, start: float = 0.0, dur: float = 0.0) -> np.ndarray:
    """Mono float32 samples (empty when there is no audio)."""
    args = [need("ffmpeg"), "-v", "error", "-nostdin"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if dur:
        args += ["-t", f"{dur:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    r = subprocess.run(args, capture_output=True, timeout=3600)
    if r.returncode != 0:
        return np.zeros(0, np.float32)
    return np.frombuffer(r.stdout, np.float32).copy()


def db(x: float) -> float:
    return float(20 * np.log10(max(1e-9, x)))


# ─────────────────────────── frame metrics ───────────────────────────

def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian, normalised for size (computed on a 640-wide frame) — higher = sharper.
    Rough scale: < 40 soft/blurry, 40–150 OK, > 150 crisp."""
    g = gray.astype(np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Global translation between two gray frames by phase correlation (pixels)."""
    A = np.fft.fft2(a.astype(np.float32) - a.mean())
    B = np.fft.fft2(b.astype(np.float32) - b.mean())
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-6
    r = np.abs(np.fft.ifft2(R))
    y, x = np.unravel_index(np.argmax(r), r.shape)
    h, w = a.shape
    if y > h // 2:
        y -= h
    if x > w // 2:
        x -= w
    return float(x), float(y)


def exposure(gray: np.ndarray) -> dict:
    g = gray.astype(np.float32) / 255
    return {"brightness": round(float(g.mean()), 3), "contrast": round(float(g.std()), 3),
            "clip_low": round(float((g < 0.02).mean()), 3), "clip_high": round(float((g > 0.98).mean()), 3)}


def faces(im: Image.Image) -> list:
    try:
        from ..photo.saliency import detect_faces
        return detect_faces(im)
    except Exception:
        return []


# ─────────────────────────── previews ───────────────────────────

def tile_sheet(tiles: list[dict], dest: Path, title: str = "", subtitle: str = "", cols: int = 4, tile_w: int = 360) -> Path:
    """tiles = [{img: PIL.Image, head: str, lines: [str], badge: str, colour: (r,g,b)}] → one labelled PNG."""
    if not tiles:
        raise ToolError("nothing to put on the sheet")
    cols = max(1, min(cols, len(tiles)))
    ims = []
    for t in tiles:
        im = t["img"].convert("RGB")
        r = tile_w / im.width
        ims.append(im.resize((tile_w, max(2, int(im.height * r))), Image.LANCZOS))
    th = max(i.height for i in ims)
    th = min(th, int(tile_w * 1.8))
    nl = max((len(t.get("lines", [])) for t in tiles), default=0)
    lab_h = 30 + 20 * nl
    pad = 14
    head_h = 70 if title else 10
    rows = (len(tiles) + cols - 1) // cols
    W = pad + cols * (tile_w + pad)
    H = head_h + rows * (th + lab_h + pad) + pad
    sheet = Image.new("RGB", (W, H), (20, 21, 26))
    d = ImageDraw.Draw(sheet)
    if title:
        d.text((pad, 14), title, font=font(24, True, title), fill=(245, 245, 245))
        if subtitle:
            d.text((pad, 44), subtitle, font=font(14, False, subtitle), fill=(160, 165, 175))
    for k, (t, im) in enumerate(zip(tiles, ims)):
        x = pad + (k % cols) * (tile_w + pad)
        y = head_h + (k // cols) * (th + lab_h + pad)
        d.rectangle([x - 1, y - 1, x + tile_w, y + th], fill=(8, 8, 10))
        if im.height > th:
            im = im.crop((0, (im.height - th) // 2, tile_w, (im.height - th) // 2 + th))
        sheet.paste(im, (x, y + (th - im.height) // 2))
        col = tuple(t.get("colour") or (90, 160, 255))
        d.rectangle([x, y + th, x + tile_w - 1, y + th + 4], fill=col)
        if t.get("badge"):
            b = str(t["badge"])
            f = font(15, True, b)
            bw = d.textlength(b, font=f) + 12
            d.rectangle([x + 6, y + 6, x + 6 + bw, y + 30], fill=(0, 0, 0))
            d.text((x + 12, y + 9), b, font=f, fill=(255, 255, 255))
        if t.get("corner"):
            b = str(t["corner"])
            f = font(14, True, b)
            bw = d.textlength(b, font=f) + 12
            d.rectangle([x + tile_w - 6 - bw, y + 6, x + tile_w - 6, y + 28], fill=col)
            d.text((x + tile_w - bw, y + 9), b, font=f, fill=(0, 0, 0))
        hd = str(t.get("head", ""))
        d.text((x + 2, y + th + 8), _fit(d, hd, font(15, True, hd), tile_w - 4), font=font(15, True, hd), fill=(240, 240, 240))
        for i, ln in enumerate(t.get("lines", [])):
            f = font(13, False, ln)
            d.text((x + 2, y + th + 30 + 20 * i), _fit(d, ln, f, tile_w - 4), font=f, fill=(175, 180, 190))
    sheet.save(dest)
    return dest


def _fit(d: ImageDraw.ImageDraw, s: str, f, maxw: int) -> str:
    if d.textlength(s, font=f) <= maxw:
        return s
    while s and d.textlength(s + "…", font=f) > maxw:
        s = s[:-1]
    return s + "…"


STATUS_COL = {"PASS": (46, 170, 90), "WARN": (230, 165, 30), "FAIL": (215, 60, 60), "INFO": (90, 130, 200), "SKIP": (110, 110, 120)}


def table_image(rows: list[dict], dest: Path, title: str, subtitle: str = "", thumb: Image.Image | None = None) -> Path:
    """rows = [{check, status (PASS/WARN/FAIL/INFO/SKIP), value, detail}] → a readable report PNG."""
    W = 1280
    rh = 34
    head = 96
    th_h = 0
    if thumb is not None:
        thumb = thumb.convert("RGB")
        thumb.thumbnail((360, 240))
        th_h = thumb.height + 16
    H = head + max(th_h, 0) + rh * (len(rows) + 1) + 30
    im = Image.new("RGB", (W, H), (248, 248, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, head - 12], fill=(24, 26, 32))
    d.text((20, 16), title, font=font(26, True, title), fill=(255, 255, 255))
    if subtitle:
        d.text((20, 54), _fit(d, subtitle, font(15), W - 40), font=font(15, False, subtitle), fill=(180, 185, 195))
    y = head
    if thumb is not None:
        im.paste(thumb, (20, y))
        counts = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        x = 40 + thumb.width
        for k in ("FAIL", "WARN", "PASS", "INFO", "SKIP"):
            if counts.get(k):
                d.rounded_rectangle([x, y + 10, x + 150, y + 70], 10, fill=STATUS_COL[k])
                d.text((x + 14, y + 16), str(counts[k]), font=font(28, True), fill=(255, 255, 255))
                d.text((x + 60, y + 26), k, font=font(18, True), fill=(255, 255, 255))
                x += 166
        y += th_h
    cols = [(20, "Check", 230), (255, "Status", 90), (355, "Measured", 330), (695, "Detail / guideline", 570)]
    d.rectangle([10, y, W - 10, y + rh], fill=(225, 228, 235))
    for x, name, _ in cols:
        d.text((x, y + 8), name, font=font(15, True), fill=(30, 30, 40))
    y += rh
    for i, r in enumerate(rows):
        if i % 2:
            d.rectangle([10, y, W - 10, y + rh], fill=(238, 240, 244))
        st = r.get("status", "INFO")
        d.text((cols[0][0], y + 8), _fit(d, str(r.get("check", "")), font(15, True), cols[0][2]), font=font(15, True), fill=(20, 20, 30))
        d.rounded_rectangle([cols[1][0], y + 5, cols[1][0] + 70, y + rh - 5], 6, fill=STATUS_COL.get(st, (120, 120, 120)))
        d.text((cols[1][0] + 8, y + 8), st, font=font(14, True), fill=(255, 255, 255))
        v = str(r.get("value", ""))
        d.text((cols[2][0], y + 8), _fit(d, v, font(15, False, v), cols[2][2]), font=font(15, False, v), fill=(30, 30, 40))
        dt = str(r.get("detail", ""))
        d.text((cols[3][0], y + 8), _fit(d, dt, font(14, False, dt), cols[3][2]), font=font(14, False, dt), fill=(80, 85, 95))
        y += rh
    im.save(dest)
    return dest
