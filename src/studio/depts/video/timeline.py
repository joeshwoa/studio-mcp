"""The edit: a JSON timeline → one ffmpeg render (+ the same edit as a Kdenlive project, kdenlive.py).

Timeline (all times in seconds; every key optional except clips):
{
  "size": "1920x1080" | "9:16" | …,  "fps": 30,  "background": "#000000",
  "clips": [                                   # V1 storyline, played one after another
    {"src": "a.mp4", "in": 2.0, "out": 6.5,    # source in/out (out defaults to the end)
     "speed": 1.0, "volume_db": 0, "mute": false,
     "fit": "cover" | "contain" | "blur" | "auto",   "zoom": 1.0, "focus": [0.5, 0.5],
     "transition": {"type": "dissolve", "duration": 0.5},   # INTO this clip (dissolve, dip, wipe_left, slide_up, zoom, iris…)
     "j_cut": 0.0,   # this clip's AUDIO starts this many seconds before its picture
     "l_cut": 0.0,   # this clip's AUDIO continues this many seconds under the next picture
     "grade": "teal_orange" | {"preset"|"lut", "strength", "exposure", "contrast", "saturation", "temperature"}},
    {"image": "photo.jpg", "duration": 3, "ken_burns": "in" | "out" | "left" | "right" | "none"},
    {"color": "#101018", "duration": 1},
    {"title": "Chapter 1", "subtitle": "…", "duration": 3, "style": "bold", "brand": "slug"}   # full-frame motion title card
  ],
  "overlays": [                                # V2+ (drawn in order, later = on top)
    {"type": "lower_third", "at": 1, "duration": 5, "name": "Joshua George", "role": "Founder", "style": "modern"},
    {"type": "title", "at": 0.5, "duration": 3, "text": "Big idea", "style": "minimal"},
    {"type": "motion", "tool": "motion_cta", "args": {"kind": "subscribe"}, "at": 8, "duration": 4},
    {"type": "shape_wipe", "at": 6.0, "shape": "diagonal", "duration": 1.2},      # covers the frame around t=at
    {"type": "overlay", "src": "graphic.mov", "at": 2},                            # any alpha .mov/.webm/.png
    {"type": "image", "src": "logo.png", "at": 0, "duration": 30, "position": "top-right", "width": 0.12, "opacity": 0.9},
    {"type": "broll", "src": "b.mp4", "at": 4, "in": 0, "duration": 3, "fade": 0.25, "volume_db": null},
    {"type": "pip", "src": "cam.mp4", "at": 0, "duration": 10, "position": "bottom-right", "width": 0.3, "radius": 24}
  ],
  "audio": [{"src": "music.mp3", "at": 0, "in": 0, "volume_db": -18, "duck": true, "duck_db": 10,
             "fade_in": 1, "fade_out": 2, "loop": true},
            {"src": "whoosh.wav", "at": 5.8, "volume_db": -6}],
  "captions": {"auto": true, "language": "auto", "style": "reels"} | {"srt": "subs.srt", "style": "clean"} | {"words": [...]},
  "grade": "teal_orange" | {...},              # whole-programme grade (under titles)
  "fade_in": 0.5, "fade_out": 1.0,             # from/to black (+ audio)
  "loudness": "youtube" | -14 | null,          # master loudness (default: streaming -14 LUFS)
  "cut_to_beats": {"music": "song.mp3", "every": 2 | [4, 2, 2], "align": "downbeat"}   # pro_beats: clip lengths
                                               # snapped to the song's beats, song added as an audio track
}
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ...core.result import ToolError
from . import _common as C

POS = {"top-left": ("m", "m"), "top-right": ("W-w-m", "m"), "bottom-left": ("m", "H-h-m"), "bottom-right": ("W-w-m", "H-h-m"),
       "top": ("(W-w)/2", "m"), "bottom": ("(W-w)/2", "H-h-m"), "center": ("(W-w)/2", "(H-h)/2"),
       "left": ("m", "(H-h)/2"), "right": ("W-w-m", "(H-h)/2")}


@dataclass
class Clip:
    kind: str                  # video | image | color
    src: Path | None
    dur: float                 # timeline duration (after speed)
    src_in: float = 0.0
    src_out: float = 0.0
    speed: float = 1.0
    volume_db: float = 0.0
    mute: bool = False
    has_audio: bool = False
    fit: str = "auto"
    zoom: float = 1.0
    focus: tuple = (0.5, 0.5)
    trans: str = ""            # xfade name into this clip ("" = cut)
    trans_name: str = ""       # the friendly name (for Kdenlive)
    trans_dur: float = 0.0
    j: float = 0.0
    l: float = 0.0
    grade: object = None
    color: str = "#000000"
    ken_burns: str = "in"
    start: float = 0.0         # timeline start (set by layout)
    src_w: int = 0
    src_h: int = 0
    src_dur: float = 0.0
    label: str = ""
    generated: bool = False    # rendered by the studio (title card)
    rect_kf: str = ""          # Kdenlive transform keyframes (reframe)
    ax: float = 0.0            # audio micro-crossfade INTO this clip at a cut (s; e.g. 0.015 for text-based edits)
    meta: dict = field(default_factory=dict)   # what made it (motion tool + args) — native rebuilds in pro NLEs


@dataclass
class Overlay:
    kind: str                  # alpha | image | broll | pip
    src: Path
    at: float
    dur: float
    src_in: float = 0.0
    position: str = "center"
    width: float = 0.0         # fraction of frame width (0 = full frame)
    opacity: float = 1.0
    fade: float = 0.0
    radius: int = 0
    border: str = ""
    volume_db: float | None = None
    label: str = ""
    has_audio: bool = False
    meta: dict = field(default_factory=dict)   # motion tool + args → native editable graphics in pro NLEs


@dataclass
class AudioTrack:
    src: Path
    at: float = 0.0
    src_in: float = 0.0
    dur: float | None = None
    volume_db: float = 0.0
    duck: bool = False
    duck_db: float = 10.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    loop: bool = False
    src_dur: float = 0.0


@dataclass
class Timeline:
    W: int
    H: int
    fps: float
    background: str
    clips: list[Clip]
    overlays: list[Overlay] = field(default_factory=list)
    audio: list[AudioTrack] = field(default_factory=list)
    captions: dict | None = None
    grade: object = None
    fade_in: float = 0.0
    fade_out: float = 0.0
    loudness: object = "streaming"
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)
    assets: list[Path] = field(default_factory=list)   # files the studio rendered for this edit (titles…)
    assets_dir: Path | None = None


def fps_str(fps: float) -> str:
    fr = Fraction(fps).limit_denominator(1001)
    return f"{fr.numerator}/{fr.denominator}"


def _path(v, base: Path | None) -> Path:
    p = Path(str(v)).expanduser()
    if not p.is_absolute() and base is not None:
        p = base / p
    if not p.exists():
        raise ToolError(f"timeline: file not found: {p}")
    return p.resolve()


def _f(d: dict, k: str, default: float = 0.0) -> float:
    v = d.get(k, default)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ToolError(f"timeline: {k}={v!r} is not a number")


def load(spec, base_dir: str | Path | None = None) -> dict:
    """spec: dict, JSON string, or path to a .json timeline."""
    if isinstance(spec, dict):
        return spec
    s = str(spec).strip()
    if s.startswith("{"):
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise ToolError(f"timeline JSON is invalid: {e}")
    p = Path(s).expanduser()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    raise ToolError("timeline must be a dict, a JSON string or a path to a .json file")


def normalize(spec: dict, assets_dir: Path, base_dir: Path | None = None, brand: str = "") -> Timeline:
    """Validate + resolve a timeline: probe sources, compute durations and start times, render titles."""
    raw = spec.get("clips") or []
    if not raw:
        raise ToolError("timeline has no clips", "add clips: [{\"src\": \"a.mp4\"}, …]")
    warnings: list[str] = []
    # output size / fps default to the first video clip
    first = None
    for c in raw:
        if isinstance(c, str) or c.get("src") or c.get("video"):
            p = _path(c if isinstance(c, str) else (c.get("src") or c.get("video")), base_dir)
            if p.suffix.lower() not in C.IMAGE_EXTS:
                first = C.probe(p)
                break
    size = C.parse_size(spec.get("size"), (first["width"], first["height"]) if first and first.get("video") else (1920, 1080))
    W, H = size
    fps = float(spec.get("fps") or (first or {}).get("fps") or 30)
    if fps > 120 or fps < 1:
        fps = 30.0
    tl = Timeline(W, H, fps, spec.get("background", "#000000"), [], warnings=warnings, assets_dir=assets_dir)
    brand = spec.get("brand", brand)
    for i, c in enumerate(raw):
        if isinstance(c, str):
            c = {"src": c}
        if not isinstance(c, dict):
            raise ToolError(f"clip {i + 1}: expected an object")
        tr = c.get("transition")
        if isinstance(tr, str):
            tr = {"type": tr}
        tname, tdur = "", 0.0
        if tr and i > 0 and str(tr.get("type", "dissolve")).lower() not in ("cut", "none", ""):
            tname = str(tr.get("type", "dissolve"))
            tdur = float(tr.get("duration", 0.5))
        common = dict(volume_db=_f(c, "volume_db", 0.0), mute=bool(c.get("mute", False)),
                      fit=str(c.get("fit", "auto")), zoom=max(1.0, _f(c, "zoom", 1.0)),
                      focus=tuple(c.get("focus", (0.5, 0.5))), trans=C.xfade_name(tname) if tname else "",
                      trans_name=tname, trans_dur=tdur, j=max(0.0, _f(c, "j_cut", 0.0)), l=max(0.0, _f(c, "l_cut", 0.0)),
                      grade=c.get("grade"), label=c.get("label", ""),
                      ax=min(0.25, max(0.0, _f(c, "audio_crossfade", 0.0))))
        if c.get("title") is not None:
            dur = _f(c, "duration", 3.0)
            from ...core import registry
            args = {"title": c["title"], "subtitle": c.get("subtitle", ""), "kicker": c.get("kicker", ""),
                    "style": c.get("style", "bold"), "duration": dur, "size": f"{W}x{H}", "fps": int(round(fps)),
                    "brand": c.get("brand", brand), "formats": ["mp4"], "out": str(assets_dir)}
            for k in ("colors", "fonts", "align"):
                if c.get(k):
                    args[k] = c[k]
            r = registry.call("motion_title_card", args)
            mp4 = Path(next(f for f in r.files if f.endswith(".mp4")))
            tl.assets.append(mp4)
            tl.clips.append(Clip("video", mp4, dur, 0.0, dur, 1.0, has_audio=False, src_w=W, src_h=H, src_dur=dur,
                                 generated=True, meta={"tool": "motion_title_card", "type": "title_card",
                                                       "args": {k: v for k, v in args.items() if k not in ("out", "formats")},
                                                       "html": next((f for f in r.files if f.endswith(".html")), "")},
                                 **{**common, "fit": "cover"}))
            continue
        if c.get("color") is not None:
            tl.clips.append(Clip("color", None, _f(c, "duration", 1.0), color=str(c["color"]), **common))
            continue
        src = c.get("src") or c.get("video") or c.get("image")
        if not src:
            raise ToolError(f"clip {i + 1}: needs src (video/image), title or color")
        p = _path(src, base_dir)
        if p.suffix.lower() in C.IMAGE_EXTS:
            with Image.open(p) as im:
                sw, sh = im.size
            fit = str(c.get("fit", "auto"))
            if fit != "cover" and abs((sw / sh) / (W / H) - 1) >= 0.12:
                # a still of another shape (a 4:5 post in a 9:16 reel): composite it onto a full frame first
                # so Ken Burns moves the whole design instead of cropping its text off (same file in Kdenlive)
                p = _still_to_frame(p, W, H, "contain" if fit == "contain" else "blur", tl.background, assets_dir, i)
                tl.assets.append(p)
                sw, sh = W, H
            tl.clips.append(Clip("image", p, _f(c, "duration", 3.0), src_w=sw, src_h=sh,
                                 ken_burns=str(c.get("ken_burns", "in")), **common))
            continue
        info = C.probe(p)
        if not info.get("video"):
            raise ToolError(f"clip {i + 1}: {p.name} has no video (use it in \"audio\" instead)")
        sd = info["duration"]
        s_in = max(0.0, _f(c, "in", 0.0))
        s_out = _f(c, "out", 0.0) or (s_in + _f(c, "duration", 0.0) * max(0.01, _f(c, "speed", 1.0)) if c.get("duration") else sd)
        s_out = min(s_out, sd)
        if s_out - s_in < 1.0 / fps:
            raise ToolError(f"clip {i + 1} ({p.name}): in {s_in}s / out {s_out}s leaves nothing (source is {sd:.2f}s)")
        speed = _f(c, "speed", 1.0)
        if not (0.1 <= speed <= 16):
            raise ToolError(f"clip {i + 1}: speed {speed} out of range 0.1–16")
        tl.clips.append(Clip("video", p, (s_out - s_in) / speed, s_in, s_out, speed, has_audio=bool(info.get("audio")),
                             src_w=info["width"], src_h=info["height"], src_dur=sd, **common))
    # layout: transitions overlap the previous clip
    t = 0.0
    for i, c in enumerate(tl.clips):
        if i == 0:
            c.trans, c.trans_dur = "", 0.0
        if c.trans_dur > 0:
            lim = 0.45 * min(c.dur, tl.clips[i - 1].dur)
            if c.trans_dur > lim:
                warnings.append(f"clip {i + 1}: transition shortened {c.trans_dur:.2f}s → {lim:.2f}s (clips too short)")
                c.trans_dur = round(lim, 3)
            t -= c.trans_dur
        c.start = round(t, 4)
        t += c.dur
    tl.duration = round(t, 4)
    for i, c in enumerate(tl.clips):  # J/L cuts need source audio outside in/out
        if c.kind != "video" or not c.has_audio:
            c.j = c.l = 0.0
            continue
        if c.j:
            avail = c.src_in / c.speed
            if c.j > avail + 1e-6:
                warnings.append(f"clip {i + 1}: j_cut {c.j}s needs audio before its in-point; only {avail:.2f}s available")
                c.j = round(avail, 3)
            c.j = min(c.j, c.start)
        if c.l:
            avail = (c.src_dur - c.src_out) / c.speed
            if c.l > avail + 1e-6:
                warnings.append(f"clip {i + 1}: l_cut {c.l}s needs audio after its out-point; only {avail:.2f}s available")
                c.l = round(max(0.0, avail), 3)
            c.l = min(c.l, max(0.0, tl.duration - (c.start + c.dur)))
    # overlays
    for k, o in enumerate(spec.get("overlays") or []):
        tl.overlays.append(_overlay(o, k, tl, assets_dir, base_dir, brand))
    for a in spec.get("audio") or []:
        p = _path(a.get("src"), base_dir)
        info = C.probe(p)
        if not info.get("audio"):
            raise ToolError(f"audio track {p.name} has no audio stream")
        tl.audio.append(AudioTrack(p, at=_f(a, "at", 0.0), src_in=_f(a, "in", 0.0),
                                   dur=_f(a, "duration", 0.0) or None, volume_db=_f(a, "volume_db", 0.0),
                                   duck=bool(a.get("duck", False)), duck_db=_f(a, "duck_db", 10.0),
                                   fade_in=_f(a, "fade_in", 0.0), fade_out=_f(a, "fade_out", 0.0),
                                   loop=bool(a.get("loop", False)), src_dur=info["duration"]))
    # voice/dialogue tracks (not looping beds) must not be cut off by the picture ending first
    need = max([a.at + ((a.dur or (a.src_dur - a.src_in))) for a in tl.audio if not a.loop] or [0.0])
    if need > tl.duration + 0.05:
        last = tl.clips[-1]
        extra = round(need - tl.duration + 0.4, 3)  # a short breath after the last word
        if last.kind in ("image", "color") and spec.get("extend_to_audio", True):
            last.dur = round(last.dur + extra, 4)
            tl.duration = round(tl.duration + extra, 4)
            warnings.append(f"audio runs {need:.2f}s but the picture ended at {tl.duration - extra:.2f}s — "
                            f"held the last {last.kind} {extra:.2f}s longer (extend_to_audio=false to keep the cut)")
        else:
            warnings.append(f"audio runs to {need:.2f}s but the picture ends at {tl.duration:.2f}s — the audio is CUT. "
                            f"Lengthen a clip, add a still/colour/title at the end, or trim the audio")
    tl.captions = spec.get("captions") or None
    tl.grade = spec.get("grade")
    tl.fade_in, tl.fade_out = _f(spec, "fade_in", 0.0), _f(spec, "fade_out", 0.0)
    tl.loudness = spec.get("loudness", "streaming")
    return tl


MOTION_TYPES = {"title": "motion_title_card", "lower_third": "motion_lower_third", "cta": "motion_cta",
                "kinetic": "motion_kinetic_text", "kinetic_text": "motion_kinetic_text", "countdown": "motion_countdown",
                "counter": "motion_counter", "infographic": "motion_infographic", "logo": "motion_logo_reveal",
                "logo_reveal": "motion_logo_reveal", "shape_wipe": "motion_transition", "captions_motion": "motion_captions"}


def _overlay(o: dict, k: int, tl: Timeline, assets_dir: Path, base_dir: Path | None, brand: str) -> Overlay:
    typ = str(o.get("type", "overlay")).lower()
    at = _f(o, "at", 0.0)
    if typ in MOTION_TYPES or typ == "motion":
        from ...core import registry
        tool = o.get("tool") or MOTION_TYPES.get(typ)
        if not tool:
            raise ToolError(f"overlay {k + 1}: type 'motion' needs tool=<motion_… tool name>")
        args = dict(o.get("args") or {})
        for key in ("name", "role", "text", "subtitle", "kicker", "style", "position", "kind", "handle", "shape",
                    "start", "end_text", "items", "value", "label", "prefix", "suffix", "data", "tagline", "logo",
                    "colors", "fonts", "photo", "unit"):
            if key in o and key not in args:
                args[key] = o[key]
        if tool == "motion_title_card":
            args.setdefault("title", args.pop("text", "") or o.get("title", ""))
            args["transparent"] = True
        if tool in ("motion_kinetic_text", "motion_counter", "motion_infographic", "motion_logo_reveal", "motion_countdown"):
            args["transparent"] = True
        if tool == "motion_countdown":
            args.pop("duration", None)
        elif o.get("duration"):
            args["duration"] = _f(o, "duration", 3.0)
        if o.get("brand", brand) and "brand" not in args:
            args["brand"] = o.get("brand", brand)
        args.update({"size": f"{tl.W}x{tl.H}", "fps": int(round(tl.fps)), "formats": ["mov"], "out": str(assets_dir)})
        r = registry.call(tool, args)
        mov = Path(next(f for f in r.files if f.endswith(".mov")))
        tl.assets.append(mov)
        dur = C.probe(mov)["duration"]
        if typ == "shape_wipe":
            at = at - dur / 2
            if at < 0:
                tl.warnings.append(f"overlay {k + 1}: shape wipe starts before 0 — clipped")
        tl.warnings += [f"overlay {k + 1} ({tool}): {w}" for w in r.warnings if not w.startswith(("overlay on footage", "edit the"))]
        return Overlay("alpha", mov, max(0.0, at), dur, label=tool,
                       meta={"tool": tool, "type": typ, "args": {k: v for k, v in args.items() if k not in ("out", "formats")},
                             "html": next((f for f in r.files if f.endswith(".html")), ""),
                             "webm": next((f for f in r.files if f.endswith(".webm")), "")})
    src = o.get("src")
    if not src:
        raise ToolError(f"overlay {k + 1} ({typ}): needs src")
    p = _path(src, base_dir)
    if typ == "image" or p.suffix.lower() in C.IMAGE_EXTS:
        dur = _f(o, "duration", 0.0) or max(0.1, tl.duration - at)
        return Overlay("image", p, at, dur, position=o.get("position", "top-right"), width=_f(o, "width", 0.15),
                       opacity=_f(o, "opacity", 1.0), fade=_f(o, "fade", 0.3), label="image")
    info = C.probe(p)
    s_in = _f(o, "in", 0.0)
    avail = max(0.0, info["duration"] - s_in)
    dur = min(_f(o, "duration", 0.0) or avail, avail)
    if typ in ("broll", "b-roll", "insert"):
        return Overlay("broll", p, at, dur, src_in=s_in, fade=_f(o, "fade", 0.2), volume_db=o.get("volume_db"),
                       has_audio=bool(info.get("audio")), label="b-roll")
    if typ == "pip":
        return Overlay("pip", p, at, dur, src_in=s_in, position=o.get("position", "bottom-right"), width=_f(o, "width", 0.3),
                       fade=_f(o, "fade", 0.25), radius=int(_f(o, "radius", 24)), border=o.get("border", "#FFFFFF"),
                       volume_db=o.get("volume_db"), has_audio=bool(info.get("audio")), label="picture-in-picture")
    # alpha video / anything else full frame
    return Overlay("alpha", p, at, dur, src_in=s_in, fade=_f(o, "fade", 0.0), opacity=_f(o, "opacity", 1.0),
                   position=o.get("position", "center"), width=_f(o, "width", 0.0), label="overlay")


# ───────────────────────────── video graph ─────────────────────────────

def _still_to_frame(p: Path, W: int, H: int, mode: str, bg: str, assets_dir: Path, i: int) -> Path:
    from PIL import ImageFilter, ImageEnhance
    with Image.open(p) as im:
        im = im.convert("RGBA")
        scale = min(W / im.width, H / im.height)
        fg = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
        if mode == "blur":
            s2 = max(W / im.width, H / im.height)
            back = im.convert("RGB").resize((max(W, round(im.width * s2)), max(H, round(im.height * s2))), Image.LANCZOS)
            l, t = (back.width - W) // 2, (back.height - H) // 2
            back = back.crop((l, t, l + W, t + H)).filter(ImageFilter.GaussianBlur(max(W, H) / 40))
            back = ImageEnhance.Brightness(back).enhance(0.8)
        else:
            back = Image.new("RGB", (W, H), bg)
        back.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2), fg)
    assets_dir.mkdir(parents=True, exist_ok=True)
    out = assets_dir / f"still-{i + 1}-{p.stem[:40]}-{W}x{H}.png"
    back.save(out)
    return out


def _fit_chain(c: Clip, W: int, H: int, bg: str, lbl_in: str, lbl_out: str, n: int) -> str:
    """Scale/crop/pad one source into W×H according to fit/zoom/focus."""
    fit = c.fit
    if fit == "auto":
        if c.src_w and c.src_h:
            ar_s, ar_t = c.src_w / c.src_h, W / H
            fit = "cover" if abs(ar_s / ar_t - 1) < 0.12 else "blur"
        else:
            fit = "cover"
    z = c.zoom
    fx, fy = (float(c.focus[0]), float(c.focus[1])) if len(c.focus) == 2 else (0.5, 0.5)
    if fit == "contain":
        return (f"[{lbl_in}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={bg}[{lbl_out}]")
    if fit == "blur":
        return (f"[{lbl_in}]split[bgs{n}][fgs{n}];[bgs{n}]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
                f"crop={W // 4}:{H // 4},gblur=sigma=12,eq=brightness=-0.06:saturation=1.1,scale={W}:{H}[bgb{n}];"
                f"[fgs{n}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fgf{n}];"
                f"[bgb{n}][fgf{n}]overlay=(W-w)/2:(H-h)/2[{lbl_out}]")
    sw, sh = int(W * z) + (int(W * z) % 2), int(H * z) + (int(H * z) % 2)
    return (f"[{lbl_in}]scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{H}:(iw-{W})*{fx:.4f}:(ih-{H})*{fy:.4f}[{lbl_out}]")


def _kenburns(c: Clip, W: int, H: int, fps: float) -> str:
    n = max(1, int(round(c.dur * fps)))
    kb = (c.ken_burns or "in").lower()
    zmax = 1.12
    if kb == "none":
        return f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}"
    z = {"in": f"1+{zmax - 1}*on/{n}", "out": f"{zmax}-{zmax - 1}*on/{n}"}.get(kb, f"{zmax}")
    x = {"left": f"(iw-iw/zoom)*(1-on/{n})", "right": f"(iw-iw/zoom)*on/{n}"}.get(kb, "iw/2-(iw/zoom/2)")
    y = "ih/2-(ih/zoom/2)"
    # oversample 2× before zoompan so the slow move is smooth (no 1-px stepping)
    return (f"scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase:flags=lanczos,crop={2 * W}:{2 * H},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps_str(fps)}")


def build_video_graph(tl: Timeline, sub_file: Path | None = None, fonts_dir: Path | None = None) -> tuple[list[str], str, str]:
    """→ (input args, filter_complex script, output label). Video only (audio is rendered separately)."""
    W, H, fps = tl.W, tl.H, tl.fps
    fr = fps_str(fps)
    ins: list[str] = []
    fl: list[str] = []
    idx = 0

    def add_input(args: list[str]) -> int:
        nonlocal idx
        ins.extend(args)
        idx += 1
        return idx - 1

    labels = []
    for n, c in enumerate(tl.clips):
        out = f"c{n}"
        if c.kind == "color":
            k = add_input(["-f", "lavfi", "-i", f"color=c={c.color}:s={W}x{H}:r={fr}:d={c.dur:.4f}"])
            fl.append(f"[{k}:v]format=yuv420p,setsar=1[{out}]")
        elif c.kind == "image":
            k = add_input(["-loop", "1", "-framerate", fr, "-t", f"{c.dur:.4f}", "-i", str(c.src)])
            fl.append(f"[{k}:v]format=yuv444p,{_kenburns(c, W, H, fps)},trim=duration={c.dur:.4f},setpts=PTS-STARTPTS,"
                      f"fps={fr},format=yuv420p,setsar=1[{out}]")
        else:
            span = c.src_out - c.src_in
            k = add_input(["-ss", f"{c.src_in:.4f}", "-t", f"{span + 0.1:.4f}", "-i", str(c.src)])
            sp = f",setpts=(PTS-STARTPTS)/{c.speed:.5f}" if abs(c.speed - 1) > 1e-6 else ""
            fl.append(f"[{k}:v]trim=duration={span:.4f},setpts=PTS-STARTPTS{sp},fps={fr}[r{n}]")
            fl.append(_fit_chain(c, W, H, tl.background, f"r{n}", f"f{n}", n))
            fl.append(f"[f{n}]trim=duration={c.dur:.4f},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[{out}]")
        g = C.grade_filter(c.grade) if c.grade else ""
        if g:
            fl.append(f"[{out}]format=gbrp,{g.replace('_ga', f'_ga{n}').replace('_gb', f'_gb{n}').replace('_gc', f'_gc{n}')},format=yuv420p[{out}g]")
            out = out + "g"
        labels.append(out)
    # join: xfade for transitions, concat for cuts
    acc = labels[0]
    for n in range(1, len(tl.clips)):
        c = tl.clips[n]
        nxt = labels[n]
        if c.trans and c.trans_dur > 0:
            fl.append(f"[{acc}][{nxt}]xfade=transition={c.trans}:duration={c.trans_dur:.4f}:offset={c.start:.4f}[j{n}]")
        else:
            fl.append(f"[{acc}][{nxt}]concat=n=2:v=1:a=0[j{n}]")
        acc = f"j{n}"
    g = C.grade_filter(tl.grade) if tl.grade else ""
    if g:
        fl.append(f"[{acc}]format=gbrp,{g},format=yuv420p[pg]")
        acc = "pg"
    # overlays
    for n, o in enumerate(tl.overlays):
        end = o.at + o.dur
        if o.kind == "image":
            k = add_input(["-loop", "1", "-framerate", fr, "-t", f"{o.dur:.4f}", "-i", str(o.src)])
            w = int(W * o.width) if o.width else W
            chain = f"[{k}:v]format=rgba,scale={w}:-2:flags=lanczos"
            if o.opacity < 1:
                chain += f",colorchannelmixer=aa={o.opacity:.3f}"
        elif o.kind == "alpha":
            k = add_input([*C.webm_alpha_decoder(o.src), "-ss", f"{o.src_in:.4f}", "-t", f"{o.dur:.4f}", "-i", str(o.src)])
            w = int(W * o.width) if o.width else W
            chain = f"[{k}:v]format=rgba,scale={w}:{'-2' if o.width else H}:flags=lanczos,fps={fr}"
            if o.opacity < 1:
                chain += f",colorchannelmixer=aa={o.opacity:.3f}"
        elif o.kind == "broll":
            k = add_input(["-ss", f"{o.src_in:.4f}", "-t", f"{o.dur + 0.1:.4f}", "-i", str(o.src)])
            bc = Clip("video", o.src, o.dur, fit="cover")
            info = C.probe(o.src)
            bc.src_w, bc.src_h = info["width"], info["height"]
            fl.append(f"[{k}:v]trim=duration={o.dur:.4f},setpts=PTS-STARTPTS,fps={fr}[br{n}]")
            fl.append(_fit_chain(bc, W, H, tl.background, f"br{n}", f"brf{n}", 1000 + n))
            chain = f"[brf{n}]format=rgba"
        else:  # pip
            k = add_input(["-ss", f"{o.src_in:.4f}", "-t", f"{o.dur + 0.1:.4f}", "-i", str(o.src)])
            info = C.probe(o.src)
            pw = int(W * (o.width or 0.3)) // 2 * 2
            ph = int(pw * info["height"] / max(1, info["width"])) // 2 * 2
            b = max(0, int(round(min(W, H) * 0.004))) if o.border else 0
            mask, frame, pad = _pip_assets(pw, ph, o.radius, b, o.border or "#FFFFFF", tl.assets_dir, n)
            km = add_input(["-loop", "1", "-framerate", fr, "-t", f"{o.dur:.4f}", "-i", str(mask)])
            kf = add_input(["-loop", "1", "-framerate", fr, "-t", f"{o.dur:.4f}", "-i", str(frame)])
            fl.append(f"[{k}:v]trim=duration={o.dur:.4f},setpts=PTS-STARTPTS,fps={fr},scale={pw}:{ph}:flags=lanczos,format=rgba[pv{n}]")
            fl.append(f"[{km}:v]format=gray,scale={pw}:{ph}[pm{n}]")
            fl.append(f"[pv{n}][pm{n}]alphamerge[pa{n}]")
            fl.append(f"[{kf}:v]format=rgba[pfr{n}]")
            fl.append(f"[pfr{n}][pa{n}]overlay={pad}:{pad}:format=auto[pp{n}]")
            chain = f"[pp{n}]format=rgba"
        if o.fade:
            chain += f",fade=t=in:st=0:d={o.fade:.3f}:alpha=1,fade=t=out:st={max(0.0, o.dur - o.fade):.3f}:d={o.fade:.3f}:alpha=1"
        chain += f",setpts=PTS-STARTPTS+{o.at:.4f}/TB[ov{n}]"
        fl.append(chain)
        m = int(min(W, H) * 0.045)
        if o.kind == "pip":
            m = int(min(W, H) * 0.035)
        px, py = POS.get(o.position, POS["center"]) if (o.kind in ("image", "pip") or o.width) else ("0", "0")
        px, py = px.replace("m", str(m)), py.replace("m", str(m))
        fl.append(f"[{acc}][ov{n}]overlay=x={px}:y={py}:eof_action=pass:format=auto:"
                  f"enable='between(t,{o.at:.4f},{end:.4f})'[o{n}]")
        acc = f"o{n}"
    if sub_file:
        fd = f":fontsdir='{C.ff_path(fonts_dir)}'" if fonts_dir else ""
        fl.append(f"[{acc}]ass=filename='{C.ff_path(sub_file)}'{fd}[subs]")
        acc = "subs"
    tail = []
    if tl.fade_in:
        tail.append(f"fade=t=in:st=0:d={tl.fade_in:.3f}")
    if tl.fade_out:
        tail.append(f"fade=t=out:st={max(0.0, tl.duration - tl.fade_out):.3f}:d={tl.fade_out:.3f}")
    tail.append(f"trim=duration={tl.duration:.4f},setpts=PTS-STARTPTS,format=yuv420p")
    fl.append(f"[{acc}]{','.join(tail)}[vout]")
    return ins, ";\n".join(fl), "vout"


def _pip_assets(pw: int, ph: int, radius: int, border: int, color: str, d: Path, n: int) -> tuple[Path, Path, int]:
    """Rounded-corner alpha mask for the inset and a frame (border + soft shadow) it sits on."""
    d.mkdir(parents=True, exist_ok=True)
    ss = 4
    m = Image.new("L", (pw * ss, ph * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, pw * ss - 1, ph * ss - 1], radius=radius * ss, fill=255)
    m = m.resize((pw, ph), Image.LANCZOS)
    mp = d / f"pip-mask-{n}.png"
    m.save(mp)
    pad = int(min(pw, ph) * 0.06) + border
    fw, fh = pw + 2 * pad, ph + 2 * pad
    fw, fh = fw + fw % 2, fh + fh % 2
    frame = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
    sh = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad - border, pad - border + pad // 3, pad + pw + border, pad + ph + border + pad // 3],
                                         radius=radius + border, fill=(0, 0, 0, 120))
    sh = sh.filter(ImageFilter.GaussianBlur(pad / 2.5))
    frame.alpha_composite(sh)
    if border:
        ImageDraw.Draw(frame).rounded_rectangle([pad - border, pad - border, pad + pw + border - 1, pad + ph + border - 1],
                                                radius=radius + border, fill=color)
    fp = d / f"pip-frame-{n}.png"
    frame.save(fp)
    return mp, fp, pad


# ───────────────────────────── audio graph ─────────────────────────────

def _atempo(speed: float) -> str:
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return ",".join(parts)


def build_dialog_graph(tl: Timeline) -> tuple[list[str], str, str] | None:
    """Main-track audio (with J/L cuts, crossfades under transitions, per-clip volume) + B-roll/PiP audio
    + non-ducked extra audio tracks. None when the edit has no such audio."""
    ins: list[str] = []
    fl: list[str] = []
    labs: list[str] = []
    idx = 0
    AF = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    for n, c in enumerate(tl.clips):
        if c.kind != "video" or not c.has_audio or c.mute:
            continue
        # audio_crossfade: on a plain cut, start this clip's sound `ax` early (pre-roll from the source) and
        # crossfade it with the end of the previous clip — no clicks, no dip (needs source audio before `in`)
        ext = c.ax if (n > 0 and c.ax and not c.j and not c.trans_dur and c.src_in / c.speed >= c.ax and c.start >= c.ax) else 0.0
        a_in = c.src_in - (c.j + ext) * c.speed
        a_out = min(c.src_dur, c.src_out + c.l * c.speed)
        ins += ["-ss", f"{max(0.0, a_in):.4f}", "-t", f"{a_out - a_in + 0.05:.4f}", "-i", str(c.src)]
        k = idx
        idx += 1
        dur_tl = (a_out - a_in) / c.speed
        chain = f"[{k}:a]{AF},atrim=duration={a_out - a_in:.4f},asetpts=PTS-STARTPTS"
        if abs(c.speed - 1) > 1e-6:
            chain += "," + _atempo(c.speed)
        if c.volume_db:
            chain += f",volume={c.volume_db:.2f}dB"
        # fades: transition crossfades, gentle 0.25 s ramps on J/L extensions, 10 ms anti-click elsewhere
        fin = c.trans_dur if c.trans_dur else (0.25 if c.j else (ext or 0.012))
        nxt = tl.clips[n + 1] if n + 1 < len(tl.clips) else None
        nx_ax = nxt.ax if (nxt and nxt.ax and not nxt.j and not nxt.trans_dur and nxt.kind == "video") else 0.0
        fout = (nxt.trans_dur if nxt and nxt.trans_dur else 0.0) or (0.25 if c.l else (nx_ax or 0.012))
        chain += f",afade=t=in:st=0:d={fin:.3f},afade=t=out:st={max(0.0, dur_tl - fout):.3f}:d={fout:.3f}"
        delay = max(0.0, c.start - c.j - ext)
        chain += f",adelay={int(round(delay * 1000))}:all=1[a{n}]"
        fl.append(chain)
        labs.append(f"a{n}")
    for n, o in enumerate(tl.overlays):
        if o.kind in ("broll", "pip") and o.has_audio and o.volume_db is not None:
            ins += ["-ss", f"{o.src_in:.4f}", "-t", f"{o.dur + 0.05:.4f}", "-i", str(o.src)]
            k = idx
            idx += 1
            fl.append(f"[{k}:a]{AF},atrim=duration={o.dur:.4f},asetpts=PTS-STARTPTS,volume={float(o.volume_db):.2f}dB,"
                      f"afade=t=in:d=0.15,afade=t=out:st={max(0.0, o.dur - 0.15):.3f}:d=0.15,"
                      f"adelay={int(round(o.at * 1000))}:all=1[ob{n}]")
            labs.append(f"ob{n}")
    for n, a in enumerate(tl.audio):
        if a.duck:
            continue
        lab = _music_chain(a, tl, ins, fl, idx, f"x{n}")
        idx += 1
        labs.append(lab)
    if not labs:
        return None
    if len(labs) == 1:
        fl.append(f"[{labs[0]}]asetpts=N/SR/TB,apad,atrim=duration={tl.duration:.4f}[aout]")
    else:
        fl.append("".join(f"[{l}]" for l in labs) + f"amix=inputs={len(labs)}:duration=longest:normalize=0:dropout_transition=0,"
                  f"asetpts=N/SR/TB,apad,atrim=duration={tl.duration:.4f}[aout]")
    return ins, ";\n".join(fl), "aout"


def _music_chain(a: AudioTrack, tl: Timeline, ins: list[str], fl: list[str], k: int, lab: str) -> str:
    AF = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    avail_tl = tl.duration - a.at
    dur = min(a.dur or avail_tl, avail_tl)
    if a.loop:
        ins += ["-stream_loop", "-1"]
    ins += ["-ss", f"{a.src_in:.4f}", "-i", str(a.src)]
    if not a.loop:
        dur = min(dur, a.src_dur - a.src_in)
    chain = f"[{k}:a]{AF},atrim=duration={max(0.05, dur):.4f},asetpts=PTS-STARTPTS"
    if a.volume_db:
        chain += f",volume={a.volume_db:.2f}dB"
    if a.fade_in:
        chain += f",afade=t=in:st=0:d={a.fade_in:.3f}"
    if a.fade_out:
        chain += f",afade=t=out:st={max(0.0, dur - a.fade_out):.3f}:d={a.fade_out:.3f}"
    chain += f",adelay={int(round(a.at * 1000))}:all=1[{lab}]"
    fl.append(chain)
    return lab


def build_bed_graph(a: AudioTrack, tl: Timeline) -> tuple[list[str], str, str]:
    """A ducked music bed prepared to the programme length (placed at `at`, faded) for audio_mix."""
    ins: list[str] = []
    fl: list[str] = []
    lab = _music_chain(AudioTrack(**{**a.__dict__, "volume_db": 0.0}), tl, ins, fl, 0, "m")
    fl.append(f"[{lab}]asetpts=N/SR/TB,apad,atrim=duration={tl.duration:.4f}[bed]")
    return ins, ";\n".join(fl), "bed"
