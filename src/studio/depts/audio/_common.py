"""Shared helpers for the audio department: decode/encode, loudness, previews, captions.

Everything here is internal (no @tool). Audio is handled as float32 numpy arrays shaped
(samples, channels); ffmpeg does all decoding so any input format (and video files) works."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import numpy as np

from ...config import output_dir, unique_path, slug
from ...core import qc
from ...core.deps import need
from ...core.result import ToolError

KIND = "audio"
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif", ".wma", ".webm",
              ".mp4", ".mov", ".mkv", ".avi", ".m4v"}

TARGETS = {  # name → (integrated LUFS, true-peak ceiling dBTP)
    "streaming": (-14.0, -1.0), "youtube": (-14.0, -1.0), "spotify": (-14.0, -1.0), "reels": (-14.0, -1.0),
    "tiktok": (-14.0, -1.0), "apple": (-16.0, -1.0), "podcast": (-16.0, -1.0), "voiceover": (-16.0, -1.0),
    "broadcast": (-23.0, -1.0), "ebu": (-23.0, -1.0), "atsc": (-24.0, -2.0),
}


# ───────────────────────────── paths ─────────────────────────────

def src_path(path: str) -> Path:
    p = Path(str(path)).expanduser()
    if not p.exists():
        raise ToolError(f"no such file: {p}", "pass an absolute path to an audio or video file")
    if p.is_dir():
        raise ToolError(f"{p} is a folder, expected an audio/video file")
    return p.resolve()


def out_path(project: str, out: str, stem: str, ext: str, kind: str = KIND) -> Path:
    """Where to write: `out` (file or folder) or projects/<project>/audio/. Never overwrites."""
    ext = ext if ext.startswith(".") else "." + ext
    stem = slug(stem, 60)
    if out:
        o = Path(out).expanduser()
        if o.suffix and not o.is_dir():
            o.parent.mkdir(parents=True, exist_ok=True)
            return unique_path(o.parent, o.stem, o.suffix)
        o.mkdir(parents=True, exist_ok=True)
        return unique_path(o, stem, ext)
    return unique_path(output_dir(project or None, kind), stem, ext)


def sibling(path: Path, suffix: str) -> Path:
    """Companion file next to an output (same stem, other extension), never overwriting."""
    return unique_path(path.parent, path.stem, suffix)


# ───────────────────────────── decode / encode ─────────────────────────────

def load(path: str | Path, sr: int | None = 48000, mono: bool = False) -> tuple[np.ndarray, int]:
    """Decode anything ffmpeg reads → float32 (n, ch). sr=None keeps the native rate."""
    need("ffmpeg")
    p = str(path)
    info = qc.probe(p)
    if not info.get("audio"):
        raise ToolError(f"{Path(p).name} has no audio stream")
    rate = sr or int(info.get("sample_rate") or 48000)
    src_ch = max(1, min(2, int(info.get("channels") or 1)))
    ch = src_ch  # decode natively (≤2 ch) and average ourselves: ffmpeg's -ac 1 adds +3 dB and can clip
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", p, "-vn", "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", str(ch), "-ar", str(rate), "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=1800)
    if r.returncode != 0:
        raise ToolError(f"could not decode {Path(p).name}: {r.stderr.decode(errors='ignore')[-400:]}")
    a = np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, ch).copy()
    if mono and ch > 1:
        a = a.mean(axis=1, keepdims=True)
    return a, rate


def as2d(a: np.ndarray) -> np.ndarray:
    return a[:, None] if a.ndim == 1 else a


def save(path: str | Path, a: np.ndarray, sr: int, fmt: str = "", bitrate: str = "") -> Path:
    """Write audio. .wav/.flac via soundfile (24-bit); mp3/m4a/ogg/opus via ffmpeg."""
    path = Path(path)
    a = np.clip(as2d(np.asarray(a, dtype=np.float32)), -1.0, 1.0)
    ext = (fmt or path.suffix.lstrip(".")).lower()
    if ext in ("wav", "flac"):
        import soundfile as sf
        sf.write(str(path), a, sr, subtype="PCM_24")
        return path
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "x.wav"
        import soundfile as sf
        sf.write(str(tmp), a, sr, subtype="PCM_24")
        encode(tmp, path, bitrate=bitrate)
    return path


CODECS = {"mp3": ["-c:a", "libmp3lame"], "m4a": ["-c:a", "aac"], "aac": ["-c:a", "aac"], "ogg": ["-c:a", "libvorbis"],
          "opus": ["-c:a", "libopus"], "flac": ["-c:a", "flac"], "wav": ["-c:a", "pcm_s24le"],
          "aiff": ["-c:a", "pcm_s24be"]}
DEFAULT_BITRATE = {"mp3": "192k", "m4a": "192k", "aac": "192k", "ogg": "192k", "opus": "128k"}


def encode(src: Path, dest: Path, bitrate: str = "", sr: int | None = None, channels: int | None = None) -> Path:
    ext = dest.suffix.lstrip(".").lower()
    if ext not in CODECS:
        raise ToolError(f"unsupported audio format .{ext}", f"one of {sorted(CODECS)}")
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src), "-vn", *CODECS[ext]]
    br = bitrate or DEFAULT_BITRATE.get(ext, "")
    if br:
        cmd += ["-b:a", br]
    if sr:
        cmd += ["-ar", str(sr)]
    if channels:
        cmd += ["-ac", str(channels)]
    if ext == "opus" and (sr or 48000) not in (8000, 12000, 16000, 24000, 48000):
        cmd += ["-ar", "48000"]
    r = subprocess.run(cmd + [str(dest)], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise ToolError(f"ffmpeg could not write {dest.name}: {r.stderr[-400:]}")
    return dest


# ───────────────────────────── DSP basics ─────────────────────────────

def db(x: float) -> float:
    return 20 * math.log10(max(float(x), 1e-12))


def undb(d: float) -> float:
    return 10 ** (d / 20)


def resample(a: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return a
    from scipy.signal import resample_poly
    g = math.gcd(sr_from, sr_to)
    return resample_poly(as2d(a), sr_to // g, sr_from // g, axis=0).astype(np.float32)


def fade(a: np.ndarray, sr: int, fade_in: float = 0.0, fade_out: float = 0.0) -> np.ndarray:
    a = as2d(a).copy()
    n = len(a)
    if fade_in > 0:
        k = min(n, int(fade_in * sr))
        a[:k] *= (np.sin(np.linspace(0, np.pi / 2, k)) ** 2)[:, None]
    if fade_out > 0:
        k = min(n, int(fade_out * sr))
        a[n - k:] *= (np.cos(np.linspace(0, np.pi / 2, k)) ** 2)[:, None]
    return a


def rms_env(x: np.ndarray, sr: int, win: float = 0.05) -> np.ndarray:
    """RMS envelope (per sample, mono) using a moving window."""
    m = as2d(x).mean(axis=1)
    k = max(1, int(win * sr))
    c = np.cumsum(np.concatenate([[0.0], m.astype(np.float64) ** 2]))
    e = np.sqrt(np.maximum((c[k:] - c[:-k]) / k, 0))
    pad = len(m) - len(e)
    return np.pad(e, (pad // 2, pad - pad // 2), mode="edge").astype(np.float32)


def true_peak_db(a: np.ndarray, sr: int) -> float:
    from scipy.signal import resample_poly
    up = resample_poly(as2d(a), 4, 1, axis=0) if len(a) < sr * 1200 else as2d(a)
    return db(np.max(np.abs(up)) if up.size else 0.0)


def lufs(a: np.ndarray, sr: int) -> float | None:
    """Integrated loudness (BS.1770 via ffmpeg ebur128) of an in-memory signal."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.wav"
        import soundfile as sf
        sf.write(str(p), as2d(a), sr, subtype="FLOAT")
        m = qc.loudness(p)
    return m.get("lufs")


def limit(a: np.ndarray, sr: int, ceiling_db: float = -1.0, release_ms: float = 80.0) -> np.ndarray:
    """Look-ahead true-peak-ish limiter: gain computed on a 4x oversampled peak envelope."""
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    from scipy.signal import resample_poly
    a = as2d(a).astype(np.float32)
    ceil = undb(ceiling_db - 0.15)
    up = resample_poly(a, 4, 1, axis=0)
    pk = np.abs(up).max(axis=1).reshape(-1, 4).max(axis=1)[: len(a)]
    if len(pk) < len(a):
        pk = np.pad(pk, (0, len(a) - len(pk)), mode="edge")
    if pk.max() <= ceil:
        return a
    # Gain = moving minimum of the required gain over ±R, then a moving average over ±R.
    # Because every value averaged at sample i is ≤ need[i], the result never overshoots the
    # ceiling, and attack/release are smooth (≈ release_ms/2 each side, look-ahead included).
    R = max(1, int(release_ms / 2000 * sr))
    need_g = np.minimum(1.0, ceil / np.maximum(pk, 1e-9)).astype(np.float64)
    g = -maximum_filter1d(-need_g, size=2 * R + 1, mode="nearest")
    g = uniform_filter1d(g, size=2 * R + 1, mode="nearest")
    y = a * g[:, None].astype(np.float32)
    m = np.abs(y).max()
    if m > ceil:  # numerical safety only
        y *= ceil / m
    return y.astype(np.float32)


def normalize_loudness(a: np.ndarray, sr: int, target_lufs: float, tp: float = -1.0) -> tuple[np.ndarray, dict]:
    """Gain to target integrated LUFS, then limit to the true-peak ceiling; one corrective pass."""
    before = lufs(a, sr)
    if before is None or before < -70:
        return a, {"lufs_before": before, "note": "signal too quiet/short to normalise"}
    y = as2d(a) * undb(target_lufs - before)
    y = limit(y, sr, tp)
    after = lufs(y, sr)
    if after is not None and abs(after - target_lufs) > 0.4:
        y = limit(y * undb(target_lufs - after), sr, tp)
        after = lufs(y, sr)
    return y, {"lufs_before": before, "lufs_after": after, "gain_db": round(target_lufs - before, 2)}


def target_of(target: str | float) -> tuple[float, float]:
    if isinstance(target, (int, float)):
        return float(target), -1.0
    t = str(target).strip().lower()
    if t in TARGETS:
        return TARGETS[t]
    try:
        return float(t), -1.0
    except ValueError:
        raise ToolError(f"unknown loudness target {target!r}", f"a LUFS number or one of {sorted(TARGETS)}")


# ───────────────────────────── measurement + preview ─────────────────────────────

def measure(path: str | Path, target_lufs: float | None = None, expect_duration: float | None = None) -> tuple[dict, list[str]]:
    """qc.loudness + honest listen-proxy warnings (silent, clipped, wrong duration)."""
    m = qc.loudness(path)
    w = qc.audio_verdict(m, target_lufs)
    try:
        a, sr = load(path, sr=None)
        clipped = int(np.sum(np.abs(a) >= 0.999))
        m["clipped_samples"] = clipped
        if clipped > 10:
            w.append(f"{clipped} clipped samples (digital overs)")
        if a.shape[1] == 2:
            corr = float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]) if a.std() > 0 else 1.0
            m["stereo_correlation"] = round(corr, 3)
            if corr < -0.3:
                w.append("left/right are out of phase (mono playback will cancel)")
    except Exception:
        pass
    if expect_duration and m.get("duration") and abs(m["duration"] - expect_duration) > max(1.0, 0.1 * expect_duration):
        w.append(f"duration {m['duration']}s vs expected ~{expect_duration:.1f}s")
    return m, w


_CMAP = None


def _cmap(v: np.ndarray) -> np.ndarray:
    """Perceptual-ish dark→violet→orange→yellow map, v in [0,1] → uint8 RGB."""
    stops = np.array([[8, 8, 20], [40, 20, 90], [120, 30, 120], [210, 70, 60], [250, 170, 40], [252, 250, 190]], float)
    x = np.clip(v, 0, 1) * (len(stops) - 1)
    i = np.minimum(x.astype(int), len(stops) - 2)
    f = (x - i)[..., None]
    return (stops[i] * (1 - f) + stops[i + 1] * f).astype(np.uint8)


def preview(path: str | Path, dest: str | Path | None = None, title: str = "", metrics: dict | None = None,
            marks: list[tuple[float, float, str]] | None = None) -> Path:
    """Waveform + log-frequency spectrogram PNG the agent LOOKS at (listen proxy)."""
    from PIL import Image, ImageDraw, ImageFont
    a, sr = load(path, sr=22050, mono=True)
    x = a[:, 0]
    W, H_w, H_s, pad, top = 1400, 170, 300, 12, 40
    img = Image.new("RGB", (W, top + H_w + H_s + 3 * pad + 26), (18, 18, 24))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 15)
    except Exception:
        font = ImageFont.load_default()
    dur = len(x) / sr if len(x) else 0
    head = title or Path(path).name
    if metrics:
        bits = []
        for k, lab in (("lufs", "LUFS"), ("true_peak", "dBTP"), ("duration", "s"), ("silence_pct", "% silence")):
            if metrics.get(k) is not None:
                bits.append(f"{metrics[k]} {lab}")
        head += "   ·   " + "  ".join(bits)
    d.text((pad, 10), head[:170], fill=(230, 230, 235), font=font)
    # waveform (min/max per column)
    y0 = top
    d.rectangle([pad, y0, W - pad, y0 + H_w], fill=(28, 28, 38))
    cols = W - 2 * pad
    if len(x):
        idx = np.linspace(0, len(x), cols + 1).astype(int)
        mid = y0 + H_w / 2
        for c in range(cols):
            seg = x[idx[c]:max(idx[c] + 1, idx[c + 1])]
            lo, hi = float(seg.min()), float(seg.max())
            clip = max(abs(lo), abs(hi)) >= 0.99
            d.line([(pad + c, mid - hi * H_w / 2), (pad + c, mid - lo * H_w / 2)],
                   fill=(255, 80, 80) if clip else (110, 190, 255))
        for lvl in (-6, -12):
            for s in (1, -1):
                yy = mid - s * undb(lvl) * H_w / 2
                d.line([(pad, yy), (W - pad, yy)], fill=(55, 55, 70))
    # spectrogram
    y1 = y0 + H_w + pad
    if len(x) > 2048:
        from scipy.signal import stft
        f, t, Z = stft(x, sr, nperseg=2048, noverlap=2048 - max(64, len(x) // cols))
        S = 20 * np.log10(np.abs(Z) + 1e-9)
        # log-frequency rows 40 Hz … 11 kHz
        fr = np.geomspace(40, sr / 2 - 1, H_s)
        rows = np.clip(np.searchsorted(f, fr), 0, len(f) - 1)
        S = S[rows][::-1]
        cidx = np.linspace(0, S.shape[1] - 1, cols).astype(int)
        S = S[:, cidx]
        vmax = np.percentile(S, 99.5)
        v = (S - (vmax - 80)) / 80
        img.paste(Image.fromarray(_cmap(v)), (pad, y1))
        for hz in (100, 1000, 5000, 10000):
            yy = y1 + H_s - 1 - int(np.searchsorted(fr, hz) * 1.0)
            d.text((pad + 4, yy - 16), f"{hz // 1000}k" if hz >= 1000 else str(hz), fill=(200, 200, 200), font=font)
    # time axis + marks
    y2 = y1 + H_s + 4
    if dur > 0:
        step = next(s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if dur / s <= 14)
        tt = 0.0
        while tt <= dur:
            xx = pad + int(tt / dur * cols)
            d.line([(xx, y2), (xx, y2 + 6)], fill=(160, 160, 160))
            lab = f"{int(tt // 60)}:{tt % 60:04.1f}" if dur >= 60 else f"{tt:g}s"
            d.text((xx + 2, y2 + 4), lab, fill=(160, 160, 160), font=font)
            tt += step
        for (s, e, lab) in marks or []:
            xs, xe = pad + int(s / dur * cols), pad + int(e / dur * cols)
            d.rectangle([xs, y0, max(xs + 1, xe), y0 + 6], fill=(255, 200, 0))
    dest = Path(dest) if dest else sibling(Path(path), ".png")
    img.save(dest)
    return dest


def compare_preview(before: Path, after: Path, dest: Path, title: str = "") -> Path:
    from PIL import Image, ImageDraw
    with tempfile.TemporaryDirectory() as td:
        a = preview(before, Path(td) / "a.png", title=f"BEFORE  {before.name}", metrics=qc.loudness(before))
        b = preview(after, Path(td) / "b.png", title=f"AFTER  {after.name}", metrics=qc.loudness(after))
        ia, ib = Image.open(a), Image.open(b)
        out = Image.new("RGB", (ia.width, ia.height + ib.height + 6), (0, 0, 0))
        out.paste(ia, (0, 0))
        out.paste(ib, (0, ia.height + 6))
        out.save(dest)
    return dest


# ───────────────────────────── text + captions ─────────────────────────────

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")


def is_arabic(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(bool(ARABIC_RE.match(c)) for c in letters) / len(letters) > 0.3


def ts(t: float, sep: str = ",") -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


_END = re.compile(r"[.!?؟…:;؛]$")
_SOFT = re.compile(r"[,،\-–—]$")


def _wrap(words: list[str], max_chars: int, max_lines: int) -> str:
    text = " ".join(words)
    if len(text) <= max_chars or max_lines < 2:
        return text
    # balanced two-line split, preferring after punctuation
    best, best_cost = text, 1e9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        cost = abs(len(a) - len(b)) + (0 if _SOFT.search(words[i - 1]) or _END.search(words[i - 1]) else 6)
        if max(len(a), len(b)) > max_chars:
            cost += 100 * (max(len(a), len(b)) - max_chars)
        if cost < best_cost:
            best, best_cost = a + "\n" + b, cost
    return best


def words_to_cues(words: list[dict], max_chars: int = 42, max_lines: int = 2, max_dur: float = 6.0,
                  min_dur: float = 0.8, gap_split: float = 0.6) -> list[dict]:
    """Group timed words ({word,start,end}) into subtitle cues that respect reading limits:
    ≤ max_chars per line, ≤ max_lines, ≤ max_dur seconds; break on sentence ends and pauses."""
    cues, cur = [], []
    cap = max_chars * max_lines

    def flush():
        if cur:
            cues.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                         "text": _wrap([w["word"].strip() for w in cur], max_chars, max_lines)})
            cur.clear()

    for w in words:
        tok = w["word"].strip()
        if not tok:
            continue
        if cur:
            length = len(" ".join(x["word"].strip() for x in cur)) + 1 + len(tok)
            gap = w["start"] - cur[-1]["end"]
            if length > cap or w["end"] - cur[0]["start"] > max_dur or gap > gap_split:
                flush()
        cur.append({**w, "word": tok})
        if _END.search(tok) and (cur[-1]["end"] - cur[0]["start"]) > 1.2:
            flush()
    flush()
    # timing hygiene: minimum duration, no overlaps, small gap
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else None
        if c["end"] - c["start"] < min_dur:
            c["end"] = c["start"] + min_dur
        if nxt is not None and c["end"] > nxt - 0.04:
            c["end"] = max(c["start"] + 0.2, nxt - 0.04)
        c["start"], c["end"] = round(c["start"], 3), round(c["end"], 3)
    return cues


def write_srt(cues: list[dict], path: Path) -> Path:
    lines = []
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{ts(c['start'])} --> {ts(c['end'])}", c["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_vtt(cues: list[dict], path: Path) -> Path:
    lines = ["WEBVTT", ""]
    for c in cues:
        lines += [f"{ts(c['start'], '.')} --> {ts(c['end'], '.')}", c["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_json(obj, path: Path) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1,
                               default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    return path


# ───────────────────────────── WER ─────────────────────────────

_AR_DIAC = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")


def normalize_text(text: str, arabic_fold: bool = True) -> list[str]:
    """Lower-case, strip punctuation/diacritics/tatweel; fold Arabic letter variants (أإآ→ا, ة→ه, ى→ي)."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = _AR_DIAC.sub("", t)
    if arabic_fold:
        t = re.sub("[أإآٱ]", "ا", t)
        t = t.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    t = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in t)
    return t.split()


def wer(ref: str, hyp: str) -> dict:
    r, h = normalize_text(ref), normalize_text(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=int)
    d[:, 0] = np.arange(len(r) + 1)
    d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return {"wer": round(d[len(r), len(h)] / max(1, len(r)), 4), "errors": int(d[len(r), len(h)]),
            "ref_words": len(r), "hyp_words": len(h)}


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?؟…])\s+|\n{2,}|(?<=[.!?؟])(?=[؀-ۿ])", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def run_ff(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise ToolError(f"ffmpeg failed: {r.stderr[-600:]}")
    return r


def env_key(*names: str) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    try:
        from ...config import studio_home
        f = studio_home() / "keys.json"
        if f.exists():
            j = json.loads(f.read_text())
            for n in names:
                if j.get(n):
                    return str(j[n])
    except Exception:
        pass
    return ""
