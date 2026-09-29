"""Social-first tools: auto-captions, dead-air removal, smart reframe, platform export, thumbnails."""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from ...core import qc
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import render as R
from . import reframe as RF
from . import timeline as TL


# ─────────────────────────── captions ───────────────────────────

@tool("video")
def video_captions(path: str, style: str = "auto", language: str = "auto", srt: str = "", words: list = [],
                   position: str = "auto", accent: str = "", words_per_chunk: int = 0, uppercase: bool | None = None,
                   size: float = 1.0, model: str = "auto", dialect: str = "", allow_download: bool = False, brand: str = "",
                   project: str = "", out: str = "") -> Result:
    """Auto-captions burned into the video + a clean .srt (upload it to YouTube/LinkedIn) + the styled
    .ass. Speech is transcribed with audio_transcribe (word timestamps) unless srt= or words= is given.
    style: reels (big bold words, the spoken word gets an accent box, pop-in — Reels/TikTok/Shorts) |
    bold (spoken word turns accent-coloured) | karaoke (words fill with colour as spoken) | clean
    (sentence subtitles, soft shadow — YouTube/LinkedIn) | boxed (white on a dark box) | auto (reels
    for vertical, clean for landscape). Arabic is shaped and ordered right-to-left (Egyptian dialect:
    dialect='egyptian'). position: auto | bottom | lower | middle | top. accent: highlight colour
    (default brand accent or yellow). Also writes a .kdenlive with the captions as a subtitle filter."""
    p = C.src_path(path)
    info = C.need_video(p)
    if not info.get("audio") and not (srt or words):
        raise ToolError(f"{p.name} has no audio to transcribe", "pass srt=<file> or words=[…]")
    st = style if style != "auto" else ("reels" if info["height"] > info["width"] * 1.05 else "clean")
    cap = {"style": st, "position": position, "words_per_chunk": words_per_chunk, "size": size}
    if accent:
        cap["accent"] = accent
    if uppercase is not None:
        cap["uppercase"] = uppercase
    if words:
        cap["words"] = words
    elif srt:
        cap["srt"] = str(C.src_path(srt))
    else:
        cap.update({"auto": True, "language": language, "allow_download": allow_download})
        if model != "auto":
            cap["model"] = model
        if dialect:
            cap["dialect"] = dialect
    spec = {"clips": [{"src": str(p)}], "captions": cap, "loudness": None, "brand": brand}
    res = R.render(spec, project=project, out=out, name=f"{p.stem}-captions", brand=brand,
                   summary=f"Captioned {p.name} ({st} style).")
    return res


# ─────────────────────────── silence / dead air ───────────────────────────

@tool("video")
def video_remove_silence(path: str, max_pause: float = 0.35, threshold_db: float = -42.0, keep_edges: float = 0.12,
                         jump_zoom: float = 1.0, project: str = "", out: str = "") -> Result:
    """Cut dead air out of a talking video (jump-cut edit): every pause longer than max_pause seconds
    is shortened to max_pause, leading/trailing silence trimmed to keep_edges. Uses audio_trim_silence's
    speech detection (threshold_db relative to the loudest speech; raise to -35 for noisy rooms), then
    cuts the picture at the same points with click-free audio. jump_zoom: e.g. 1.12 alternates a
    punch-in on every other segment so jump cuts feel intentional. Returns MP4 + .kdenlive (every cut
    editable) + data.segments (kept source ranges) + seconds removed."""
    p = C.src_path(path)
    info = C.need_video(p)
    if not info.get("audio"):
        raise ToolError(f"{p.name} has no audio — nothing to detect silence in")
    from ...core import registry
    work = C.scratch("sil-")
    try:
        r = registry.call("audio_trim_silence", {"path": str(p), "max_pause": max_pause, "threshold_db": threshold_db,
                                                 "keep_edges": keep_edges, "out": str(work / "tight.wav")})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    segs = [(float(s["src_start"]), float(s["src_end"])) for s in r.data.get("keep_segments", [])]
    segs = [(a, min(b, info["duration"])) for a, b in segs if b - a > 0.08]
    if not segs:
        raise ToolError("no speech found", "lower threshold_db (e.g. -50)")
    clips = []
    for i, (a, b) in enumerate(segs):
        c = {"src": str(p), "in": a, "out": b}
        if jump_zoom and jump_zoom > 1 and i % 2 == 1:
            c.update({"zoom": jump_zoom, "focus": [0.5, 0.4]})
        clips.append(c)
    kept = sum(b - a for a, b in segs)
    res = R.render({"clips": clips, "loudness": None}, project=project, out=out, name=f"{p.stem}-tight",
                   summary=f"Removed dead air from {p.name}: {len(segs)} segment(s) kept, {info['duration']:.1f}s → {kept:.1f}s "
                           f"({info['duration'] - kept:.1f}s cut).")
    res.data.update({"segments": [{"start": round(a, 3), "end": round(b, 3)} for a, b in segs],
                     "removed_seconds": round(info["duration"] - kept, 2)})
    if kept / max(0.01, info["duration"]) < 0.5:
        res.warnings.append("more than half the video was removed — check threshold_db")
    return res


# ─────────────────────────── smart reframe ───────────────────────────

def _aspect(a: str) -> float:
    s = str(a).strip().lower().replace("x", ":")
    if ":" in s:
        x, y = s.split(":")
        return float(x) / float(y)
    return float(s)


def reframe_file(p: Path, info: dict, aspect: float, out_size: tuple[int, int], dest: Path, mode: str, deadzone: float,
                 smoothness: float, work: Path, crf: int = 18) -> dict:
    W, H = info["width"], info["height"]
    ow, oh = out_size
    data: dict = {}
    if mode == "blur":
        C.ff(["-i", str(p), "-filter_complex",
              f"[0:v]split[a][b];[a]scale={ow // 4}:{oh // 4}:force_original_aspect_ratio=increase,crop={ow // 4}:{oh // 4},"
              f"gblur=sigma=12,eq=brightness=-0.06,scale={ow}:{oh}[bg];[b]scale={ow}:{oh}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]", "-map", "[v]", "-map", "0:a?",
              *C.v_encode_args(crf), *C.a_encode_args(), str(dest)], what="reframe (blur)")
        return {"mode": "blur"}
    if W / H >= aspect:     # crop width, full height (16:9 → 9:16, 1:1, 4:5)
        cw, ch = int(round(H * aspect / 2)) * 2, H
    else:                   # crop height (e.g. 9:16 → 16:9 or 1:1)
        cw, ch = W, int(round(W / aspect / 2)) * 2
    an = {"t": [], "x": [], "y": [], "kind": [], "cuts": []}
    if mode == "track":
        an = RF.analyse(p, info)
    data["faces_found_pct"] = round(100 * an["kind"].count("face") / max(1, an.get("samples", 1)), 1) if an["t"] else 0.0
    horizontal = cw < W
    if horizontal:
        frac = cw / W
        grid, path = RF.camera_path(an, info["duration"], frac, deadzone, smoothness)
        kf = RF.simplify(grid, path * W - cw / 2, tol=max(1.0, W * 0.002))
        xexpr = RF.crop_expr(kf, W - cw)
        yexpr = "0"
        data["keyframes"] = len(kf)
        plot = C.sibling(dest, "-track", "png")
        RF.plot(an, grid, path, frac, info["duration"], plot)
        data["plot"] = str(plot)
    else:
        ys = [y for y, k in zip(an["y"], an["kind"]) if k == "face"] or [0.45]
        cy = float(np.median(ys)) * H
        yexpr = str(int(max(0, min(H - ch, cy - ch * 0.42))))
        xexpr = "0"
        kf = [(0.0, 0.0)]
    script = work / "reframe.txt"
    script.write_text(f"[0:v]crop=w={cw}:h={ch}:x='{xexpr}':y='{yexpr}',scale={ow}:{oh}:flags=lanczos,setsar=1,format=yuv420p[v]")
    C.ff(["-i", str(p), "-filter_complex_script", str(script), "-map", "[v]", "-map", "0:a?", *C.v_encode_args(crf),
          *C.a_encode_args(), str(dest)], what="reframe")
    data.update({"crop": f"{cw}x{ch}", "mode": mode, "scene_cuts": an.get("cuts", []), "_kf": kf, "_cw": cw, "_ch": ch,
                 "_horizontal": horizontal, "_y": yexpr})
    return data


@tool("video")
def video_reframe(path: str, aspect: str = "9:16", mode: str = "track", size: str = "", deadzone: float = 0.1,
                  smoothness: float = 0.5, project: str = "", out: str = "") -> Result:
    """Smart reframe a landscape video for Reels/TikTok/Shorts (9:16), square (1:1) or feed (4:5) — the
    crop FOLLOWS the speaker's face (OpenCV YuNet; falls back to the most salient region when no face)
    like a camera operator: holds still inside a dead zone, eases into moves, re-frames instantly at
    scene cuts. mode: track (default) | center (static centre crop) | blur (whole frame over a blurred
    fill, for wide group shots). size: output size (default 1080-wide). deadzone: 0–0.3 of the crop
    width; smoothness: seconds of easing. Returns MP4, a tracking plot + contact sheet to LOOK at and
    a .kdenlive with the same moves as Transform keyframes."""
    p = C.src_path(path)
    info = C.need_video(p)
    ar = _aspect(aspect)
    out_size = C.parse_size(size) if size else ((1080, int(round(1080 / ar / 2)) * 2) if ar <= 1 else (int(round(1080 * ar / 2)) * 2, 1080))
    if mode not in ("track", "center", "blur"):
        raise ToolError(f"unknown mode {mode!r}", "track | center | blur")
    dest = C.out_path(project, out, f"{p.stem}-{aspect.replace(':', 'x')}", ".mp4")
    work = C.scratch("rf-")
    try:
        d = reframe_file(p, info, ar, out_size, dest, mode, deadzone, smoothness, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    res = Result(f"Reframed {p.name} {info['width']}x{info['height']} → {out_size[0]}x{out_size[1]} ({aspect}, {mode}).",
                 files=[str(dest)], data={k: v for k, v in d.items() if not k.startswith("_")})
    if d.get("plot"):
        res.previews.append(d["plot"])
        res.files.append(d["plot"])
    if mode == "track" and d.get("faces_found_pct", 0) < 30:
        res.warnings.append(f"faces found in only {d.get('faces_found_pct')}% of sampled frames — the crop followed "
                            "visual saliency there; check the preview (mode='blur' keeps the whole frame)")
    if d.get("_horizontal") and d.get("_ch") and out_size[1] / d["_ch"] > 1.9:
        res.warnings.append(f"the {d['_cw']}x{d['_ch']} crop is upscaled {out_size[1] / d['_ch']:.1f}× to {out_size[0]}x{out_size[1]} — expect softness (use 4K sources for vertical crops)")
    C.finish(res, dest, expect={"width": out_size[0], "height": out_size[1]})
    # editable master: Kdenlive project with the same moves as transform keyframes
    if mode != "blur":
        try:
            from . import kdenlive as KD
            tl = TL.Timeline(out_size[0], out_size[1], info["fps"] or 30, "#000000", [], assets_dir=dest.parent)
            c = TL.Clip("video", p, info["duration"], 0.0, info["duration"], 1.0, has_audio=bool(info.get("audio")),
                        src_w=info["width"], src_h=info["height"], src_dur=info["duration"], fit="cover")
            s = out_size[1] / d["_ch"] if d["_horizontal"] else out_size[0] / d["_cw"]
            fps = info["fps"] or 30
            if d["_horizontal"]:
                c.rect_kf = ";".join(f"{int(round(t * fps))}={-x * s:.1f} 0 {info['width'] * s:.1f} {info['height'] * s:.1f} 1"
                                     for t, x in d["_kf"])
            else:
                c.rect_kf = f"0=0 {-float(d['_y']) * s:.1f} {info['width'] * s:.1f} {info['height'] * s:.1f} 1"
            tl.clips = [c]
            tl.duration = info["duration"]
            tl.loudness = None
            kd = KD.write(tl, C.sibling(dest, "", ".kdenlive"))
            res.files.append(str(kd))
            wk = C.scratch("kdv-")
            try:
                v = KD.validate(kd, dest, wk)
            finally:
                shutil.rmtree(wk, ignore_errors=True)
            res.data["kdenlive_check"] = v
            if v.get("preview"):
                res.previews.append(v["preview"])
            res.warnings += v.get("warnings", [])
        except Exception as e:  # never lose the render over the master
            res.warnings.append(f"Kdenlive project not written: {e}")
    return res


# ─────────────────────────── export presets ───────────────────────────

@tool("video")
def video_export(path: str, preset: str = "youtube", fit: str = "auto", loudness: bool = True, trim_to_limit: bool = False,
                 project: str = "", out: str = "") -> Result:
    """Deliver a finished video to a platform spec: reels | tiktok | shorts (1080x1920) | youtube
    (1080p) | youtube_4k | linkedin | linkedin_square | instagram_feed (1080x1350) | x | whatsapp |
    prores (ProRes 422 HQ master .mov) | prores4444 | gif | webm | master. Sets size, fps, H.264
    high profile + bitrate cap, AAC 48 kHz, faststart, and masters loudness to the platform target
    (-14 LUFS, true peak -1). fit when the aspect ratio differs: auto (smart face-tracking reframe) |
    track | blur | pad | crop. Warns about platform length limits (trim_to_limit=true cuts to fit)."""
    p = C.src_path(path)
    info = C.need_video(p)
    pr = C.PRESETS.get(preset)
    if not pr:
        raise ToolError(f"unknown preset {preset!r}", ", ".join(C.PRESETS))
    work = C.scratch("exp-")
    warnings: list[str] = []
    data: dict = {"preset": preset, "note": pr["note"]}
    try:
        src = p
        D = info["duration"]
        if pr.get("max_s") and D > pr["max_s"]:
            if trim_to_limit:
                t = work / "trim.mp4"
                C.ff(["-i", str(src), "-t", f"{pr['max_s']:.3f}", "-c", "copy", str(t)], what="trim")
                src, D = t, pr["max_s"]
                warnings.append(f"trimmed to the {pr['max_s']}s limit of {preset}")
            else:
                warnings.append(f"{D:.0f}s is longer than {preset}'s {pr['max_s']}s limit (trim_to_limit=true cuts it)")
        W, H = info["width"], info["height"]
        size = pr.get("size")
        if size and (size[0] / size[1]) > (W / H) and W < size[0] * 0.9:
            pass
        # 1) geometry
        if size and abs((size[0] / size[1]) / (W / H) - 1) > 0.03:
            f = fit if fit != "auto" else "track"
            g = work / "geo.mp4"
            if f in ("track", "center", "blur"):
                d = reframe_file(src, C.probe(src), size[0] / size[1], size, g, f, 0.1, 0.5, work, crf=14)
                data["reframe"] = {k: v for k, v in d.items() if not k.startswith("_") and k != "plot"}
            elif f in ("pad", "crop"):
                vf = (f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease,pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2"
                      if f == "pad" else f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=increase,crop={size[0]}:{size[1]}")
                C.ff(["-i", str(src), "-vf", vf + ",setsar=1", "-map", "0:v", "-map", "0:a?", *C.v_encode_args(14),
                      *C.a_encode_args(), str(g)], what="fit")
            else:
                raise ToolError(f"unknown fit {fit!r}", "auto | track | blur | pad | crop")
            src = g
            data["fit"] = f
        # 2) loudness
        audio = None
        if info.get("audio") and loudness and pr.get("lufs") is not None:
            from ...core import registry
            wav = C.extract_audio(src, work / "a.wav")
            r = registry.call("audio_master", {"path": str(wav), "target": str(pr["lufs"]), "out": str(work / "m.wav")})
            audio = Path(next(f for f in r.files if f.endswith(".wav")))
        # 3) encode
        codec = pr.get("codec", "h264")
        ext = {"prores": ".mov", "prores4444": ".mov", "gif": ".gif", "vp9": ".webm"}.get(codec, ".mp4")
        dest = C.out_path(project, out, f"{p.stem}-{preset}", ext)
        ins = ["-i", str(src)] + (["-i", str(audio)] if audio else [])
        amap = ["-map", "1:a"] if audio else ["-map", "0:a?"]
        vf = []
        if size and codec == "h264":
            vf.append(f"scale={size[0]}:{size[1]}:flags=lanczos,setsar=1")
        fps = pr.get("fps") or 0
        if codec == "gif":
            w = min(480, info["width"])
            gfps = pr.get("fps", 12)
            C.ff(["-i", str(src), "-filter_complex",
                  f"[0:v]fps={gfps},scale={w}:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[pl];"
                  f"[b][pl]paletteuse=dither=sierra2_4a:diff_mode=rectangle", "-loop", "0", str(dest)], what="gif")
        elif codec in ("prores", "prores4444"):
            prof = ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"] if codec == "prores" else \
                   ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le" if info.get("alpha") else "yuv444p10le"]
            C.ff([*ins, "-map", "0:v", *amap, *prof, "-vendor", "apl0", "-c:a", "pcm_s24le", "-ar", "48000", str(dest)], what="prores")
        elif codec == "vp9":
            C.ff([*ins, "-map", "0:v", *amap, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-row-mt", "1", "-deadline", "good",
                  "-cpu-used", "2", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", "128k", "-ar", "48000", str(dest)], what="webm")
        else:
            C.ff([*ins, "-map", "0:v", *amap, *(["-vf", ",".join(vf)] if vf else []),
                  *C.v_encode_args(pr.get("crf", 18), "slow" if D < 120 else "medium", pr.get("maxrate", ""), fps or None),
                  *C.a_encode_args("192k"), "-shortest", str(dest)], what="export")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    res = Result(f"Exported {p.name} for {preset} ({pr['note']}).", files=[str(dest)], warnings=warnings, data=data)
    if codec == "gif" and dest.stat().st_size > 8e6:
        res.warnings.append(f"GIF is {dest.stat().st_size / 1e6:.1f} MB — trim it (video_trim) or use webm/mp4 for anything over a few seconds")
    exp = {"width": size[0], "height": size[1]} if size and codec == "h264" else None
    C.finish(res, dest, expect=exp, target_lufs=pr.get("lufs") if loudness and info.get("audio") and codec != "gif" else None,
             check_black=codec != "gif")
    return res


# ─────────────────────────── thumbnails ───────────────────────────

def _frame_scores(p: Path, info: dict, n: int, work: Path) -> list[dict]:
    from ..photo.saliency import detect_faces
    D = info["duration"]
    rows = []
    for i in range(n):
        t = D * (i + 0.5) / n
        f = work / f"c{i:03d}.png"
        C.frame_at(p, t, f, 640)
        if not f.exists():
            continue
        im = Image.open(f).convert("RGB")
        g = np.asarray(im.convert("L"), np.float32)
        lap = np.abs(g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]).var()
        mean = g.mean() / 255
        clip = float(((g < 8) | (g > 247)).mean())
        a = np.asarray(im, np.float32)
        rg, yb = a[..., 0] - a[..., 1], 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
        colorful = float(np.sqrt(rg.var() + yb.var()) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 100
        faces = detect_faces(im)
        fa = max((w * h for _, _, w, h in faces), default=0) / (im.width * im.height)
        score = (min(1.0, math.log1p(lap) / 7) * 1.0 + (1 - abs(mean - 0.5) * 2) * 0.6 - clip * 2 + min(1.0, colorful) * 0.6
                 + (0.8 + min(0.8, fa * 8) if faces else 0))
        rows.append({"t": round(t, 2), "file": f, "score": round(float(score), 3), "faces": len(faces), "sharpness": round(float(lap), 1),
                     "brightness": round(float(mean), 2)})
    return rows


@tool("video")
def video_thumbnail(path: str, count: int = 3, title: str = "", brand: str = "", template: str = "thumbnail",
                    project: str = "", out: str = "") -> Result:
    """Pick the best frames of a video for a thumbnail/cover: scores ~60 candidate frames for
    sharpness, exposure, colourfulness and faces (bigger face = better), keeps the top `count` spread
    across the video, saves them full-resolution + a comparison sheet. With title= it also designs a
    finished YouTube thumbnail from the best frame (design_create 'thumbnail' template, brand-aware,
    Arabic ok). Returns frames, the designed thumbnail and previews to LOOK at."""
    p = C.src_path(path)
    info = C.need_video(p)
    work = C.scratch("thumb-")
    try:
        rows = _frame_scores(p, info, 60 if info["duration"] > 20 else max(12, int(info["duration"] * 3)), work)
        if not rows:
            raise ToolError("could not read frames")
        rows.sort(key=lambda r: -r["score"])
        gap = info["duration"] / (count * 2.5)
        pick = []
        for r in rows:
            if all(abs(r["t"] - q["t"]) >= gap for q in pick):
                pick.append(r)
            if len(pick) >= count:
                break
        d = C.out_folder(project, out, p.stem + "-thumbs")
        files = []
        for k, r in enumerate(pick):
            f = d / f"{p.stem}-frame-{k + 1}-{r['t']:.1f}s.png"
            C.frame_at(p, r["t"], f)
            files.append(str(f))
            r["path"] = str(f)
        sheet = d / "candidates.png"
        qc.sheet_of_images(files, sheet, tile=420, cols=min(3, len(files)))
        res = Result(f"Best {len(files)} frame(s) of {p.name}: " + ", ".join(f"{r['t']}s (score {r['score']})" for r in pick),
                     files=files, previews=[str(sheet)],
                     data={"picked": [{k: v for k, v in r.items() if k != "file"} for r in pick]})
        if title:
            from ...core import registry
            args = {"template": template, "content": {"headline": title, "image": files[0]}, "size": "youtube_thumbnail",
                    "out": str(d)}
            if brand:
                args["brand"] = brand
            try:
                r = registry.call("design_create", args)
                res.files += r.files
                res.previews += r.previews
                res.warnings += [f"design: {w}" for w in r.warnings]
            except ToolError as e:
                res.warnings.append(f"thumbnail design failed: {e}")
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
