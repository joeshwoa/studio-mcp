"""Timeline → Kdenlive project (.kdenlive = MLT XML, Kdenlive document version 1.1) + headless check with
melt. The project uses Kdenlive's own conventions so it opens as an editable timeline:

- bin producers with kdenlive:id, a main_bin playlist, black_track background;
- one tractor per track holding TWO playlists; a transition between two main clips is a same-track
  "mix" (the incoming clip sits on the track's second playlist and a luma transition blends them),
  exactly how Kdenlive ≥ 20.12 stores mixes;
- internal track compositing (qtblend, video) and summing (mix, audio) on the main tractor;
- per-clip effects inside the timeline <entry>: LUT (avfilter.lut3d), Ken Burns (qtblend rect keyframes),
  fades (brightness / volume), speed via timewarp producers;
- burned captions as an avfilter.subtitles filter on the main tractor pointing at the .ass file.

Kdenlive-only niceties (keyframe curves, effect-stack UI names) are approximations; melt renders the
file to prove the MLT graph is valid and matches the ffmpeg render."""
from __future__ import annotations

import math
import re
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import numpy as np
from PIL import Image

from ...core import qc
from ...core.deps import find_bin, DEPS
from ...core.result import ToolError
from . import _common as C
from . import timeline as TL

LAST_NOTES: list[str] = []


class _W:
    def __init__(self, fps: float):
        self.fps = fps
        self.lines: list[str] = []
        self.pid = 0

    def f(self, t: float) -> int:
        return int(round(t * self.fps))

    def add(self, s: str):
        self.lines.append(s)


def _props(d: dict, ind: str = "  ") -> str:
    return "".join(f"{ind}<property name={quoteattr(str(k))}>{escape(str(v))}</property>\n" for k, v in d.items() if v is not None)


def _color(hexcol: str) -> str:
    h = hexcol.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "0x" + h[:6].lower() + "ff"


def write(tl: TL.Timeline, dest: Path, audio_duck: list | None = None, subtitles: Path | None = None,
          fonts_dir: Path | None = None) -> Path:
    LAST_NOTES.clear()
    w = _W(tl.fps)
    fr = TL.Fraction(tl.fps).limit_denominator(1001)
    total = max(1, w.f(tl.duration))
    kid = [2]  # kdenlive bin ids (1 is reserved for the root folder)
    bin_entries: list[str] = []
    producers: list[str] = []

    def producer(kind: str, src: Path | None, length: int, extra: dict | None = None, name: str = "", speed: float = 1.0,
                 color: str = "") -> tuple[str, int]:
        pid = f"producer{w.pid}"
        w.pid += 1
        k = kid[0]
        kid[0] += 1
        props: dict = {"length": length, "eof": "pause"}
        if kind == "color":
            props.update({"resource": _color(color), "mlt_service": "color", "mlt_image_format": "rgba",
                          "kdenlive:clip_type": 2})
        elif kind == "image":
            props.update({"resource": str(src), "mlt_service": "qimage", "ttl": 25, "kdenlive:clip_type": 5,
                          "kdenlive:duration": length})
        elif abs(speed - 1) > 1e-6:
            props.update({"resource": f"{speed:.6g}:{src}", "mlt_service": "timewarp", "warp_speed": f"{speed:.6g}",
                          "warp_resource": str(src), "warp_pitch": 1, "seekable": 1, "kdenlive:clip_type": 0,
                          "kdenlive:originalurl": str(src)})
        else:
            props.update({"resource": str(src), "mlt_service": "avformat-novalidate", "seekable": 1, "kdenlive:clip_type": 0})
        props.update({"kdenlive:id": k, "kdenlive:clipname": name or (src.name if src else color), "kdenlive:folderid": -1})
        props.update(extra or {})
        producers.append(f' <producer id="{pid}" in="0" out="{max(0, length - 1)}">\n{_props(props)} </producer>\n')
        bin_entries.append(f'  <entry producer="{pid}" in="0" out="{max(0, length - 1)}"/>\n')
        return pid, k

    def entry(pid: str, k: int, a: int, b: int, filters: str = "") -> str:
        return (f'  <entry producer="{pid}" in="{a}" out="{max(a, b)}">\n   <property name="kdenlive:id">{k}</property>\n'
                f'{filters}  </entry>\n')

    def blank(n: int) -> str:
        return f'  <blank length="{n}"/>\n' if n > 0 else ""

    # ── main video track V1: two playlists (A = clips, B = incoming side of mixes)
    plA, plB, cursorA, cursorB = [], [], 0, 0
    mixes = []
    on_b = False
    clip_prod = []
    for n, c in enumerate(tl.clips):
        L = w.f(c.dur)
        st = w.f(c.start)
        if c.kind == "color":
            pid, k = producer("color", None, max(L, 1), color=c.color, name=f"colour {c.color}")
            a = 0
        elif c.kind == "image":
            pid, k = producer("image", c.src, max(L, 1) + 1)
            a = 0
        else:
            full = max(1, int(math.ceil(c.src_dur * tl.fps / c.speed)))
            pid, k = producer("video", c.src, full, speed=c.speed, extra={"kdenlive:clipname": c.src.name})
            a = int(round(c.src_in * tl.fps / c.speed))
        clip_prod.append((pid, k, a))
        filt = ""
        if c.rect_kf:
            filt += ('   <filter in="%d" out="%d">\n' % (a, a + L - 1) + _props({"mlt_service": "qtblend", "kdenlive_id": "qtblend",
                     "rect": c.rect_kf, "compositing": 0, "distort": 0, "rotate_center": 0}, "    ") + "   </filter>\n")
        elif c.kind == "image" and (c.ken_burns or "in") != "none":
            filt += _kenburns_filter(c, tl, L)
        g = c.grade
        if g:
            filt += _lut_filter(g, a, a + L - 1)
        if n == 0 and tl.fade_in:
            filt += _fade_video(a, a + w.f(tl.fade_in), True)
        if n == len(tl.clips) - 1 and tl.fade_out:
            filt += _fade_video(a + L - w.f(tl.fade_out), a + L - 1, False)
        if c.trans and n > 0:
            on_b = not on_b
            mixes.append((st, st + w.f(c.trans_dur), on_b, c.trans_name))
        if on_b:
            plB.append(blank(st - cursorB) + entry(pid, k, a, a + L - 1, filt))
            cursorB = st + L
        else:
            plA.append(blank(st - cursorA) + entry(pid, k, a, a + L - 1, filt))
            cursorA = st + L
        if not c.rect_kf and (c.kind == "video" and c.fit not in ("cover", "auto") or (c.src_w and abs(c.src_w / max(1, c.src_h) - tl.W / tl.H) > 0.12)):
            if c.kind == "video" and not c.generated:
                LAST_NOTES.append(f"Kdenlive: clip {n + 1} has a different aspect ratio — Kdenlive letterboxes it "
                                  "(the ffmpeg render used fit='" + c.fit + "'); adjust with the Transform effect")
        if c.zoom > 1.0 and not c.rect_kf:  # punch-in → Transform effect
            fx, fy = (float(c.focus[0]), float(c.focus[1])) if len(c.focus) == 2 else (0.5, 0.5)
            zw, zh = tl.W * c.zoom, tl.H * c.zoom
            filt_z = ('   <filter in="%d" out="%d">\n' % (a, a + L - 1) + _props({"mlt_service": "qtblend", "kdenlive_id": "qtblend",
                      "rect": f"{-(zw - tl.W) * fx:.1f} {-(zh - tl.H) * fy:.1f} {zw:.1f} {zh:.1f} 1", "compositing": 0,
                      "distort": 0, "rotate_center": 0}, "    ") + "   </filter>\n")
            tgt = plB if on_b else plA
            tgt[-1] = tgt[-1].replace("  </entry>\n", filt_z + "  </entry>\n")
    tracks = []  # (tractor xml id, xml, is_audio)
    trk = 0

    def track_tractor(pls: list[list[str]], audio: bool, inner: str = "", name: str = "", filt: str = "") -> str:
        nonlocal trk
        ids = []
        for p in pls:
            pid = f"playlist{len(tracks) * 2 + len(ids)}"
            ids.append(pid)
            producers.append(f' <playlist id="{pid}">\n{"".join(p)} </playlist>\n')
        tid = f"tractor{trk}"
        trk += 1
        hide = "video" if audio else "audio"
        props = {"kdenlive:trackheight": 67, "kdenlive:timeline_active": 1, "kdenlive:collapsed": 0,
                 "kdenlive:track_name": name, "kdenlive:thumbs_format": None}
        if audio:
            props["kdenlive:audio_track"] = 1
        xml = (f' <tractor id="{tid}" in="0" out="{total - 1}">\n{_props(props)}'
               + "".join(f'  <track hide="{hide}" producer="{i}"/>\n' for i in ids) + inner + filt + " </tractor>\n")
        tracks.append((tid, xml, audio))
        return tid

    mix_xml = ""
    for (a, b, into_b, name) in mixes:
        mix_xml += (f'  <transition in="{a}" out="{max(a, b - 1)}">\n' + _props({
            "a_track": 0, "b_track": 1, "mlt_service": "luma", "kdenlive_id": "luma", "kdenlive:mixcut": 0,
            "reverse": 0 if into_b else 1, "softness": 0, "alpha_over": 1, "fix_background_alpha": 1}, "   ") + "  </transition>\n")
        if name and C.xfade_name(name) not in ("fade", "dissolve"):
            LAST_NOTES.append(f"Kdenlive: '{name}' transition is written as a dissolve mix (change its wipe in Kdenlive)")
    vgrade = _lut_filter(tl.grade, 0, total - 1, " ") if tl.grade else ""
    v1 = track_tractor([plA, plB], False, mix_xml, "V1", vgrade)
    # ── audio of the main clips: alternate A1/A2 so J/L overlaps and crossfades never collide
    aA, aB, ca, cb = [], [], 0, 0
    use_b = False
    prev_end = -1
    for n, c in enumerate(tl.clips):
        if c.kind != "video" or not c.has_audio or c.mute:
            continue
        pid, k, a0 = clip_prod[n]
        st = w.f(max(0.0, c.start - c.j))
        a = a0 - w.f(c.j)
        L = w.f(c.dur + c.j + c.l)
        if st < prev_end:
            use_b = not use_b
        fin = w.f(c.trans_dur or (0.25 if c.j else 0.0))
        nxt = tl.clips[n + 1] if n + 1 < len(tl.clips) else None
        fout = w.f((nxt.trans_dur if nxt and nxt.trans_dur else 0.0) or (0.25 if c.l else 0.0))
        filt = _vol(c.volume_db, a, a + L - 1) if c.volume_db else ""
        if fin:
            filt += _afade(a, a + fin, True)
        if fout:
            filt += _afade(a + L - fout, a + L - 1, False)
        if n == 0 and tl.fade_in:
            filt += _afade(a, a + w.f(tl.fade_in), True)
        if n == len(tl.clips) - 1 and tl.fade_out:
            filt += _afade(a + L - w.f(tl.fade_out), a + L - 1, False)
        if use_b:
            aB.append(blank(st - cb) + entry(pid, k, a, a + L - 1, filt))
            cb = st + L
        else:
            aA.append(blank(st - ca) + entry(pid, k, a, a + L - 1, filt))
            ca = st + L
        prev_end = st + L
    audio_tracks = []
    if aA or aB:
        audio_tracks.append(track_tractor([aA, []], True, name="A1 dialogue"))
        if aB:
            audio_tracks.append(track_tractor([aB, []], True, name="A2 dialogue"))
    # ── extra audio (music / sfx)
    for i, at in enumerate(tl.audio):
        length = max(1, int(math.ceil(at.src_dur * tl.fps)))
        pid, k = producer("audio", at.src, length, name=at.src.name)
        st = w.f(at.at)
        dur = w.f(min(at.dur or (tl.duration - at.at), tl.duration - at.at))
        a0 = w.f(at.src_in)
        ents = []
        pos = 0
        remaining = dur
        cur = st
        first = True
        while remaining > 0:  # loop music by repeating entries
            seg = min(remaining, length - a0) if (at.loop or first) else 0
            if seg <= 0:
                break
            filt = ""
            if at.duck and audio_duck:
                filt += _duck_filter(at, audio_duck, cur, a0, a0 + seg - 1, w, tl)
            elif at.volume_db:
                filt += _vol(at.volume_db, a0, a0 + seg - 1)
            if first and at.fade_in:
                filt += _afade(a0, a0 + w.f(at.fade_in), True)
            if remaining - seg <= 0 and at.fade_out:
                filt += _afade(a0 + seg - w.f(at.fade_out), a0 + seg - 1, False)
            ents.append(blank(cur - pos) + entry(pid, k, a0, a0 + seg - 1, filt))
            pos = cur + seg
            cur += seg
            remaining -= seg
            a0 = 0
            first = False
            if not at.loop:
                break
        audio_tracks.append(track_tractor([ents, []], True, name=("music" if at.duck else f"A{3 + i}")))
    if any(a.duck for a in tl.audio):
        LAST_NOTES.append("Kdenlive: music ducking is written as volume keyframes from the rendered mix's duck regions; "
                          "levels are approximate (the MP4 has the mastered mix)")
    # ── overlays: one video track each (V2, V3 …)
    ov_tracks = []
    for n, o in enumerate(tl.overlays):
        st = w.f(o.at)
        L = max(1, w.f(o.dur))
        if o.kind == "image":
            pid, k = producer("image", o.src, L + 1, name=o.src.name)
            a = 0
        else:
            info = C.probe(o.src)
            pid, k = producer("video", o.src, max(L, int(math.ceil(info["duration"] * tl.fps))), name=o.src.name)
            a = w.f(o.src_in)
        filt = _transform_filter(o, tl, a, a + L - 1)
        ov_tracks.append(track_tractor([[blank(st) + entry(pid, k, a, a + L - 1, filt)], []], False,
                                       name=f"V{n + 2} {o.label}"))
        if o.fade:
            LAST_NOTES.append(f"Kdenlive: overlay {n + 1} fades are not carried over (add Fade in/out effects)")
        if o.kind == "pip" and o.radius:
            LAST_NOTES.append(f"Kdenlive: picture-in-picture {n + 1} has square corners (rounded corners/border exist only in the MP4)")
    # ── main tractor: black, audio tracks, V1, overlays
    order = ["black_track"] + audio_tracks[::-1] + [v1] + ov_tracks
    trans = ""
    for i, tid in enumerate(order):
        if i == 0:
            continue
        is_audio = tid in audio_tracks
        if is_audio:
            trans += (" <transition>\n" + _props({"a_track": 0, "b_track": i, "mlt_service": "mix", "kdenlive_id": "mix",
                                                   "internal_added": 237, "always_active": 1, "accepts_blanks": 1, "sum": 1}, "  ")
                      + " </transition>\n")
        else:
            trans += (" <transition>\n" + _props({"a_track": 0, "b_track": i, "compositing": 0, "distort": 0,
                                                   "rotate_center": 0, "mlt_service": "qtblend", "kdenlive_id": "qtblend",
                                                   "internal_added": 237, "always_active": 1}, "  ") + " </transition>\n")
    subf = ""
    if subtitles:
        subf = " <filter>\n" + _props({"mlt_service": "avfilter.subtitles", "internal_added": 237, "av.filename": str(subtitles),
                                        "av.fontsdir": str(fonts_dir) if fonts_dir else None, "kdenlive:locked": 1}, "  ") + " </filter>\n"
    profile = (f' <profile description="{tl.W}x{tl.H} {float(fr):g} fps" width="{tl.W}" height="{tl.H}" progressive="1" '
               f'sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="{tl.W}" display_aspect_den="{tl.H}" '
               f'frame_rate_num="{fr.numerator}" frame_rate_den="{fr.denominator}" colorspace="709"/>\n')
    black = (f' <producer id="black_track" in="0" out="{total - 1}">\n' + _props({
        "length": total, "eof": "continue", "resource": "0x000000ff", "aspect_ratio": 1, "mlt_service": "color",
        "kdenlive:playlistid": "black_track", "mlt_image_format": "rgba", "set.test_audio": 0}) + " </producer>\n")
    main_bin = (' <playlist id="main_bin">\n' + _props({
        "kdenlive:docproperties.version": "1.1", "kdenlive:docproperties.kdenliveversion": "23.08.5",
        "kdenlive:docproperties.audioChannels": 2, "kdenlive:docproperties.enableproxy": 0,
        "kdenlive:docproperties.seekOffset": 30000, "kdenlive:docproperties.position": 0,
        "kdenlive:docproperties.documentid": str(abs(hash(str(dest))) % 10 ** 13), "xml_retain": 1}) + "".join(bin_entries) + " </playlist>\n")
    main = (f' <tractor id="maintractor" global_feed="1" in="0" out="{total - 1}">\n'
            + _props({"kdenlive:projectTractor": 1})
            + "".join(f'  <track producer="{t}"/>\n' for t in order) + trans + subf + " </tractor>\n")
    xml = (f"<?xml version='1.0' encoding='utf-8'?>\n<mlt LC_NUMERIC=\"C\" producer=\"main_bin\" version=\"7.22.0\" "
           f"root={quoteattr(str(dest.parent))}>\n" + profile + "".join(producers[:0]) + black
           + "".join(p for p in producers) + main_bin + "".join(x for _, x, _ in tracks) + main + "</mlt>\n")
    dest.write_text(xml, encoding="utf-8")
    return dest


def _vol(db: float, a: int, b: int) -> str:
    return ("   <filter in=\"%d\" out=\"%d\">\n" % (a, b) + _props({"mlt_service": "volume", "kdenlive_id": "volume",
                                                                  "level": f"{db:.2f}"}, "    ") + "   </filter>\n")


def _afade(a: int, b: int, fade_in: bool) -> str:
    return (f'   <filter in="{a}" out="{max(a, b)}">\n' + _props({
        "mlt_service": "volume", "kdenlive_id": "fadein" if fade_in else "fadeout",
        "gain": 0 if fade_in else 1, "end": 1 if fade_in else 0}, "    ") + "   </filter>\n")


def _fade_video(a: int, b: int, fade_in: bool) -> str:
    n = max(1, b - a)
    return (f'   <filter in="{a}" out="{max(a, b)}">\n' + _props({
        "mlt_service": "brightness", "kdenlive_id": "fade_from_black" if fade_in else "fade_to_black",
        "level": f"0=0;{n}=1" if fade_in else f"0=1;{n}=0", "alpha": 1}, "    ") + "   </filter>\n")


def _lut_filter(g, a: int, b: int, ind: str = "   ") -> str:
    if isinstance(g, str):
        g = {"preset": g}
    lut = g.get("lut") or (C.grade_lut(g["preset"]) if g.get("preset") else None)
    if not lut:
        return ""
    if g.get("strength", 1) not in (None, 1, 1.0) or any(g.get(k) for k in ("exposure", "contrast", "saturation", "temperature", "vibrance", "vignette")):
        LAST_NOTES.append("Kdenlive: only the LUT of a grade is carried over (strength/exposure/contrast/saturation are in the MP4 only)")
    return (f'{ind}<filter in="{a}" out="{b}">\n' + _props({"mlt_service": "avfilter.lut3d", "kdenlive_id": "avfilter.lut3d",
                                                              "av.file": str(Path(str(lut)).expanduser()), "av.interp": "tetrahedral"},
                                                             ind + " ") + f"{ind}</filter>\n")


def _kenburns_filter(c, tl, L: int) -> str:
    W, H = tl.W, tl.H
    z = 1.12
    kb = (c.ken_burns or "in").lower()
    def rect(s):
        return f"{-(W * s - W) / 2:.1f} {-(H * s - H) / 2:.1f} {W * s:.1f} {H * s:.1f} 1"
    if kb == "out":
        r0, r1 = rect(z), rect(1.0)
    elif kb == "left":
        r0 = f"0 {-(H * z - H) / 2:.1f} {W * z:.1f} {H * z:.1f} 1"
        r1 = f"{-(W * z - W):.1f} {-(H * z - H) / 2:.1f} {W * z:.1f} {H * z:.1f} 1"
    elif kb == "right":
        r0 = f"{-(W * z - W):.1f} {-(H * z - H) / 2:.1f} {W * z:.1f} {H * z:.1f} 1"
        r1 = f"0 {-(H * z - H) / 2:.1f} {W * z:.1f} {H * z:.1f} 1"
    else:
        r0, r1 = rect(1.0), rect(z)
    return ('   <filter in="0" out="%d">\n' % max(0, L - 1) + _props({
        "mlt_service": "qtblend", "kdenlive_id": "qtblend", "rect": f"0={r0};{max(1, L - 1)}={r1}",
        "compositing": 0, "distort": 0, "rotate_center": 1}, "    ") + "   </filter>\n")


def _transform_filter(o, tl, a: int, b: int) -> str:
    W, H = tl.W, tl.H
    if o.kind in ("alpha", "broll") and not o.width:
        if o.opacity < 1:
            return ('   <filter in="%d" out="%d">\n' % (a, b) + _props({"mlt_service": "qtblend", "kdenlive_id": "qtblend",
                                                                        "rect": f"0 0 {W} {H} {o.opacity:.3f}"}, "    ") + "   </filter>\n")
        return ""
    if o.kind == "image":
        with Image.open(o.src) as im:
            iw, ih = im.size
    else:
        info = C.probe(o.src)
        iw, ih = info["width"], info["height"]
    ww = W * (o.width or 0.3)
    hh = ww * ih / max(1, iw)
    m = min(W, H) * (0.035 if o.kind == "pip" else 0.045)
    pos = o.position
    x = m if "left" in pos else (W - ww - m if "right" in pos else (W - ww) / 2)
    y = m if "top" in pos else (H - hh - m if "bottom" in pos else (H - hh) / 2)
    return ('   <filter in="%d" out="%d">\n' % (a, b) + _props({
        "mlt_service": "qtblend", "kdenlive_id": "qtblend", "rect": f"{x:.1f} {y:.1f} {ww:.1f} {hh:.1f} {o.opacity:.3f}",
        "compositing": 0, "distort": 0, "rotate_center": 1}, "    ") + "   </filter>\n")


def _duck_filter(at, regions, st_frame: int, a: int, b: int, w: _W, tl) -> str:
    """Keyframed volume (dB) that dips during speech — relative frames inside the entry."""
    base = at.volume_db or -18.0
    low = base - at.duck_db
    kf = [(0, base)]
    for s, e in regions:
        fs, fe = w.f(float(s)) - st_frame, w.f(float(e)) - st_frame
        if fe < 0 or fs > b - a:
            continue
        kf += [(max(0, fs - w.f(0.12)), base), (max(0, fs), low), (min(b - a, fe), low), (min(b - a, fe + w.f(0.6)), base)]
    kf.sort()
    ded = []
    for f, v in kf:
        if ded and f <= ded[-1][0]:
            ded[-1] = (ded[-1][0], v)
        else:
            ded.append((f, v))
    level = ";".join(f"{f}={v:.1f}" for f, v in ded)
    return (f'   <filter in="{a}" out="{b}">\n' + _props({"mlt_service": "volume", "kdenlive_id": "volume", "level": level}, "    ")
            + "   </filter>\n")


def validate(project: Path, reference: Path, work: Path) -> dict:
    """Render the .kdenlive headless with melt (small size) and compare it with the ffmpeg render."""
    melt = find_bin(DEPS["melt"])
    if not melt:
        return {"ok": None, "warnings": ["melt not installed — the .kdenlive was not render-checked (brew install mlt / apt install melt)"]}
    info = C.probe(reference)
    W, H = info["width"], info["height"]
    fr = TL.Fraction(info["fps"] or 30).limit_denominator(1001)
    # small check render; both sides multiples of 8 (odd chroma sizes crash some MLT builds)
    sw = 384 if W >= H else 216
    sh = max(8, int(round(H * sw / W / 8)) * 8)
    out = work / "melt-check.mp4"
    import os
    import platform
    pre = []
    if platform.system() == "Linux" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        xv = shutil.which("xvfb-run")
        if xv:  # MLT's Qt module (qtblend, qimage) needs a display on Linux
            pre = [xv, "-a"]
        else:
            return {"ok": None, "warnings": ["melt check skipped: MLT's Qt module needs a display (install xvfb)"]}
    cmd = pre + [melt, "-quiet", str(project), "-consumer", f"avformat:{out}", "vcodec=libx264", "acodec=aac",
           f"width={sw}", f"height={sh}", f"frame_rate_num={fr.numerator}", f"frame_rate_den={fr.denominator}",
           "preset=ultrafast", "crf=28", "real_time=-1"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(300, int(info["duration"] * 20)))
    except subprocess.TimeoutExpired:
        return {"ok": False, "warnings": ["melt check timed out"]}
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        return {"ok": False, "warnings": [f"melt could not render the .kdenlive (exit {r.returncode}): {(r.stderr or r.stdout)[-400:]}"]}
    mi = C.probe(out)
    res = {"ok": True, "melt_duration": round(mi["duration"], 3), "ffmpeg_duration": round(info["duration"], 3), "warnings": []}
    if abs(mi["duration"] - info["duration"]) > 0.25:
        res["warnings"].append(f"Kdenlive/melt duration {mi['duration']:.2f}s vs render {info['duration']:.2f}s")
    # visual similarity at 8 points (mean absolute difference on 64 px thumbnails, 0–255)
    diffs = []
    for i in range(8):
        t = info["duration"] * (i + 0.5) / 8
        a, b = work / f"ka{i}.png", work / f"kb{i}.png"
        C.frame_at(reference, t, a, 64)
        C.frame_at(out, t, b, 64)
        if a.exists() and b.exists():
            A = np.asarray(Image.open(a).convert("RGB").resize((64, 36 if W >= H else 114)), dtype=np.float32)
            B = np.asarray(Image.open(b).convert("RGB").resize((64, 36 if W >= H else 114)), dtype=np.float32)
            diffs.append(float(np.abs(A - B).mean()))
    if diffs:
        res["frame_diff_mean"] = round(float(np.mean(diffs)), 1)
        res["frame_diff_max"] = round(float(np.max(diffs)), 1)
        if max(diffs) > 40:
            res["warnings"].append(f"the Kdenlive project looks different from the render at some points (diff {max(diffs):.0f}/255) — check its preview")
    prev = C.sibling(project, "-kdenlive-check", "png")
    C.contact_sheet(out, prev, cols=4, rows=2, width=320)
    res["preview"] = str(prev)
    if mi.get("audio"):
        m = qc.loudness(out)
        res["melt_lufs"] = m.get("lufs")
    return res
