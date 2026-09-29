"""Finishing: colour grade / LUT, stabilisation, music with ducking, loudness, rendering Kdenlive projects."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image

from ...core import qc
from ...core.deps import DEPS, find_bin
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import render as R


def _before_after(src: Path, dest_video: Path, sheet: Path, n: int = 3) -> Path:
    info = C.probe(dest_video)
    work = C.scratch("ba-")
    try:
        pairs = []
        for i in range(n):
            t = info["duration"] * (i + 0.5) / n
            a, b = work / f"a{i}.png", work / f"b{i}.png"
            C.frame_at(src, t, a, 480)
            C.frame_at(dest_video, t, b, 480)
            if a.exists() and b.exists():
                pairs.append((Image.open(a).convert("RGB"), Image.open(b).convert("RGB")))
        tw, th = pairs[0][0].size
        im = Image.new("RGB", (2 * tw + 18, n * (th + 6) + 30), (22, 22, 26))
        from PIL import ImageDraw
        d = ImageDraw.Draw(im)
        d.text((6, 8), "BEFORE", fill=(220, 220, 220))
        d.text((tw + 12, 8), "AFTER", fill=(220, 220, 220))
        for k, (a, b) in enumerate(pairs):
            im.paste(a, (6, 26 + k * (th + 6)))
            im.paste(b.resize(a.size), (tw + 12, 26 + k * (th + 6)))
        im.save(sheet)
        return sheet
    finally:
        shutil.rmtree(work, ignore_errors=True)


@tool("video")
def video_grade(path: str, preset: str = "", lut: str = "", strength: float = 1.0, exposure: float = 0.0,
                contrast: float = 0.0, saturation: float = 0.0, temperature: float = 0.0, vibrance: float = 0.0,
                vignette: float = 0.0, project: str = "", out: str = "") -> Result:
    """Colour-grade a video: a named look (teal_orange, film_portra, film_fuji, kodachrome, bleach_bypass,
    noir, bw_soft, vintage_warm, moody_matte, cross_process, golden_hour, clean_bright, blockbuster_cool,
    sepia — the SAME looks as photo_look, via its .cube export) or any .cube LUT (Resolve/LUT packs),
    blended by strength 0–1, plus manual exposure (stops-ish, ±1), contrast/saturation (−1…+1),
    temperature (−1 cool … +1 warm), vibrance, vignette 0–1. Returns the graded MP4, a BEFORE/AFTER
    sheet and a .kdenlive with the LUT as an effect."""
    p = C.src_path(path)
    info = C.need_video(p)
    g = {"preset": preset, "lut": str(C.src_path(lut)) if lut else "", "strength": strength, "exposure": exposure,
         "contrast": contrast, "saturation": saturation, "temperature": temperature, "vibrance": vibrance, "vignette": vignette}
    g = {k: v for k, v in g.items() if v not in ("", 0, 0.0) or k == "strength"}
    if not any(k in g for k in ("preset", "lut", "exposure", "contrast", "saturation", "temperature", "vibrance", "vignette")):
        raise ToolError("nothing to do", "pass preset=<look>, lut=<file.cube> or an adjustment")
    res = R.render({"clips": [{"src": str(p), "grade": g}], "loudness": None}, project=project, out=out,
                   name=f"{p.stem}-{preset or Path(lut).stem if (preset or lut) else 'graded'}",
                   summary=f"Graded {p.name}: " + ", ".join(f"{k}={v}" for k, v in g.items()))
    ba = C.sibling(Path(res.files[0]), "-before-after", "png")
    _before_after(p, Path(res.files[0]), ba)
    res.previews.insert(0, str(ba))
    return res


@tool("video")
def video_stabilize(path: str, smoothing: int = 20, zoom: float = 0.0, tripod: bool = False, project: str = "",
                    out: str = "") -> Result:
    """Stabilise shaky handheld footage (two-pass vid.stab: motion analysis, then smooth camera path).
    smoothing: frames of smoothing each side (10 subtle … 40 gimbal-like); tripod=true locks the
    frame completely; zoom: extra % zoom to hide moving borders (0 = automatic optimal zoom).
    Falls back to ffmpeg's simpler 'deshake' when vid.stab isn't compiled in. Returns MP4 + a
    before/after sheet + measured shake reduction."""
    p = C.src_path(path)
    info = C.need_video(p)
    dest = C.out_path(project, out, f"{p.stem}-stable", ".mp4")
    work = C.scratch("stab-")
    try:
        method = "vidstab"
        if C.has_filter("vidstabdetect") and C.has_filter("vidstabtransform"):
            trf = work / "t.trf"
            C.ff(["-i", str(p), "-vf", f"vidstabdetect=shakiness=6:accuracy=12:result='{C.ff_path(trf)}'", "-f", "null", "-"],
                 what="stabilise (analyse)")
            opt = f"smoothing={int(smoothing)}:interpol=bicubic:" + (f"zoom={zoom}" if zoom else "optzoom=1:zoomspeed=0.25")
            if tripod:
                opt += ":tripod=1"
            vf = f"vidstabtransform=input='{C.ff_path(trf)}':{opt},unsharp=5:5:0.6:3:3:0.3,format=yuv420p"
        else:
            method = "deshake"
            vf = "deshake=rx=32:ry=32:edge=mirror,format=yuv420p"
        C.ff(["-i", str(p), "-vf", vf, "-map", "0:v", "-map", "0:a?", *C.v_encode_args(17), *C.a_encode_args(), str(dest)],
             what="stabilise")
        before = _shake(p, work)
        after = _shake(dest, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    red = round(100 * (1 - after / before), 1) if before > 0 else 0.0
    res = Result(f"Stabilised {p.name} with {method} (smoothing {smoothing}{', tripod' if tripod else ''}); "
                 f"frame-to-frame motion reduced {red}%.", files=[str(dest)],
                 data={"method": method, "shake_before": round(before, 3), "shake_after": round(after, 3), "reduction_pct": red})
    ba = C.sibling(dest, "-before-after", "png")
    _before_after(p, dest, ba)
    res.previews.append(str(ba))
    if method == "deshake":
        res.warnings.append("vid.stab not available in this ffmpeg — used 'deshake' (weaker); brew install ffmpeg includes vid.stab")
    if red < 10:
        res.warnings.append("little shake was removed — the footage may already be steady, or motion is intentional (pans)")
    return C.finish(res, dest, expect={"duration": info["duration"]})


def _shake(p: Path, work: Path) -> float:
    """Mean global frame-to-frame translation (px at 320 wide, first 20 s) via phase correlation — lower is steadier."""
    import numpy as np
    info = C.probe(p)
    sw = 320
    sh = int(round(info["height"] * sw / info["width"] / 2)) * 2
    cmd = [C.need("ffmpeg"), "-v", "error", "-i", str(p), "-t", "20", "-vf", f"fps=15,scale={sw}:{sh},format=gray",
           "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    a = np.frombuffer(r.stdout, np.uint8)
    n = len(a) // (sw * sh)
    if n < 3:
        return 0.0
    fr = a[: n * sw * sh].reshape(n, sh, sw).astype(np.float32)
    win = np.outer(np.hanning(sh), np.hanning(sw))
    # crop 10% border so stabiliser borders/zoom don't count
    ys, xs = slice(int(sh * .1), int(sh * .9)), slice(int(sw * .1), int(sw * .9))
    moves = []
    for i in range(1, n):
        A = np.fft.fft2((fr[i - 1] * win)[ys, xs])
        B = np.fft.fft2((fr[i] * win)[ys, xs])
        Rr = A * np.conj(B)
        Rr /= np.abs(Rr) + 1e-9
        c = np.abs(np.fft.ifft2(Rr))
        y, x = np.unravel_index(np.argmax(c), c.shape)
        y = y - c.shape[0] if y > c.shape[0] / 2 else y
        x = x - c.shape[1] if x > c.shape[1] / 2 else x
        moves.append((x, y))
    mv = np.array(moves, np.float32)
    # mean global frame-to-frame motion (includes deliberate pans, so compare before/after of the same clip)
    return float(np.sqrt((mv ** 2).sum(1)).mean())


@tool("video")
def video_music(path: str, music: str, duck_db: float = 10.0, music_level_db: float = -18.0, fade_in: float = 1.0,
                fade_out: float = 2.5, loop: bool = True, target: str = "streaming", project: str = "", out: str = "") -> Result:
    """Add a music bed under a video with automatic ducking: the music dips duck_db whenever someone
    speaks and swells in the gaps (audio_mix), fades in/out, loops short tracks with crossfades and
    masters the result to `target` (-14 LUFS streaming, 'podcast' -16, 'broadcast' -23). The picture
    is copied untouched. Returns the MP4 + a .kdenlive with the ducking as volume keyframes."""
    p = C.src_path(path)
    m = C.src_path(music)
    info = C.need_video(p)
    spec = {"clips": [{"src": str(p)}], "audio": [{"src": str(m), "volume_db": music_level_db, "duck": bool(info.get("audio")),
                                                  "duck_db": duck_db, "fade_in": fade_in, "fade_out": fade_out, "loop": loop}],
            "loudness": target}
    if not info.get("audio"):
        spec["audio"][0]["volume_db"] = 0.0
    return R.render(spec, project=project, out=out, name=f"{p.stem}-music",
                    summary=f"Added {m.name} under {p.name} ({'ducked ' + str(duck_db) + ' dB under speech' if info.get('audio') else 'no speech to duck'}), mastered to {target}.")


@tool("video")
def video_loudness(path: str, target: str = "youtube", true_peak: float = -1.0, project: str = "", out: str = "") -> Result:
    """Normalise a video's audio to a platform loudness target and report the MEASURED result: youtube/
    streaming/reels/tiktok (-14 LUFS), podcast/apple (-16), broadcast/ebu (-23), atsc (-24) or a number.
    True-peak limited (default -1 dBTP). The picture is stream-copied (no re-encode)."""
    p = C.src_path(path)
    info = C.probe(p)
    if not info.get("audio"):
        raise ToolError(f"{p.name} has no audio")
    from ...core import registry
    work = C.scratch("loud-")
    try:
        wav = C.extract_audio(p, work / "a.wav")
        before = qc.loudness(wav)
        r = registry.call("audio_master", {"path": str(wav), "target": str(target), "true_peak": true_peak, "out": str(work / "m.wav")})
        mw = Path(next(f for f in r.files if f.endswith(".wav")))
        dest = C.out_path(project, out, f"{p.stem}-{str(target).replace('-', 'm')}", p.suffix if p.suffix.lower() in (".mp4", ".mov", ".mkv") else ".mp4")
        vcopy = ["-c:v", "copy"] if info.get("video") else []
        C.ff(["-i", str(p), "-i", str(mw), "-map", "0:v?", "-map", "1:a", *vcopy, *C.a_encode_args("256k"), "-shortest",
              "-movflags", "+faststart", str(dest)], what="loudness mux")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tgt = R._target(target)
    res = Result(f"Loudness of {p.name}: {before.get('lufs')} LUFS → target {tgt} LUFS (true peak ≤ {true_peak} dBTP).",
                 files=[str(dest)], data={"before": {k: before.get(k) for k in ("lufs", "true_peak")}})
    return C.finish(res, dest, target_lufs=tgt, sheet=bool(info.get("video")), check_black=False)


@tool("video")
def video_render_project(project_file: str, size: str = "", preset: str = "youtube", project: str = "", out: str = "") -> Result:
    """Render a Kdenlive/Shotcut/MLT project (.kdenlive, .mlt) to MP4 headless with melt — e.g. after the
    user fine-tuned a studio edit by hand in Kdenlive. size: WxH (default the project's own). Returns
    the MP4, measured, with a contact sheet."""
    pf = C.src_path(project_file)
    melt = find_bin(DEPS["melt"])
    if not melt:
        raise ToolError("melt (MLT) is not installed", "brew install mlt   ·   apt install melt")
    dest = C.out_path(project, out, pf.stem + "-render", ".mp4")
    import os
    import platform
    pre = []
    if platform.system() == "Linux" and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        pre = [shutil.which("xvfb-run"), "-a"]
    cons = [f"avformat:{dest}", "vcodec=libx264", "crf=18", "preset=medium", "acodec=aac", "ab=192k", "ar=48000",
            "pix_fmt=yuv420p", "movflags=+faststart", "real_time=-1"]
    sz = C.parse_size(size) if size else None
    if sz:
        cons += [f"width={sz[0]}", f"height={sz[1]}"]
    import re
    txt = pf.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'frame_rate_num="(\d+)"\s+frame_rate_den="(\d+)"', txt)
    if m:
        cons += [f"frame_rate_num={m.group(1)}", f"frame_rate_den={m.group(2)}"]
    r = subprocess.run(pre + [melt, "-quiet", str(pf), "-consumer", *cons], capture_output=True, text=True, timeout=7200)
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size < 1000:
        raise ToolError(f"melt failed (exit {r.returncode}): {(r.stderr or r.stdout)[-600:]}")
    res = Result(f"Rendered {pf.name} with melt.", files=[str(dest)])
    return C.finish(res, dest)
