"""Procedural sound effects (offline) for edits, reels, UI and motion graphics."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _synth as S

SR = S.SR


def _t(d):
    return np.arange(int(d * SR)) / SR


def _sweep_noise(d: float, f0: float, f1: float, q: float = 1.5, seed: int = 0) -> np.ndarray:
    """Noise through a band-pass whose centre glides f0→f1 (block-wise, smooth)."""
    from scipy.signal import butter, sosfilt, sosfilt_zi
    n = int(d * SR)
    x = S.noise(n, seed)
    out = np.zeros(n, np.float32)
    blk = 512
    zi = None
    for i in range(0, n, blk):
        fc = f0 * (f1 / f0) ** (i / max(1, n))
        lo, hi = fc / (1 + 1 / q), min(fc * (1 + 1 / q), SR / 2 * 0.95)
        sos = butter(2, [lo, hi], "bandpass", fs=SR, output="sos")
        if zi is None or zi.shape[0] != sos.shape[0]:
            zi = sosfilt_zi(sos) * 0
        y, zi = sosfilt(sos, x[i:i + blk], zi=zi)
        out[i:i + blk] = y
    return out


def whoosh(d=0.9, seed=0):
    t = _t(d)
    e = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2.2
    return _sweep_noise(d, 300, 3500, 1.2, seed) * e * 2.5, np.linspace(-0.8, 0.8, len(t))


def riser(d=3.0, seed=0):
    t = _t(d)
    e = (t / d) ** 2
    tone = sum(np.sin(2 * np.pi * np.cumsum(f * (1 + 2.5 * (t / d) ** 2)) / SR) for f in (220, 331, 440.5)) / 3
    return (_sweep_noise(d, 200, 9000, 2, seed) * 2 + tone * 0.35) * e, None


def downlifter(d=2.0, seed=0):
    x, _ = riser(d, seed)
    return x[::-1] * 1.0, None


def impact(d=2.2, seed=0):
    b = S.boom(1.0)[: int(d * SR)]
    t = _t(len(b) / SR)
    crack = S.bp(S.noise(len(b), seed), 800, 8000) * np.exp(-t * 18) * 0.6
    return b + crack, None


def pop(d=0.12, seed=0):
    t = _t(d)
    f = 400 + 900 * (1 - np.exp(-t * 60))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 45) * 0.8, None


def click(d=0.05, seed=0):
    return S.rim(0.9)[: int(d * SR)] * 1.5, None


def ding(d=1.6, seed=0):
    return S.add(S.bell(88, 0.2, 0.9), 0.6 * S.bell(95, 0.2, 0.6))[: int(d * SR)] * 1.6, None


def success(d=1.4, seed=0):
    out = np.zeros(int(d * SR), np.float32)
    for i, n in enumerate((76, 80, 83, 88)):
        x = S.bell(n, 0.15, 0.7) * 1.4
        k = int(i * 0.09 * SR)
        m = min(len(x), len(out) - k)
        out[k:k + m] += x[:m]
    return out, None


def error(d=0.6, seed=0):
    t = _t(d)
    out = np.zeros(len(t), np.float32)
    for k, f in ((0, 330), (0.18, 247)):
        s = int(k * SR)
        tt = t[: len(t) - s]
        out[s:] += (np.sign(np.sin(2 * np.pi * f * tt)) * 0.25 + np.sin(2 * np.pi * f * tt) * 0.4) * np.exp(-tt * 9)
    return S.lp(out, 3000), None


def glitch(d=0.7, seed=0):
    rng = np.random.default_rng(seed)
    out = np.zeros(int(d * SR), np.float32)
    src = S.bp(S.noise(len(out), seed), 300, 6000) + np.sign(np.sin(2 * np.pi * 120 * _t(d))) * 0.3
    i = 0
    while i < len(out):
        L = int(rng.uniform(0.012, 0.06) * SR)
        if rng.random() < 0.7:
            seg = src[i:i + L] * rng.uniform(0.3, 1)
            rep = int(rng.integers(1, 4))
            for r in range(rep):
                a = i + r * len(seg)
                m = min(len(seg), len(out) - a)
                if m > 0:
                    out[a:a + m] = np.round(seg[:m] * 6) / 6          # bit-crushed stutter
            i += L * rep
        else:
            i += L
    return out * 0.8, None


def typing(d=2.0, seed=0):
    rng = np.random.default_rng(seed)
    out = np.zeros(int(d * SR), np.float32)
    t = 0.05
    while t < d - 0.1:
        x = S.bp(S.noise(int(0.03 * SR), int(t * 1000)), 1500, 7000) * np.exp(-np.arange(int(0.03 * SR)) / SR * 150)
        x += np.sin(2 * np.pi * rng.uniform(180, 260) * np.arange(len(x)) / SR) * np.exp(-np.arange(len(x)) / SR * 120) * 0.4
        k = int(t * SR)
        out[k:k + len(x)] += x[: len(out) - k] * rng.uniform(0.5, 1)
        t += rng.uniform(0.07, 0.19) + (0.25 if rng.random() < 0.08 else 0)
    return out, None


def tick(d=1.0, seed=0):
    out = np.zeros(int(d * SR), np.float32)
    for i in range(int(d * 2)):
        x = S.rim(0.8 if i % 2 == 0 else 0.6)
        k = int(i * 0.5 * SR)
        out[k:k + len(x)] += x[: len(out) - k]
    return out, None


def shutter(d=0.35, seed=0):
    out = np.zeros(int(d * SR), np.float32)
    for k, v in ((0, 1.0), (0.09, 0.7)):
        s = int(k * SR)
        n = int(0.05 * SR)
        x = S.bp(S.noise(n, int(k * 99)), 1200, 9000) * np.exp(-np.arange(n) / SR * 90) * v
        out[s:s + n] += x
    return out, None


KINDS = {"whoosh": (whoosh, 0.9), "riser": (riser, 3.0), "downlifter": (downlifter, 2.0), "impact": (impact, 2.2),
         "pop": (pop, 0.12), "click": (click, 0.05), "ding": (ding, 1.6), "success": (success, 1.4),
         "error": (error, 0.6), "glitch": (glitch, 0.7), "typing": (typing, 2.0), "tick": (tick, 1.0),
         "shutter": (shutter, 0.35)}


@tool("audio")
def audio_sfx(kind: str = "whoosh", duration: float = 0.0, variations: int = 1, seed: int = 0,
              loudness: float = -16.0, project: str = "", out: str = "") -> Result:
    """Generate a sound effect offline (original, royalty-free): whoosh | riser | downlifter | impact |
    pop | click | ding | success | error | glitch | typing | tick | shutter. duration 0 = natural
    length (risers/typing/ticks stretch to any duration). variations=N renders N takes with different
    seeds (pick by ear). Returns WAV file(s) + preview. For recorded/realistic effects (rain, door,
    crowd…) use audio_library_search (Freesound/Openverse)."""
    if kind not in KINDS:
        raise ToolError(f"unknown sfx kind {kind!r}", f"one of {sorted(KINDS)}")
    fn, nat = KINDS[kind]
    d = duration or nat
    files, previews, warns = [], [], []
    for v in range(max(1, min(variations, 8))):
        x, pan = fn(d, seed + v)
        x = np.asarray(x, np.float32)
        if pan is not None:
            ang = (pan + 1) * np.pi / 4
            st = np.stack([x * np.cos(ang), x * np.sin(ang)], 1)
        else:
            st = np.stack([x, x], 1)
        st = C.fade(st, SR, 0.002, min(0.05, d / 5))
        if loudness and len(st) > SR * 0.4:
            st, _ = C.normalize_loudness(st, SR, loudness, -1.0)
        else:  # too short for LUFS: peak-normalise to -3 dBFS
            st = st * (C.undb(-3) / (np.abs(st).max() + 1e-9))
        p = C.out_path(project, out, f"sfx-{kind}-{seed + v}", ".wav", kind="audio/sfx")
        C.save(p, st, SR)
        files.append(str(p))
        m, w = C.measure(p)
        warns += w
    previews.append(str(C.preview(files[0], C.sibling(Path(files[0]), ".png"), title=f"sfx {kind}")))
    return Result(f"Generated {len(files)} '{kind}' effect(s), {d:.2f}s each (procedural, royalty-free).",
                  files=files, previews=previews, warnings=warns, data={"kind": kind, "duration": d})
