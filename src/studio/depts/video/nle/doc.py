"""Timeline (+ what the render produced) → an app-neutral edit document every NLE writer reads.

Times are seconds on the programme timeline, geometry is pixels in the W×H frame (top-left origin):
an item's `box` [x, y, w, h] is where the WHOLE source frame lands (it may be larger than the frame —
that is a crop/punch-in). Keyframes are relative to the item start. The geometry is computed with the
same maths as the ffmpeg render (timeline.py) so every app shows the same picture as the MP4.

Video lanes, bottom → top:
  backdrop   blurred backdrops for clips that use fit=blur (pre-rendered clips, only when needed)
  picture    the storyline (V1 in most apps): clips, stills, colour cards, title cards; overlapping = transitions
  overlay    B-roll, picture-in-picture, logos/images, alpha overlays (one lane per overlap level)
  gfx_render rendered motion graphics (alpha) — a disabled backup when the native version exists
  gfx        native, editable graphics (text, shapes, images) rebuilt from the motion design
  captions   native caption text (from the same chunks as the burned-in captions)
Audio lanes: dialog (the clips' own sound, J/L cuts), broll (B-roll/PiP sound), music (ducked bed),
sfx/extra tracks.
"""
from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

from .. import _common as C
from .. import timeline as TL

POS = ("top-left", "top-right", "bottom-left", "bottom-right", "top", "bottom", "center", "left", "right")


def rate(fps: float) -> tuple[int, int]:
    """fps → exact rational (29.97 → 30000/1001)."""
    for nominal in (24, 30, 60, 120):
        if abs(fps - nominal * 1000 / 1001) < 0.005:
            return nominal * 1000, 1001
    f = Fraction(fps).limit_denominator(1001)
    return f.numerator, f.denominator


class Ids:
    def __init__(self):
        self.n = {}

    def __call__(self, prefix: str) -> str:
        self.n[prefix] = self.n.get(prefix, 0) + 1
        return f"{prefix}{self.n[prefix]}"


# ─────────────────────────── geometry (mirrors timeline.py) ───────────────────────────

def fit_mode(c: TL.Clip, W: int, H: int) -> str:
    fit = c.fit
    if fit == "auto":
        if c.src_w and c.src_h:
            fit = "cover" if abs((c.src_w / c.src_h) / (W / H) - 1) < 0.12 else "blur"
        else:
            fit = "cover"
    return fit


def cover_box(sw: int, sh: int, W: int, H: int, zoom: float = 1.0, focus=(0.5, 0.5)) -> list[float]:
    s = max(W * zoom / sw, H * zoom / sh)
    w, h = sw * s, sh * s
    fx, fy = (float(focus[0]), float(focus[1])) if len(focus) == 2 else (0.5, 0.5)
    return [round(-(w - W) * fx, 2), round(-(h - H) * fy, 2), round(w, 2), round(h, 2)]


def contain_box(sw: int, sh: int, W: int, H: int) -> list[float]:
    s = min(W / sw, H / sh)
    w, h = sw * s, sh * s
    return [round((W - w) / 2, 2), round((H - h) / 2, 2), round(w, 2), round(h, 2)]


def kenburns_boxes(c: TL.Clip, W: int, H: int) -> tuple[list[float], list[float]]:
    """Start/end boxes of the still's move (zoompan in timeline._kenburns)."""
    base = cover_box(c.src_w, c.src_h, W, H)
    bw, bh = base[2], base[3]
    zmax = 1.12
    kb = (c.ken_burns or "in").lower()

    def centred(z):
        w, h = bw * z, bh * z
        return [round((W - w) / 2, 2), round((H - h) / 2, 2), round(w, 2), round(h, 2)]
    if kb == "none":
        return base, base
    if kb == "out":
        return centred(zmax), centred(1.0)
    if kb in ("left", "right"):
        w, h = bw * zmax, bh * zmax
        y = round((H - h) / 2, 2)
        right_edge = [round(W - w, 2), y, round(w, 2), round(h, 2)]   # window at the source's right side
        left_edge = [0.0, y, round(w, 2), round(h, 2)]
        return (right_edge, left_edge) if kb == "left" else (left_edge, right_edge)
    return centred(1.0), centred(zmax)


def overlay_box(o: TL.Overlay, sw: int, sh: int, W: int, H: int) -> list[float]:
    if o.kind == "broll":
        return cover_box(sw, sh, W, H)
    if o.kind == "alpha" and not o.width:
        return [0.0, 0.0, float(W), float(H)]
    if o.kind == "pip":
        w = int(W * (o.width or 0.3)) // 2 * 2
        h = int(w * sh / max(1, sw)) // 2 * 2
        m = int(min(W, H) * 0.035)
    else:
        w = int(W * o.width) if o.width else W
        h = round(w * sh / max(1, sw))
        m = int(min(W, H) * 0.045)
    pos = o.position if o.position in POS else "center"
    x = m if "left" in pos else (W - w - m if "right" in pos else (W - w) / 2)
    y = m if pos.startswith("top") else (H - h - m if pos.startswith("bottom") else (H - h) / 2)
    return [round(float(x), 2), round(float(y), 2), float(w), float(h)]


# ─────────────────────────── the document ───────────────────────────

def build(tl: TL.Timeline, *, name: str, side: dict) -> dict:
    """side: {"duck_regions": [...], "captions": {"chunks", "geometry", "style", "family", "family_ar",
    "accent", "srt", "ass", "uppercase"}, "gfx": {item-key: spec}, "loudness_target": -14,
    "dialog_gain_db": float}"""
    W, H, fps = tl.W, tl.H, tl.fps
    ids = Ids()
    media: dict[str, dict] = {}
    by_path: dict[str, str] = {}
    notes: list[str] = []

    def add_media(p: Path, kind: str, **kw) -> str:
        key = f"{kind}:{p}"
        if key in by_path:
            return by_path[key]
        mid = ids("m")
        info = {"src": str(p), "kind": kind, "name": p.name}
        if kind in ("video", "graphic", "audio"):
            pr = C.probe(p)
            info.update({"w": pr.get("width") or 0, "h": pr.get("height") or 0, "dur": pr.get("duration") or 0.0,
                         "fps": pr.get("fps") or fps, "has_video": bool(pr.get("video")), "has_audio": bool(pr.get("audio")),
                         "alpha": bool(pr.get("alpha")) or kind == "graphic",
                         "channels": pr.get("channels") or (2 if pr.get("audio") else 0), "rate": pr.get("sample_rate") or 48000})
        elif kind == "image":
            from PIL import Image
            with Image.open(p) as im:
                info.update({"w": im.width, "h": im.height, "dur": 0.0, "has_video": True, "has_audio": False,
                             "alpha": im.mode in ("RGBA", "LA", "P")})
        info.update(kw)
        media[mid] = info
        by_path[key] = mid
        return mid

    lanes: dict[str, list] = {"backdrop": [], "picture": [], "gfx_render": [], "gfx": [], "captions": []}
    overlay_lanes: list[list[dict]] = []
    audio: dict[str, list] = {"dialog": [], "broll": [], "music": [], "sfx": []}

    # ── storyline
    for n, c in enumerate(tl.clips):
        it = {"id": ids("v"), "name": c.label or (c.src.name if c.src else f"colour {c.color}"), "rec_in": round(c.start, 4),
              "rec_out": round(c.start + c.dur, 4), "speed": c.speed, "opacity": 1.0, "kf": {}, "enabled": True, "index": n}
        if c.trans and c.trans_dur > 0 and n > 0:
            it["trans_in"] = {"type": c.trans_name or "dissolve", "xfade": c.trans, "dur": round(c.trans_dur, 4)}
        if c.grade or tl.grade:
            it["grade"] = {"clip": c.grade, "programme": tl.grade}
        if c.kind == "color":
            it.update({"kind": "color", "color": c.color, "src_in": 0.0, "box": [0.0, 0.0, float(W), float(H)]})
        elif c.kind == "image":
            mid = add_media(c.src, "image")
            b0, b1 = kenburns_boxes(c, W, H)
            it.update({"kind": "image", "mid": mid, "src_in": 0.0, "box": b0})
            if b0 != b1:
                it["kf"]["box"] = [(0.0, b0), (round(c.dur, 4), b1)]
        else:
            kind = "title" if c.generated else "video"
            mid = add_media(c.src, "graphic" if c.generated else "video", generated=c.generated)
            fit = fit_mode(c, W, H)
            if fit == "contain":
                box = contain_box(c.src_w, c.src_h, W, H)
            elif fit == "blur":
                box = contain_box(c.src_w, c.src_h, W, H)
                lanes["backdrop"].append({"id": ids("v"), "name": f"backdrop · {c.src.name}", "kind": "backdrop",
                                          "for": it["id"], "src": str(c.src), "rec_in": it["rec_in"], "rec_out": it["rec_out"],
                                          "src_in": c.src_in, "speed": c.speed, "opacity": 1.0, "kf": {}, "enabled": True,
                                          "box": [0.0, 0.0, float(W), float(H)], "trans_in": it.get("trans_in")})
            else:
                box = cover_box(c.src_w, c.src_h, W, H, c.zoom, c.focus)
            it.update({"kind": kind, "mid": mid, "src_in": round(c.src_in, 4), "box": box, "fit": fit})
            if c.rect_kf:
                notes.append(f"clip {n + 1}: the reframe camera move is carried to Kdenlive only — other apps get the "
                             "static framing (re-track it with the app's auto-reframe)")
            if c.meta.get("tool"):
                it["gfx_key"] = f"clip{n}"
        lanes["picture"].append(it)
        # its sound
        if c.kind == "video" and c.has_audio and not c.mute:
            ext = c.ax if (n > 0 and c.ax and not c.j and not c.trans_dur and c.src_in / c.speed >= c.ax and c.start >= c.ax) else 0.0
            a_in = c.src_in - (c.j + ext) * c.speed
            a_out = min(c.src_dur, c.src_out + c.l * c.speed)
            nxt = tl.clips[n + 1] if n + 1 < len(tl.clips) else None
            nx_ax = nxt.ax if (nxt and nxt.ax and not nxt.j and not nxt.trans_dur and nxt.kind == "video") else 0.0
            fin = c.trans_dur if c.trans_dur else (0.25 if c.j else (ext or 0.0))
            fout = (nxt.trans_dur if nxt and nxt.trans_dur else 0.0) or (0.25 if c.l else nx_ax)
            st = max(0.0, c.start - c.j - ext)
            audio["dialog"].append({"id": ids("a"), "mid": mid, "name": c.src.name, "rec_in": round(st, 4),
                                    "rec_out": round(st + (a_out - max(0.0, a_in)) / c.speed, 4), "src_in": round(max(0.0, a_in), 4),
                                    "speed": c.speed, "gain_db": round(c.volume_db + side.get("dialog_gain_db", 0.0), 2),
                                    "fade_in": round(fin, 3), "fade_out": round(fout, 3), "kf": [], "link": it["id"],
                                    "j": c.j, "l": c.l})
            it["link"] = audio["dialog"][-1]["id"]

    # ── overlays
    for k, o in enumerate(tl.overlays):
        if o.kind == "image":
            mid = add_media(o.src, "image")
        else:
            mid = add_media(o.src, "graphic" if o.meta.get("tool") or o.kind == "alpha" else "video")
        mi = media[mid]
        box = overlay_box(o, mi["w"], mi["h"], W, H)
        it = {"id": ids("v"), "mid": mid, "name": o.label or o.src.name, "kind": o.kind, "rec_in": round(o.at, 4),
              "rec_out": round(o.at + o.dur, 4), "src_in": round(o.src_in, 4), "speed": 1.0, "box": box,
              "opacity": round(o.opacity, 3), "kf": {}, "enabled": True, "index": k}
        if o.fade:
            f = min(o.fade, o.dur / 2)
            it["kf"]["opacity"] = [(0.0, 0.0), (round(f, 3), o.opacity), (round(o.dur - f, 3), o.opacity), (round(o.dur, 3), 0.0)]
        if o.kind == "pip":
            it["mask"] = {"radius": o.radius, "border": o.border}
        if o.meta.get("tool"):
            it["gfx_key"] = f"ov{k}"
            it["tool"] = o.meta["tool"]
            lanes["gfx_render"].append(it)
        else:
            for lane in overlay_lanes:
                if all(it["rec_in"] >= x["rec_out"] - 1e-6 or it["rec_out"] <= x["rec_in"] + 1e-6 for x in lane):
                    lane.append(it)
                    break
            else:
                overlay_lanes.append([it])
        if o.kind in ("broll", "pip") and o.has_audio and o.volume_db is not None:
            audio["broll"].append({"id": ids("a"), "mid": mid, "name": o.src.name, "rec_in": it["rec_in"], "rec_out": it["rec_out"],
                                   "src_in": it["src_in"], "speed": 1.0, "gain_db": float(o.volume_db), "fade_in": 0.15,
                                   "fade_out": 0.15, "kf": [], "link": it["id"]})

    # ── native graphics (rebuilt from the motion design) + their rendered backups
    gfx_specs = side.get("gfx") or {}
    graphics = {}
    for it in lanes["picture"] + lanes["gfx_render"]:
        key = it.get("gfx_key")
        spec = gfx_specs.get(key) if key else None
        if not spec:
            continue
        gid = ids("g")
        graphics[gid] = {**spec, "id": gid}
        g_it = {"id": ids("v"), "kind": "gfx", "gfx": gid, "name": spec.get("name") or it["name"], "rec_in": it["rec_in"],
                "rec_out": it["rec_out"], "src_in": 0.0, "speed": 1.0, "box": [0.0, 0.0, float(W), float(H)], "opacity": 1.0,
                "kf": {}, "enabled": True, "backup_of": it["id"]}
        lanes["gfx"].append(g_it)
        if it in lanes["gfx_render"]:
            it["enabled"] = False      # the rendered version stays one click away
            it["backup"] = True
    for k, o in enumerate(tl.overlays):
        if o.meta.get("tool") and f"ov{k}" not in gfx_specs:
            notes.append(f"overlay {k + 1} ({o.meta['tool']}): kept as the rendered alpha graphic (no native rebuild)")

    # ── music / sfx
    regions = side.get("duck_regions") or []
    for a in tl.audio:
        mid = add_media(a.src, "audio")
        mi = media[mid]
        total = min(a.dur or 1e9, tl.duration - a.at) if a.loop else min(a.dur or (mi["dur"] - a.src_in), tl.duration - a.at)
        base = a.volume_db if not a.duck else (a.volume_db or -18.0)
        lane = "music" if (a.duck or a.loop or total > 8) else "sfx"
        t, first = a.at, True
        piece_len = max(0.05, mi["dur"] - a.src_in)
        while t < a.at + total - 1e-3:
            ln = min(piece_len if a.loop else total, a.at + total - t)
            item = {"id": ids("a"), "mid": mid, "name": a.src.name, "rec_in": round(t, 4), "rec_out": round(t + ln, 4),
                    "src_in": round(a.src_in if first else 0.0, 4), "speed": 1.0, "gain_db": round(base, 2),
                    "fade_in": a.fade_in if first else 0.0, "fade_out": 0.0, "kf": []}
            if a.duck and regions:
                item["kf"] = duck_keyframes(base, a.duck_db, regions, item["rec_in"], item["rec_out"])
            audio[lane].append(item)
            t += ln
            first = False
            if not a.loop:
                break
        if audio[lane]:
            audio[lane][-1]["fade_out"] = a.fade_out

    # ── captions
    cap = side.get("captions")
    if cap and cap.get("chunks"):
        geo = cap["geometry"]
        for ch in cap["chunks"]:
            words = ch["words"]
            text = " ".join(w["word"] for w in words)
            lanes["captions"].append({"id": ids("v"), "kind": "caption", "name": text[:40], "rec_in": round(ch["start"], 4),
                                      "rec_out": round(ch["end"], 4), "text": text, "words": words, "enabled": True,
                                      "src_in": 0.0, "speed": 1.0, "box": [0.0, 0.0, float(W), float(H)], "opacity": 1.0, "kf": {}})
        cap = {**cap, "geometry": geo}

    video = []
    if lanes["backdrop"]:
        video.append({"name": "Backdrops (blurred fill)", "role": "backdrop", "items": lanes["backdrop"]})
    video.append({"name": "Picture", "role": "picture", "items": lanes["picture"]})
    for i, ln in enumerate(overlay_lanes):
        video.append({"name": f"Overlays {i + 1}" if len(overlay_lanes) > 1 else "Overlays · B-roll · PiP", "role": "overlay",
                      "items": sorted(ln, key=lambda x: x["rec_in"])})
    if lanes["gfx_render"]:
        gl = _pack(lanes["gfx_render"])
        for i, ln in enumerate(gl):
            video.append({"name": "Graphics (rendered)" + (f" {i + 1}" if len(gl) > 1 else ""), "role": "gfx_render", "items": ln})
    if lanes["gfx"]:
        gl = _pack(lanes["gfx"])
        for i, ln in enumerate(gl):
            video.append({"name": "Graphics (editable)" + (f" {i + 1}" if len(gl) > 1 else ""), "role": "gfx", "items": ln})
    if lanes["captions"]:
        video.append({"name": "Captions", "role": "captions", "items": lanes["captions"]})
    alanes = []
    for role, label in (("dialog", "Dialogue"), ("broll", "B-roll / PiP sound"), ("music", "Music"), ("sfx", "SFX")):
        items = audio[role]
        if not items:
            continue
        packed = _pack(items) if role != "dialog" else _pack(items)
        for i, ln in enumerate(packed):
            alanes.append({"name": label + (f" {i + 1}" if len(packed) > 1 else ""), "role": role, "items": ln})
    num, den = rate(fps)
    return {"name": name, "W": W, "H": H, "fps": fps, "rate": [num, den], "duration": round(tl.duration, 4),
            "background": tl.background, "media": media, "video": video, "audio": alanes, "graphics": graphics,
            "captions": cap if lanes["captions"] else None, "fades": {"in": tl.fade_in, "out": tl.fade_out},
            "loudness_target": side.get("loudness_target"), "notes": notes, "markers": side.get("markers") or []}


def _pack(items: list[dict]) -> list[list[dict]]:
    """Distribute items over as few non-overlapping lanes as possible (keeps order)."""
    lanes: list[list[dict]] = []
    for it in sorted(items, key=lambda x: x["rec_in"]):
        for ln in lanes:
            if it["rec_in"] >= ln[-1]["rec_out"] - 1e-6:
                ln.append(it)
                break
        else:
            lanes.append([it])
    return lanes


def duck_keyframes(base: float, duck_db: float, regions, a: float, b: float) -> list[tuple[float, float]]:
    """Music level (dB) that dips under speech — same envelope as the render (audio_mix)."""
    low = base - duck_db
    kf = [(0.0, base)]
    for s, e in regions:
        s, e = float(s) - a, float(e) - a
        if e < 0 or s > b - a:
            continue
        kf += [(max(0.0, s - 0.12), base), (max(0.0, s), low), (min(b - a, e), low), (min(b - a, e + 0.6), base)]
    kf.sort()
    out: list[tuple[float, float]] = []
    for t, v in kf:
        t = round(t, 3)
        if out and t <= out[-1][0] + 1e-4:
            out[-1] = (out[-1][0], v)
        else:
            out.append((t, v))
    return out if len(out) > 1 else []


# ─────────────────────────── helpers for writers ───────────────────────────

def abutted(items: list[dict]) -> list[dict]:
    """Storyline items as abutting clips (each transition centred on its cut, using the overlap as
    handles) — the form FCP7 XML, FCPXML and CapCut expect. Returns copies with rec_in/rec_out/src_in
    adjusted and `trans_in` kept (its duration straddles the new cut)."""
    out = [dict(x) for x in items]
    for i in range(1, len(out)):
        t = out[i].get("trans_in")
        if not t:
            continue
        d = t["dur"]
        cut = out[i]["rec_in"] + d / 2
        prev = out[i - 1]
        prev["rec_out"] = round(cut, 4)
        out[i]["src_in"] = round(out[i]["src_in"] + (cut - out[i]["rec_in"]) * out[i].get("speed", 1.0), 4)
        if out[i].get("kf"):
            out[i]["kf"] = shift_kf(out[i]["kf"], -(cut - out[i]["rec_in"]))
        out[i]["rec_in"] = round(cut, 4)
    return out


def shift_kf(kf: dict, dt: float) -> dict:
    return {k: [(round(t + dt, 4), v) for t, v in lst] for k, lst in kf.items()}


def box_at(item: dict, t: float) -> list[float]:
    """Interpolated box at item-relative time t."""
    kf = item.get("kf", {}).get("box")
    if not kf:
        return item["box"]
    if t <= kf[0][0]:
        return kf[0][1]
    for (t0, b0), (t1, b1) in zip(kf, kf[1:]):
        if t <= t1:
            u = (t - t0) / max(1e-9, t1 - t0)
            return [b0[i] + (b1[i] - b0[i]) * u for i in range(4)]
    return kf[-1][1]


def frames(t: float, fps: float) -> int:
    return int(math.floor(t * fps + 0.5))
