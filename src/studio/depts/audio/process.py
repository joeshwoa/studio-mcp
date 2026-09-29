"""Studio voice clean-up, mastering to loudness targets, voice+music mixing with auto-ducking, and
everyday edits (trim silences, convert, join with crossfades, analyse)."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ...core.deps import need
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C

# ───────────────────────────── building blocks ─────────────────────────────


def _butter(x: np.ndarray, sr: int, kind: str, hz: float | tuple, order: int = 4) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt
    sos = butter(order, hz, btype=kind, fs=sr, output="sos")
    return sosfiltfilt(sos, x, axis=0).astype(np.float32)


def _frames_env(x: np.ndarray, sr: int, hop: float = 0.01) -> tuple[np.ndarray, int]:
    m = C.as2d(x).mean(axis=1)
    h = max(1, int(hop * sr))
    n = len(m) // h
    if n == 0:
        return np.zeros(1, np.float32), h
    e = np.sqrt((m[: n * h].reshape(n, h) ** 2).mean(axis=1) + 1e-12)
    return e.astype(np.float32), h


def _smooth(g: np.ndarray, hop: float, attack: float, release: float) -> np.ndarray:
    """Asymmetric one-pole smoothing at frame rate (attack when gain falls, release when it rises)."""
    a_att = math.exp(-hop / max(attack, 1e-4))
    a_rel = math.exp(-hop / max(release, 1e-4))
    out = np.empty_like(g)
    cur = g[0] if len(g) else 1.0
    for i, t in enumerate(g):
        a = a_att if t < cur else a_rel
        cur = t + (cur - t) * a
        out[i] = cur
    return out


def _to_samples(g: np.ndarray, h: int, n: int) -> np.ndarray:
    xs = np.arange(len(g)) * h + h / 2
    return np.interp(np.arange(n), xs, g).astype(np.float32)


def _deess(x: np.ndarray, sr: int, amount_db: float = 6.0, split_hz: float = 5000) -> np.ndarray:
    """Split-band de-esser: attenuate the >split_hz band only while it dominates (sibilants)."""
    x = C.as2d(x)
    hi = _butter(x, sr, "highpass", split_hz, 4)
    lo = x - hi
    eh, h = _frames_env(hi, sr, 0.005)
    el, _ = _frames_env(lo, sr, 0.005)
    ratio_db = 20 * np.log10(eh / (el + 1e-9) + 1e-9)
    over = np.clip(ratio_db + 6, 0, None)          # sibilance: hi band within 6 dB of (or above) the body
    red = np.minimum(amount_db, over * 1.5)
    red[20 * np.log10(eh + 1e-9) < -50] = 0
    g = _smooth(C.undb(-red).astype(np.float32), 0.005, 0.002, 0.06)
    return (lo + hi * _to_samples(g, h, len(x))[:, None]).astype(np.float32)


def _expander(x: np.ndarray, sr: int, floor_db: float, range_db: float = 10, ratio: float = 2.0) -> np.ndarray:
    """Downward expander: pushes room tone/reverb tails down between words (does not remove reverb ON words)."""
    e, h = _frames_env(x, sr, 0.01)
    lev = 20 * np.log10(e + 1e-9)
    below = np.clip(floor_db - lev, 0, None)
    red = np.minimum(range_db, below * (ratio - 1))
    g = _smooth(C.undb(-red).astype(np.float32), 0.01, 0.01, 0.12)
    return (C.as2d(x) * _to_samples(g, h, len(x))[:, None]).astype(np.float32)


def _noise_floor_db(x: np.ndarray, sr: int) -> float:
    e, _ = _frames_env(x, sr, 0.02)
    return float(20 * np.log10(np.percentile(e, 10) + 1e-9))


def _denoise(x: np.ndarray, sr: int, strength: float, stationary: bool) -> np.ndarray:
    need("noisereduce")
    import noisereduce as nr
    x = C.as2d(x)
    out = np.empty_like(x)
    for c in range(x.shape[1]):
        kw = dict(y=x[:, c], sr=sr, prop_decrease=float(strength), stationary=stationary, n_fft=2048)
        if not stationary:
            kw["time_constant_s"] = 2.0
        out[:, c] = nr.reduce_noise(**kw)
    return out.astype(np.float32)


PRESETS = {
    # hp: high-pass Hz · nr: denoise strength (0..1) · nr_stat · mud: dB @280 Hz · pres: dB @3.5 kHz · air: dB shelf 10 kHz
    # deess: max dB · comp: (threshold dB, ratio) · exp: expander range dB · lp: low-pass Hz · target: LUFS
    "podcast": dict(hp=80, nr=0.75, nr_stat=False, mud=-2.5, pres=2.0, air=1.5, deess=5, comp=(-22, 2.5), exp=8, lp=0, target=-16),
    "reel": dict(hp=90, nr=0.8, nr_stat=False, mud=-3.0, pres=3.0, air=2.5, deess=6, comp=(-24, 3.5), exp=10, lp=0, target=-14),
    "phone": dict(hp=150, nr=0.9, nr_stat=False, mud=-4.0, pres=4.0, air=0.0, deess=4, comp=(-26, 3.5), exp=12, lp=7500, target=-16),
    "light": dict(hp=70, nr=0.5, nr_stat=True, mud=0.0, pres=1.0, air=1.0, deess=3, comp=(-20, 2.0), exp=0, lp=0, target=-16),
    "music": dict(hp=25, nr=0.0, nr_stat=True, mud=0.0, pres=0.0, air=0.0, deess=0, comp=None, exp=0, lp=0, target=-14),
}


def _chain(x: np.ndarray, sr: int, p: dict, declick: bool) -> tuple[np.ndarray, list[str]]:
    need("pedalboard")
    import pedalboard as pb
    steps = []
    x = C.as2d(x)
    if declick:
        x = _declick(x, sr)
        steps.append("de-click")
    x = _butter(x, sr, "highpass", p["hp"], 2)
    steps.append(f"high-pass {p['hp']} Hz")
    if p["nr"] > 0:
        x = _denoise(x, sr, p["nr"], p["nr_stat"])
        steps.append(f"noise reduction {int(p['nr'] * 100)}% ({'stationary' if p['nr_stat'] else 'adaptive'})")
    fx = []
    if p["mud"]:
        fx.append(pb.PeakFilter(cutoff_frequency_hz=280, gain_db=p["mud"], q=1.0))
    if p["pres"]:
        fx.append(pb.PeakFilter(cutoff_frequency_hz=3500, gain_db=p["pres"], q=0.9))
    if p["air"]:
        fx.append(pb.HighShelfFilter(cutoff_frequency_hz=10000, gain_db=p["air"], q=0.7))
    if p["lp"]:
        fx.append(pb.LowpassFilter(cutoff_frequency_hz=p["lp"]))
    if fx:
        x = pb.Pedalboard(fx)(x.T, sr).T.astype(np.float32)
        steps.append(f"EQ (mud {p['mud']} dB@280, presence +{p['pres']} dB@3.5k, air +{p['air']} dB)")
    if p["deess"]:
        x = _deess(x, sr, p["deess"])
        steps.append(f"de-ess ≤{p['deess']} dB")
    if p["exp"]:
        floor = _noise_floor_db(x, sr)
        x = _expander(x, sr, floor + 12, p["exp"])
        steps.append(f"expander ≤{p['exp']} dB below {floor + 12:.0f} dBFS (tames room tone/tails)")
    if p["comp"]:
        th, ra = p["comp"]
        # set threshold relative to the speech level so the preset behaves the same on quiet and loud takes
        e, _ = _frames_env(x, sr, 0.05)
        speech = 20 * np.log10(np.percentile(e, 90) + 1e-9)
        thr = speech + (th + 12)
        x = pb.Pedalboard([pb.Compressor(threshold_db=thr, ratio=ra, attack_ms=6, release_ms=90)])(x.T, sr).T.astype(np.float32)
        steps.append(f"compressor {ra}:1 @ {thr:.0f} dBFS")
    return x, steps


def _declick(x: np.ndarray, sr: int) -> np.ndarray:
    """Replace isolated impulsive spikes with interpolation (mouth clicks, crackle)."""
    from scipy.signal import medfilt
    x = C.as2d(x).copy()
    for c in range(x.shape[1]):
        s = x[:, c]
        med = medfilt(s, 7)
        resid = s - med
        mad = np.median(np.abs(resid)) + 1e-9
        bad = np.abs(resid) > 12 * mad * 1.4826
        bad = np.convolve(bad.astype(float), np.ones(5), "same") > 0
        if bad.any() and bad.mean() < 0.02:
            idx = np.arange(len(s))
            s[bad] = np.interp(idx[bad], idx[~bad], s[~bad])
        x[:, c] = s
    return x


def _finish(res: Result, dest: Path, target: float | None, before: Path | None = None, title: str = "") -> Result:
    m, w = C.measure(dest, target)
    res.warnings += w
    res.data["metrics"] = m
    if before is not None:
        res.previews.append(str(C.compare_preview(before, dest, C.sibling(dest, ".png"))))
    else:
        res.previews.append(str(C.preview(dest, C.sibling(dest, ".png"), metrics=m, title=title)))
    return res


# ───────────────────────────── tools ─────────────────────────────


@tool("audio")
def audio_clean(path: str, preset: str = "podcast", denoise: float = -1.0, declick: bool = False,
                target_lufs: float = 0.0, format: str = "wav", project: str = "", out: str = "") -> Result:
    """'Studio voice' clean-up of a spoken recording (audio or the audio of a video): high-pass,
    noise reduction, EQ (mud cut, presence, air), de-ess, expander (pushes down room tone and
    reverb tails between words), compression and loudness normalisation. Returns the cleaned file,
    a before/after waveform+spectrogram PNG, and measured loudness.

    preset: podcast (natural, -16 LUFS) | reel (punchy, -14 LUFS) | phone (rescue a phone/laptop
    recording: stronger denoise, band-limited, -16) | light (minimal) | music (just HP + loudness).
    denoise: override strength 0..1 (-1 = preset). declick: remove mouth clicks/crackle.
    target_lufs: override the loudness target (0 = preset). Honest limits: real de-reverberation of
    speech needs ML (not free/offline here) — this reduces room tone between words, not echo on words;
    heavy denoise (≥0.9) can sound watery."""
    src = C.src_path(path)
    if preset not in PRESETS:
        raise ToolError(f"unknown preset {preset!r}", f"one of {sorted(PRESETS)}")
    p = dict(PRESETS[preset])
    if 0 <= denoise <= 1:
        p["nr"] = denoise
    tgt = target_lufs or p["target"]
    x, sr = C.load(src, sr=48000)
    if len(x) < sr * 0.3:
        raise ToolError("audio shorter than 0.3 s")
    nf0 = _noise_floor_db(x, sr)
    y, steps = _chain(x, sr, p, declick)
    y, norm = C.normalize_loudness(y, sr, tgt, -1.0)
    y = C.fade(y, sr, 0.005, 0.02)
    nf1 = _noise_floor_db(y, sr) - (norm.get("gain_db") or 0)
    dest = C.out_path(project, out, f"{src.stem}-clean-{preset}", "." + format.lstrip("."))
    C.save(dest, y, sr)
    res = Result(f"Cleaned {src.name} with preset '{preset}': " + " → ".join(steps) +
                 f" → loudness {tgt} LUFS. Noise floor (at the original gain) {nf0:.0f} → {nf1:.0f} dBFS.",
                 files=[str(dest)], data={"steps": steps, "normalize": norm, "noise_floor_before": round(nf0, 1),
                                          "noise_floor_after_same_gain": round(nf1, 1), "preset": p})
    _finish(res, dest, tgt, before=src)
    if nf0 > -40:
        res.warnings.append(f"very noisy source (floor {nf0:.0f} dBFS): expect some artefacts; re-recording beats repair")
    res.next_steps.append("Listen to a few seconds on headphones; if it sounds watery, lower denoise (e.g. 0.5).")
    return res


@tool("audio")
def audio_master(path: str, target: str = "streaming", true_peak: float = -1.0, glue: bool = True,
                 format: str = "wav", project: str = "", out: str = "") -> Result:
    """Master/normalise a finished mix or voice to a loudness target with a true-peak ceiling, and
    report the MEASURED result. target: streaming/youtube/reels (-14 LUFS) | podcast/apple/voiceover
    (-16) | broadcast/ebu (-23) | atsc (-24) | or a number like '-18'. true_peak: ceiling in dBTP
    (-1 default; -2 for lossy delivery safety). glue: gentle 1.5:1 bus compression before limiting.
    Returns the mastered file + preview; data.metrics has lufs/true_peak as measured by ffmpeg ebur128."""
    src = C.src_path(path)
    tgt, tp_default = C.target_of(target)
    tp = true_peak if true_peak else tp_default
    x, sr = C.load(src, sr=48000)
    steps = []
    if glue:
        need("pedalboard")
        import pedalboard as pb
        e, _ = _frames_env(x, sr, 0.05)
        lvl = 20 * np.log10(np.percentile(e, 95) + 1e-9)
        x = pb.Pedalboard([pb.Compressor(threshold_db=lvl - 6, ratio=1.5, attack_ms=25, release_ms=200)])(C.as2d(x).T, sr).T
        steps.append("glue compression 1.5:1")
    y, norm = C.normalize_loudness(x, sr, tgt, tp)
    steps.append(f"gain {norm.get('gain_db')} dB + look-ahead true-peak limiter @ {tp} dBTP")
    dest = C.out_path(project, out, f"{src.stem}-master{int(tgt)}", "." + format.lstrip("."))
    C.save(dest, y, sr)
    res = Result(f"Mastered {src.name} to {tgt} LUFS / {tp} dBTP ({'; '.join(steps)}).", files=[str(dest)],
                 data={"target_lufs": tgt, "true_peak_ceiling": tp, "normalize": norm})
    _finish(res, dest, tgt, before=src)
    m = res.data["metrics"]
    if m.get("true_peak") is not None and m["true_peak"] > tp + 0.3:
        res.warnings.append(f"measured true peak {m['true_peak']} dBTP exceeds ceiling {tp}")
    if format in ("mp3", "m4a", "aac", "ogg", "opus") and tp > -1.5:
        res.warnings.append("lossy encoding can add ~0.5–1 dB of peaks; use true_peak=-1.5 or -2 for lossy delivery")
    res.summary += f" Measured: {m.get('lufs')} LUFS, {m.get('true_peak')} dBTP."
    return res


def _fit_music(music: np.ndarray, sr: int, n: int, loop: bool, xfade: float = 2.0) -> np.ndarray:
    music = C.as2d(music)
    if len(music) >= n or not loop:
        return music[:n] if len(music) >= n else np.pad(music, ((0, n - len(music)), (0, 0)))
    k = int(xfade * sr)
    out = music.copy()
    while len(out) < n:
        a, b = out[:-k], out[-k:]
        ramp = np.linspace(0, 1, k)[:, None]
        mid = b * np.cos(ramp * np.pi / 2) + music[:k] * np.sin(ramp * np.pi / 2)
        out = np.concatenate([a, mid, music[k:]])
    return out[:n]


@tool("audio")
def audio_mix(voice: str, music: str, duck_db: float = 10.0, music_level_db: float = -18.0,
              intro: float = 2.0, outro: float = 3.0, fade_in: float = 1.5, fade_out: float = 3.0,
              loop_music: bool = True, attack: float = 0.12, release: float = 0.6, target: str = "streaming",
              format: str = "wav", project: str = "", out: str = "") -> Result:
    """Mix a voice (voiceover/podcast) over a music bed with automatic ducking: the music dips by
    duck_db whenever the voice speaks and swells back in the gaps, with a music-only intro/outro,
    fades, looping of short music (crossfaded) and final mastering to `target` (-14 streaming default).
    music_level_db: music loudness UNDER the voice relative to the voice (-18 ≈ clear speech; -12 more
    music). attack/release: ducking speed in seconds. Returns the mix, a stem-annotated preview and
    measured loudness; data.duck_regions lists when the music is ducked."""
    vs, ms = C.src_path(voice), C.src_path(music)
    sr = 48000
    v, _ = C.load(vs, sr=sr)
    mu, _ = C.load(ms, sr=sr)
    if v.shape[1] == 1:
        v = np.repeat(v, 2, axis=1)
    if mu.shape[1] == 1:
        mu = np.repeat(mu, 2, axis=1)
    lead, tail = int(intro * sr), int(outro * sr)
    n = lead + len(v) + tail
    if len(mu) < n and not loop_music:
        n = max(len(mu), lead + len(v))
    mu = _fit_music(mu, sr, n, loop_music)
    vfull = np.zeros((n, 2), np.float32)
    vfull[lead:lead + len(v)] = v[: n - lead]
    # levels: voice to -16 LUFS-ish reference, music relative to that
    vl = C.lufs(v, sr) or -20
    ml = C.lufs(mu, sr) or -20
    vfull *= C.undb(-16 - vl)
    base_music = C.undb(-16 + music_level_db + duck_db - ml)       # music level when NOT ducked
    # voice activity → duck gain
    e, h = _frames_env(vfull, sr, 0.01)
    lev = 20 * np.log10(e + 1e-9)
    active = lev > max(-45.0, np.percentile(lev[lev > -90], 60) - 18) if (lev > -90).any() else lev > -45
    # bridge short gaps (<0.35 s) so music doesn't pump between words
    k = int(0.35 / 0.01)
    act = np.convolve(active.astype(float), np.ones(k), "same") > 0
    g = np.where(act, C.undb(-duck_db), 1.0).astype(np.float32)
    g = _smooth(g, 0.01, attack, release)
    gm = _to_samples(g, h, n)[:, None]
    mu = C.fade(mu, sr, fade_in, fade_out) * base_music * gm
    mix = vfull + mu
    mix, norm = C.normalize_loudness(mix, sr, C.target_of(target)[0], C.target_of(target)[1])
    dest = C.out_path(project, out, f"{vs.stem}-mix", "." + format.lstrip("."))
    C.save(dest, mix, sr)
    # duck regions for the report/preview
    regions, on = [], None
    for i, a in enumerate(act):
        if a and on is None:
            on = i
        if (not a or i == len(act) - 1) and on is not None:
            regions.append((round(on * 0.01, 2), round(i * 0.01, 2)))
            on = None
    res = Result(f"Mixed {vs.name} over {ms.name}: music {music_level_db} dB under the voice, ducking {duck_db} dB "
                 f"(attack {attack}s / release {release}s), intro {intro}s, outro {outro}s, mastered to {target}.",
                 files=[str(dest)], data={"duck_regions": regions[:200], "normalize": norm,
                                          "voice_offset_seconds": intro})
    m, w = C.measure(dest, C.target_of(target)[0])
    res.warnings += w
    res.data["metrics"] = m
    res.previews.append(str(C.preview(dest, C.sibling(dest, ".png"), metrics=m,
                                      title="mix — yellow = music ducked under voice",
                                      marks=[(s, e, "") for s, e in regions])))
    res.next_steps.append(f"Voice starts at {intro}s in the mix — offset captions by +{intro}s (or pass intro=0).")
    return res


@tool("audio")
def audio_trim_silence(path: str, max_pause: float = 0.45, threshold_db: float = -42.0, keep_edges: float = 0.15,
                       format: str = "wav", project: str = "", out: str = "") -> Result:
    """Tighten a recording: shortens every pause longer than max_pause seconds down to max_pause
    (jump-cut style, with 15 ms crossfades so there are no clicks) and trims leading/trailing silence
    to keep_edges seconds. threshold_db is relative to the loudest part (-42 default; raise to -35 for
    noisy rooms). Returns the tightened file and data.cuts [{start,end}] in ORIGINAL time plus an
    edit list mapping — use it to cut matching video."""
    src = C.src_path(path)
    x, sr = C.load(src, sr=48000)
    e, h = _frames_env(x, sr, 0.01)
    lev = 20 * np.log10(e / (e.max() + 1e-12) + 1e-12)
    speech = lev > threshold_db
    idx = np.where(speech)[0]
    if not len(idx):
        raise ToolError("everything is below the threshold (silent file?)", "lower threshold_db, e.g. -55")
    keep = []  # (start_sample, end_sample) segments to keep
    start = max(0, idx[0] * h - int(keep_edges * sr))
    end_all = min(len(x), (idx[-1] + 1) * h + int(keep_edges * sr))
    # find silent runs within [start, end_all]
    cuts = []
    run = None
    for i in range(idx[0], idx[-1] + 1):
        if not speech[i]:
            run = i if run is None else run
        elif run is not None:
            dur = (i - run) * 0.01
            if dur > max_pause:
                half = max_pause / 2
                cuts.append((run * h + int(half * sr), i * h - int(half * sr)))
            run = None
    pos = start
    for a, b in cuts:
        keep.append((pos, a))
        pos = b
    keep.append((pos, end_all))
    xf = int(0.015 * sr)
    out_parts = []
    for i, (a, b) in enumerate(keep):
        seg = x[a:b].copy()
        if i > 0:
            seg = C.fade(seg, sr, 0.015, 0)
        if i < len(keep) - 1:
            seg = C.fade(seg, sr, 0, 0.015)
        out_parts.append(seg)
    y = out_parts[0]
    for seg in out_parts[1:]:  # overlap-add 15 ms
        if len(y) > xf and len(seg) > xf:
            y = np.concatenate([y[:-xf], y[-xf:] + seg[:xf], seg[xf:]])
        else:
            y = np.concatenate([y, seg])
    dest = C.out_path(project, out, f"{src.stem}-tight", "." + format.lstrip("."))
    C.save(dest, y, sr)
    removed = (len(x) - len(y)) / sr
    edl = [{"src_start": round(a / sr, 3), "src_end": round(b / sr, 3)} for a, b in keep]
    res = Result(f"Tightened {src.name}: {len(cuts)} long pauses shortened to {max_pause}s, edges trimmed; "
                 f"{len(x) / sr:.1f}s → {len(y) / sr:.1f}s ({removed:.1f}s removed).", files=[str(dest)],
                 data={"cuts": [{"start": round(a / sr, 3), "end": round(b / sr, 3)} for a, b in cuts],
                       "keep_segments": edl, "removed_seconds": round(removed, 2)})
    _finish(res, dest, None, title=f"tightened {src.name}")
    if removed / (len(x) / sr) > 0.5:
        res.warnings.append("more than half the audio was removed — check threshold_db")
    return res


@tool("audio")
def audio_convert(path: str, format: str = "mp3", sample_rate: int = 0, channels: int = 0, bitrate: str = "",
                  project: str = "", out: str = "") -> Result:
    """Convert audio (or extract the audio of a video) to wav | mp3 | m4a | aac | flac | ogg | opus | aiff,
    optionally changing sample rate (e.g. 48000 for video, 44100 for music) and channels (1/2).
    bitrate like '192k' (defaults: mp3/m4a 192k, opus 128k). Returns the new file, measured."""
    src = C.src_path(path)
    fmt = format.lower().lstrip(".")
    dest = C.out_path(project, out, src.stem, "." + fmt)
    C.encode(src, dest, bitrate=bitrate, sr=sample_rate or None, channels=channels or None)
    res = Result(f"Converted {src.name} → {dest.name}.", files=[str(dest)], data={"probe": C.qc.probe(dest)})
    return _finish(res, dest, None, title=dest.name)


@tool("audio")
def audio_join(paths: list[str], crossfade: float = 0.0, gap: float = 0.0, format: str = "wav",
               project: str = "", out: str = "") -> Result:
    """Join audio files in order into one file, with an optional equal-power crossfade (seconds) or a
    silent gap between them. Returns the joined file and data.offsets (start time of each input in the
    result — useful for chapters/captions)."""
    if len(paths) < 2:
        raise ToolError("need at least two files to join")
    sr = 48000
    arrs = [C.load(C.src_path(p), sr=sr)[0] for p in paths]
    ch = max(a.shape[1] for a in arrs)
    arrs = [np.repeat(a, 2, axis=1) if a.shape[1] < ch else a for a in arrs]
    y = arrs[0]
    offsets = [0.0]
    k = int(crossfade * sr)
    for a in arrs[1:]:
        if k > 0 and len(y) > k and len(a) > k:
            r = np.linspace(0, np.pi / 2, k)[:, None]
            offsets.append(round((len(y) - k) / sr, 3))
            y = np.concatenate([y[:-k], y[-k:] * np.cos(r) + a[:k] * np.sin(r), a[k:]])
        else:
            if gap > 0:
                y = np.concatenate([y, np.zeros((int(gap * sr), ch), np.float32)])
            offsets.append(round(len(y) / sr, 3))
            y = np.concatenate([y, a])
    dest = C.out_path(project, out, f"{Path(paths[0]).stem}-joined", "." + format.lstrip("."))
    C.save(dest, y, sr)
    res = Result(f"Joined {len(paths)} files ({len(y) / sr:.1f}s).", files=[str(dest)], data={"offsets": offsets})
    return _finish(res, dest, None, title=f"joined ({len(paths)} parts)")


@tool("audio")
def audio_analyze(path: str, target: str = "", project: str = "", out: str = "") -> Result:
    """Measure and SEE any audio (or a video's audio): duration, integrated loudness (LUFS), true peak,
    silence %, clipping, stereo phase, noise floor, spectral balance, plus a waveform+spectrogram PNG to
    look at. Pass target (e.g. 'podcast', '-14') to get a pass/fail against it. Use before and after
    every audio operation you are unsure about."""
    src = C.src_path(path)
    tgt = C.target_of(target)[0] if target else None
    m, w = C.measure(src, tgt)
    x, sr = C.load(src, sr=48000, mono=True)
    m["noise_floor_dbfs"] = round(_noise_floor_db(x, sr), 1)
    spec = np.abs(np.fft.rfft(x[: sr * 120, 0] * 1.0)) ** 2 if len(x) else np.zeros(1)
    f = np.fft.rfftfreq(min(len(x), sr * 120), 1 / sr) if len(x) else np.zeros(1)
    tot = spec.sum() + 1e-12
    bands = {"sub <60": (0, 60), "low 60-250": (60, 250), "mid 250-2k": (250, 2000), "presence 2-6k": (2000, 6000),
             "air >6k": (6000, 24000)}
    m["spectral_balance_pct"] = {k: round(100 * spec[(f >= a) & (f < b)].sum() / tot, 1) for k, (a, b) in bands.items()}
    if m["noise_floor_dbfs"] > -45:
        w.append(f"audible noise floor ({m['noise_floor_dbfs']} dBFS) — audio_clean may help")
    d = Path(out) if out else C.output_dir(project or None, "audio")
    d.mkdir(parents=True, exist_ok=True)
    pv = C.preview(src, C.unique_path(d, f"{src.stem}-analysis", ".png"), metrics=m)
    verdict = "PASS" if tgt is not None and not w else ("CHECK" if tgt is not None else "")
    return Result(f"{src.name}: {m.get('duration')}s, {m.get('lufs')} LUFS, {m.get('true_peak')} dBTP, "
                  f"noise floor {m['noise_floor_dbfs']} dBFS, {m.get('silence_pct')}% silence. {verdict}".strip(),
                  previews=[str(pv)], warnings=w, data=m)
