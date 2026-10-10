"""Adobe Premiere Pro: Final Cut Pro 7 XML (xmeml v4) — Premiere's own interchange (File › Import), also
read by DaVinci Resolve, Avid (via adapters) and Audition. Carries: every track, cuts, transitions
(cross dissolve / dip to black / dip to white / wipes / push), speed (Time Remap), position and scale
(Basic Motion) with Ken Burns keyframes, opacity with fades, disabled backup tracks, audio levels with
the ducking keyframes, J/L cuts (audio on its own clips, linked), fades, markers.
Graphics: the motion graphics come in as alpha clips (Premiere has no XML text); the editable versions
are in the After Effects project (Dynamic Link) or as .mogrt when built on the Mac. Captions: the .srt
next to the XML (File › Import → drag onto the timeline = a native caption track)."""
from __future__ import annotations

import uuid
from pathlib import Path

from . import doc as D
from .common import clip_kf, db_to_gain, fr, kf_at, x, xmeml_url

TRANS = {"dissolve": ("Cross Dissolve", "Dissolve"), "crossfade": ("Cross Dissolve", "Dissolve"), "fade": ("Cross Dissolve", "Dissolve"),
         "dip": ("Dip to Color Dissolve", "Dissolve"), "dip_black": ("Dip to Color Dissolve", "Dissolve"),
         "dip_white": ("Dip to Color Dissolve", "Dissolve"), "flash": ("Dip to Color Dissolve", "Dissolve"),
         "wipe_left": ("Edge Wipe", "Wipe"), "wipe_right": ("Edge Wipe", "Wipe"), "wipe_up": ("Edge Wipe", "Wipe"),
         "wipe_down": ("Edge Wipe", "Wipe"), "wipe": ("Edge Wipe", "Wipe"), "push": ("Push", "Slide"),
         "slide_left": ("Push", "Slide"), "slide_right": ("Push", "Slide"), "slide_up": ("Push", "Slide"),
         "slide_down": ("Push", "Slide"), "iris": ("Iris Round", "Iris"), "zoom": ("Cross Zoom", "Zoom")}


class _W:
    def __init__(self, d: dict):
        self.d = d
        self.fps = d["fps"]
        num, den = d["rate"]
        self.tb = round(num / den)
        self.ntsc = "TRUE" if den == 1001 else "FALSE"
        self.files_written: set[str] = set()
        self.n = 0
        self.lines: list[str] = []

    def f(self, t: float) -> int:
        return fr(t, self.fps)

    def rate(self, ind: str) -> str:
        return f"{ind}<rate><timebase>{self.tb}</timebase><ntsc>{self.ntsc}</ntsc></rate>\n"


def _file(w: _W, mid: str, ind: str) -> str:
    m = w.d["media"][mid]
    fid = f"file-{mid}"
    if fid in w.files_written:
        return f'{ind}<file id="{fid}"/>\n'
    w.files_written.add(fid)
    dur = max(1, w.f(m.get("dur") or 3600)) if m["kind"] != "image" else 108000
    s = f'{ind}<file id="{fid}">\n{ind} <name>{x(m["name"])}</name>\n{ind} <pathurl>{x(xmeml_url(m["host"]))}</pathurl>\n'
    s += w.rate(ind + " ") + f"{ind} <duration>{dur}</duration>\n"
    s += f"{ind} <timecode>\n" + w.rate(ind + "  ") + f"{ind}  <string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat>\n{ind} </timecode>\n"
    s += f"{ind} <media>\n"
    if m.get("has_video", True) and m["kind"] != "audio":
        s += (f"{ind}  <video><samplecharacteristics>\n" + w.rate(ind + "   ") +
              f"{ind}   <width>{m.get('w') or w.d['W']}</width><height>{m.get('h') or w.d['H']}</height>"
              f"<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>\n"
              f"{ind}  </samplecharacteristics></video>\n")
    if m.get("has_audio"):
        s += (f"{ind}  <audio><samplecharacteristics><depth>16</depth><samplerate>{int(m.get('rate') or 48000)}</samplerate>"
              f"</samplecharacteristics><channelcount>{int(m.get('channels') or 2)}</channelcount></audio>\n")
    s += f"{ind} </media>\n{ind}</file>\n"
    return s


def _param(pid: str, name: str, value, ind: str, kfs: list | None = None, vmin=None, vmax=None, fmt=None) -> str:
    fmt = fmt or (lambda v: f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))
    s = f"{ind}<parameter authoringApp=\"PremierePro\">\n{ind} <parameterid>{pid}</parameterid>\n{ind} <name>{name}</name>\n"
    if vmin is not None:
        s += f"{ind} <valuemin>{vmin}</valuemin>\n{ind} <valuemax>{vmax}</valuemax>\n"
    s += f"{ind} <value>{fmt(value)}</value>\n"
    for when, v in kfs or []:
        s += f"{ind} <keyframe><when>{when}</when><value>{fmt(v)}</value></keyframe>\n"
    return s + f"{ind}</parameter>\n"


def _center_fmt(v) -> str:
    return f"<horiz>{v[0]:.6f}</horiz><vert>{v[1]:.6f}</vert>"


def _motion(w: _W, it: dict, m: dict, in_f: int, ind: str) -> str:
    """Basic Motion: scale (% of the source's own size) + centre (offset from frame centre as a fraction
    of the frame — FCP7 convention, also Premiere's on import)."""
    W, H = w.d["W"], w.d["H"]
    sw, sh = m.get("w") or W, m.get("h") or H

    def sc(b):
        return 100.0 * b[2] / max(1, sw)

    def ce(b):
        return [((b[0] + b[2] / 2) - W / 2) / W, ((b[1] + b[3] / 2) - H / 2) / H]
    kf = it.get("kf", {}).get("box")
    span = it["rec_out"] - it["rec_in"]
    if kf:
        kf = clip_kf(kf, 0.0, span)
        skf = [(in_f + w.f(t), sc(b)) for t, b in kf]
        ckf = [(in_f + w.f(t), ce(b)) for t, b in kf]
        b0 = kf[0][1]
    else:
        skf = ckf = None
        b0 = it["box"]
    if abs(sc(b0) - 100) < 0.01 and abs(ce(b0)[0]) < 1e-4 and abs(ce(b0)[1]) < 1e-4 and not kf:
        return ""
    return (f"{ind}<filter>\n{ind} <effect>\n{ind}  <name>Basic Motion</name>\n{ind}  <effectid>basic</effectid>\n"
            f"{ind}  <effectcategory>motion</effectcategory>\n{ind}  <effecttype>motion</effecttype>\n{ind}  <mediatype>video</mediatype>\n"
            + _param("scale", "Scale", sc(b0), ind + "  ", skf, 0, 1000)
            + _param("rotation", "Rotation", 0, ind + "  ", None, -8640, 8640)
            + _param("center", "Center", ce(b0), ind + "  ", ckf, fmt=_center_fmt)
            + _param("centerOffset", "Anchor Point", [0.0, 0.0], ind + "  ", None, fmt=_center_fmt)
            + f"{ind} </effect>\n{ind}</filter>\n")


def _opacity(w: _W, it: dict, in_f: int, ind: str) -> str:
    kf = it.get("kf", {}).get("opacity")
    op = it.get("opacity", 1.0)
    if not kf and op >= 0.999:
        return ""
    k = [(in_f + w.f(t), v * 100) for t, v in clip_kf(kf, 0.0, it["rec_out"] - it["rec_in"])] if kf else None
    return (f"{ind}<filter>\n{ind} <effect>\n{ind}  <name>Opacity</name>\n{ind}  <effectid>opacity</effectid>\n"
            f"{ind}  <effectcategory>motion</effectcategory>\n{ind}  <effecttype>motion</effecttype>\n{ind}  <mediatype>video</mediatype>\n"
            + _param("opacity", "opacity", op * 100, ind + "  ", k, 0, 100) + f"{ind} </effect>\n{ind}</filter>\n")


def _speed(it: dict, ind: str) -> str:
    sp = it.get("speed", 1.0)
    if abs(sp - 1) < 1e-6:
        return ""
    return (f"{ind}<filter>\n{ind} <effect>\n{ind}  <name>Time Remap</name>\n{ind}  <effectid>timeremap</effectid>\n"
            f"{ind}  <effectcategory>motion</effectcategory>\n{ind}  <effecttype>motion</effecttype>\n{ind}  <mediatype>video</mediatype>\n"
            + _param("variablespeed", "variablespeed", 0, ind + "  ", None, 0, 1)
            + _param("speed", "speed", sp * 100, ind + "  ", None, -100000, 100000)
            + _param("reverse", "reverse", "FALSE", ind + "  ")
            + _param("frameblending", "frameblending", "FALSE", ind + "  ")
            + f"{ind} </effect>\n{ind}</filter>\n")


def _levels(w: _W, a: dict, in_f: int, ind: str) -> str:
    span = a["rec_out"] - a["rec_in"]
    pts = list(a.get("kf") or [])
    base = a.get("gain_db", 0.0)
    if a.get("fade_in"):
        pts = sorted(pts + [(0.0, -60.0), (min(a["fade_in"], span), kf_at(a.get("kf"), a["fade_in"]) if a.get("kf") else base)])
    if a.get("fade_out"):
        t0 = max(0.0, span - a["fade_out"])
        pts = sorted(pts + [(t0, kf_at(a.get("kf"), t0) if a.get("kf") else base), (span, -60.0)])
    if not pts and abs(base) < 0.01:
        return ""
    k = [(in_f + w.f(t), db_to_gain(v)) for t, v in _dedupe(pts)] if pts else None
    return (f"{ind}<filter>\n{ind} <effect>\n{ind}  <name>Audio Levels</name>\n{ind}  <effectid>audiolevels</effectid>\n"
            f"{ind}  <effectcategory>audiolevels</effectcategory>\n{ind}  <effecttype>audiolevels</effecttype>\n{ind}  <mediatype>audio</mediatype>\n"
            + _param("level", "Level", db_to_gain(base), ind + "  ", k, 0, 3.98109) + f"{ind} </effect>\n{ind}</filter>\n")


def _dedupe(pts):
    out = []
    for t, v in sorted(pts):
        if out and abs(t - out[-1][0]) < 1e-4:
            out[-1] = (t, v)
        else:
            out.append((t, v))
    return out


def _transition(w: _W, t: dict, cut: float, ind: str) -> str:
    name, cat = TRANS.get(t["type"], ("Cross Dissolve", "Dissolve"))
    a, b = w.f(cut - t["dur"] / 2), w.f(cut + t["dur"] / 2)
    s = (f"{ind}<transitionitem>\n" + w.rate(ind + " ") + f"{ind} <start>{a}</start>\n{ind} <end>{b}</end>\n"
         f"{ind} <alignment>center</alignment>\n{ind} <effect>\n{ind}  <name>{name}</name>\n{ind}  <effectid>{name}</effectid>\n"
         f"{ind}  <effectcategory>{cat}</effectcategory>\n{ind}  <effecttype>transition</effecttype>\n{ind}  <mediatype>video</mediatype>\n"
         f"{ind}  <wipecode>0</wipecode>\n{ind}  <wipeaccuracy>100</wipeaccuracy>\n{ind}  <startratio>0</startratio>\n"
         f"{ind}  <endratio>1</endratio>\n{ind}  <reverse>FALSE</reverse>\n")
    if name == "Dip to Color Dissolve":
        white = t["type"] in ("dip_white", "flash")
        c = 255 if white else 0
        s += (f"{ind}  <parameter><parameterid>color</parameterid><name>Color</name><value><alpha>255</alpha><red>{c}</red>"
              f"<green>{c}</green><blue>{c}</blue></value></parameter>\n")
    return s + f"{ind} </effect>\n{ind}</transitionitem>\n"


def _clipitem(w: _W, it: dict, ind: str, links: list[str] | None = None, audio: bool = False) -> str:
    m = w.d["media"][it["mid"]]
    cid = f"clipitem-{it['id']}"
    sp = it.get("speed", 1.0)
    rin, rout = w.f(it["rec_in"]), w.f(it["rec_out"])
    if m["kind"] == "image":
        sin = 0
        sout = rout - rin
    else:
        sin = w.f(it["src_in"])
        sout = sin + int(round((rout - rin) * sp))
    s = (f'{ind}<clipitem id="{cid}">\n{ind} <masterclipid>masterclip-{it["mid"]}</masterclipid>\n{ind} <name>{x(it["name"])}</name>\n'
         f'{ind} <enabled>{"TRUE" if it.get("enabled", True) else "FALSE"}</enabled>\n'
         f'{ind} <duration>{max(sout, w.f(m.get("dur") or 0)) if m["kind"] != "image" else 108000}</duration>\n' + w.rate(ind + " ") +
         f"{ind} <start>{rin}</start>\n{ind} <end>{rout}</end>\n{ind} <in>{sin}</in>\n{ind} <out>{sout}</out>\n")
    if m["kind"] != "image":
        s += f"{ind} <alphatype>{'straight' if m.get('alpha') else 'none'}</alphatype>\n"
    s += _file(w, it["mid"], ind + " ")
    if audio:
        s += f"{ind} <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>\n"
        s += _levels(w, it, sin, ind + " ")
    else:
        s += _motion(w, it, m, sin, ind + " ") + _opacity(w, it, sin, ind + " ") + _speed(it, ind + " ")
    for ln in links or []:
        s += ln
    return s + f"{ind}</clipitem>\n"


def write(d: dict, dest: Path) -> Path:
    """→ <name>.xml (xmeml v4)."""
    w = _W(d)
    total = w.f(d["duration"])
    # tracks as they will be written (so A/V links can name track + clip indexes)
    vlanes = []
    for lane in d["video"]:
        if lane["role"] in ("gfx", "captions"):
            continue     # native text lives in AE/FCP/Resolve/CapCut; Premiere gets the rendered clips + .srt
        items = D.abutted(lane["items"]) if lane["role"] in ("picture", "backdrop") else [dict(i) for i in lane["items"]]
        items = [i for i in items if "mid" in i]
        if lane["role"] == "gfx_render":
            for i in items:
                i["enabled"] = True     # no native text in XML → the rendered graphic is the live one here
        vlanes.append((lane, items))
    pos: dict[str, tuple[str, int, int]] = {}
    for ti, (lane, items) in enumerate(vlanes, start=1):
        for ci, it in enumerate(items, start=1):
            pos[it["id"]] = ("video", ti, ci)
    for ti, lane in enumerate(d["audio"], start=1):
        for ci, a in enumerate(lane["items"], start=1):
            pos[a["id"]] = ("audio", ti, ci)
    links: dict[str, list[str]] = {}
    for lane in d["audio"]:
        for a in lane["items"]:
            if a.get("link") in pos:
                grp = [a["link"], a["id"]]
                for gid in grp:
                    links.setdefault(gid, [])
                    for other in grp:
                        k, ti, ci = pos[other]
                        links[gid].append(f"     <link><linkclipref>clipitem-{other}</linkclipref><mediatype>{k}</mediatype>"
                                          f"<trackindex>{ti}</trackindex><clipindex>{ci}</clipindex></link>\n")
    vtracks, atracks = [], []
    for lane, items in vlanes:
        body = []
        for i, it in enumerate(items):
            if it.get("trans_in") and i > 0:
                body.append(_transition(w, it["trans_in"], it["rec_in"], "    "))
            body.append(_clipitem(w, it, "    ", links.get(it["id"])))
        vtracks.append(f'   <track MZ.TrackName="{x(lane["name"])}">\n' + "".join(body) +
                       "    <enabled>TRUE</enabled>\n    <locked>FALSE</locked>\n   </track>\n")
    for lane in d["audio"]:
        body = [_clipitem(w, a, "    ", links.get(a["id"]), audio=True) for a in lane["items"]]
        atracks.append(f'   <track premiereTrackType="Stereo" MZ.TrackName="{x(lane["name"])}">\n' + "".join(body) +
                       "    <enabled>TRUE</enabled>\n    <locked>FALSE</locked>\n    <outputchannelindex>1</outputchannelindex>\n   </track>\n")
    markers = "".join(f"  <marker><name>{x(mk.get('name', ''))}</name><comment>{x(mk.get('note', ''))}</comment>"
                      f"<in>{w.f(mk['t'])}</in><out>-1</out></marker>\n" for mk in d.get("markers", []))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">\n'
           f' <sequence id="sequence-1">\n  <uuid>{uuid.uuid4()}</uuid>\n  <name>{x(d["name"])}</name>\n  <duration>{total}</duration>\n'
           + w.rate("  ") +
           f"  <timecode>\n" + w.rate("   ") + "   <string>00:00:00:00</string>\n   <frame>0</frame>\n   <displayformat>NDF</displayformat>\n  </timecode>\n"
           f"  <in>-1</in>\n  <out>-1</out>\n{markers}"
           f"  <media>\n   <video>\n    <format>\n     <samplecharacteristics>\n" + w.rate("      ") +
           f"      <width>{d['W']}</width>\n      <height>{d['H']}</height>\n      <anamorphic>FALSE</anamorphic>\n"
           f"      <pixelaspectratio>square</pixelaspectratio>\n      <fielddominance>none</fielddominance>\n      <colordepth>24</colordepth>\n"
           f"     </samplecharacteristics>\n    </format>\n" + "".join(vtracks) + "   </video>\n"
           f"   <audio>\n    <numOutputChannels>2</numOutputChannels>\n    <format>\n     <samplecharacteristics>\n"
           f"      <depth>16</depth>\n      <samplerate>48000</samplerate>\n     </samplecharacteristics>\n    </format>\n"
           + "".join(atracks) + "   </audio>\n  </media>\n </sequence>\n</xmeml>\n")
    dest.write_text(xml, encoding="utf-8")
    return dest


def check(path: Path, d: dict) -> list[str]:
    """Structural check (XML parses; every clip's record range is inside the sequence; opentimelineio's
    FCP7 adapter reads it back with the same track count)."""
    import xml.etree.ElementTree as ET
    issues = []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return [f"XML does not parse: {e}"]
    total = int(root.find("sequence/duration").text)
    for ci in root.iter("clipitem"):
        st, en = int(ci.find("start").text), int(ci.find("end").text)
        if en <= st or en > total + 1:
            issues.append(f"{ci.get('id')}: record {st}–{en} outside 0–{total}")
    try:
        import opentimelineio as otio
        tl = otio.adapters.read_from_file(str(path), adapter_name="fcp_xml")
        nv = len([t for t in tl.tracks if t.kind == "Video"])
        exp = len([l for l in d["video"] if l["role"] not in ("gfx", "captions")])
        if nv != exp:
            issues.append(f"opentimelineio reads {nv} video tracks (expected {exp})")
    except ImportError:
        pass
    except Exception as e:
        issues.append(f"opentimelineio fcp_xml read failed: {str(e)[:160]}")
    return issues
