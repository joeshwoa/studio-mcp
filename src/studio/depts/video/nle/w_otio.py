"""OpenTimelineIO (.otio — Resolve 18+, Kdenlive 23+, Nuke Studio/Hiero, RV, and every OTIO adapter) and AAF
(Avid Media Composer, Pro Tools, Resolve, Premiere, Logic) from the same document; CMX3600 EDL for the
storyline (any NLE ever made). Built with the opentimelineio library itself, so the files are exactly
what OTIO tools expect; positions/opacity ride along as metadata (studio namespace)."""
from __future__ import annotations

from pathlib import Path

from . import doc as D
from .common import file_url


def _rt(t: float, rate: float):
    import opentimelineio as otio
    return otio.opentime.RationalTime(round(t * rate), rate)


def _tr(start: float, dur: float, rate: float):
    import opentimelineio as otio
    return otio.opentime.TimeRange(_rt(start, rate), _rt(dur, rate))


AAF_DISSOLVE = {"Identification": "0c3bea41-fc05-11d2-8a29-0050040ef7d2", "Name": "Video Dissolve", "Description": "Video Dissolve",
                "IsTimeWarp": False, "Bypass": 1, "NumberInputs": 2, "OperationCategory": "OperationCategory_Effect",
                "DataDefinition": {"Name": "Picture"}}


def build(d: dict, with_text: bool = True, for_aaf: bool = False):
    import opentimelineio as otio
    rate = d["fps"]
    tl = otio.schema.Timeline(name=d["name"], global_start_time=_rt(0, rate))
    tl.metadata["studio"] = {"width": d["W"], "height": d["H"], "fps": d["fps"]}
    refs = {}

    def ref(mid: str):
        m = d["media"][mid]
        if mid not in refs:
            avail = (_tr(0, 3600, rate) if for_aaf else None) if m["kind"] == "image" else _tr(0, m.get("dur") or 0, rate)
            refs[mid] = (m, avail)
        m, avail = refs[mid]
        r = otio.schema.ExternalReference(target_url=file_url(m["host"]), available_range=avail)
        r.name = m["name"]
        return r

    def clip(it: dict, audio: bool = False):
        m = d["media"][it["mid"]]
        span = it["rec_out"] - it["rec_in"]
        sp = it.get("speed", 1.0)
        src_start = 0.0 if m["kind"] == "image" else it["src_in"]
        c = otio.schema.Clip(name=it["name"], media_reference=ref(it["mid"]), source_range=_tr(src_start, span * sp if sp else span, rate))
        if abs(sp - 1) > 1e-6:
            c.effects.append(otio.schema.LinearTimeWarp(time_scalar=sp))
            c.source_range = _tr(src_start, span, rate)
        c.enabled = bool(it.get("enabled", True))
        c.metadata["studio"] = {k: it[k] for k in ("box", "opacity", "kf", "gain_db", "fade_in", "fade_out") if k in it}
        return c

    def lay(items: list[dict], kind, name: str, abut: bool, audio: bool = False):
        tr = otio.schema.Track(name=name, kind=kind)
        seq = D.abutted(items) if abut else items
        t = 0.0
        for it in seq:
            if "mid" not in it and not (with_text and it.get("kind") in ("caption",)):
                continue
            if it["rec_in"] > t + 1e-4:
                tr.append(otio.schema.Gap(source_range=_tr(0, it["rec_in"] - t, rate)))
                t = it["rec_in"]
            if abut and it.get("trans_in") and len(tr) and isinstance(tr[-1], otio.schema.Clip):
                h = it["trans_in"]["dur"] / 2
                tn = otio.schema.Transition(name=it["trans_in"]["type"], transition_type=otio.schema.TransitionTypes.SMPTE_Dissolve,
                                            in_offset=_rt(h, rate), out_offset=_rt(h, rate))
                n = round(it["trans_in"]["dur"] * rate)
                tn.metadata["AAF"] = {"PointList": [{"Value": 0.0, "Time": 0.0}, {"Value": 1.0, "Time": 1.0}],
                                      "OperationGroup": {"Operation": dict(AAF_DISSOLVE)}, "CutPoint": n // 2}
                tr.append(tn)
            if "mid" in it:
                tr.append(clip(it, audio))
            else:   # caption / text → a named gap carrying the text
                g = otio.schema.Gap(source_range=_tr(0, it["rec_out"] - it["rec_in"], rate))
                g.name = it.get("text", "")[:60]
                g.metadata["studio"] = {"caption": it.get("text", "")}
                tr.append(g)
            t = max(t, it["rec_out"])
        return tr
    for lane in d["video"]:
        if lane["role"] == "gfx":
            continue
        if lane["role"] == "captions" and not with_text:
            continue
        tl.tracks.append(lay(lane["items"], otio.schema.TrackKind.Video, lane["name"], lane["role"] in ("picture", "backdrop")))
    for lane in d["audio"]:
        tl.tracks.append(lay(lane["items"], otio.schema.TrackKind.Audio, lane["name"], False, audio=True))
    for mk in d.get("markers", []):
        tl.tracks.markers.append(otio.schema.Marker(name=mk.get("name", ""), marked_range=_tr(mk["t"], 0, rate)))
    return tl


def write(d: dict, dest_otio: Path, dest_aaf: Path | None = None) -> list[Path]:
    import opentimelineio as otio
    out = []
    tl = build(d)
    otio.adapters.write_to_file(tl, str(dest_otio))
    out.append(dest_otio)
    if dest_aaf is not None:
        try:
            t2 = build(d, with_text=False, for_aaf=True)
            # AAF needs clips with real media and no metadata dicts it cannot store
            for tr in t2.tracks:
                for it in tr:
                    if hasattr(it, "metadata"):
                        it.metadata.pop("studio", None)
            otio.adapters.write_to_file(t2, str(dest_aaf), adapter_name="AAF", use_empty_mob_ids=True)
            out.append(dest_aaf)
        except Exception as e:  # adapter missing or AAF limits
            (dest_aaf.with_suffix(".aaf-error.txt")).write_text(f"AAF not written: {e}\n"
                                                                 "pip install otio-aaf-adapter pyaaf2", encoding="utf-8")
    return out


def check(path: Path, d: dict) -> list[str]:
    import opentimelineio as otio
    issues = []
    try:
        tl = otio.adapters.read_from_file(str(path))
    except Exception as e:
        return [f"{path.name}: does not load ({str(e)[:160]})"]
    dur = tl.duration().to_seconds()
    if dur < d["duration"] - 0.1:
        issues.append(f"{path.name}: duration {dur:.2f}s < edit {d['duration']:.2f}s")
    return issues
