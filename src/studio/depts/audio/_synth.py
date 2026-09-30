"""Small numpy synthesiser used by the procedural music and SFX generators.

All voices return mono float32 arrays at SR. Pitch is given as a (possibly fractional) MIDI note so
maqam quarter-tones (e.g. 63.5 = E half-flat above middle C's octave) render exactly."""
from __future__ import annotations

import numpy as np


def _sig():
    try:
        from scipy.signal import butter, sosfilt
    except ImportError as e:  # the audio extra is not installed
        from ...core.result import MissingTool
        raise MissingTool("scipy is not installed (music & SFX filters).", "pip install 'studio-mcp[audio]' or: pip install scipy") from e
    return butter, sosfilt

SR = 44100


def hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _t(dur: float) -> np.ndarray:
    return np.arange(int(max(dur, 0.001) * SR)) / SR


def env_adsr(n: int, a: float, d: float, s: float, r: float, hold: float) -> np.ndarray:
    """ADSR with `hold` seconds of sustain before release; length n samples."""
    e = np.zeros(n, np.float32)
    ia, idd, ih = int(a * SR), int(d * SR), int(hold * SR)
    ir = int(r * SR)
    seg = [np.linspace(0, 1, max(ia, 1), endpoint=False), np.linspace(1, s, max(idd, 1), endpoint=False)]
    body = np.concatenate(seg)
    if ih > len(body):
        body = np.concatenate([body, np.full(ih - len(body), s)])
    body = body[:max(ih, 1)]
    rel = np.linspace(body[-1] if len(body) else s, 0, max(ir, 1))
    full = np.concatenate([body, rel])[:n]
    e[: len(full)] = full
    return e


def lp(x: np.ndarray, hz_: float, order: int = 2) -> np.ndarray:
    hz_ = min(hz_, SR / 2 * 0.95)
    butter, sosfilt = _sig()
    return sosfilt(butter(order, hz_, "lowpass", fs=SR, output="sos"), x).astype(np.float32)


def hp(x: np.ndarray, hz_: float, order: int = 2) -> np.ndarray:
    butter, sosfilt = _sig()
    return sosfilt(butter(order, hz_, "highpass", fs=SR, output="sos"), x).astype(np.float32)


def bp(x: np.ndarray, lo: float, hi: float, order: int = 2) -> np.ndarray:
    butter, sosfilt = _sig()
    return sosfilt(butter(order, [lo, min(hi, SR / 2 * 0.95)], "bandpass", fs=SR, output="sos"), x).astype(np.float32)


def saw(f: float, t: np.ndarray, maxh: int = 40) -> np.ndarray:
    """Band-limited sawtooth by additive synthesis (no aliasing)."""
    out = np.zeros_like(t)
    k = 1
    while k <= maxh and k * f < SR / 2 * 0.9:
        out += np.sin(2 * np.pi * k * f * t + k) / k
        k += 1
    return out * 0.6


# ───────────────────────────── pitched voices ─────────────────────────────

def epiano(note: float, dur: float, vel: float = 0.8, bright: float = 1.0) -> np.ndarray:
    """FM electric piano (Rhodes-like): 1:1 modulator with decaying index + a short 'tine' partial."""
    f = hz(note)
    tail = 1.2
    t = _t(dur + tail)
    idx = (1.6 * bright * vel) * np.exp(-t * 3.5) + 0.25
    mod = np.sin(2 * np.pi * f * t) * idx
    body = np.sin(2 * np.pi * f * t + mod)
    tine = 0.18 * vel * np.sin(2 * np.pi * f * 14.0 * t) * np.exp(-t * 28)
    decay = np.exp(-t * (1.1 + note / 90))
    y = (body + tine) * decay
    rel = np.ones_like(t)
    k = int(dur * SR)
    rel[k:] = np.exp(-(t[k:] - dur) * 9)
    return (y * rel * vel * 0.5).astype(np.float32)


def bell(note: float, dur: float, vel: float = 0.7) -> np.ndarray:
    f = hz(note)
    t = _t(dur + 1.5)
    mod = np.sin(2 * np.pi * f * 3.5 * t) * 2.2 * np.exp(-t * 5)
    y = np.sin(2 * np.pi * f * t + mod) * np.exp(-t * 2.2)
    return (y * vel * 0.35).astype(np.float32)


def pluck_ks(note: float, dur: float, vel: float = 0.8, bright: float = 0.5, damp: float = 0.996,
             body_hz: float = 0.0) -> np.ndarray:
    """Karplus-Strong plucked string, vectorised per period. bright 0..1 (pluck position / excitation
    filter); body_hz adds a resonant body (oud ≈ 180 Hz, guitar ≈ 110 Hz)."""
    f = hz(note)
    n = int((dur + 0.6) * SR)
    N = max(2, int(SR / f - 0.5))  # the 2-tap average adds half a sample of delay
    rng = np.random.default_rng(int(note * 1000) % 2**31)
    exc = rng.uniform(-1, 1, N).astype(np.float64)
    exc = lp(exc.astype(np.float32), 800 + 7000 * bright).astype(np.float64)
    exc -= exc.mean()
    y = np.zeros(n + N + 1)
    y[:N] = exc
    i = N
    while i < n:
        blk = min(N, n - i)
        a = y[i - N:i - N + blk]
        b = y[i - N - 1:i - N - 1 + blk] if i - N - 1 >= 0 else np.concatenate([[0.0], y[i - N:i - N + blk - 1]])
        y[i:i + blk] = damp * 0.5 * (a + b)
        i += blk
    out = y[:n]
    # integer delay → slightly sharp; resample to the exact pitch (matters for high qanun notes/maqam)
    ratio = f / (SR / (N + 0.5))
    if abs(ratio - 1) > 1e-4:
        pos = np.arange(n) * ratio
        out = np.interp(pos, np.arange(len(y)), y, right=0.0)
    out = out.astype(np.float32)
    if body_hz:
        out = out + 0.5 * bp(out, body_hz * 0.7, body_hz * 1.6)
    k = int(dur * SR)
    if k < n:
        out[k:] *= np.exp(-np.arange(n - k) / SR * 12)
    return (out * vel * 0.9).astype(np.float32)


def bass(note: float, dur: float, vel: float = 0.8, drive: float = 1.5) -> np.ndarray:
    f = hz(note)
    t = _t(dur + 0.08)
    y = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 2 * f * t)
    y = np.tanh(drive * y) / np.tanh(drive)
    e = env_adsr(len(t), 0.006, 0.25, 0.7, 0.07, dur)
    return (y * e * vel * 0.55).astype(np.float32)


def pad(notes: list[float], dur: float, vel: float = 0.6, cutoff: float = 1800, attack: float = 0.8,
        release: float = 1.2, detune_cents: float = 8, vibrato: float = 0.0) -> np.ndarray:
    """Warm detuned-saw pad (strings when vibrato > 0)."""
    t = _t(dur + release)
    y = np.zeros_like(t)
    for nt in notes:
        f = hz(nt)
        for dc in (-detune_cents, 0, detune_cents):
            ff = f * 2 ** (dc / 1200)
            if vibrato:
                inst = ff * (1 + 0.004 * vibrato * np.sin(2 * np.pi * (5.2 + dc / 40) * t))
                ph = 2 * np.pi * np.cumsum(inst) / SR
                y += sum(np.sin(k * ph) / k for k in range(1, 12) if k * ff < SR / 2.2) * 0.6
            else:
                y += saw(ff, t, 24)
    y = lp(y.astype(np.float32), cutoff, 2)
    e = env_adsr(len(t), attack, 0.5, 0.85, release, dur)
    return (y * e * vel * 0.12 / max(1, len(notes)) ** 0.5).astype(np.float32)


def ney(note: float, dur: float, vel: float = 0.6) -> np.ndarray:
    """Breathy flute (ney-like): sine + 2nd/3rd harmonics, slow vibrato, band-passed breath noise."""
    f = hz(note)
    t = _t(dur + 0.3)
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.0 * t) * np.clip(t / 0.4, 0, 1)
    ph = 2 * np.pi * f * np.cumsum(vib) / SR
    y = np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)
    rng = np.random.default_rng(int(note * 7))
    br = bp(rng.standard_normal(len(t)).astype(np.float32), f * 0.8, f * 3) * 0.35
    e = env_adsr(len(t), 0.12, 0.2, 0.85, 0.25, dur)
    return ((y + br) * e * vel * 0.3).astype(np.float32)


# ───────────────────────────── drums ─────────────────────────────

_rng = np.random.default_rng(7)


def noise(n: int, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(n).astype(np.float32)


def kick(vel: float = 1.0, tone: float = 1.0) -> np.ndarray:
    t = _t(0.5)
    f = 45 * tone + 110 * np.exp(-t * 28)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
    click = hp(noise(len(t), 1), 2000) * np.exp(-t * 300) * 0.3
    return (np.tanh(1.6 * (y + click)) * vel * 0.9).astype(np.float32)


def snare(vel: float = 0.9) -> np.ndarray:
    t = _t(0.35)
    tone = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 25) * 0.5
    nz = bp(noise(len(t), 2), 1500, 9000) * np.exp(-t * 16)
    return ((tone + nz) * vel * 0.6).astype(np.float32)


def hat(vel: float = 0.5, open_: bool = False) -> np.ndarray:
    t = _t(0.35 if open_ else 0.08)
    y = hp(noise(len(t), 3), 5500, 4) * np.exp(-t * (9 if open_ else 60))
    return (y * vel * 0.35).astype(np.float32)


def clap(vel: float = 0.8) -> np.ndarray:
    t = _t(0.3)
    e = np.zeros_like(t)
    for off in (0, 0.011, 0.022):
        k = int(off * SR)
        e[k:] += np.exp(-(t[: len(t) - k]) * 60)
    e += 0.6 * np.exp(-t * 14)
    y = bp(noise(len(t), 4), 900, 5000) * e
    return (y * vel * 0.5).astype(np.float32)


def rim(vel: float = 0.6) -> np.ndarray:
    t = _t(0.08)
    y = (np.sin(2 * np.pi * 1700 * t) + 0.6 * np.sin(2 * np.pi * 820 * t)) * np.exp(-t * 80)
    return (y * vel * 0.35).astype(np.float32)


def darbuka(stroke: str = "dum", vel: float = 0.8) -> np.ndarray:
    """Goblet-drum strokes: dum (deep centre), tek (sharp rim), ka (softer rim, other hand)."""
    if stroke == "dum":
        t = _t(0.6)
        f = 95 + 60 * np.exp(-t * 30)
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6.5)
        y += 0.25 * np.sin(2 * np.pi * 2.3 * np.cumsum(f) / SR) * np.exp(-t * 14)
        y += 0.15 * bp(noise(len(t), 5), 200, 1500) * np.exp(-t * 40)
        return (np.tanh(1.4 * y) * vel * 0.8).astype(np.float32)
    t = _t(0.18)
    ring = np.sin(2 * np.pi * 690 * t) * 0.5 + np.sin(2 * np.pi * 1210 * t) * 0.3 + np.sin(2 * np.pi * 2750 * t) * 0.2
    snap = bp(noise(len(t), 6 if stroke == "tek" else 8), 2500, 11000) * np.exp(-t * 70)
    y = ring * np.exp(-t * (32 if stroke == "tek" else 45)) + snap * 0.9
    return (y * vel * (0.55 if stroke == "tek" else 0.35)).astype(np.float32)


def riq(vel: float = 0.4) -> np.ndarray:
    t = _t(0.25)
    jing = sum(np.sin(2 * np.pi * f * t) for f in (5100, 6700, 8300, 9900)) / 4
    y = (jing * 0.6 + hp(noise(len(t), 9), 6000) * 0.6) * np.exp(-t * 18)
    return (y * vel * 0.3).astype(np.float32)


def boom(vel: float = 1.0) -> np.ndarray:
    """Cinematic low hit (taiko/trailer boom)."""
    t = _t(2.5)
    f = 38 + 60 * np.exp(-t * 12)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 1.8)
    y += lp(noise(len(t), 11), 900) * np.exp(-t * 9) * 0.5
    return (np.tanh(1.5 * y) * vel * 0.9).astype(np.float32)


def crackle(n: int, density: float = 18, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = np.zeros(n, np.float32)
    k = int(n / SR * density)
    pos = rng.integers(0, n - 50, k)
    amp = rng.uniform(0.02, 0.18, k) * rng.choice([-1, 1], k)
    for p, a in zip(pos, amp):
        y[p:p + 30] += a * np.exp(-np.arange(30) / 5)
    y += hp(noise(n, seed + 1), 3000) * 0.004
    return lp(y, 7000)


def add(*xs: np.ndarray) -> np.ndarray:
    """Sum mono arrays of different lengths."""
    n = max(len(x) for x in xs)
    out = np.zeros(n, np.float32)
    for x in xs:
        out[: len(x)] += x
    return out


def place(buf: np.ndarray, x: np.ndarray, at: int, gain: float = 1.0, pan: float = 0.0) -> None:
    """Mix mono x into stereo buf at sample `at` with equal-power pan (-1..1)."""
    if at >= len(buf) or at < 0:
        return
    n = min(len(x), len(buf) - at)
    ang = (pan + 1) * np.pi / 4
    buf[at:at + n, 0] += x[:n] * gain * np.cos(ang)
    buf[at:at + n, 1] += x[:n] * gain * np.sin(ang)
