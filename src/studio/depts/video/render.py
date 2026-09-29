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
            raise ToolError(f"unknown loudness target {loud!r}", "youtube/streaming (-14), podcast (-16), broadcast (-23) or a number")
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
    C.ff(["-i", str(wav), "-af", f"apad,atrim=duration={dur:.4f}", "-c:a", "pcm_s24le", str(dest)], what="audio length")
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


def caption_style(cap: dict, tl: TL.Timeline, brand_colors: dict) -> dict:
    return {"style": cap.get("style", "reels" if tl.H > tl.W else "clean"),
            "accent": cap.get("accent") or brand_colors.get("accent") or "#FFD400",
            "position": cap.get("position", "auto"), "per": int(cap.get("words_per_chunk", 0) or 0),
            "size_scale": float(cap.get("size", 1.0) or 1.0), "uppercase": cap.get("uppercase")}


def make_captions(tl: TL.Timeline, words: list[dict], work: Path, fonts: dict, cst: dict, warnings: list[str]) -> tuple[Path, Path]:
    fd = work / "fonts"
    lat, ar, fw = K.font_files(fonts.get("head", "Montserrat"), fonts.get("head_ar", "Cairo"),
                               800 if cst["style"] in ("reels", "bold") else 700, fd)
    warnings += fw
    ass, chunks = K.build_ass(words, tl.W, tl.H, cst["style"], per=cst["per"], accent=cst["accent"], font=lat, font_ar=ar,
                              position=cst["position"], uppercase=cst["uppercase"], size_scale=cst["size_scale"])
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
           extra_data: dict | None = None) -> Result:
    dest = C.out_path(project, out, name, ".mp4")
    assets = dest.parent / f"{dest.stem}-assets"
    assets.mkdir(parents=True, exist_ok=True)
    work = C.scratch("edit-")
    warnings: list[str] = []
    data: dict = dict(extra_data or {})
    try:
        tl = TL.normalize(spec, assets, base_dir, brand)
        warnings += tl.warnings
        bcol, bfont = brand_bits(spec.get("brand", brand))
        audio = render_audio(tl, work, warnings, data)
        sub = fonts_dir = None
        srt = ass_keep = None
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
                kd = KD.write(tl, C.sibling(dest, "", ".kdenlive"), audio_duck=data.get("duck_regions"),
                              subtitles=ass_keep, fonts_dir=assets / "fonts" if ass_keep else None)
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
        if tl.assets:
            res.data["assets"] = [str(a) for a in tl.assets]
        else:
            shutil.rmtree(assets, ignore_errors=True) if not any(assets.iterdir()) else None
        res.next_steps.append("open the .kdenlive in Kdenlive (open_in_app) to fine-tune by hand; it references the "
                              "same source files and the -assets folder")
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
