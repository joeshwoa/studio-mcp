"""Editing tools: probe, trim/cut/split/concat, the full JSON-timeline edit, overlays, B-roll, PiP, speed."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import render as R
from . import timeline as TL


@tool("video")
def video_probe(path: str, sheet: bool = True, project: str = "", out: str = "") -> Result:
    """Inspect a video before editing: duration, resolution, fps, codecs, rotation, alpha, audio
    (codec/rate/channels + measured loudness), bitrate, and a timestamped contact sheet to LOOK at.
    Returns data.info with everything ffprobe knows that matters for editing."""
    p = C.src_path(path)
    info = C.probe(p)
    lines = [f"{p.name}: {info['duration']:.2f}s"]
    if info.get("video"):
        lines.append(f"video {info['width']}x{info['height']} @ {info['fps']:g} fps, {info['codec']} {info.get('pix_fmt')}"
                     + (f", rotated {info['rotation']}°" if info.get("rotation") else "") + (", with ALPHA" if info.get("alpha") else ""))
    if info.get("audio"):
        from ...core import qc
        m = qc.loudness(p)
        info["loudness"] = {k: m.get(k) for k in ("lufs", "true_peak", "silence_pct")}
        lines.append(f"audio {info['audio_codec']} {info['sample_rate']} Hz {info['channels']} ch, "
                     f"{m.get('lufs')} LUFS, peak {m.get('true_peak')} dBTP, {m.get('silence_pct')}% silence")
    else:
        lines.append("no audio")
    res = Result("\n".join(lines), data={"info": info})
    if sheet and info.get("video"):
        dest = C.out_path(project, out, p.stem + "-probe", ".png")
        C.contact_sheet(p, dest, cols=4, rows=3)
        res.previews.append(str(dest))
        res.files.append(str(dest))
    if info.get("video") and info["fps"] and abs(info["fps"] - round(info["fps"])) > 0.01 and info["fps"] not in (23.976, 29.97, 59.94):
        res.warnings.append(f"unusual frame rate {info['fps']} (variable frame rate phone footage?) — edits conform to a constant fps")
    return res


@tool("video")
def video_edit(timeline: dict | str, brand: str = "", kdenlive: bool = True, project: str = "", out: str = "",
               name: str = "edit") -> Result:
    """The editor: render a JSON timeline to MP4 AND write the same edit as an editable Kdenlive
    project (.kdenlive, checked by rendering it headless with melt). Use for anything multi-clip:
    cuts, transitions (dissolve, dip, dip_white, wipe_*, slide_*, push, zoom, iris, blur…), J/L cuts,
    speed changes, stills with Ken Burns, colour cards, full-frame title cards, overlays (motion
    lower thirds/titles/CTAs rendered with the motion department, logos, alpha graphics, B-roll,
    picture-in-picture), music with automatic ducking, auto-captions, a programme grade and loudness.

    timeline = {"size": "1920x1080"|"9:16", "fps": 30, "clips": [{"src", "in", "out", "speed", "transition":
    {"type","duration"}, "j_cut", "l_cut", "grade", "fit", "zoom"} | {"image", "duration", "ken_burns"} |
    {"color", "duration"} | {"title", "subtitle", "duration", "style"}], "overlays": [{"type": lower_third|
    title|cta|kinetic|counter|motion|shape_wipe|overlay|image|broll|pip, "at", "duration", …}], "audio":
    [{"src", "at", "volume_db", "duck", "fade_in", "fade_out", "loop"}], "captions": {"auto": true,
    "style": reels|bold|karaoke|clean|boxed}, "grade": "teal_orange", "fade_in", "fade_out", "loudness":
    "youtube"} (a dict, JSON string or .json path; relative paths resolve next to the .json).
    brand: brand kit slug for titles/lower thirds/captions. Returns MP4, .kdenlive, .srt/.ass when
    captioned, a contact sheet and the melt check preview — LOOK at both."""
    base = None
    if isinstance(timeline, str) and not timeline.strip().startswith("{"):
        base = Path(timeline).expanduser().resolve().parent
    spec = TL.load(timeline)
    return R.render(spec, project=project, out=out, name=name or "edit", base_dir=base, brand=brand, kdenlive=kdenlive)


@tool("video")
def video_timeline_example(kind: str = "full") -> Result:
    """Example timelines for video_edit (copy, change the paths, render): kind = full (every feature) |
    talking_head (cuts + lower third + captions + music) | reel (9:16 with hook title, B-roll, captions) |
    slideshow (photos with Ken Burns and dissolves)."""
    ex = {
        "talking_head": {"size": "1920x1080", "fps": 30, "clips": [
            {"src": "/path/interview.mp4", "in": 3.2, "out": 21.0},
            {"src": "/path/interview.mp4", "in": 24.5, "out": 48.0, "zoom": 1.15, "focus": [0.5, 0.4]}],
            "overlays": [{"type": "lower_third", "at": 1.0, "duration": 5, "name": "Name Surname", "role": "Title"}],
            "audio": [{"src": "/path/music.mp3", "volume_db": -20, "duck": True, "fade_out": 2, "loop": True}],
            "captions": {"auto": True, "style": "clean"}, "grade": "clean_bright", "loudness": "youtube"},
        "reel": {"size": "9:16", "fps": 30, "clips": [
            {"src": "/path/talk.mp4", "in": 0, "out": 12, "fit": "cover", "focus": [0.5, 0.5]},
            {"src": "/path/talk.mp4", "in": 14, "out": 25, "transition": {"type": "zoom", "duration": 0.3}}],
            "overlays": [{"type": "title", "at": 0, "duration": 2.5, "text": "3 tips *nobody* tells you", "style": "minimal"},
                         {"type": "broll", "src": "/path/broll.mp4", "at": 5, "duration": 3}],
            "captions": {"auto": True, "style": "reels"}, "audio": [{"src": "/path/beat.mp3", "volume_db": -22, "duck": True}],
            "loudness": "reels"},
        "slideshow": {"size": "1920x1080", "fps": 30, "clips": [
            {"image": "/path/1.jpg", "duration": 4, "ken_burns": "in"},
            {"image": "/path/2.jpg", "duration": 4, "ken_burns": "left", "transition": {"type": "dissolve", "duration": 1}},
            {"image": "/path/3.jpg", "duration": 4, "ken_burns": "out", "transition": {"type": "dip", "duration": 1}}],
            "audio": [{"src": "/path/music.mp3", "fade_in": 1, "fade_out": 3}], "fade_in": 1, "fade_out": 1.5},
    }
    ex["full"] = {"size": "1920x1080", "fps": 30, "background": "#000000", "clips": [
        {"title": "Chapter One", "subtitle": "How it started", "duration": 3, "style": "bold"},
        {"src": "/path/a.mp4", "in": 2, "out": 8, "transition": {"type": "dip", "duration": 0.6}, "j_cut": 0.5},
        {"src": "/path/b.mp4", "in": 0, "out": 5, "speed": 1.5, "transition": {"type": "wipe_left", "duration": 0.5},
         "l_cut": 0.8, "grade": {"preset": "teal_orange", "strength": 0.7}},
        {"image": "/path/photo.jpg", "duration": 3, "ken_burns": "in", "transition": {"type": "dissolve", "duration": 0.8}},
        {"color": "#101018", "duration": 1}],
        "overlays": [{"type": "lower_third", "at": 4, "duration": 4, "name": "Joshua George", "role": "Founder"},
                     {"type": "image", "src": "/path/logo.png", "at": 0, "position": "top-right", "width": 0.1, "opacity": 0.85},
                     {"type": "shape_wipe", "at": 9.0, "shape": "diagonal"},
                     {"type": "pip", "src": "/path/cam.mp4", "at": 10, "duration": 4, "position": "bottom-right", "width": 0.28},
                     {"type": "motion", "tool": "motion_cta", "args": {"kind": "subscribe"}, "at": 12, "duration": 4}],
        "audio": [{"src": "/path/music.wav", "at": 0, "volume_db": -18, "duck": True, "fade_in": 1, "fade_out": 2, "loop": True},
                  {"src": "/path/whoosh.wav", "at": 8.7, "volume_db": -6}],
        "captions": {"auto": True, "language": "auto", "style": "clean"},
        "grade": "film_portra", "fade_in": 0.5, "fade_out": 1.0, "loudness": "youtube"}
    if kind not in ex:
        raise ToolError(f"unknown example {kind!r}", "full | talking_head | reel | slideshow")
    return Result(json.dumps(ex[kind], indent=1, ensure_ascii=False), data={"timeline": ex[kind]})


# ─────────────────────────── trim / split / concat ───────────────────────────

@tool("video")
def video_trim(path: str, start: float = 0.0, end: float = 0.0, duration: float = 0.0, mode: str = "accurate",
               project: str = "", out: str = "") -> Result:
    """Cut one section out of a video (start → end, or start + duration). mode: accurate (re-encode,
    frame-exact, default) | fast (stream copy, instant and lossless but snaps to the keyframe before
    `start`). Returns the clip + contact sheet."""
    p = C.src_path(path)
    info = C.need_video(p)
    end = end or (start + duration if duration else info["duration"])
    if not (0 <= start < end <= info["duration"] + 0.05):
        raise ToolError(f"bad range {start}–{end}s for a {info['duration']:.2f}s video")
    dest = C.out_path(project, out, f"{p.stem}-{start:g}-{end:g}", p.suffix if mode == "fast" else ".mp4")
    if mode == "fast":
        C.ff(["-ss", f"{start:.3f}", "-i", str(p), "-t", f"{end - start:.3f}", "-map", "0", "-c", "copy",
              "-avoid_negative_ts", "make_zero", str(dest)], what="trim")
    else:
        args = ["-ss", f"{start:.4f}", "-i", str(p), "-t", f"{end - start:.4f}", *C.v_encode_args(17)]
        args += C.a_encode_args() if info.get("audio") else ["-an"]
        C.ff(args + [str(dest)], what="trim")
    res = Result(f"Trimmed {p.name} {start:.2f}s → {end:.2f}s ({mode}).", files=[str(dest)],
                 data={"start": start, "end": end})
    if mode == "fast":
        res.warnings.append("fast mode cuts on keyframes: the clip may start slightly before the requested time")
    return C.finish(res, dest, expect={"duration": end - start} if mode != "fast" else None, sheet_rows=2)


@tool("video")
def video_split(path: str, at: list[float] = [], every: float = 0.0, scenes: bool = False, threshold: float = 0.35,
                project: str = "", out: str = "") -> Result:
    """Split a video into parts: at given times (at=[12.5, 40]), every N seconds (every=60), or at
    detected scene cuts (scenes=true, threshold 0.2 sensitive … 0.5 strict). Parts are frame-accurate
    re-encodes. Returns the parts, their ranges in data.parts and a contact sheet of first frames."""
    p = C.src_path(path)
    info = C.need_video(p)
    D = info["duration"]
    cuts = sorted({float(t) for t in (at or []) if 0 < float(t) < D})
    if every and every > 0:
        t = every
        while t < D - 0.2:
            cuts.append(t)
            t += every
    if scenes:
        cuts += scene_cuts(p, threshold)
    cuts = sorted({round(c, 3) for c in cuts if 0.2 < c < D - 0.2})
    if not cuts:
        raise ToolError("nothing to split at", "pass at=[…], every=<s> or scenes=true (no scene cuts found?)")
    bounds = [0.0] + cuts + [D]
    d = C.out_folder(project, out, p.stem + "-parts")
    files, parts = [], []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        f = d / f"{p.stem}-part{i + 1:02d}.mp4"
        args = ["-ss", f"{a:.4f}", "-i", str(p), "-t", f"{b - a:.4f}", *C.v_encode_args(17)]
        args += C.a_encode_args() if info.get("audio") else ["-an"]
        C.ff(args + [str(f)], what=f"split part {i + 1}")
        files.append(str(f))
        parts.append({"file": str(f), "start": round(a, 3), "end": round(b, 3)})
    from ...core.qc import sheet_of_images
    thumbs = []
    for i, f in enumerate(files[:24]):
        tp = d / f".thumb{i}.png"
        C.frame_at(f, 0.05, tp, 320)
        thumbs.append(tp)
    sheet = d / "parts-sheet.png"
    sheet_of_images(thumbs, sheet, tile=320, cols=6)
    for t in thumbs:
        t.unlink(missing_ok=True)
    return Result(f"Split {p.name} into {len(files)} parts at {', '.join(f'{c:.2f}s' for c in cuts)}.", files=files,
                  previews=[str(sheet)], data={"parts": parts, "cuts": cuts})


def scene_cuts(p: Path, threshold: float = 0.35) -> list[float]:
    import re
    import subprocess
    r = subprocess.run([C.need("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(p), "-vf",
                        f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=3600)
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]


@tool("video")
def video_concat(paths: list[str], transition: str = "cut", transition_duration: float = 0.5, size: str = "",
                 fps: float = 0, fit: str = "auto", project: str = "", out: str = "") -> Result:
    """Join clips in order into one video (any mix of sizes/fps/codecs — they are conformed to `size`/
    `fps`, default the first clip's). transition between every pair: cut | dissolve | dip | dip_white |
    wipe_left | slide_left | zoom | iris … (transition_duration seconds, audio crossfades under it).
    fit: auto | cover | contain | blur (for clips of another aspect ratio). Also writes a .kdenlive."""
    if not paths or len(paths) < 2:
        raise ToolError("need at least two clips", "paths=['a.mp4', 'b.mp4']")
    clips = []
    for i, s in enumerate(paths):
        c = {"src": str(C.src_path(s)), "fit": fit}
        if i and transition not in ("cut", "", "none"):
            c["transition"] = {"type": transition, "duration": transition_duration}
        clips.append(c)
    spec = {"clips": clips, "loudness": None}
    if size:
        spec["size"] = size
    if fps:
        spec["fps"] = fps
    return R.render(spec, project=project, out=out, name=Path(paths[0]).stem + "-joined",
                    summary=f"Joined {len(paths)} clips ({transition}).")


# ─────────────────────────── overlays / B-roll / PiP ───────────────────────────

@tool("video")
def video_overlay(path: str, overlay: str, at: float = 0.0, duration: float = 0.0, position: str = "center",
                  width: float = 0.0, opacity: float = 1.0, fade: float = 0.0, project: str = "", out: str = "") -> Result:
    """Put a graphic on top of a video: an alpha video from the motion department (.mov ProRes 4444 /
    .webm VP9 alpha — lower thirds, titles, CTAs, transitions), a PNG logo/watermark or any image.
    at/duration in seconds (duration 0 = the overlay's own length, images: to the end). position:
    center | top-left | top-right | bottom-left | bottom-right | top | bottom | left | right (used when
    width < 1: width = fraction of the frame width). Audio of the base video is kept."""
    p = C.src_path(path)
    o = C.src_path(overlay)
    kind = "image" if o.suffix.lower() in C.IMAGE_EXTS else "overlay"
    ov = {"type": kind, "src": str(o), "at": at, "position": position, "opacity": opacity, "fade": fade}
    if duration:
        ov["duration"] = duration
    if width:
        ov["width"] = width
    elif kind == "image":
        ov["width"] = 0.15
    spec = {"clips": [{"src": str(p)}], "overlays": [ov], "loudness": None}
    return R.render(spec, project=project, out=out, name=p.stem + "-overlay",
                    summary=f"Overlaid {o.name} on {p.name} at {at:g}s.")


@tool("video")
def video_broll(path: str, inserts: list[dict], fade: float = 0.2, keep_audio: bool = True, project: str = "",
                out: str = "") -> Result:
    """Insert B-roll over a main video (A-roll): inserts=[{"src": "b.mp4", "at": 5.0, "duration": 3,
    "in": 0}] — the main picture is covered for that time while the main AUDIO keeps playing (the
    classic cutaway). fade: soft edge in seconds (0 = hard cut). Set "volume_db" on an insert to mix
    its own sound in. Returns MP4 + .kdenlive + contact sheet."""
    p = C.src_path(path)
    if not inserts:
        raise ToolError("no inserts", "inserts=[{'src': 'b.mp4', 'at': 5, 'duration': 3}]")
    ovs = []
    for i in inserts:
        d = {"type": "broll", "src": str(C.src_path(i["src"])), "at": float(i.get("at", 0)), "fade": float(i.get("fade", fade))}
        for k in ("duration", "in", "volume_db"):
            if i.get(k) is not None:
                d[k] = i[k]
        ovs.append(d)
    spec = {"clips": [{"src": str(p), "mute": not keep_audio}], "overlays": ovs, "loudness": None}
    return R.render(spec, project=project, out=out, name=p.stem + "-broll",
                    summary=f"Inserted {len(ovs)} B-roll shot(s) over {p.name}.")


@tool("video")
def video_pip(path: str, inset: str, at: float = 0.0, duration: float = 0.0, position: str = "bottom-right",
              width: float = 0.3, radius: int = 24, border: str = "#FFFFFF", inset_audio_db: float | None = None,
              project: str = "", out: str = "") -> Result:
    """Picture-in-picture: `inset` (webcam, reaction, product shot) in a corner of `path` with rounded
    corners, a thin border and a soft shadow. width = fraction of the frame width (0.3); position
    top-left | top-right | bottom-left | bottom-right; duration 0 = as long as the inset lasts.
    inset_audio_db: mix the inset's own sound at this gain (default: muted)."""
    p = C.src_path(path)
    ins = C.src_path(inset)
    ov = {"type": "pip", "src": str(ins), "at": at, "position": position, "width": width, "radius": radius,
          "border": border}
    if duration:
        ov["duration"] = duration
    if inset_audio_db is not None:
        ov["volume_db"] = inset_audio_db
    spec = {"clips": [{"src": str(p)}], "overlays": [ov], "loudness": None}
    return R.render(spec, project=project, out=out, name=p.stem + "-pip", summary=f"Picture-in-picture: {ins.name} over {p.name}.")


# ─────────────────────────── speed ───────────────────────────

def _ramp_segments(D: float, ramps: list[dict], ease_steps: int = 6) -> list[tuple[float, float, float]]:
    """ramps=[{start,end,speed,ease}] → contiguous (src_start, src_end, speed) pieces, with eased
    transitions from 1× into each ramp and back (ease seconds of source time, stepped)."""
    pts: list[tuple[float, float, float]] = []
    t = 0.0
    for r in sorted(ramps, key=lambda r: float(r["start"])):
        a, b, sp = float(r["start"]), float(r["end"]), float(r.get("speed", 2.0))
        e = float(r.get("ease", 0.4))
        a, b = max(t, a), min(D, b)
        if b - a < 0.05:
            continue
        e = min(e, (b - a) / 3)
        if a > t:
            pts.append((t, a, 1.0))
        if e > 0.02:
            for k in range(ease_steps):  # ease in: 1 → sp
                s0, s1 = a + e * k / ease_steps, a + e * (k + 1) / ease_steps
                f = (k + 0.5) / ease_steps
                pts.append((s0, s1, 1.0 + (sp - 1.0) * (0.5 - 0.5 * __import__("math").cos(__import__("math").pi * f))))
            pts.append((a + e, b - e, sp))
            for k in range(ease_steps):  # ease out: sp → 1
                s0, s1 = b - e + e * k / ease_steps, b - e + e * (k + 1) / ease_steps
                f = (k + 0.5) / ease_steps
                pts.append((s0, s1, sp + (1.0 - sp) * (0.5 - 0.5 * __import__("math").cos(__import__("math").pi * f))))
        else:
            pts.append((a, b, sp))
        t = b
    if t < D:
        pts.append((t, D, 1.0))
    return [(round(a, 4), round(b, 4), round(s, 4)) for a, b, s in pts if b - a > 0.01]


@tool("video")
def video_speed(path: str, speed: float = 0.0, ramps: list[dict] = [], preset: str = "", audio: str = "auto",
                project: str = "", out: str = "") -> Result:
    """Change playback speed: constant (speed=2 → twice as fast, 0.5 → slow motion) or speed RAMPS —
    ramps=[{"start": 3, "end": 6, "speed": 4, "ease": 0.4}] (source seconds; eased in and out so it
    feels like a real ramp). preset: 'ramp_middle' (fast through the middle third). audio: auto (pitch-
    preserved time-stretch for 0.5–2×, muted beyond) | keep | mute. Also writes a .kdenlive."""
    p = C.src_path(path)
    info = C.need_video(p)
    D = info["duration"]
    if preset == "ramp_middle":
        ramps = [{"start": D / 3, "end": 2 * D / 3, "speed": 4, "ease": min(0.6, D / 12)}]
    if speed and not ramps:
        pieces = [(0.0, D, float(speed))]
    elif ramps:
        pieces = _ramp_segments(D, ramps)
    else:
        raise ToolError("pass speed=<factor> or ramps=[…] or preset='ramp_middle'")
    clips = []
    for a, b, s in pieces:
        mute = audio == "mute" or (audio == "auto" and not (0.5 <= s <= 2.0))
        clips.append({"src": str(p), "in": a, "out": b, "speed": s, "mute": mute})
    res = R.render({"clips": clips, "loudness": None}, project=project, out=out, name=f"{p.stem}-speed",
                   summary=f"Speed {'ramped' if ramps else f'{speed:g}×'}: {D:.2f}s → "
                           f"{sum((b - a) / s for a, b, s in pieces):.2f}s ({len(pieces)} piece(s)).")
    res.data["pieces"] = [{"src_start": a, "src_end": b, "speed": s} for a, b, s in pieces]
    if any(s < 1 for _, _, s in pieces):
        res.warnings.append("slow motion repeats frames (no frame interpolation); shoot at a high frame rate for smooth slow-mo")
    return res
