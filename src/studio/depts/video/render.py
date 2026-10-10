"""Render a normalized Timeline: dialog/music audio → (auto) captions → one ffmpeg video pass → mux →
editable Kdenlive project validated with melt. Used by video_edit and by every tool that is really an
edit (concat, silence removal, speed ramps, B-roll, PiP…) so all of them also get a .kdenlive master."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ...core import qc
from ...core.result import Result, ToolError
from . import _common as C
from . import captions as K
from . import timeline as TL


def _target(loud) -> float | None:
    if loud in (None, False, "", "none", "off"):
        return None
    try:
        return float(loud)
    except (TypeError, ValueError):
        from ..audio._common import TARGETS
        k = str(loud).lower()
        if k not in TARGETS:
            raise ToolError(f"unknown loudness target {loud!r}", f"a LUFS number or one of {sorted(TARGETS)}")
        return TARGETS[k][0]


def render_audio(tl: TL.Timeline, work: Path, warnings: list[str], data: dict) -> Path | None:
    """→ final programme audio WAV (dialog + music with ducking, mastered), or None (silent edit)."""
    from ...core import registry
    dialog = None
    g = TL.build_dialog_graph(tl)
    if g:
        ins, graph, lab = g
        (work / "dialog.txt").write_text(graph)
        dialog = work / "dialog.wav"
        C.ff([*ins, "-filter_complex_script", str(work / "dialog.txt"), "-map", f"[{lab}]", "-ac", "2", "-ar", "48000",
              "-c:a", "pcm_s24le", str(dialog)], what="audio render")
        data.setdefault("_stems", {})["dialogue + sound"] = str(dialog)
    ducked = [a for a in tl.audio if a.duck]
    target = _target(tl.loudness)
    if ducked:
        if len(ducked) > 1:
            warnings.append("only the first ducked music track is ducked; add the others without duck")
        a = ducked[0]
        ins, graph, lab = TL.build_bed_graph(a, tl)
        (work / "bed.txt").write_text(graph)
        bed = work / "bed.wav"
        C.ff([*ins, "-filter_complex_script", str(work / "bed.txt"), "-map", f"[{lab}]", "-ac", "2", "-ar", "48000",
              "-c:a", "pcm_s24le", str(bed)], what="music bed")
        data.setdefault("_stems", {})["music"] = str(bed)
        if dialog is None:
            warnings.append("ducking needs speech on the timeline — the music plays at its own level")
            dialog = bed
        else:
            r = registry.call("audio_mix", {"voice": str(dialog), "music": str(bed), "duck_db": a.duck_db,
                                            "music_level_db": a.volume_db or -18.0, "intro": 0.0, "outro": 0.0,
                                            "fade_in": 0.0, "fade_out": 0.0, "loop_music": False,
                                            "target": str(target if target is not None else -14),
                                            "out": str(work / "mix.wav")})
            mix = Path(next(f for f in r.files if f.endswith(".wav")))
            data["duck_regions"] = r.data.get("duck_regions", [])
            data["mix"] = {k: r.data.get(k) for k in ("metrics",) if r.data.get(k)}
            warnings += [f"audio_mix: {w}" for w in r.warnings]
            return _fit_len(mix, tl.duration, work)
    if dialog is None:
        return None
    if target is not None:
        r = registry.call("audio_master", {"path": str(dialog), "target": str(target), "out": str(work / "master.wav")})
        mastered = Path(next(f for f in r.files if f.endswith(".wav")))
        warnings += [f"audio_master: {w}" for w in r.warnings if "silence" not in w]
        return _fit_len(mastered, tl.duration, work)
    return dialog


def _fit_len(wav: Path, dur: float, work: Path) -> Path:
    info = C.probe(wav)
    if abs(info["duration"] - dur) < 0.02:
        return wav
    dest = work / (wav.stem + "-fit.wav")
    C.ff(["-i", str(wav), "-af", f"asetpts=N/SR/TB,apad,atrim=duration={dur:.4f}", "-c:a", "pcm_s24le", str(dest)], what="audio length")
    return dest


def caption_words(tl: TL.Timeline, audio: Path | None, work: Path, warnings: list[str], data: dict) -> tuple[list[dict], list[dict]]:
    """→ (words, sentence segments) in programme time."""
    cap = tl.captions or {}
    if cap.get("words"):
        ws = K.clean_words(cap["words"])
        return ws, K.segments_from_words(ws)
    if cap.get("srt"):
        segs = K.parse_srt(cap["srt"])
        warnings.append("caption word timing estimated from the .srt (pass words or auto=true for exact timing)")
        return K.words_from_segments(segs), segs
    if cap.get("auto") or cap.get("transcribe"):
        if audio is None:
            raise ToolError("captions auto=true but the edit has no audio to transcribe")
        from ...core import registry
        args = {"path": str(audio), "language": cap.get("language", "auto"), "formats": ["json"],
                "out": str(work / "transcript"), "allow_download": bool(cap.get("allow_download", False))}
        for k in ("model", "speed", "dialect", "prompt", "hotwords"):
            if cap.get(k):
                args[k] = cap[k]
        r = registry.call("audio_transcribe", args)
        words = r.data.get("words") or []
        data["transcript_language"] = r.data.get("language")
        data["transcript_confidence"] = r.data.get("confidence")
        if r.data.get("flagged_segments"):
            warnings.append(f"{len(r.data['flagged_segments'])} transcript segment(s) flagged as uncertain — review the .srt")
        ws = K.clean_words(words)
        segs = []
        jp = next((f for f in r.files if f.endswith(".json")), None)
        if jp:
            try:
                j = json.loads(Path(jp).read_text(encoding="utf-8"))
                segs = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in j.get("segments", [])]
            except Exception:
                segs = []
        # subtitle cues from the word timings (≤ 12 words, sentence/pause aware) — Whisper segments can run 10 s+
        return ws, K.segments_from_words(ws) if ws else segs
    raise ToolError("captions: pass auto=true (transcribe), srt=<file> or words=[…]")


def alpha_captions(ass: Path, fonts_dir: Path, tl: TL.Timeline, dest: Path) -> Path:
    """Captions as a transparent video. libass blends colour but never writes alpha, so render the
    subtitles over black AND over white: alpha = 1 - (white - black), colour = black / alpha."""
    import subprocess
    import numpy as np
    W, H = tl.W, tl.H
    fps = TL.fps_str(tl.fps)
    vf = f"ass=filename='{C.ff_path(ass)}':fontsdir='{C.ff_path(fonts_dir)}'"

    def src(col):
        return subprocess.Popen(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                 f"color=c={col}:s={W}x{H}:r={fps}:d={tl.duration:.3f}", "-vf", vf + ",format=rgb24",
                                 "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    pb, pw = src("black"), src("white")
    out = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
                            "-r", fps, "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb", str(dest)], stdin=subprocess.PIPE)
    n = W * H * 3
    try:
        while True:
            b, w = pb.stdout.read(n), pw.stdout.read(n)
            if len(b) < n or len(w) < n:
                break
            fb = np.frombuffer(b, np.uint8).reshape(H, W, 3).astype(np.float32)
            fw = np.frombuffer(w, np.uint8).reshape(H, W, 3).astype(np.float32)
            a = np.clip(255.0 - (fw - fb).mean(axis=2), 0, 255)
            colr = np.where(a[..., None] > 0, np.clip(fb * 255.0 / np.maximum(a[..., None], 1), 0, 255), 0)
            out.stdin.write(np.dstack([colr, a]).astype(np.uint8).tobytes())
    finally:
        out.stdin.close()
        out.wait()
        pb.wait()
        pw.wait()
    if out.returncode != 0 or not dest.exists():
        raise ToolError("could not render the caption overlay for Kdenlive")
    return dest


def caption_style(cap: dict, tl: TL.Timeline, brand_colors: dict) -> dict:
    return {"style": cap.get("style", "reels" if tl.H > tl.W else "clean"),
            "accent": cap.get("accent") or brand_colors.get("accent") or "#FFD400",
            "position": cap.get("position", "auto"), "per": int(cap.get("words_per_chunk", 0) or 0),
            "size_scale": float(cap.get("size", 1.0) or 1.0), "uppercase": cap.get("uppercase"),
            "font": cap.get("font", ""), "font_ar": cap.get("font_ar", "")}


# Arabic caption faces that render correctly in BOTH libass paths (ffmpeg with a fonts folder, and
# MLT/Kdenlive through fontconfig). Display faces whose hamza/marks are built with GSUB marks (El Messiri,
# Cairo…) show boxes in the Kdenlive path, so Arabic captions use a safe face unless font_ar is given.
SAFE_AR_CAPTION = ("Tajawal",)


def make_captions(tl: TL.Timeline, words: list[dict], work: Path, fonts: dict, cst: dict, warnings: list[str]) -> tuple[Path, Path]:
    fd = work / "fonts"
    ar_fam = cst.get("font_ar") or (fonts.get("body_ar") if fonts.get("body_ar") in SAFE_AR_CAPTION else SAFE_AR_CAPTION[0])
    lat, ar, fw = K.font_files(cst.get("font") or fonts.get("head", "Montserrat"), ar_fam,
                               800 if cst["style"] in ("reels", "bold") else 700, fd)
    warnings += fw
    ass, chunks = K.build_ass(words, tl.W, tl.H, cst["style"], per=cst["per"], accent=cst["accent"], font=lat, font_ar=ar,
                              position=cst["position"], uppercase=cst["uppercase"], size_scale=cst["size_scale"])
    # kept for the pro-NLE project package: the same chunks become native, editable caption layers
    cst["native"] = {"chunks": [{"start": round(c["start"], 3), "end": round(c["end"], 3),
                                 "words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in c["words"]]}
                                for c in chunks],
                     "font": lat, "font_ar": ar, "family": cst.get("font") or fonts.get("head", "Montserrat"), "family_ar": ar_fam,
                     "geometry": K.caption_geometry(tl.W, tl.H, cst["style"], cst["position"], cst["size_scale"])}
    ap = work / "captions.ass"
    ap.write_text(ass, encoding="utf-8")
    return ap, fd


def brand_bits(brand: str) -> tuple[dict, dict]:
    if not brand:
        return {}, {}
    from ..motion import brand_theme
    th = brand_theme(brand)
    return th["colors"], th["fonts"]


def render(spec: dict, *, project: str, out: str, name: str, base_dir: Path | None = None, brand: str = "",
           kdenlive: bool = True, validate: bool = True, preset_crf: int = 18, summary: str = "",
           extra_data: dict | None = None, projects="all", project_media: str = "copy", host_map=None,
           mogrts: bool = False) -> Result:
    dest = C.out_path(project, out, name, ".mp4")
    assets = dest.parent / f"{dest.stem}-assets"
    assets.mkdir(parents=True, exist_ok=True)
    work = C.scratch("edit-")
    warnings: list[str] = []
    data: dict = dict(extra_data or {})
    try:
        if spec.get("cut_to_beats"):   # snap clip lengths to the music's beats (pro_beats)
            from .pro_beats import apply_cut_to_beats
            spec, cinfo = apply_cut_to_beats(spec, base_dir)
            warnings += cinfo.pop("warnings", [])
            data["cut_to_beats"] = cinfo
        tl = TL.normalize(spec, assets, base_dir, brand)
        warnings += tl.warnings
        bcol, bfont = brand_bits(spec.get("brand", brand))
        audio = render_audio(tl, work, warnings, data)
        sub = fonts_dir = None
        srt = ass_keep = None
        cst = None
        if tl.captions:
            words, segs = caption_words(tl, audio, work, warnings, data)
            if not words:
                warnings.append("no words to caption (silent audio?) — captions skipped")
            else:
                cst = caption_style(tl.captions, tl, bcol)
                sub, fonts_dir = make_captions(tl, words, work, {**{"head": "Montserrat", "head_ar": "Cairo"}, **bfont}, cst, warnings)
                srt = C.sibling(dest, "", ".srt")
                K.write_srt(segs, srt)
                ass_keep = C.sibling(dest, "", ".ass")
                shutil.copy2(sub, ass_keep)
                fk = assets / "fonts"
                shutil.copytree(fonts_dir, fk, dirs_exist_ok=True)
                data["captions"] = {"style": cst["style"], "words": len(words), "srt": str(srt), "ass": str(ass_keep)}
        ins, graph, lab = TL.build_video_graph(tl, sub, fonts_dir)
        (work / "video.txt").write_text(graph, encoding="utf-8")
        vtmp = work / "video.mp4"
        C.ff([*ins, "-filter_complex_script", str(work / "video.txt"), "-map", f"[{lab}]", "-an",
              *C.v_encode_args(preset_crf, "medium"), "-r", TL.fps_str(tl.fps), str(vtmp)], what="video render")
        if audio is not None:
            C.ff(["-i", str(vtmp), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "copy", *C.a_encode_args("192k"),
                  "-shortest", "-movflags", "+faststart", str(dest)], what="mux")
        else:
            shutil.move(str(vtmp), dest)
        res = Result(summary or f"Rendered the edit: {len(tl.clips)} clip(s), {len(tl.overlays)} overlay(s), "
                     f"{tl.duration:.2f}s at {tl.W}x{tl.H} {tl.fps:g} fps.", files=[str(dest)], warnings=warnings)
        if srt:
            res.files += [str(srt), str(ass_keep)]
        data.update({"duration": tl.duration, "size": f"{tl.W}x{tl.H}", "fps": tl.fps,
                     "clips": [{"src": str(c.src) if c.src else c.kind, "start": c.start, "duration": round(c.dur, 3),
                                "in": c.src_in, "out": c.src_out, "speed": c.speed, "transition": c.trans_name or "cut"}
                               for c in tl.clips]})
        res.data.update(data)
        C.finish(res, dest, expect={"width": tl.W, "height": tl.H, "duration": tl.duration, "audio": audio is not None},
                 target_lufs=_target(tl.loudness) if audio is not None else None)
        if kdenlive:
            from . import kdenlive as KD
            try:
                kd_subs, kd_tl = ass_keep, tl
                if ass_keep and K.is_rtl(Path(ass_keep).read_text(encoding="utf-8")):
                    # MLT's subtitle filter mis-shapes Arabic (boxes for hamza/final forms) — ffmpeg's does not.
                    # So the Kdenlive project gets the captions as a transparent video on the top track, rendered
                    # by ffmpeg exactly like the MP4; the .srt/.ass stay beside it for re-timing or re-styling.
                    import copy
                    cap_mov = C.sibling(dest, "-captions", ".mov")
                    fdir = assets / "fonts"
                    alpha_captions(Path(ass_keep), assets / "fonts", tl, cap_mov)
                    kd_tl = copy.copy(tl)
                    kd_tl.overlays = list(tl.overlays) + [TL.Overlay("alpha", cap_mov, 0.0, tl.duration, label="Captions (burned)")]
                    kd_subs = None
                    res.files.append(str(cap_mov))
                kd = KD.write(kd_tl, C.sibling(dest, "", ".kdenlive"), audio_duck=data.get("duck_regions"),
                              subtitles=kd_subs, fonts_dir=assets / "fonts" if kd_subs else None)
                if ass_keep and kd_subs is None:
                    KD.LAST_NOTES.append("Kdenlive: Arabic captions are a transparent video track (MLT mis-shapes Arabic "
                                         "subtitles); edit wording/timing in the .srt and re-run, or import the .srt as a subtitle track")
                res.files.append(str(kd))
                if validate:
                    v = KD.validate(kd, dest, work)
                    res.data["kdenlive_check"] = v
                    if v.get("preview"):
                        res.previews.append(v["preview"])
                    res.warnings += v.get("warnings", [])
                res.warnings += list(dict.fromkeys(KD.LAST_NOTES))
            except ToolError as e:
                res.warnings.append(f"Kdenlive project not written: {e}")
        if projects and projects not in ("none", "off", False):
            _package(res, tl, dest, name, spec, data, audio, cst if tl.captions and srt else None, srt, ass_keep,
                     projects, project_media, host_map, mogrts, work)
        if tl.assets:
            res.data["assets"] = [str(a) for a in tl.assets]
        else:
            shutil.rmtree(assets, ignore_errors=True) if not any(assets.iterdir()) else None
        res.next_steps.append("open the .kdenlive in Kdenlive (open_in_app) to fine-tune by hand; it references the "
                              "same source files and the -assets folder")
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _package(res: Result, tl, dest: Path, name: str, spec: dict, data: dict, audio, cst, srt, ass, projects, media, host_map,
             mogrts: bool, work: Path) -> None:
    """The same edit as editable projects for every pro app (video/nle) — one portable folder next to the MP4."""
    from .nle import APPS
    from .nle import package as PK
    apps = list(APPS) if projects in (True, "all", None) else [a.strip() for a in (projects if isinstance(projects, list) else str(projects).split(","))]
    stems = dict(data.pop("_stems", {}) or {})
    if audio is not None:
        stems["full mix"] = str(audio)
    side = {"duck_regions": data.get("duck_regions"), "loudness_target": _target(tl.loudness), "stems": stems}
    try:
        if audio is not None and stems.get("dialogue + sound"):
            m_a, m_d = qc.loudness(Path(audio)), qc.loudness(Path(stems["dialogue + sound"]))
            if m_a.get("lufs") is not None and m_d.get("lufs") is not None and not data.get("duck_regions"):
                side["dialog_gain_db"] = round(float(m_a["lufs"]) - float(m_d["lufs"]), 2)
    except Exception:
        pass
    if cst and cst.get("native"):
        nat = cst["native"]
        side["captions"] = {"chunks": nat["chunks"], "geometry": nat["geometry"], "style": cst["style"], "family": nat["family"],
                            "family_ar": nat["family_ar"], "accent": cst["accent"], "uppercase": cst.get("uppercase"),
                            "srt": str(srt) if srt else None, "ass": str(ass) if ass else None}
    try:
        r = PK.build(tl, master=dest, name=name if name not in ("edit", "") else dest.stem, side=side, out_dir=dest.parent, apps=apps,
                     media=media, host_map=host_map, make_mogrts=mogrts, spec=spec, work=work / "pkg")
    except Exception as e:
        import traceback
        res.warnings.append(f"project package not built: {e}")
        res.data["package_error"] = traceback.format_exc()[-2000:]
        return
    res.files.append(str(Path(r["dir"]) / "OPEN-IN.md"))
    res.previews.append(r["preview"])
    res.previews += r.get("gfx_previews", [])
    bad = {a: v for a, v in r["checks"].items() if v}
    res.data["project_package"] = {"dir": r["dir"], "apps": apps, "checks": r["checks"], "notes": r["notes"],
                                   "graphics_native": len(r["doc"]["graphics"]), "captions_native": bool(r["doc"].get("captions"))}
    for a, v in bad.items():
        res.warnings += [f"{a}: {x}" for x in v[:3]]
    res.next_steps.insert(0, f"open the editable projects: {Path(r['dir']).name}/OPEN-IN.md (Premiere, After Effects, Resolve, "
                             "Final Cut, CapCut, Avid/Pro Tools, Kdenlive)")
