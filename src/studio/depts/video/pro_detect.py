"""Footage intelligence: shot boundaries with per-shot measurements (video_scene_detect) and ranking a
pile of clips/shots to find the usable takes (video_select_takes) — what an assistant editor does
before the first cut."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _pro as P

AW, AH = 160, 90   # analysis resolution (16:9; other shapes are squeezed, which does not matter for differences)


def analyse(path: Path, threshold: float = 0.3, min_scene: float = 0.6, analysis_fps: float = 0.0,
            method: str = "auto") -> dict:
    """Shot boundaries + frame-level motion/shake for one file. Scene score = ffmpeg's `scene` metric
    (min(MAFD, |ΔMAFD|)/100 on the frame), computed in one decode pass together with phase-correlation
    camera motion. PySceneDetect's ContentDetector is used instead when installed and method='pyscenedetect'."""
    info = C.need_video(path)
    D = info["duration"]
    fps = analysis_fps or info.get("fps") or 25.0
    if fps > 60:
        fps = 60.0
    scores, mafds, shifts = [], [], []
    prev = prevg = None
    prev_mafd = 0.0
    for f in P.frames(path, AW, AH, fps=analysis_fps or 0.0):
        x = f.astype(np.int16)
        g = f.mean(axis=2)
        if prev is None:
            scores.append(0.0)
            mafds.append(0.0)
            shifts.append((0.0, 0.0))
        else:
            mafd = float(np.abs(x - prev).mean())
            diff = abs(mafd - prev_mafd)
            scores.append(float(np.clip(min(mafd, diff) / 255.0, 0, 1)))  # = ffmpeg scene (MAFD in %)
            mafds.append(mafd)
            prev_mafd = mafd
            shifts.append(P.shift(prevg, g))
        prev, prevg = x, g
    n = len(scores)
    if n == 0:
        raise ToolError(f"could not decode frames from {path.name}")
    fps_eff = n / D if D > 0 else fps
    cuts: list[int] = []
    used = "ffmpeg-scene (numpy)"
    if method in ("pyscenedetect", "auto"):
        try:
            from scenedetect import detect, ContentDetector  # type: ignore
            sl = detect(str(path), ContentDetector(threshold=27.0 * threshold / 0.3, min_scene_len=max(1, int(min_scene * fps_eff))))
            cuts = [int(round(s[0].get_seconds() * fps_eff)) for s in sl[1:]]
            used = "PySceneDetect ContentDetector"
        except ImportError:
            if method == "pyscenedetect":
                raise ToolError("method='pyscenedetect' needs `pip install scenedetect`", "or use method='auto'")
    if not cuts and used.startswith("ffmpeg"):
        last = 0
        for i, s in enumerate(scores):
            if i and s >= threshold and (i - last) / fps_eff >= min_scene:
                # a 1-frame flash spikes twice: keep the cut only if the picture really changed (vs 2 frames later)
                cuts.append(i)
                last = i
    bounds = [0] + cuts + [n]
    shots = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < 1:
            continue
        mf = np.array(mafds[a + 1:b] or [0.0])
        sh = np.array(shifts[a + 1:b] or [(0.0, 0.0)])
        path_xy = np.cumsum(sh, axis=0)
        k = max(1, int(fps_eff * 0.5))
        if len(path_xy) > 2 * k:
            ker = np.ones(k) / k
            smooth = np.stack([np.convolve(np.pad(path_xy[:, j], (k // 2, k - 1 - k // 2), mode="edge"), ker, "valid")
                               for j in range(2)], axis=1)
            jitter = float(np.sqrt(((path_xy - smooth) ** 2).sum(axis=1).mean()))
        else:
            jitter = 0.0
        pan = float(np.abs(sh).sum(axis=1).mean())
        shots.append({"start": round(a / fps_eff, 3), "end": round(b / fps_eff, 3),
                      "motion": round(float(mf.mean()) / 2.55, 2),   # 0–100: mean frame difference
                      "shake": round(100 * jitter / AW, 2),          # % of frame width of hand-held jitter
                      "camera_move": round(100 * pan * fps_eff / AW, 1),  # % of frame width per second
                      "cut_score": round(scores[a], 3) if a else None})
    shots[-1]["end"] = round(D, 3)
    for s in shots:
        s["duration"] = round(s["end"] - s["start"], 3)
    return {"info": info, "shots": shots, "method": used, "fps": fps_eff, "scores": scores}


def measure_shots(path: Path, an: dict, work: Path, thumbs: bool = True, max_thumbs: int = 200) -> list[dict]:
    """Add thumbnail-based (sharpness, exposure, faces) and audio measurements to each shot."""
    info = an["info"]
    audio = P.audio_mono(path, 16000) if info.get("audio") else np.zeros(0, np.float32)
    out = []
    for i, s in enumerate(an["shots"]):
        s = dict(s, n=i + 1)
        if audio.size:
            seg = audio[int(s["start"] * 16000):int(s["end"] * 16000)]
            if seg.size:
                rms = float(np.sqrt((seg.astype(np.float64) ** 2).mean()))
                s["audio_db"] = round(P.db(rms), 1)
                s["audio_peak_db"] = round(P.db(float(np.abs(seg).max())), 1)
        if thumbs and i < max_thumbs:
            t = s["start"] + s["duration"] * 0.5
            tp = work / f"shot{i + 1:03d}.jpg"
            C.frame_at(path, t, tp, 640)
            if tp.exists():
                im = Image.open(tp).convert("RGB")
                g = np.asarray(im.convert("L"))
                s["thumb"] = str(tp)
                s["sharpness"] = round(P.sharpness(g), 1)
                s["blurry"] = s["sharpness"] < 40
                s.update(P.exposure(g))
                fc = P.faces(im)
                s["has_face"] = bool(fc)
                s["faces"] = len(fc)
                if fc:
                    s["face_area"] = round(max(w * h for _, _, w, h in fc) / (im.width * im.height), 3)
        out.append(s)
    return out


def _shot_tile(s: dict, src_name: str = "") -> dict:
    im = Image.open(s["thumb"]) if s.get("thumb") else Image.new("RGB", (640, 360), (40, 40, 40))
    flags = []
    if s.get("blurry"):
        flags.append("SOFT")
    if s.get("shake", 0) > 0.6:
        flags.append("SHAKY")
    if s.get("brightness") is not None and (s["brightness"] < 0.15 or s["brightness"] > 0.85):
        flags.append("EXPOSURE")
    lines = [f"{P.tc(s['start'])} → {P.tc(s['end'])}  ({s['duration']:.2f}s)",
             f"sharp {s.get('sharpness', '–')} · motion {s.get('motion', 0):.1f} · shake {s.get('shake', 0):.2f}%",
             f"bright {s.get('brightness', '–')} · audio {s.get('audio_db', '–')} dB" + (f" · {s['faces']} face(s)" if s.get("faces") else "")]
    if src_name:
        lines.insert(0, src_name)
    col = (215, 60, 60) if flags else (46, 170, 90)
    return {"img": im, "head": f"Shot {s['n']}" + (f"  [{' '.join(flags)}]" if flags else ""), "lines": lines,
            "badge": f"#{s['n']}", "corner": "FACE" if s.get("has_face") else "", "colour": col}


@tool("video")
def video_scene_detect(path: str, threshold: float = 0.3, min_scene: float = 0.6, method: str = "auto",
                       analysis_fps: float = 0.0, thumbs: bool = True, project: str = "", out: str = "") -> Result:
    """Find the shots in a video (cut detection) and measure each one — the assistant-editor pass before
    cutting. Returns a labelled shot-list contact sheet (LOOK at it: shot number, in/out timecodes,
    duration, flags SOFT / SHAKY / EXPOSURE, FACE badge) and data.shots [{n, start, end, duration, thumb,
    motion (0–100 frame difference), shake (% of frame width of hand-held jitter), camera_move, sharpness
    (Laplacian variance at 640 px: <40 soft, >150 crisp), blurry, brightness/contrast/clip_low/clip_high,
    has_face/faces (OpenCV when installed), audio_db/audio_peak_db}].

    threshold: scene-change sensitivity 0.15 (catches soft cuts, more false hits) … 0.3 (default) … 0.5
    (only hard cuts). min_scene: shortest shot in seconds (suppresses flashes). method: auto (ffmpeg-style
    scene score in numpy) | pyscenedetect (if installed). Dissolves/fades may be missed — lower threshold.
    Feed shots to video_edit clips ({"src", "in": start, "out": end}) or to video_select_takes."""
    p = C.src_path(path)
    an = analyse(p, threshold=threshold, min_scene=min_scene, analysis_fps=analysis_fps, method=method)
    d = C.out_folder(project, out, p.stem + "-shots")
    shots = measure_shots(p, an, d, thumbs=thumbs)
    res = Result(f"{p.name}: {len(shots)} shot(s) in {an['info']['duration']:.2f}s "
                 f"(threshold {threshold}, {an['method']}).", files=[], data={"shots": shots, "method": an["method"],
                                                                             "source": str(p), "fps": round(an["fps"], 3)})
    if thumbs:
        tiles = [_shot_tile(s) for s in shots if s.get("thumb")][:60]
        sheet = d / "shot-list.png"
        P.tile_sheet(tiles, sheet, title=f"Shot list — {p.name}", cols=4 if len(tiles) > 6 else min(3, len(tiles)),
                     subtitle=f"{len(shots)} shots · threshold {threshold} · thumbnails at each shot's midpoint · "
                              "SOFT=blurry SHAKY=hand-held jitter EXPOSURE=too dark/bright")
        res.previews.append(str(sheet))
        res.files.append(str(sheet))
        if len(shots) > 60:
            res.warnings.append(f"sheet shows the first 60 of {len(shots)} shots (all are in data.shots)")
    # score timeline plot so a missed/false cut is visible
    try:
        res.previews.append(str(_score_plot(an, threshold, d / "cut-scores.png")))
    except Exception:
        pass
    (d / "shots.json").write_text(__import__("json").dumps(shots, indent=1), encoding="utf-8")
    res.files.append(str(d / "shots.json"))
    if len(shots) == 1:
        res.warnings.append("no cuts found — one continuous shot (or lower threshold, e.g. 0.18)")
    soft = [s["n"] for s in shots if s.get("blurry")]
    if soft:
        res.warnings.append(f"soft/blurry shot(s): {soft}")
    res.next_steps.append("use shots as clips: {\"src\": path, \"in\": start, \"out\": end} in video_edit; "
                          "rank many clips with video_select_takes")
    return res


def _score_plot(an: dict, thr: float, dest: Path) -> Path:
    from PIL import ImageDraw
    sc = np.array(an["scores"])
    W, H = 1400, 220
    im = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(im)
    n = len(sc)
    D = an["info"]["duration"]
    x = lambda i: 40 + (W - 60) * i / max(1, n - 1)  # noqa: E731
    y = lambda v: H - 30 - (H - 60) * min(1.0, v)  # noqa: E731
    d.line([(40, y(thr)), (W - 20, y(thr))], fill=(220, 80, 80), width=1)
    d.text((W - 160, y(thr) - 18), f"threshold {thr}", font=P.font(13), fill=(200, 60, 60))
    pts = [(x(i), y(v)) for i, v in enumerate(sc)]
    if len(pts) > 1:
        d.line(pts, fill=(40, 90, 200), width=1)
    for s in an["shots"][1:]:
        xx = 40 + (W - 60) * s["start"] / max(D, 1e-6)
        d.line([(xx, 24), (xx, H - 30)], fill=(46, 170, 90), width=2)
    for k in range(0, int(D) + 1, max(1, int(D / 10) or 1)):
        xx = 40 + (W - 60) * k / max(D, 1e-6)
        d.text((xx - 10, H - 24), P.tc(k)[:5], font=P.font(12), fill=(90, 90, 100))
    d.text((40, 4), "scene-change score per frame (blue) · detected cuts (green)", font=P.font(14, True), fill=(30, 30, 40))
    im.save(dest)
    return dest


# ─────────────────────────── select takes ───────────────────────────

WANTS = {   # weights: sharp, stable, exposure, face, audio, motion(+ = action, - = calm), duration
    "best":   (1.0, 0.8, 0.8, 0.5, 0.3, 0.0, 0.3),
    "sharp":  (2.0, 0.6, 0.5, 0.2, 0.0, 0.0, 0.2),
    "stable": (0.8, 2.0, 0.5, 0.2, 0.0, -0.3, 0.2),
    "faces":  (0.8, 0.6, 0.6, 2.0, 0.3, 0.0, 0.2),
    "talking": (0.8, 0.8, 0.6, 1.6, 1.6, 0.0, 0.3),
    "audio":  (0.5, 0.4, 0.4, 0.3, 2.0, 0.0, 0.2),
    "action": (0.8, 0.3, 0.6, 0.2, 0.0, 1.5, 0.2),
    "calm":   (0.8, 1.2, 0.6, 0.2, 0.0, -1.2, 0.2),
    "broll":  (1.2, 1.0, 0.8, -0.2, 0.0, 0.3, 0.4),
}


def score_shot(s: dict, want: str = "best", min_duration: float = 1.0) -> tuple[float, list[str]]:
    w = WANTS.get(want, WANTS["best"])
    why = []
    sharp = min(1.0, math.log1p(s.get("sharpness", 60)) / math.log1p(250))
    stable = max(0.0, 1.0 - s.get("shake", 0) / 1.0)
    b = s.get("brightness", 0.45)
    expo = max(0.0, 1.0 - abs(b - 0.45) * 2.2 - 3 * max(0.0, s.get("clip_high", 0) - 0.02) - 2 * max(0.0, s.get("clip_low", 0) - 0.08))
    face = min(1.0, 0.5 + s.get("face_area", 0) * 8) if s.get("has_face") else 0.0
    adb = s.get("audio_db")
    audio = 0.0 if adb is None else max(0.0, 1.0 - abs(adb + 20) / 20)
    if s.get("audio_peak_db") is not None and s["audio_peak_db"] > -0.3:
        audio -= 0.4
        why.append("audio clips")
    motion = min(1.0, s.get("motion", 0) / 12)
    dur = min(1.0, s.get("duration", 0) / max(0.1, min_duration * 2)) if s.get("duration", 0) >= min_duration else 0.0
    tot = w[0] * sharp + w[1] * stable + w[2] * expo + w[3] * face + w[4] * audio + w[5] * motion + w[6] * dur
    norm = sum(abs(x) for x in w)
    score = max(0.0, tot / norm)
    if sharp > 0.85:
        why.append("sharp")
    elif sharp < 0.6:
        why.append("soft")
    if stable > 0.8:
        why.append("steady")
    elif stable < 0.4:
        why.append("shaky")
    if expo > 0.75:
        why.append("well exposed")
    elif expo < 0.4:
        why.append("bad exposure")
    if face:
        why.append("face")
    if adb is not None and audio > 0.6:
        why.append("good audio level")
    if s.get("duration", 0) < min_duration:
        why.append(f"too short (<{min_duration:g}s)")
    return round(score, 3), why


@tool("video")
def video_select_takes(paths: list[str] = [], folder: str = "", want: str = "best", count: int = 6, per_shot: bool = True,
                       min_duration: float = 1.0, threshold: float = 0.3, project: str = "", out: str = "") -> Result:
    """Pick the good footage from a pile: analyse every clip (or every shot inside each clip when
    per_shot=true) for sharpness, stability (hand-held jitter), exposure, faces, audio level and motion,
    rank them and return the top `count` with reasons — plus a ranked contact sheet to LOOK at and
    ready-to-paste timeline clips ({"src","in","out"}) in data.picks.

    paths: video files, and/or folder: every video in it. want: best | sharp | stable | faces | talking
    (faces + clean audio) | audio | action (more motion) | calm (less motion) | broll (sharp, steady, no
    faces needed). min_duration: shots shorter than this score low. data.ranked has every unit."""
    files: list[Path] = [C.src_path(x) for x in (paths or [])]
    if folder:
        fd = Path(folder).expanduser()
        if not fd.is_dir():
            raise ToolError(f"not a folder: {fd}")
        files += sorted(f for f in fd.iterdir() if f.suffix.lower() in C.VIDEO_EXTS and not f.name.startswith("."))
    if not files:
        raise ToolError("no footage given", "paths=['a.mp4', …] or folder='/path/to/footage'")
    if want not in WANTS:
        raise ToolError(f"unknown want {want!r}", " | ".join(WANTS))
    d = C.out_folder(project, out, "select-takes")
    units, warns = [], []
    for k, f in enumerate(files[:60]):
        try:
            an = analyse(f, threshold=threshold, min_scene=max(0.5, min_duration * 0.5))
        except ToolError as e:
            warns.append(f"{f.name}: skipped ({e})")
            continue
        if not per_shot:
            sh = an["shots"]
            dd = an["info"]["duration"]
            agg = {"start": 0.0, "end": round(dd, 3), "duration": round(dd, 3),
                   "motion": round(float(np.mean([s["motion"] for s in sh])), 2),
                   "shake": round(float(np.mean([s["shake"] for s in sh])), 2)}
            an = {**an, "shots": [agg]}
        wk = d / f"f{k:02d}"
        wk.mkdir()
        for s in measure_shots(f, an, wk):
            sc, why = score_shot(s, want, min_duration)
            units.append({**s, "file": str(f), "score": sc, "why": why})
    if len(files) > 60:
        warns.append(f"only the first 60 of {len(files)} files were analysed")
    if not units:
        raise ToolError("nothing could be analysed", "; ".join(warns))
    units.sort(key=lambda u: -u["score"])
    top = units[:max(1, count)]
    tiles = []
    for r, u in enumerate(top):
        t = _shot_tile(u, Path(u["file"]).name)
        t["head"] = f"#{r + 1}  score {u['score']:.2f}  " + ", ".join(u["why"][:3])
        t["badge"] = f"RANK {r + 1}"
        tiles.append(t)
    sheet = d / "ranked.png"
    P.tile_sheet(tiles, sheet, title=f"Best takes — want={want}", cols=3 if len(tiles) > 4 else len(tiles),
                 subtitle=f"{len(units)} unit(s) from {len(files)} file(s) ranked by sharpness, stability, exposure, faces, audio")
    picks = [{"src": u["file"], "in": u["start"], "out": u["end"]} for u in top]
    for u in units:
        u.pop("thumb", None) if u not in top else None
    res = Result(f"Ranked {len(units)} {'shot' if per_shot else 'clip'}(s) from {len(files)} file(s) for want={want}. Top: "
                 + "; ".join(f"{Path(u['file']).name} {P.tc(u['start'])}–{P.tc(u['end'])} ({u['score']:.2f})" for u in top[:3]),
                 files=[str(sheet)], previews=[str(sheet)], warnings=warns,
                 data={"picks": picks, "ranked": [{k: v for k, v in u.items() if k != "thumb"} for u in units[:200]]})
    res.next_steps.append("paste data.picks into a video_edit timeline's clips (trim in/out as needed)")
    return res
