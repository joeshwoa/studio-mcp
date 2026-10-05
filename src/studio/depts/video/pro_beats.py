"""Music-driven editing: tempo + beat/downbeat tracking (video_beats) and snapping cuts to the beat
(video_cut_to_beats, and "cut_to_beats" inside any video_edit timeline).

Beat tracking without librosa: log-magnitude spectral flux onset envelope → tempo by autocorrelation
with a log-normal prior around 120 BPM → beats by dynamic programming (Ellis 2007) → downbeats by the
4/4 phase whose beats carry the most low-frequency (kick/bass) onset energy. librosa is used for the
beats when installed (method='librosa' or 'auto')."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _pro as P

SR = 22050
LATENCY = 0.0  # set below after HOP/NFFT
HOP = 256
NFFT = 1024
LATENCY = 0.25 * NFFT / SR


def _stft_mag(x: np.ndarray) -> np.ndarray:
    x = np.pad(x, (NFFT // 2, NFFT // 2))  # centred frames: frame k is centred on sample k*HOP
    if x.size < NFFT:
        x = np.pad(x, (0, NFFT - x.size))
    n = 1 + (x.size - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    win = np.hanning(NFFT).astype(np.float32)
    out = []
    for k in range(0, n, 2048):  # chunked: bounded memory on long songs
        fr = x[idx[k:k + 2048]] * win
        out.append(np.abs(np.fft.rfft(fr, axis=1)).astype(np.float32))
    return np.concatenate(out, axis=0)


def onset_envelope(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(full-band onset strength, low-band (<200 Hz) onset strength, log spectrogram), one row per hop."""
    S = np.log1p(1000.0 * _stft_mag(x))
    flux = np.maximum(0.0, np.diff(S, axis=0, prepend=S[:1]))
    nb = int(200 / (SR / NFFT))
    env = flux.mean(axis=1)
    low = flux[:, 1:nb].mean(axis=1)

    def norm(e):
        k = 32
        loc = np.convolve(e, np.ones(k) / k, mode="same")
        e = np.maximum(0.0, e - loc)
        return e / (e.std() + 1e-9)
    return norm(env), norm(low), S


def chroma(S: np.ndarray) -> np.ndarray:
    """12-bin pitch-class energy per frame (80 Hz–2 kHz) from the log spectrogram."""
    f = np.fft.rfftfreq(NFFT, 1 / SR)
    sel = (f >= 80) & (f <= 2000)
    pc = (np.round(12 * np.log2(f[sel] / 440.0)) % 12).astype(int)
    M = np.expm1(S[:, sel]) / 1000.0
    out = np.zeros((S.shape[0], 12), np.float32)
    for k in range(12):
        out[:, k] = M[:, pc == k].sum(axis=1)
    return out


def tempo_estimate(env: np.ndarray, fr: float, lo: float = 60, hi: float = 200, prior: float = 120.0,
                   hint: float = 0.0) -> tuple[float, float]:
    """(BPM, confidence 0–1) from the onset autocorrelation weighted by a log-normal tempo prior."""
    e = env - env.mean()
    n = e.size
    ac = np.fft.irfft(np.abs(np.fft.rfft(e, 2 * n)) ** 2)[:n]
    ac /= ac[0] + 1e-9
    lags = np.arange(n)
    bpm = np.where(lags > 0, 60.0 * fr / np.maximum(lags, 1), 0)
    ok = (bpm >= lo) & (bpm <= hi)
    if not ok.any():
        return prior, 0.0
    centre = hint or prior
    w = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1e-3) / centre) / (0.6 if not hint else 0.25)) ** 2)
    # harmonic support: a true beat period also correlates at 2× and (weaker) 0.5×
    sc = np.zeros(n)
    for L in np.nonzero(ok)[0]:
        v = ac[L]
        if 2 * L < n:
            v += 0.5 * ac[2 * L]
        if L // 2 > 0:
            v += 0.25 * ac[L // 2]
        sc[L] = v * w[L]
    L = int(np.argmax(sc))
    # parabolic interpolation for a sub-hop period
    if 1 <= L < n - 1:
        a, b, c = sc[L - 1], sc[L], sc[L + 1]
        den = a - 2 * b + c
        off = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
    else:
        off = 0.0
    period = L + float(np.clip(off, -0.5, 0.5))
    conf = float(np.clip(ac[L] / (np.abs(ac[1:]).max() + 1e-9), 0, 1))
    return 60.0 * fr / period, conf


def track_beats(env: np.ndarray, period: float, tightness: float = 100.0) -> np.ndarray:
    """Dynamic-programming beat tracker: indices (hops) of beats."""
    n = env.size
    score = env.astype(np.float64).copy()
    back = -np.ones(n, dtype=np.int64)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    lags = np.arange(lo, hi + 1)
    pen = -tightness * np.log(lags / period) ** 2
    for t in range(n):
        prev = t - lags
        m = prev >= 0
        if not m.any():
            continue
        cand = score[prev[m]] + pen[m]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] = env[t] + cand[j]
            back[t] = prev[m][j]
    # end on the best-scoring beat in the last period
    tail = max(0, n - int(period) - 1)
    t = tail + int(np.argmax(score[tail:]))
    beats = [t]
    while back[t] >= 0:
        t = int(back[t])
        beats.append(t)
    b = np.array(beats[::-1])
    # drop weak spurious beats at the very start (before the music really starts)
    thr = 0.1 * np.median(env[b]) if b.size else 0
    while b.size > 2 and env[b[0]] < thr:
        b = b[1:]
    return b


def analyse_beats(path: Path, bpm_hint: float = 0.0, start: float = 0.0, duration: float = 0.0, method: str = "auto",
                  meter: int = 4) -> dict:
    x = P.audio_mono(path, SR, start, duration)
    if x.size < SR:
        raise ToolError(f"{path.name}: less than 1 s of audio to analyse")
    fr = SR / HOP
    env, low, an_spec = onset_envelope(x)
    used = "spectral-flux + DP (numpy)"
    beats_t = None
    tempo = conf = None
    if method in ("auto", "librosa"):
        try:
            import librosa  # type: ignore
            t, bf = librosa.beat.beat_track(y=x, sr=SR, hop_length=HOP, start_bpm=bpm_hint or 120.0, units="frames")
            tempo = float(np.atleast_1d(t)[0])
            beats_idx = np.asarray(bf, dtype=int)
            used = "librosa beat_track"
            conf = None
        except ImportError:
            if method == "librosa":
                raise ToolError("method='librosa' needs `pip install librosa`", "or use method='auto'")
    if used.startswith("spectral"):
        tempo, conf = tempo_estimate(env, fr, hint=bpm_hint)
        beats_idx = track_beats(env, 60.0 * fr / tempo)
    # sub-hop refinement: each beat moves to the local onset peak (±2 hops, parabolic), then a fixed
    # latency correction — a centred window "sees" a transient ~NFFT/4 samples before it happens
    ref = []
    for b in beats_idx:
        a0, a1 = max(0, b - 2), min(env.size - 1, b + 2)
        j = a0 + int(np.argmax(env[a0:a1 + 1]))
        off = 0.0
        if 0 < j < env.size - 1:
            y0, y1, y2 = env[j - 1], env[j], env[j + 1]
            den = y0 - 2 * y1 + y2
            off = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5)) if abs(den) > 1e-9 else 0.0
        ref.append(j + off)
    beats_t = np.array(ref) / fr + start + LATENCY
    if conf is None:
        conf = 0.5
    # regularity: how evenly spaced the tracked beats are
    if beats_t.size > 3:
        ib = np.diff(beats_t)
        reg = float(np.clip(1 - ib.std() / (ib.mean() + 1e-9) * 4, 0, 1))
        # tempo = slope of a robust line through (beat index, time): immune to per-beat jitter
        k = np.arange(beats_t.size)
        slope = float(np.polyfit(k, beats_t, 1)[0])
        tempo_med = 60.0 / slope if slope > 0 else tempo
    else:
        reg, tempo_med = 0.0, tempo
    # downbeats: the meter phase whose beats carry most low-band (kick) + full-band energy
    # plus harmonic change: chords tend to change on the bar line, so the spectrum between beats
    # changes most across downbeats (separates beat 1 from beat 3 when the kick plays on both)
    hc = np.zeros(beats_idx.size)
    if beats_idx.size > meter:
        ch = chroma(an_spec)
        segs = [ch[a:max(b, a + 1)].mean(axis=0) for a, b in zip(beats_idx, list(beats_idx[1:]) + [ch.shape[0]])]
        for i in range(1, len(segs)):
            u, v = segs[i - 1], segs[i]
            hc[i] = 1 - float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9))
        hc /= hc.max() + 1e-9
    strength = []
    for ph in range(meter):
        bi = beats_idx[ph::meter]
        strength.append(float(low[bi].mean() / (low[beats_idx].mean() + 1e-9) + 0.3 * env[bi].mean() / (env[beats_idx].mean() + 1e-9)
                              + 1.5 * hc[ph::meter].mean() / (hc.mean() + 1e-9)) if bi.size else 0.0)
    phase = int(np.argmax(strength)) if beats_idx.size >= meter else 0
    down = beats_t[phase::meter]
    srt = sorted(strength, reverse=True)
    down_conf = float(np.clip((srt[0] - srt[1]) / (srt[0] + 1e-9) * 3, 0, 1)) if len(srt) > 1 else 0.0
    return {"tempo": round(float(tempo_med), 2), "tempo_autocorr": round(float(tempo), 2), "confidence": round(conf, 3),
            "regularity": round(reg, 3), "beats": [round(float(b), 3) for b in beats_t],
            "downbeats": [round(float(b), 3) for b in down], "downbeat_confidence": round(down_conf, 3),
            "meter": meter, "method": used, "duration": round(x.size / SR, 3), "start": start,
            "_env": env, "_x": x, "_fr": fr}


def beat_plot(an: dict, dest: Path, title: str) -> Path:
    x = an["_x"]
    W, H = 1600, 360
    im = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(im)
    D = an["duration"]
    st = an["start"]
    L, R, T, B = 50, W - 20, 50, H - 40
    cols = R - L
    # waveform envelope (min/max per column)
    n = x.size
    step = max(1, n // cols)
    m = x[: step * cols].reshape(-1, step) if n >= cols else x.reshape(-1, 1)
    mx, mn = m.max(axis=1), m.min(axis=1)
    sc = (B - T) / 2 / (np.abs(x).max() + 1e-9)
    mid = (T + B) / 2
    for i in range(min(cols, mx.size)):
        d.line([(L + i, mid - mx[i] * sc), (L + i, mid - mn[i] * sc)], fill=(150, 170, 200))
    # onset envelope
    env = an["_env"]
    ex = [L + cols * (k / an["_fr"]) / D for k in range(env.size)]
    ey = [B - (B - T) * 0.35 * min(1.0, v / (env.max() + 1e-9)) for v in env]
    d.line(list(zip(ex, ey)), fill=(240, 150, 60), width=1)
    downs = set(an["downbeats"])
    for b in an["beats"]:
        xx = L + cols * (b - st) / D
        if b in downs:
            d.line([(xx, T - 6), (xx, B)], fill=(210, 40, 60), width=3)
        else:
            d.line([(xx, T + 10), (xx, B)], fill=(40, 90, 200), width=1)
    for k in range(0, int(D) + 1, max(1, int(round(D / 12)) or 1)):
        xx = L + cols * k / D
        d.text((xx - 12, B + 8), P.tc(k + st)[:5], font=P.font(12), fill=(80, 80, 90))
    d.text((L, 10), title, font=P.font(18, True), fill=(20, 20, 30))
    d.text((W - 640, 14), "blue = beats · red = downbeats (bar starts) · orange = onset strength", font=P.font(13), fill=(90, 90, 100))
    im.save(dest)
    return dest


@tool("video")
def video_beats(path: str, bpm_hint: float = 0.0, start: float = 0.0, duration: float = 0.0, meter: int = 4,
                method: str = "auto", project: str = "", out: str = "") -> Result:
    """Tempo and beat/downbeat times of a song (or a video's soundtrack) for cutting to music. Returns
    data {tempo (BPM), beats [s], downbeats [s] (bar starts, assuming `meter` beats per bar), confidence,
    regularity, method}, a plot to LOOK at (waveform + blue beat lines + red downbeats), beats.json and an
    Audacity label track (.txt). bpm_hint: nudge toward a known tempo (fixes half/double-time picks).
    start/duration: analyse part of the file. method: auto (librosa if installed, else numpy spectral
    flux + dynamic programming) | numpy | librosa. Use the beats with video_cut_to_beats or a timeline's
    "cut_to_beats"."""
    p = C.src_path(path)
    info = C.probe(p)
    if not info.get("audio"):
        raise ToolError(f"{p.name} has no audio")
    an = analyse_beats(p, bpm_hint, start, duration, "numpy" if method == "numpy" else method, meter)
    d = C.out_folder(project, out, p.stem + "-beats")
    plot = beat_plot(an, d / "beats.png", f"{p.name} — {an['tempo']:.1f} BPM, {len(an['beats'])} beats, {an['method']}")
    data = {k: v for k, v in an.items() if not k.startswith("_")}
    (d / "beats.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    labels = d / "beats-audacity.txt"
    downs = set(an["downbeats"])
    labels.write_text("".join(f"{b:.3f}\t{b:.3f}\t{'BAR' if b in downs else 'beat'}\n" for b in an["beats"]), encoding="utf-8")
    res = Result(f"{p.name}: {an['tempo']:.1f} BPM ({len(an['beats'])} beats, {len(an['downbeats'])} bars of {meter}); "
                 f"beat regularity {an['regularity']:.2f}. Downbeats are an estimate (meter assumed {meter}/4).",
                 files=[str(plot), str(d / "beats.json"), str(labels)], previews=[str(plot)], data=data)
    if an["regularity"] < 0.5:
        res.warnings.append("beats are irregular (rubato, no clear pulse, or speech) — check the plot; try bpm_hint")
    if an["downbeat_confidence"] < 0.2:
        res.warnings.append("downbeat phase is uncertain — the red lines may be off by a beat")
    res.next_steps.append("video_cut_to_beats(clips, music, every=2) — or add \"cut_to_beats\": {\"music\": …, \"every\": 2} to a video_edit timeline")
    return res


# ─────────────────────────── cutting to the beat ───────────────────────────

def apply_cut_to_beats(spec: dict, base_dir: Path | None = None) -> tuple[dict, dict]:
    """Rewrite a timeline whose spec has "cut_to_beats": {"music", "every": 1|2|4|[pattern], "align":
    "downbeat"|"beat"|"start", "volume_db", "fade_out", "max_duration"} so every cut lands on a beat:
    clip durations are snapped to beat spans (and transitions start on the beat), the music is added as
    an audio track whose first (down)beat sits at t=0. Returns (new spec, info)."""
    cfg = spec.get("cut_to_beats")
    if isinstance(cfg, str):
        cfg = {"music": cfg}
    if not cfg or not cfg.get("music"):
        return spec, {}
    mp = Path(str(cfg["music"])).expanduser()
    if not mp.is_absolute() and base_dir is not None:
        mp = base_dir / mp
    if not mp.exists():
        raise ToolError(f"cut_to_beats: music not found: {mp}")
    an = analyse_beats(mp, float(cfg.get("bpm_hint", 0) or 0))
    beats = an["beats"]
    align = str(cfg.get("align", "downbeat"))
    if align == "start":
        t0 = 0.0
        grid = [0.0] + [b for b in beats if b > 0.05]
    else:
        t0 = an["downbeats"][0] if (align == "downbeat" and an["downbeats"]) else beats[0]
        grid = [b for b in beats if b >= t0 - 1e-6]
    if len(grid) < 3:
        raise ToolError("cut_to_beats: too few beats found in the music")
    # extend the grid past the end of the song with the median beat period (for long clip lists)
    per = float(np.median(np.diff(beats))) if len(beats) > 2 else 0.5
    while len(grid) < 4000 and grid[-1] < t0 + 3600:
        grid.append(grid[-1] + per)
    every = cfg.get("every", 2)
    pattern = [int(x) for x in every] if isinstance(every, list) else [int(every)]
    pattern = [max(1, x) for x in pattern] or [2]
    maxd = float(cfg.get("max_duration", 0) or 0)
    clips = [dict(c) if isinstance(c, dict) else {"src": c} for c in spec.get("clips", [])]
    new, k, gi, plan, warns = [], 0, 0, [], []
    for i, c in enumerate(clips):
        nb = pattern[k % len(pattern)]
        k += 1
        if gi + nb >= len(grid):
            break
        span = grid[gi + nb] - grid[gi]
        nxt = clips[i + 1] if i + 1 < len(clips) else None
        tr = nxt.get("transition") if nxt else None
        tr_d = float((tr or {}).get("duration", 0.5)) if isinstance(tr, dict) else (0.5 if tr else 0.0)
        need = span + tr_d
        src = c.get("src") or c.get("video")
        if src and Path(str(src)).suffix.lower() not in C.IMAGE_EXTS:
            sp = Path(str(src)).expanduser()
            if not sp.is_absolute() and base_dir is not None:
                sp = base_dir / sp
            avail = (C.probe(sp)["duration"] - float(c.get("in", 0) or 0)) / float(c.get("speed", 1) or 1)
            # a clip too short for its beat span takes fewer beats (still lands on a beat)
            while avail + 1e-3 < need and nb > 1:
                nb -= 1
                span = grid[gi + nb] - grid[gi]
                need = span + tr_d
            if avail + 1e-3 < need:
                warns.append(f"cut_to_beats: clip {i + 1} is {need - avail:.2f}s shorter than one beat span — "
                             "this cut lands off the beat; use a longer clip or every=1")
                need = avail
            c.pop("out", None)
            c["duration"] = round(need, 4)
        else:
            c["duration"] = round(need, 4)
        new.append(c)
        plan.append({"clip": i + 1, "beats": nb, "at": round(grid[gi] - t0, 3), "span": round(span, 3)})
        gi += nb
        if maxd and grid[gi] - t0 >= maxd:
            break
    if not new:
        raise ToolError("cut_to_beats: no clips")
    total = grid[gi] - t0
    out = {k: v for k, v in spec.items() if k != "cut_to_beats"}
    out["clips"] = new
    music = {"src": str(mp), "at": 0.0, "in": round(t0, 4), "duration": round(total, 4),
             "volume_db": float(cfg.get("volume_db", 0) or 0), "fade_out": float(cfg.get("fade_out", min(1.5, per * 2))),
             "duck": bool(cfg.get("duck", False))}
    out["audio"] = list(spec.get("audio") or []) + [music]
    return out, {"tempo": an["tempo"], "every": pattern, "align": align, "music_in": round(t0, 3), "cuts": plan,
                 "duration": round(total, 3), "beats_used": gi, "warnings": warns}


@tool("video")
def video_cut_to_beats(clips: list, music: str, every: int | list = 2, align: str = "downbeat", size: str = "",
                       transition: str = "cut", max_duration: float = 0.0, music_volume_db: float = 0.0, grade: str = "",
                       captions: bool = False, project: str = "", out: str = "") -> Result:
    """Cut a montage to music: each clip lasts `every` beats (1 = every beat, 2, 4 = every bar; or a
    pattern like [4,2,2] that repeats) so every cut lands exactly on a beat; the song starts on its first
    downbeat (align: downbeat | beat | start). clips: paths or timeline clip dicts ({"src","in"} — "out"/
    "duration" are replaced; images get Ken Burns). Clips too short for their span take fewer beats.
    transition: cut (default) or dissolve/zoom/… (starts on the beat). max_duration: stop after N s.
    Renders MP4 + .kdenlive; data.cut_to_beats lists every cut time; also writes the timeline JSON."""
    if not clips:
        raise ToolError("no clips", "clips=['a.mp4', 'b.mp4', …]")
    cl = []
    for i, c in enumerate(clips):
        c = dict(c) if isinstance(c, dict) else {"src": str(c)}
        if "src" in c and Path(str(c["src"])).suffix.lower() in C.IMAGE_EXTS:
            c["image"] = c.pop("src")
        if i and transition not in ("cut", "", "none") and "transition" not in c:
            c["transition"] = {"type": transition, "duration": 0.35}
        c.setdefault("fit", "cover")
        cl.append(c)
    spec = {"clips": cl, "cut_to_beats": {"music": music, "every": every, "align": align, "max_duration": max_duration,
                                          "volume_db": music_volume_db}, "loudness": "streaming"}
    if size:
        spec["size"] = size
    if grade:
        spec["grade"] = grade
    if captions:
        spec["captions"] = {"auto": True}
    from . import render as R
    spec2, info = apply_cut_to_beats(spec)
    tl_path = C.out_path(project, out, Path(music).stem + "-beatcut", ".json")
    tl_path.write_text(json.dumps(spec2, indent=1, ensure_ascii=False), encoding="utf-8")
    res = R.render(spec2, project=project, out=out, name=Path(music).stem + "-beatcut",
                   summary=f"Cut {len(spec2['clips'])} clip(s) to {info['tempo']:.1f} BPM, every {info['every']} beat(s): "
                           f"{info['duration']:.2f}s, every cut on a beat.")
    res.files.append(str(tl_path))
    res.data["cut_to_beats"] = info
    res.data["timeline_json"] = str(tl_path)
    res.warnings += info.get("warnings", [])
    if len(spec2["clips"]) < len(cl):
        res.warnings.append(f"only {len(spec2['clips'])} of {len(cl)} clips fit (music/max_duration ran out)")
    return res
