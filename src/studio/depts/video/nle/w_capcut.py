"""CapCut (desktop 8.x, macOS/Windows): a real CapCut draft folder (draft_content.json + draft_meta_info.json)
that opens in CapCut's project list. Carries: the main track (cuts, dissolves, speed, the clips' framing
and Ken Burns keyframes, blurred-canvas fill), overlay tracks (B-roll, PiP, logos), each graphic as its
shapes image + LIVE text (font file, size, colour, position — edit in CapCut), captions as live text, the
rendered graphics on a hidden track, music/SFX with volume keyframes (ducking) and fades.
Draft schema after capcut-cli-david (MIT, Rene Zander & David Beles) — see assets/capcut/LICENSE."""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path

from . import doc as D
from .common import clip_kf, db_to_gain, hex_rgb

TEMPLATE = Path(__file__).resolve().parents[3] / "assets" / "capcut" / "draft_content.json"
DISSOLVE = {"name": "Mix", "effect_id": "6724845717472416269", "resource_id": "6724845717472416269"}
SIZE_UNIT = 0.004   # one CapCut text-size unit as a fraction of the canvas height (size 15 ≈ 6 %) — calibrated on the Mac


def us(t: float) -> int:
    return int(round(t * 1_000_000))


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _companions(d: dict, kind: str, speed: float = 1.0, canvas_blur: float = 0.0) -> list[str]:
    M = d["materials"]
    sp = {"id": _uid(), "type": "speed", "speed": speed, "mode": 0, "curve_speed": None}
    ph = {"id": _uid(), "type": "placeholder_info", "error_path": "", "error_text": "", "meta_type": "none", "res_path": "", "res_text": ""}
    scm = {"id": _uid(), "type": "none", "audio_channel_mapping": 0, "is_config_open": False}
    voc = {"id": _uid(), "type": "vocal_separation", "choice": 0, "enter_from": "", "final_algorithm": "", "production_path": "",
           "removed_sounds": [], "time_range": None}
    M["speeds"].append(sp)
    M["placeholder_infos"].append(ph)
    M["sound_channel_mappings"].append(scm)
    M["vocal_separations"].append(voc)
    ids = [sp["id"], ph["id"], scm["id"], voc["id"]]
    if kind == "video":
        cv = {"id": _uid(), "type": "canvas_blur" if canvas_blur else "canvas_color", "album_image": "", "blur": canvas_blur,
              "color": "", "image": "", "image_id": "", "image_name": "", "source_platform": 0, "team_id": ""}
        mc = {"id": _uid(), "type": "material_color", "gradient_angle": 90, "gradient_colors": [], "gradient_percents": [],
              "height": 0, "is_color_clip": False, "is_gradient": False, "solid_color": "", "width": 0}
        M["canvases"].append(cv)
        M["material_colors"].append(mc)
        ids += [cv["id"], mc["id"]]
    return ids


def _segment(mat_id: str, track_id: str, rin: float, rout: float, src_in: float, speed: float, refs: list[str], render: int) -> dict:
    dur = rout - rin
    return {"id": _uid(), "material_id": mat_id, "raw_segment_id": track_id,
            "target_timerange": {"start": us(rin), "duration": us(dur)},
            "source_timerange": {"start": us(src_in), "duration": us(dur * speed)},
            "speed": speed, "volume": 1.0, "visible": True, "reverse": False,
            "clip": {"alpha": 1.0, "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0},
                     "flip": {"horizontal": False, "vertical": False}},
            "render_index": render, "track_render_index": 0, "track_attribute": 0, "extra_material_refs": refs,
            "common_keyframes": [], "keyframe_refs": [], "enable_adjust": True, "enable_color_curves": True,
            "enable_color_wheels": True, "enable_lut": True, "is_placeholder": False, "intensifies_audio": False,
            "last_nonzero_volume": 1.0, "group_id": "", "cartoon": False, "template_id": "", "template_scene": "default"}


def _kf(seg: dict, prop: str, pts: list[tuple[float, float]]):
    if not pts:
        return
    seg["common_keyframes"].append({"id": _uid(), "material_id": "", "property_type": prop, "keyframe_list": [
        {"id": _uid(), "curveType": "Line", "time_offset": us(t), "left_control": {"x": 0.0, "y": 0.0},
         "right_control": {"x": 0.0, "y": 0.0}, "values": [float(v)], "string_value": "", "graphID": ""} for t, v in pts]})


def _place(seg: dict, box: list[float], sw: int, sh: int, W: int, H: int) -> tuple[float, float, float]:
    """CapCut clip transform: scale relative to 'fit' (contain), x/y in half-canvas units, +y up."""
    fit = min(W / max(1, sw), H / max(1, sh))
    sc = box[2] / max(1e-6, sw * fit)
    cx = (box[0] + box[2] / 2 - W / 2) / (W / 2)
    cy = -(box[1] + box[3] / 2 - H / 2) / (H / 2)
    return sc, cx, cy


def write(d: dict, draft_dir: Path, name: str) -> Path:
    W, H = d["W"], d["H"]
    draft = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    draft.update({"id": _uid(), "name": name, "fps": float(d["fps"]), "duration": us(d["duration"])})
    draft["canvas_config"] = {"ratio": "original", "width": W, "height": H, "background": None}
    M = draft["materials"]
    for k in ("speeds", "placeholder_infos", "sound_channel_mappings", "vocal_separations", "canvases", "material_colors",
              "videos", "audios", "texts", "transitions", "audio_fades"):
        M.setdefault(k, [])
    vmat: dict[str, str] = {}
    amat: dict[str, str] = {}

    def video_mat(mid: str) -> str:
        if mid in vmat:
            return vmat[mid]
        m = d["media"][mid]
        photo = m["kind"] == "image"
        mat = {"id": _uid(), "path": m["host"], "material_name": m["name"], "type": "photo" if photo else "video",
               "duration": 10_800_000_000 if photo else us(m.get("dur") or 0), "width": m.get("w") or W, "height": m.get("h") or H,
               "category_id": "", "category_name": "local", "check_flag": 63487 if not photo else 7,
               "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0, "lower_right_y": 1.0,
                        "upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0, "upper_right_y": 0.0},
               "crop_ratio": "free", "crop_scale": 1.0, "has_audio": bool(m.get("has_audio")) and not photo,
               "extra_type_option": 0, "formula_id": "", "freeze": None, "intensifies_audio_path": "", "intensifies_path": "",
               "is_ai_generate_content": False, "is_copyright": False, "is_text_edit_overdub": False, "is_unified_beauty_mode": False,
               "local_id": "", "local_material_id": "", "material_url": "", "media_path": "", "object_locked": None,
               "origin_material_id": "", "request_id": "", "reverse_path": "", "source_platform": 0,
               "stable": {"matrix_path": "", "stable_level": 0, "time_range": {"duration": 0, "start": 0}}, "team_id": "",
               "video_algorithm": {"algorithms": [], "deflicker": None, "motion_blur_config": None, "noise_reduction": None,
                                   "path": "", "quality_enhance": None, "time_range": None}}
        M["videos"].append(mat)
        vmat[mid] = mat["id"]
        return mat["id"]

    def audio_mat(mid: str) -> str:
        if mid in amat:
            return amat[mid]
        m = d["media"][mid]
        mat = {"id": _uid(), "path": m["host"], "name": m["name"], "duration": us(m.get("dur") or 0), "type": "extract_music",
               "category_id": "", "category_name": "local", "check_flag": 1, "music_id": "", "request_id": "", "source_platform": 0,
               "team_id": "", "text_id": "", "tone_category_id": "", "tone_category_name": "", "tone_effect_id": "",
               "tone_effect_name": "", "tone_platform": "", "tone_second_category_id": "", "tone_second_category_name": "",
               "tone_speaker": "", "tone_type": "", "wave_points": []}
        M["audios"].append(mat)
        amat[mid] = mat["id"]
        return mat["id"]

    tracks = []

    def track(kind: str, name: str, hidden: bool = False) -> dict:
        t = {"id": _uid(), "type": kind, "name": name, "attribute": 1 if hidden else 0, "segments": [], "is_default_name": False, "flag": 0}
        tracks.append(t)
        return t
    render = [14000]

    def add_visual(t: dict, it: dict, canvas_blur: float = 0.0):
        m = d["media"][it["mid"]]
        mid = video_mat(it["mid"])
        refs = _companions(draft, "video", it.get("speed", 1.0), canvas_blur)
        seg = _segment(mid, t["id"], it["rec_in"], it["rec_out"], 0.0 if m["kind"] == "image" else it["src_in"], it.get("speed", 1.0),
                       refs, render[0])
        render[0] += 1
        sw, sh = m.get("w") or W, m.get("h") or H
        sc, cx, cy = _place(seg, it["box"], sw, sh, W, H)
        seg["clip"]["scale"] = {"x": round(sc, 5), "y": round(sc, 5)}
        seg["clip"]["transform"] = {"x": round(cx, 5), "y": round(cy, 5)}
        seg["clip"]["alpha"] = float(it.get("opacity", 1.0))
        seg["volume"] = 0.0     # the sound lives on its own audio track (J/L cuts)
        kb = it.get("kf", {}).get("box")
        if kb:
            ks = clip_kf(kb, 0.0, it["rec_out"] - it["rec_in"])
            pl = [(t, _place(seg, b, sw, sh, W, H)) for t, b in ks]
            _kf(seg, "KFTypeScaleX", [(t, p[0]) for t, p in pl])
            _kf(seg, "KFTypeScaleY", [(t, p[0]) for t, p in pl])
            _kf(seg, "KFTypePositionX", [(t, p[1]) for t, p in pl])
            _kf(seg, "KFTypePositionY", [(t, p[2]) for t, p in pl])
        ko = it.get("kf", {}).get("opacity")
        if ko:
            _kf(seg, "KFTypeAlpha", clip_kf(ko, 0.0, it["rec_out"] - it["rec_in"]))
        if it.get("trans_in"):
            tr = {"id": _uid(), "type": "transition", "name": DISSOLVE["name"], "effect_id": DISSOLVE["effect_id"],
                  "resource_id": DISSOLVE["resource_id"], "third_resource_id": DISSOLVE["resource_id"], "source_platform": 1,
                  "path": "", "duration": us(it["trans_in"]["dur"]), "is_overlap": True, "platform": "all", "category_id": "",
                  "category_name": "Transitions", "request_id": "", "is_ai_transition": False, "video_path": "", "task_id": ""}
            M["transitions"].append(tr)
            # CapCut keeps the transition on the OUTGOING clip
            if t["segments"]:
                t["segments"][-1]["extra_material_refs"].append(tr["id"])
        t["segments"].append(seg)
        return seg

    def add_text(t: dict, text: str, rin: float, rout: float, cx: float, cy: float, size_px: float, color: str,
                 font_file: str = "", bold: bool = False, stroke: float = 0.0, align: int = 1, anim: dict | None = None):
        r, g, b = hex_rgb(color)
        size = round(size_px / (H * SIZE_UNIT), 2)
        n = len(text.encode("utf-16-le")) // 2
        style = {"fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [r, g, b]}}},
                 "range": [0, n], "size": size, "bold": bold, "italic": False, "underline": False}
        if font_file:
            style["font"] = {"id": "", "path": font_file}
        if stroke:
            style["strokes"] = [{"content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}}, "width": min(0.2, stroke / max(1.0, size_px))}]
        mat = {"id": _uid(), "type": "text", "content": json.dumps({"styles": [style], "text": text}, ensure_ascii=False),
               "alignment": align, "font_size": size, "text_color": color, "font_path": font_file, "typesetting": 0,
               "letter_spacing": 0, "line_spacing": 0.02, "line_feed": 1, "line_max_width": 0.9, "force_apply_line_max_width": False,
               "check_flag": 7, "fixed_width": -1, "fixed_height": -1, "base_content": "", "fonts": []}
        M["texts"].append(mat)
        refs = _companions(draft, "text")
        seg = _segment(mat["id"], t["id"], rin, rout, 0.0, 1.0, refs, render[0])
        render[0] += 1
        seg["clip"]["transform"] = {"x": round(cx, 5), "y": round(cy, 5)}
        if anim:
            dur = rout - rin
            _kf(seg, "KFTypeAlpha", [(0.0, 0.0), (anim["in"], 1.0), (max(anim["in"], dur - anim["out"]), 1.0), (dur, 0.0)])
        t["segments"].append(seg)

    # ── video tracks (CapCut: first video track = main track)
    pic = next(l for l in d["video"] if l["role"] == "picture")
    backdrop_for = {b["for"] for l in d["video"] if l["role"] == "backdrop" for b in l["items"]}
    main = track("video", "Main")
    for it in D.abutted(pic["items"]):
        if "mid" in it:
            add_visual(main, it, canvas_blur=0.375 if it["id"] in backdrop_for else 0.0)
    for lane in d["video"]:
        if lane["role"] in ("overlay", "gfx_render"):
            t = track("video", lane["name"], hidden=lane["role"] == "gfx_render" and any(not i.get("enabled", True) for i in lane["items"]))
            for it in lane["items"]:
                if "mid" in it:
                    add_visual(t, it)
    # ── native graphics: shapes plate (image) + live text
    for lane in d["video"]:
        if lane["role"] != "gfx":
            continue
        pt = track("video", lane["name"] + " · shapes")
        ttracks: list[dict] = []
        for it in lane["items"]:
            g = d["graphics"][it["gfx"]]
            if g.get("plate_mid"):
                add_visual(pt, {"mid": g["plate_mid"], "rec_in": it["rec_in"], "rec_out": it["rec_out"], "src_in": 0.0,
                                "box": g["plate"]["box"], "opacity": 1.0, "kf": {}, "speed": 1.0})
                seg = pt["segments"][-1]
                dur = it["rec_out"] - it["rec_in"]
                _kf(seg, "KFTypeAlpha", [(0.0, 0.0), (g["anim"]["in"], 1.0), (max(g["anim"]["in"], dur - g["anim"]["out"]), 1.0), (dur, 0.0)])
            for k, lay in enumerate([l for l in g["layers"] if l["type"] == "text"]):
                while len(ttracks) <= k:      # one text track per layer (CapCut tracks cannot overlap)
                    ttracks.append(track("text", f"{lane['name']} · text {len(ttracks) + 1}"))
                tt = ttracks[k]
                bx = lay["box"]
                cx, cy = (bx[0] + bx[2] / 2 - W / 2) / (W / 2), -(bx[1] + bx[3] / 2 - H / 2) / (H / 2)
                add_text(tt, lay["text"], it["rec_in"], it["rec_out"], cx, cy, lay["size"], lay["color"], lay.get("font_file", ""),
                         bold=lay.get("weight", 400) >= 600, align={"left": 0, "right": 2}.get(lay.get("align"), 1), anim=g["anim"])
    # ── captions
    cap_lane = next((l for l in d["video"] if l["role"] == "captions"), None)
    if cap_lane and d.get("captions"):
        cs = d["captions"]["spec"]
        ct = track("text", "Captions")
        for it in cap_lane["items"]:
            rtl = any("؀" <= ch <= "ۿ" for ch in it["text"])
            f = cs["font_ar"] if rtl else cs["font"]
            text = it["text"] if (rtl or not cs["uppercase"]) else it["text"].upper()
            size = cs["size_ar"] if rtl else cs["size"]
            y = cs["anchor_y"] - (size * 0.6 if cs["valign"] == "bottom" else 0)
            add_text(ct, text, it["rec_in"], it["rec_out"], 0.0, -(y - H / 2) / (H / 2), size, cs["color"], f.get("file_host", ""),
                     bold=True, stroke=cs["outline"])
    # ── audio
    for lane in d["audio"]:
        t = track("audio", lane["name"])
        for a in lane["items"]:
            mid = audio_mat(a["mid"])
            refs = _companions(draft, "audio", a.get("speed", 1.0))
            if a.get("fade_in") or a.get("fade_out"):
                fd = {"id": _uid(), "type": "audio_fade", "fade_in_duration": us(a.get("fade_in") or 0),
                      "fade_out_duration": us(a.get("fade_out") or 0), "fade_type": 0}
                M["audio_fades"].append(fd)
                refs.append(fd["id"])
            seg = _segment(mid, t["id"], a["rec_in"], a["rec_out"], a["src_in"], a.get("speed", 1.0), refs, 11000)
            seg["volume"] = round(db_to_gain(a.get("gain_db", 0.0)), 4)
            if a.get("kf"):
                _kf(seg, "KFTypeVolume", [(tt, db_to_gain(v)) for tt, v in a["kf"]])
            t["segments"].append(seg)
    draft["tracks"] = tracks
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "draft_content.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    now = int(time.time() * 1_000_000)
    meta = {"cloud_draft_cover": False, "cloud_draft_sync": False, "draft_cover": "draft_cover.jpg", "draft_fold_path": str(draft_dir),
            "draft_id": draft["id"], "draft_name": draft_dir.name, "draft_new_version": "164.0.0", "draft_root_path": str(draft_dir.parent),
            "draft_timeline_materials_size_": 0, "draft_type": "", "draft_materials": [{"type": t, "value": []} for t in (0, 1, 2, 3, 6, 7, 8)],
            "draft_is_invisible": False, "tm_draft_create": now, "tm_draft_modified": now, "tm_draft_removed": 0,
            "tm_duration": us(d["duration"]), "draft_removable_storage_device": ""}
    (draft_dir / "draft_meta_info.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return draft_dir


INSTALL = r'''#!/bin/bash
# Put this CapCut draft into CapCut's project list (close CapCut first — it rewrites its index on quit).
cd "$(dirname "$0")"
ROOT="$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft"
[ -d "$ROOT" ] || { echo "CapCut projects folder not found: $ROOT (open CapCut once first)"; exit 1; }
if pgrep -il capcut >/dev/null; then echo "Quit CapCut first, then run this again."; exit 1; fi
SRC="__DRAFT__"; DEST="$ROOT/$SRC"
[ -e "$DEST" ] && DEST="$ROOT/$SRC $(date +%H%M%S)"
cp -R "$SRC" "$DEST"
python3 - "$DEST" "$ROOT" <<'PY'
import json, os, sys, time, uuid
dest, root = sys.argv[1], sys.argv[2]
mp = os.path.join(dest, "draft_meta_info.json"); m = json.load(open(mp))
m.update({"draft_fold_path": dest, "draft_root_path": root, "draft_name": os.path.basename(dest),
          "tm_draft_modified": int(time.time() * 1e6)})
rp = os.path.join(root, "root_meta_info.json")
r = json.load(open(rp)) if os.path.exists(rp) else {"all_draft_store": [], "draft_ids": [], "root_path": root}
if any(e.get("draft_id") == m["draft_id"] for e in r["all_draft_store"]): m["draft_id"] = str(uuid.uuid4()).upper()
json.dump(m, open(mp, "w"))
r["all_draft_store"].append({"draft_cover": os.path.join(dest, "draft_cover.jpg"), "draft_fold_path": dest, "draft_id": m["draft_id"],
    "draft_json_file": os.path.join(dest, "draft_content.json"), "draft_name": m["draft_name"], "draft_new_version": "164.0.0",
    "draft_root_path": root, "draft_timeline_materials_size": 0, "draft_type": "", "streaming_edit_draft_ready": True,
    "tm_draft_create": m["tm_draft_create"], "tm_draft_modified": m["tm_draft_modified"], "tm_draft_removed": 0, "tm_duration": m["tm_duration"],
    "cloud_draft_cover": False, "cloud_draft_sync": False, "draft_is_invisible": False, "draft_is_ai_shorts": False,
    "draft_is_cloud_temp_draft": False, "draft_is_web_article_video": False, "draft_cloud_last_action_download": False,
    "draft_cloud_purchase_info": "", "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "", "draft_cloud_videocut_purchase_info": "",
    "draft_web_article_video_enter_from": "", "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1, "tm_draft_cloud_modified": 0,
    "tm_draft_cloud_parent_entry_id": -1, "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1})
r["draft_ids"] = list(dict.fromkeys(r.get("draft_ids", []) + [m["draft_id"]]))
json.dump(r, open(rp, "w"))
print("Added to CapCut:", dest)
PY
echo "Open CapCut — the project '$(basename "$DEST")' is at the top of the list."
'''


def check(draft_dir: Path, d: dict) -> list[str]:
    issues = []
    try:
        dc = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    except Exception as e:
        return [f"draft_content.json unreadable: {e}"]
    mats = {m["id"] for k, v in dc["materials"].items() if isinstance(v, list) for m in v if isinstance(m, dict) and "id" in m}
    for t in dc["tracks"]:
        last_end = -1
        for s in t["segments"]:
            if s["material_id"] not in mats:
                issues.append(f"track {t['name']}: segment → missing material")
            for r in s["extra_material_refs"]:
                if r not in mats:
                    issues.append(f"track {t['name']}: missing companion material")
            st = s["target_timerange"]["start"]
            if st < last_end - 2:
                issues.append(f"track {t['name']}: overlapping segments at {st / 1e6:.3f}s")
            last_end = st + s["target_timerange"]["duration"]
    if dc["duration"] < us(d["duration"]) - 50_000:
        issues.append("draft duration shorter than the edit")
    return issues
