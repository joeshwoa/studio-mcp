"""Hand an edit to a professional NLE: FCPXML 1.9 (Final Cut Pro, DaVinci Resolve, Premiere via
importers), OpenTimelineIO .otio (Resolve 18+, Premiere/Avid via adapters, Kdenlive 23+) and CMX3600
EDL (every NLE ever made). The timeline is the same JSON video_edit renders, so an AI-built edit can be
finished by a human colourist/editor.

What carries over: V1 storyline (cuts, dissolve transitions, speed), overlay/B-roll/PiP/motion-graphic
clips on V2+ (position/scale/opacity effects do NOT — they import full frame), music/SFX on A2+, the clips'
own sound on A1. Grades, LUTs, captions, ducking and Ken Burns moves are not part of these formats
(captions: import the .srt video_edit writes; grades: the .cube LUTs)."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _pro as P
from . import timeline as TL


# ─────────────────────────── intermediate model ───────────────────────────

def model(tl: TL.Timeline) -> dict:
    """Timeline → {fps, W, H, duration, V: [[items] per lane], A: [[items] per lane]} with record/source times."""
    v1, a1 = [], []
    for i, c in enumerate(tl.clips):
        name = c.label or (c.src.name if c.src else c.kind)
        it = {"kind": c.kind, "src": str(c.src) if c.src else None, "name": name, "rec_in": c.start, "rec_out": c.start + c.dur,
              "src_in": c.src_in if c.kind == "video" else 0.0, "speed": c.speed, "trans": c.trans_dur if c.trans else 0.0,
              "trans_name": c.trans_name or "", "color": c.color, "has_audio": c.has_audio and not c.mute}
        v1.append(it)
        if c.kind == "video" and c.has_audio and not c.mute:
            a1.append(dict(it))
    vl: list[list[dict]] = [v1]
    for o in tl.overlays:
        it = {"kind": "video" if o.src.suffix.lower() not in C.IMAGE_EXTS else "image", "src": str(o.src),
              "name": o.label or o.src.name, "rec_in": o.at, "rec_out": o.at + o.dur, "src_in": o.src_in, "speed": 1.0,
              "trans": 0.0, "trans_name": "", "has_audio": False}
        for lane in vl[1:]:
            if all(it["rec_in"] >= x["rec_out"] - 1e-6 or it["rec_out"] <= x["rec_in"] + 1e-6 for x in lane):
                lane.append(it)
                break
        else:
            vl.append([it])
    al: list[list[dict]] = [a1]
    for a in tl.audio:
        dur = min(a.dur or (a.src_dur - a.src_in), tl.duration - a.at) if not a.loop else min(a.dur or 1e9, tl.duration - a.at)
        it = {"kind": "audio", "src": str(a.src), "name": a.src.name, "rec_in": a.at, "rec_out": a.at + max(0.04, dur),
              "src_in": a.src_in, "speed": 1.0, "trans": 0.0, "trans_name": "", "volume_db": a.volume_db, "loop": a.loop,
              "src_dur": a.src_dur}
        lane = next((ln for ln in al[1:] if all(it["rec_in"] >= x["rec_out"] - 1e-6 for x in ln)), None)
        if lane is None:
            al.append([it])
        else:
            lane.append(it)
    for ln in vl + al:
        ln.sort(key=lambda x: x["rec_in"])
    return {"fps": tl.fps, "W": tl.W, "H": tl.H, "duration": tl.duration, "V": vl, "A": [x for x in al if x]}


# ─────────────────────────── CMX3600 EDL ───────────────────────────

def _reel(i: int) -> str:
    return f"AX{i:03d}"[:8]


def edl_from_segments(segs: list[tuple[str, float, float]], fps: float, dest: Path, title: str = "EDIT") -> Path:
    """Simple cuts-only EDL from (source, in, out) segments played back to back (V + A1/A2)."""
    items, t = [], 0.0
    for src, s, e in segs:
        items.append({"kind": "video", "src": src, "name": Path(src).name, "rec_in": t, "rec_out": t + (e - s), "src_in": s,
                      "speed": 1.0, "trans": 0.0, "has_audio": True})
        t += e - s
    return write_edl(items, fps, dest, title, channels="B")


def write_edl(items: list[dict], fps: float, dest: Path, title: str, channels: str = "V") -> Path:
    """CMX3600 for one track. channels: V | A | B (video+audio) | AA. Dissolves as 'D nnn' events,
    speed changes as M2 lines, source file in '* FROM CLIP NAME' / '* SOURCE FILE' comments (Resolve and
    Premiere relink by these)."""
    fr = round(fps)
    L = [f"TITLE: {title[:60].upper()}", "FCM: NON-DROP FRAME", ""]
    ev = 0
    prev = None
    for k, it in enumerate(items):
        if it["kind"] == "color":
            prev = None
            continue
        ev += 1
        speed = it.get("speed", 1.0) or 1.0
        rec_in, rec_out = it["rec_in"], it["rec_out"]
        nxt = items[k + 1] if k + 1 < len(items) else None
        if nxt is not None and nxt.get("trans") and nxt["kind"] != "color":
            rec_out = nxt["rec_in"]   # the outgoing clip's event ends where the dissolve begins
        s_in = it["src_in"]
        s_out = s_in + (rec_out - rec_in) * speed
        reel = "BL" if it["kind"] == "color" else _reel(ev)
        tr = it.get("trans", 0.0)
        if tr and prev is not None:
            # dissolve: outgoing clip as a zero-length cut, then the dissolve into the new source
            p_out = prev["src_in"] + (rec_in - prev["rec_in"]) * (prev.get("speed", 1.0) or 1.0)
            L.append(f"{ev:03d}  {prev['reel']:<8} {channels:<5} C        {P.smpte(p_out, fps)} {P.smpte(p_out, fps)} "
                     f"{P.smpte(rec_in, fps)} {P.smpte(rec_in, fps)}")
            L.append(f"{ev:03d}  {reel:<8} {channels:<5} D    {int(round(tr * fps)):03d} {P.smpte(s_in, fps)} {P.smpte(s_out, fps)} "
                     f"{P.smpte(rec_in, fps)} {P.smpte(rec_out, fps)}")
            L.append(f"* FROM CLIP NAME: {prev['name']}")
            L.append(f"* TO CLIP NAME: {it['name']}")
        else:
            L.append(f"{ev:03d}  {reel:<8} {channels:<5} C        {P.smpte(s_in, fps)} {P.smpte(s_out, fps)} "
                     f"{P.smpte(rec_in, fps)} {P.smpte(rec_out, fps)}")
            L.append(f"* FROM CLIP NAME: {it['name']}")
        if abs(speed - 1) > 1e-4:
            L.append(f"M2   {reel:<8} {fr * speed:05.1f}    {P.smpte(s_in, fps)}")
        if it.get("src"):
            L.append(f"* SOURCE FILE: {it['src']}")
        L.append("")
        prev = dict(it, reel=reel)
    dest.write_text("\n".join(L), encoding="utf-8")
    return dest


def parse_edl(path: Path, fps: float) -> list[dict]:
    import re
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\d{3})\s+(\S+)\s+(\S+)\s+(C|D)\s*(\d+)?\s+(\S+) (\S+) (\S+) (\S+)$", ln.strip())
        if m:
            def sec(tc):
                h, mi, s, f = (int(x) for x in tc.split(":"))
                return (h * 3600 + mi * 60 + s) + f / round(fps)
            out.append({"event": int(m.group(1)), "reel": m.group(2), "type": m.group(4), "src_in": sec(m.group(6)),
                        "src_out": sec(m.group(7)), "rec_in": sec(m.group(8)), "rec_out": sec(m.group(9))})
    return out


# ─────────────────────────── FCPXML 1.9 ───────────────────────────

def _uri(p: str) -> str:
    return "file://" + quote(str(Path(p).resolve()))


class _T:
    """Frame-exact rational time strings for FCPXML."""

    def __init__(self, fps: float):
        self.fr = Fraction(fps).limit_denominator(1001)
        self.fd = 1 / self.fr

    def frames(self, t: float) -> int:
        return int(round(t * float(self.fr)))

    def s(self, t: float) -> str:
        v = self.frames(t) * self.fd
        if v == 0:
            return "0s"
        return f"{v.numerator}s" if v.denominator == 1 else f"{v.numerator}/{v.denominator}s"


def write_fcpxml(m: dict, dest: Path, name: str) -> Path:
    T = _T(m["fps"])
    root = ET.Element("fcpxml", version="1.9")
    res = ET.SubElement(root, "resources")
    fd = T.fd
    ET.SubElement(res, "format", id="r0", name=f"FFVideoFormat{m['H']}p{float(T.fr):g}",
                  frameDuration=f"{fd.numerator}/{fd.denominator}s", width=str(m["W"]), height=str(m["H"]))
    ids: dict[str, str] = {}
    n = 1

    def asset(it: dict) -> str:
        nonlocal n
        src = it["src"]
        if src in ids:
            return ids[src]
        rid = f"r{n}"
        n += 1
        ids[src] = rid
        if it["kind"] == "image":
            a = ET.SubElement(res, "asset", id=rid, name=Path(src).stem, start="0s", duration="0s", hasVideo="1", format="r0")
        else:
            try:
                pi = C.probe(Path(src))
            except ToolError:
                pi = {"duration": it["rec_out"] - it["rec_in"], "video": it["kind"] != "audio", "audio": True}
            attrs = {"id": rid, "name": Path(src).stem, "start": "0s", "duration": T.s(pi["duration"])}
            if pi.get("video"):
                attrs.update(hasVideo="1", format="r0")
            if pi.get("audio"):
                attrs.update(hasAudio="1", audioSources="1", audioChannels=str(pi.get("channels") or 2),
                             audioRate=str(pi.get("sample_rate") or 48000))
            a = ET.SubElement(res, "asset", **attrs)
        ET.SubElement(a, "media-rep", kind="original-media", src=_uri(src))
        return rid
    eff = {}

    def effect(kind: str) -> str:
        nonlocal n
        if kind not in eff:
            eff[kind] = f"r{n}"
            n += 1
            uid = {"dissolve": "FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"}.get(kind, "FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265")
            ET.SubElement(res, "effect", id=eff[kind], name="Cross Dissolve", uid=uid)
        return eff[kind]
    lib = ET.SubElement(root, "library")
    ev = ET.SubElement(lib, "event", name="studio-mcp")
    proj = ET.SubElement(ev, "project", name=name)
    seq = ET.SubElement(proj, "sequence", format="r0", duration=T.s(m["duration"]), tcStart="0s", tcFormat="NDF",
                        audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(seq, "spine")
    # primary storyline: V1. Each transition overlaps the clips on both sides (FCPXML convention).
    spine_items = []
    for k, it in enumerate(m["V"][0]):
        if it.get("trans") and k:
            tr = ET.SubElement(spine, "transition", name=it.get("trans_name") or "Cross Dissolve", offset=T.s(it["rec_in"]),
                               duration=T.s(it["trans"]))
            ET.SubElement(tr, "filter-video", ref=effect("dissolve"), name="Cross Dissolve")
        dur = it["rec_out"] - it["rec_in"]
        if it["kind"] == "color" or not it.get("src"):
            el = ET.SubElement(spine, "gap", name=f"colour {it.get('color', '')}", offset=T.s(it["rec_in"]), start="0s", duration=T.s(dur))
        elif it["kind"] == "image":
            el = ET.SubElement(spine, "video", ref=asset(it), name=it["name"], offset=T.s(it["rec_in"]), start="0s", duration=T.s(dur))
        else:
            el = ET.SubElement(spine, "asset-clip", ref=asset(it), name=it["name"], offset=T.s(it["rec_in"]),
                               start=T.s(it["src_in"]), duration=T.s(dur), format="r0", tcFormat="NDF")
            sp = it.get("speed", 1.0) or 1.0
            if abs(sp - 1) > 1e-4:
                tm = ET.SubElement(el, "timeMap")
                ET.SubElement(tm, "timept", time="0s", value=T.s(it["src_in"]), interp="linear")
                ET.SubElement(tm, "timept", time=T.s(dur), value=T.s(it["src_in"] + dur * sp), interp="linear")
        spine_items.append((it, el))

    def anchor(t: float):
        """spine element covering record time t and the local time inside it"""
        best = spine_items[0]
        for it, el in spine_items:
            if it["rec_in"] - 1e-6 <= t:
                best = (it, el)
        it, el = best
        local = (it["src_in"] if it["kind"] == "video" else 0.0) + (t - it["rec_in"]) * (it.get("speed", 1.0) or 1.0)
        return el, local
    for li, lane in enumerate(m["V"][1:], start=1):
        for it in lane:
            el, local = anchor(it["rec_in"])
            tag = "video" if it["kind"] == "image" else "asset-clip"
            c = ET.SubElement(el, tag, ref=asset(it), lane=str(li), name=it["name"], offset=T.s(local),
                              start=T.s(it["src_in"]), duration=T.s(it["rec_out"] - it["rec_in"]))
            if tag == "asset-clip":
                c.set("format", "r0")
    for li, lane in enumerate(m["A"][1:], start=1):
        for it in lane:
            el, local = anchor(it["rec_in"])
            c = ET.SubElement(el, "asset-clip", ref=asset(it), lane=str(-li), name=it["name"], offset=T.s(local),
                              start=T.s(it["src_in"]), duration=T.s(min(it["rec_out"] - it["rec_in"],
                                                                         it.get("src_dur", 1e9) - it["src_in"])), audioRole="music")
            if it.get("volume_db"):
                ET.SubElement(c, "adjust-volume", amount=f"{it['volume_db']:.1f}dB")
    # FCPXML wants resources before library and assets in document order; children of a clip after
    # its own adjustments — ElementTree already preserves insertion order.
    ET.indent(root, "  ")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode")
    dest.write_text(xml, encoding="utf-8")
    return dest


def check_fcpxml(path: Path, m: dict) -> list[str]:
    """Re-read: well-formed, every ref resolves, spine adds up to the sequence duration, media exist."""
    issues = []
    root = ET.parse(path).getroot()
    ids = {e.get("id") for e in root.iter() if e.get("id")}
    for e in root.iter():
        if e.get("ref") and e.get("ref") not in ids:
            issues.append(f"dangling ref {e.get('ref')}")
    for mr in root.iter("media-rep"):
        from urllib.parse import unquote, urlparse
        if not Path(unquote(urlparse(mr.get("src")).path)).exists():
            issues.append(f"missing media {mr.get('src')}")

    def sec(s):
        s = s.rstrip("s")
        return float(Fraction(s)) if s else 0.0
    spine = root.find(".//spine")
    end = 0.0
    for el in spine:
        if el.tag == "transition":
            continue
        end = max(end, sec(el.get("offset")) + sec(el.get("duration")))
    seqd = sec(root.find(".//sequence").get("duration"))
    if abs(end - seqd) > 1.5 / m["fps"]:
        issues.append(f"spine ends at {end:.3f}s but the sequence is {seqd:.3f}s")
    return issues


# ─────────────────────────── OpenTimelineIO ───────────────────────────

def _rt(t: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(rate), "value": float(round(t * rate))}


def _tr(start: float, dur: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start, rate), "duration": _rt(dur, rate)}


def _otio_track(items: list[dict], kind: str, name: str, fps: float, total: float, audio: bool = False) -> dict:
    """Items on one lane → Track.1 with Gaps between and (for V1) Transition.1 at dissolves, splitting each
    dissolve at its midpoint (OTIO convention: transitions take no track time; handles come from the media)."""
    ch = []
    t = 0.0
    n = len(items)
    for k, it in enumerate(items):
        rin, rout = it["rec_in"], it["rec_out"]
        sp = it.get("speed", 1.0) or 1.0
        s_in = it["src_in"]
        # dissolve of F frames: a = F//2 frames before the cut point, F - a after (whole frames)
        fin = int(round(it.get("trans", 0.0) * fps)) if (k and not audio) else 0
        nx = items[k + 1] if k + 1 < n else None
        fout = int(round(nx.get("trans", 0.0) * fps)) if (nx is not None and not audio) else 0
        vis_in = rin + (fin // 2) / fps
        vis_out = (nx["rec_in"] + (fout // 2) / fps) if fout else rout
        tin = fin
        if vis_in > t + 1e-6:
            ch.append({"OTIO_SCHEMA": "Gap.1", "name": "", "source_range": _tr(0, vis_in - t, fps), "effects": [], "markers": [], "metadata": {}})
        if tin:
            ch.append({"OTIO_SCHEMA": "Transition.1", "name": it.get("trans_name") or "dissolve", "transition_type": "SMPTE_Dissolve",
                       "in_offset": {"OTIO_SCHEMA": "RationalTime.1", "rate": float(fps), "value": float(fin // 2)},
                       "out_offset": {"OTIO_SCHEMA": "RationalTime.1", "rate": float(fps), "value": float(fin - fin // 2)},
                       "metadata": {}})
        if it["kind"] == "color" or not it.get("src"):
            ch.append({"OTIO_SCHEMA": "Gap.1", "name": f"colour {it.get('color', '')}", "source_range": _tr(0, vis_out - vis_in, fps),
                       "effects": [], "markers": [], "metadata": {}})
        else:
            try:
                avail = C.probe(Path(it["src"]))["duration"] if it["kind"] != "image" else 0.0
            except ToolError:
                avail = 0.0
            ref = {"OTIO_SCHEMA": "ExternalReference.1", "name": Path(it["src"]).name, "target_url": _uri(it["src"]),
                   "available_range": _tr(0, avail, fps) if avail else None, "metadata": {}}
            eff = []
            if abs(sp - 1) > 1e-4:
                eff.append({"OTIO_SCHEMA": "LinearTimeWarp.1", "name": "speed", "effect_name": "LinearTimeWarp",
                            "time_scalar": float(sp), "metadata": {}})
            ch.append({"OTIO_SCHEMA": "Clip.2", "name": it["name"], "source_range": _tr(s_in + (vis_in - rin) * sp, vis_out - vis_in, fps),
                       "media_references": {"DEFAULT_MEDIA": ref}, "active_media_reference_key": "DEFAULT_MEDIA",
                       "effects": eff, "markers": [], "enabled": True,
                       "metadata": {"studio": {k: it.get(k) for k in ("volume_db", "loop") if it.get(k) is not None}}})
        t = vis_out
    return {"OTIO_SCHEMA": "Track.1", "name": name, "kind": kind, "children": ch, "source_range": None,
            "effects": [], "markers": [], "enabled": True, "metadata": {}}


def write_otio(m: dict, dest: Path, name: str) -> Path:
    fps = m["fps"]
    tracks = [_otio_track(lane, "Video", f"V{i + 1}", fps, m["duration"]) for i, lane in enumerate(m["V"]) if lane]
    tracks += [_otio_track(lane, "Audio", f"A{i + 1}", fps, m["duration"], audio=True) for i, lane in enumerate(m["A"]) if lane]
    tl = {"OTIO_SCHEMA": "Timeline.1", "name": name, "global_start_time": _rt(0, fps),
          "metadata": {"studio": {"size": f"{m['W']}x{m['H']}", "fps": fps}},
          "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks, "source_range": None,
                     "effects": [], "markers": [], "enabled": True, "metadata": {}}}
    dest.write_text(json.dumps(tl, indent=2), encoding="utf-8")
    return dest


def check_otio(path: Path, m: dict) -> tuple[list[str], str]:
    """Re-read with opentimelineio when installed (strict), else structural checks on the JSON."""
    issues = []
    try:
        import opentimelineio as otio  # type: ignore
        t = otio.adapters.read_from_file(str(path))
        d = t.duration().to_seconds()
        if abs(d - m["duration"]) > 1.5 / m["fps"]:
            issues.append(f"OTIO duration {d:.3f}s vs edit {m['duration']:.3f}s")
        return issues, f"opentimelineio {otio.__version__}"
    except ImportError:
        pass
    j = json.loads(path.read_text(encoding="utf-8"))
    if j.get("OTIO_SCHEMA") != "Timeline.1":
        issues.append("root is not Timeline.1")
    for tr in j["tracks"]["children"]:
        tot = 0.0
        for c in tr["children"]:
            if c["OTIO_SCHEMA"] in ("Clip.2", "Gap.1"):
                r = c["source_range"]["duration"]
                tot += r["value"] / r["rate"]
            elif c["OTIO_SCHEMA"] != "Transition.1":
                issues.append(f"unknown schema {c['OTIO_SCHEMA']}")
        if tot > m["duration"] + 1.5 / m["fps"]:
            issues.append(f"track {tr['name']} runs {tot:.3f}s > {m['duration']:.3f}s")
    return issues, "JSON structure (pip install opentimelineio for a strict check)"


# ─────────────────────────── the tool ───────────────────────────

@tool("video")
def video_export_interchange(timeline: dict | str, format: str = "all", name: str = "edit", project: str = "",
                             out: str = "") -> Result:
    """Export a video_edit timeline (dict / JSON / .json path — e.g. the one video_auto_edit or
    video_transcript_edit wrote) for a professional NLE: format fcpxml (Final Cut Pro 10.5+, DaVinci
    Resolve: File › Import › Timeline), otio (OpenTimelineIO — Resolve 18+, Kdenlive 23+, Premiere/Avid
    via OTIO adapters), edl (CMX3600 — Premiere: File › Import; Resolve: Import Timeline; V1 + its audio)
    or all. Each file is re-read and checked (time math, every media path exists; opentimelineio strict
    load when installed). Carries cuts, dissolves, speed, overlays on V2+, music on A2+; not grades,
    position/scale, captions (import the .srt) or ducking. Returns the files + a track-layout preview."""
    base = None
    if isinstance(timeline, str) and not timeline.strip().startswith("{"):
        base = Path(timeline).expanduser().resolve().parent
    spec = TL.load(timeline)
    fmts = {"all": ["fcpxml", "otio", "edl"]}.get(format, [f.strip().lower() for f in str(format).split(",")])
    bad = [f for f in fmts if f not in ("fcpxml", "otio", "edl")]
    if bad:
        raise ToolError(f"unknown format {bad}", "fcpxml | otio | edl | all")
    d = C.out_folder(project, out, f"{name}-interchange")
    if spec.get("cut_to_beats"):
        from .pro_beats import apply_cut_to_beats
        spec, _ = apply_cut_to_beats(spec, base)
    tl = TL.normalize(spec, d / "assets", base)
    m = model(tl)
    files, checks, warns = [], {}, list(tl.warnings)
    if "fcpxml" in fmts:
        f = write_fcpxml(m, d / f"{name}.fcpxml", name)
        checks["fcpxml"] = check_fcpxml(f, m)
        files.append(str(f))
    if "otio" in fmts:
        f = write_otio(m, d / f"{name}.otio", name)
        iss, how = check_otio(f, m)
        checks["otio"] = iss
        checks["otio_validator"] = how
        files.append(str(f))
    if "edl" in fmts:
        f = write_edl(m["V"][0], m["fps"], d / f"{name}.edl", name, channels="B" if any(x.get("has_audio") for x in m["V"][0]) else "V")
        back = parse_edl(f, m["fps"])
        rec_end = max((e["rec_out"] for e in back), default=0)
        checks["edl"] = [] if abs(rec_end - max((x["rec_out"] for x in m["V"][0] if x["kind"] != "color"), default=0)) < 1.5 / m["fps"] \
            else [f"EDL record end {rec_end:.3f}s does not match"]
        files.append(str(f))
        for k, lane in enumerate(m["A"][1:], start=2):
            fa = write_edl(lane, m["fps"], d / f"{name}-A{k}.edl", f"{name} A{k}", channels="A")
            files.append(str(fa))
        if len(m["V"]) > 1:
            warns.append("EDL holds one video track: V2+ overlays are only in the FCPXML/OTIO")
    for k, v in checks.items():
        if isinstance(v, list) and v:
            warns += [f"{k}: {x}" for x in v]
    if any(c.grade for c in tl.clips) or tl.grade:
        warns.append("grades are not carried — apply the same .cube LUT in Resolve (Color page › LUTs) or re-grade")
    if any(c.kind == "image" for c in tl.clips):
        warns.append("Ken Burns moves on stills are not carried (stills import static)")
    if tl.captions:
        warns.append("captions are not in these formats — import the .srt that video_edit wrote next to the MP4")
    pv = d / "tracks.png"
    _layout_png(m, pv, name)
    readme = d / "HOW-TO-OPEN.md"
    readme.write_text(HOWTO.format(name=name), encoding="utf-8")
    files += [str(pv), str(readme)]
    res = Result(f"Exported '{name}' ({m['duration']:.2f}s, {len(m['V'][0])} V1 clip(s), {len(m['V']) - 1} overlay track(s), "
                 f"{len(m['A'])} audio track(s)) as {', '.join(fmts)}. Checks: "
                 + ", ".join(f"{k} {'OK' if not v else 'ISSUES'}" for k, v in checks.items() if isinstance(v, list)),
                 files=files, previews=[str(pv)], warnings=warns,
                 data={"checks": checks, "tracks": {"video": [len(x) for x in m["V"]], "audio": [len(x) for x in m["A"]]},
                       "duration": m["duration"], "fps": m["fps"]})
    res.next_steps.append(f"open as described in {readme.name}: Resolve › File › Import › Timeline › {name}.fcpxml (or .otio); "
                          "Premiere › File › Import › .edl / .fcpxml")
    return res


HOWTO = """# Opening '{name}' in a professional editor

All media is referenced by absolute path — keep the files where they are (or relink).

## DaVinci Resolve (free)
1. File › Import › Timeline… (Ctrl/Cmd+Shift+I) → pick `{name}.fcpxml` (best) or `{name}.otio`.
2. Leave "Automatically import source clips into media pool" ON. Untick "Use sizing information".
3. If clips show offline: right-click the timeline in the Media Pool › Relink.
4. Colour: the Color page; LUTs from the studio (`.cube`) go in Resolve's LUT folder or Node › LUTs.

## Final Cut Pro (10.5+)
File › Import › XML… → `{name}.fcpxml`.

## Adobe Premiere Pro
- File › Import… → `{name}.edl` (V1 + its audio; music EDLs `-A2.edl` import as separate sequences), or
- install the FCPXML/OTIO importer (e.g. Premiere's "Final Cut Pro XML" import handles FCP7 XML only —
  for FCPXML use a converter such as XtoCC), or use `{name}.otio` with an OTIO adapter.

## Kdenlive 23+
Project › Open… the `.kdenlive` video_edit wrote (fully editable), or File › Import › OpenTimelineIO.

What is NOT carried: grades/LUTs, overlay position/scale/opacity, captions (import the .srt), ducking
keyframes, Ken Burns on stills.
"""


def _layout_png(m: dict, dest: Path, name: str) -> Path:
    from PIL import Image, ImageDraw
    lanes = [("V" + str(i + 1), ln) for i, ln in enumerate(m["V"])][::-1] + [("A" + str(i + 1), ln) for i, ln in enumerate(m["A"])]
    W, rh = 1400, 46
    H = 70 + rh * len(lanes) + 40
    im = Image.new("RGB", (W, H), (30, 32, 38))
    d = ImageDraw.Draw(im)
    d.text((16, 14), f"{name} — {m['duration']:.2f}s @ {m['fps']:g} fps, {m['W']}x{m['H']}", font=P.font(20, True), fill=(240, 240, 240))
    L, R = 70, W - 20
    D = max(m["duration"], 0.1)
    for r, (lab, ln) in enumerate(lanes):
        y = 60 + r * rh
        d.text((16, y + 12), lab, font=P.font(16, True), fill=(200, 200, 210))
        d.rectangle([L, y + 4, R, y + rh - 4], fill=(44, 46, 54))
        for it in ln:
            x0 = L + (R - L) * it["rec_in"] / D
            x1 = L + (R - L) * min(it["rec_out"], D) / D
            col = (70, 130, 220) if lab.startswith("V") else (60, 170, 110)
            if it["kind"] == "color":
                col = (90, 90, 100)
            d.rectangle([x0, y + 6, max(x0 + 2, x1 - 1), y + rh - 6], fill=col, outline=(18, 18, 22), width=2)
            if it.get("trans"):
                d.polygon([(x0, y + rh - 6), (x0 + (R - L) * it["trans"] / D, y + 6), (x0 + (R - L) * it["trans"] / D, y + rh - 6)], fill=(240, 200, 60))
            d.text((x0 + 4, y + 14), P._fit(d, it["name"], P.font(12), max(0, int(x1 - x0 - 8))) if x1 - x0 > 30 else "",
                   font=P.font(12), fill=(255, 255, 255))
    for k in range(0, int(D) + 1, max(1, int(D / 10) or 1)):
        x = L + (R - L) * k / D
        d.text((x - 8, H - 30), P.tc(k)[:5], font=P.font(12), fill=(170, 170, 180))
    im.save(dest)
    return dest
