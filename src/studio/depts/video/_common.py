"""Shared helpers for the video department (no @tool here): paths, probing, ffmpeg runs, encode
settings, platform presets, colour-grade filters, previews and measured QC."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from ...config import cache_dir, output_dir, unique_path, slug
from ...core import qc
from ...core.deps import need
from ...core.result import Result, ToolError

KIND = "video"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".mpg", ".mpeg", ".wmv", ".flv", ".3gp", ".gif"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif"}

SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
         "4k": (3840, 2160), "2160p": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720),
         "reels": (1080, 1920), "shorts": (1080, 1920), "tiktok": (1080, 1920), "story": (1080, 1920),
         "square": (1080, 1080), "portrait": (1080, 1350), "landscape": (1920, 1080)}

# Platform delivery presets. lufs: integrated loudness target; max_s: platform length limit (warn only).
PRESETS: dict[str, dict] = {
    "reels":     {"size": (1080, 1920), "fps": 30, "crf": 18, "maxrate": "14M", "lufs": -14, "max_s": 180, "note": "Instagram Reels 9:16"},
    "tiktok":    {"size": (1080, 1920), "fps": 30, "crf": 18, "maxrate": "14M", "lufs": -14, "max_s": 600, "note": "TikTok 9:16"},
    "shorts":    {"size": (1080, 1920), "fps": 30, "crf": 18, "maxrate": "14M", "lufs": -14, "max_s": 180, "note": "YouTube Shorts 9:16"},
    "youtube":   {"size": (1920, 1080), "fps": 0, "crf": 17, "maxrate": "16M", "lufs": -14, "max_s": 0, "note": "YouTube 1080p (fps kept)"},
    "youtube_4k": {"size": (3840, 2160), "fps": 0, "crf": 17, "maxrate": "60M", "lufs": -14, "max_s": 0, "note": "YouTube 2160p"},
    "linkedin":  {"size": (1920, 1080), "fps": 30, "crf": 19, "maxrate": "10M", "lufs": -14, "max_s": 600, "note": "LinkedIn 16:9 (≤10 min)"},
    "linkedin_square": {"size": (1080, 1080), "fps": 30, "crf": 19, "maxrate": "10M", "lufs": -14, "max_s": 600, "note": "LinkedIn 1:1"},
    "instagram_feed": {"size": (1080, 1350), "fps": 30, "crf": 18, "maxrate": "12M", "lufs": -14, "max_s": 3600, "note": "Instagram feed 4:5"},
    "x":         {"size": (1280, 720), "fps": 30, "crf": 20, "maxrate": "8M", "lufs": -14, "max_s": 140, "note": "X/Twitter 720p (≤2:20 free accounts)"},
    "whatsapp":  {"size": (1280, 720), "fps": 30, "crf": 23, "maxrate": "3M", "lufs": -14, "max_s": 0, "note": "WhatsApp-friendly small file"},
    "prores":    {"size": None, "fps": 0, "codec": "prores", "lufs": None, "max_s": 0, "note": "ProRes 422 HQ master (.mov, PCM audio)"},
    "prores4444": {"size": None, "fps": 0, "codec": "prores4444", "lufs": None, "max_s": 0, "note": "ProRes 4444 master (.mov)"},
    "gif":       {"size": None, "fps": 12, "codec": "gif", "lufs": None, "max_s": 30, "note": "animated GIF (palette-optimised, ≤480 px, 12 fps)"},
    "webm":      {"size": None, "fps": 0, "codec": "vp9", "lufs": -14, "max_s": 0, "note": "VP9/Opus WebM for the web"},
    "master":    {"size": None, "fps": 0, "crf": 14, "maxrate": "", "lufs": None, "max_s": 0, "note": "high-quality H.264 master, same size"},
}

# Friendly transition names → ffmpeg xfade names.
XFADE = {"dissolve": "fade", "crossfade": "fade", "fade": "fade", "dip": "fadeblack", "dip_black": "fadeblack",
         "fadeblack": "fadeblack", "dip_white": "fadewhite", "fadewhite": "fadewhite", "flash": "fadewhite",
         "wipe": "wipeleft", "wipe_left": "wipeleft", "wipe_right": "wiperight", "wipe_up": "wipeup", "wipe_down": "wipedown",
         "slide": "slideleft", "slide_left": "slideleft", "slide_right": "slideright", "slide_up": "slideup",
         "slide_down": "slidedown", "push": "smoothleft", "smooth_left": "smoothleft", "smooth_right": "smoothright",
         "zoom": "zoomin", "zoom_in": "zoomin", "iris": "circleopen", "circle": "circleopen", "circle_close": "circleclose",
         "radial": "radial", "blur": "hblur", "pixelize": "pixelize", "diagonal": "diagtl", "squeeze": "squeezeh",
         "cover_left": "coverleft", "cover_right": "coverright", "reveal_left": "revealleft", "reveal_right": "revealright"}
XFADE_NATIVE = {"fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
                "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright",
                "smoothup", "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
                "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice",
                "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin",
                "fadefast", "fadeslow", "hlwind", "hrwind", "vuwind", "vdwind", "coverleft", "coverright", "coverup",
                "coverdown", "revealleft", "revealright", "revealup", "revealdown"}


def xfade_name(t: str) -> str:
    k = (t or "dissolve").strip().lower().replace("-", "_").replace(" ", "_")
    if k in XFADE:
        return XFADE[k]
    if k in XFADE_NATIVE:
        return k
    raise ToolError(f"unknown transition {t!r}", "dissolve, dip, dip_white, wipe_left/right/up/down, slide_left/right/up/down, "
                    "push, zoom, iris, circle_close, radial, blur, pixelize, diagonal, squeeze — or any ffmpeg xfade name")


# ─────────────────────────── paths ───────────────────────────

def src_path(path: str, kinds: set[str] | None = None) -> Path:
    if not path:
        raise ToolError("no input file given")
    p = Path(str(path)).expanduser()
    if not p.exists():
        raise ToolError(f"no such file: {p}", "pass an absolute path")
    if p.is_dir():
        raise ToolError(f"{p} is a folder, expected a file")
    if kinds and p.suffix.lower() not in kinds:
        raise ToolError(f"{p.name}: unsupported file type {p.suffix}", f"expected one of {sorted(kinds)}")
    return p.resolve()


def out_path(project: str, out: str, stem: str, ext: str, kind: str = KIND) -> Path:
    """`out` (a file path or a folder) or projects/<project>/video/. Never overwrites."""
    ext = ext if ext.startswith(".") else "." + ext
    stem = slug(stem, 60)
    if out:
        o = Path(out).expanduser()
        if o.suffix and not o.is_dir():
            o.parent.mkdir(parents=True, exist_ok=True)
            return unique_path(o.parent, o.stem, ext if o.suffix.lower() != ext.lower() and ext else o.suffix)
        o.mkdir(parents=True, exist_ok=True)
        return unique_path(o, stem, ext)
    return unique_path(output_dir(project or None, kind), stem, ext)


def out_folder(project: str, out: str, stem: str, kind: str = KIND) -> Path:
    """A fresh folder for multi-file outputs (never reuses an existing one)."""
    base = Path(out).expanduser() if out else output_dir(project or None, kind)
    base.mkdir(parents=True, exist_ok=True)
    d = base / slug(stem, 60)
    n = 2
    while d.exists():
        d = base / f"{slug(stem, 60)}-{n}"
        n += 1
    d.mkdir(parents=True)
    return d


def sibling(path: Path, suffix: str, ext: str) -> Path:
    return unique_path(path.parent, path.stem + suffix, ext)


def scratch(prefix: str = "video-") -> Path:
    d = cache_dir() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=d))


def parse_size(size, default: tuple[int, int] | None = None) -> tuple[int, int] | None:
    if size in (None, "", "same", "source"):
        return default
    if isinstance(size, (list, tuple)):
        w, h = int(size[0]), int(size[1])
        return w - w % 2, h - h % 2
    s = str(size).strip().lower()
    if s in SIZES:
        return SIZES[s]
    m = re.match(r"^(\d{2,5})\s*[x×:]\s*(\d{2,5})$", s)
    if not m:
        raise ToolError(f"bad size {size!r}", "WIDTHxHEIGHT (1920x1080) or one of " + ", ".join(SIZES))
    w, h = int(m.group(1)), int(m.group(2))
    return w - w % 2, h - h % 2


# ─────────────────────────── probing ───────────────────────────

def probe(path: str | Path) -> dict:
    """Richer ffprobe: duration, size, fps, codecs, rotation, audio, frame count estimate."""
    need("ffprobe")
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ToolError(f"cannot read {Path(path).name}: {r.stderr.strip()[-300:]}")
    j = json.loads(r.stdout)
    fmt = j.get("format", {})
    out = {"duration": float(fmt.get("duration", 0) or 0), "size_bytes": int(fmt.get("size", 0) or 0),
           "bitrate": int(fmt.get("bit_rate", 0) or 0), "format": fmt.get("format_name", ""), "video": False, "audio": False}
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and not out["video"] and s.get("disposition", {}).get("attached_pic", 0) == 0:
            num, _, den = (s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1").partition("/")
            fps = float(num) / float(den or 1) if float(den or 1) else 0.0
            if fps <= 0 or fps > 240:
                num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
                fps = float(num) / float(den or 1) if float(den or 1) else 0.0
            rot = 0
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(sd["rotation"])
            rot = int((s.get("tags") or {}).get("rotate", rot) or rot)
            w, h = int(s.get("width") or 0), int(s.get("height") or 0)
            if abs(rot) in (90, 270):
                w, h = h, w
            out.update(video=True, width=w, height=h, fps=round(fps, 3), codec=s.get("codec_name"),
                       pix_fmt=s.get("pix_fmt"), rotation=rot, alpha=bool(re.search(r"a(p|\d|$)|rgba|argb|yuva", s.get("pix_fmt") or "")),
                       vduration=float(s.get("duration") or 0) or None, profile=s.get("profile"),
                       color_transfer=s.get("color_transfer"), vbitrate=int(s.get("bit_rate") or 0))
            if (s.get("tags") or {}).get("alpha_mode") == "1":
                out["alpha"] = True
        elif s.get("codec_type") == "audio" and not out["audio"]:
            out.update(audio=True, audio_codec=s.get("codec_name"), sample_rate=int(s.get("sample_rate") or 0),
                       channels=s.get("channels"), aduration=float(s.get("duration") or 0) or None)
    if out["duration"] <= 0:
        out["duration"] = float(out.get("vduration") or out.get("aduration") or 0)
    if out.get("video") and out.get("fps"):
        out["frames_est"] = int(round(out["duration"] * out["fps"]))
    return out


def need_video(p: Path) -> dict:
    info = probe(p)
    if not info.get("video"):
        raise ToolError(f"{p.name} has no video stream")
    return info


# ─────────────────────────── running ffmpeg ───────────────────────────

def ff(args: list[str], timeout: int = 7200, what: str = "ffmpeg") -> subprocess.CompletedProcess:
    exe = need("ffmpeg")
    cmd = [exe, "-hide_banner", "-nostdin", "-y", "-v", "error", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError(f"{what} timed out after {timeout}s")
    if r.returncode != 0:
        tail = (r.stderr or "").strip()[-1800:]
        raise ToolError(f"{what} failed: {tail}")
    return r


_FILTERS: set[str] | None = None


def has_filter(name: str) -> bool:
    global _FILTERS
    if _FILTERS is None:
        r = subprocess.run([need("ffmpeg"), "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
        _FILTERS = set(re.findall(r"^\s*[TSC.]{3}\s+(\S+)", r.stdout, re.M))
    return name in _FILTERS


def v_encode_args(crf: int = 18, preset: str = "medium", maxrate: str = "", fps: float | None = None) -> list[str]:
    a = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high",
         "-movflags", "+faststart", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if maxrate:
        a += ["-maxrate", maxrate, "-bufsize", str(int(re.sub(r"\D", "", maxrate) or 10) * 2) + "M"]
    if fps:
        a += ["-r", f"{fps:g}"]
    return a


def a_encode_args(bitrate: str = "192k") -> list[str]:
    return ["-c:a", "aac", "-b:a", bitrate, "-ar", "48000", "-ac", "2"]


def webm_alpha_decoder(p: Path) -> list[str]:
    """ffmpeg's native VP9 decoder drops alpha — force libvpx for .webm inputs so alpha survives."""
    if p.suffix.lower() == ".webm":
        try:
            info = probe(p)
            if info.get("codec") == "vp9":
                return ["-c:v", "libvpx-vp9"]
            if info.get("codec") == "vp8":
                return ["-c:v", "libvpx"]
        except ToolError:
            pass
    return []


# ─────────────────────────── colour ───────────────────────────

GRADES = ["teal_orange", "film_portra", "film_fuji", "kodachrome", "bleach_bypass", "noir", "bw_soft", "vintage_warm",
          "moody_matte", "cross_process", "golden_hour", "clean_bright", "blockbuster_cool", "sepia"]


def grade_lut(preset: str) -> Path:
    """.cube LUT for a named look, made once by the photo department (photo_look export_cube) and cached,
    so a video grade matches the photo grade of the same name exactly."""
    preset = preset.strip().lower()
    if preset not in GRADES:
        raise ToolError(f"unknown grade {preset!r}", "one of " + ", ".join(GRADES) + " — or pass lut=<file.cube>")
    d = cache_dir() / "luts"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{preset}.cube"
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    from ...core import registry
    res = registry.call("photo_look", {"look": preset, "export_cube": True, "out": str(d / "gen")})
    cube = next((f for f in res.files if f.lower().endswith(".cube")), None)
    if not cube:
        raise ToolError(f"photo_look did not produce a .cube for {preset}")
    shutil.copy2(cube, dest)
    shutil.rmtree(d / "gen", ignore_errors=True)
    return dest


def ff_path(p: str | Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    s = str(p).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,").replace("[", r"\[").replace("]", r"\]")


def grade_filter(grade: dict | str | None) -> str:
    """ffmpeg filter chain (no leading/trailing comma) for {preset|lut, strength, exposure, contrast,
    saturation, temperature, vibrance, vignette} — '' when nothing to do. Colour work runs in RGB."""
    if not grade:
        return ""
    if isinstance(grade, str):
        grade = {"preset": grade}
    parts = []
    exp = float(grade.get("exposure", 0) or 0)
    con = float(grade.get("contrast", 0) or 0)
    sat = float(grade.get("saturation", 0) or 0)
    if exp or con or sat:
        parts.append(f"eq=brightness={exp * 0.12:.4f}:contrast={1 + con:.4f}:saturation={max(0.0, 1 + sat):.4f}")
    temp = float(grade.get("temperature", 0) or 0)  # -1 cool … +1 warm
    if temp:
        parts.append(f"colortemperature=temperature={int(6500 - temp * 2500)}:mix=0.8")
    vib = float(grade.get("vibrance", 0) or 0)
    if vib:
        parts.append(f"vibrance=intensity={vib:.3f}")
    lut = grade.get("lut") or (grade_lut(grade["preset"]) if grade.get("preset") else None)
    strength = float(grade.get("strength", 1.0) if grade.get("strength") is not None else 1.0)
    if lut:
        lp = Path(str(lut)).expanduser()
        if not lp.exists():
            raise ToolError(f"LUT not found: {lp}")
        lf = f"lut3d=file='{ff_path(lp)}':interp=tetrahedral"
        if strength >= 0.999:
            parts.append(lf)
        else:  # blend graded over original
            parts.append(f"split[_ga][_gb];[_gb]{lf}[_gc];[_ga][_gc]blend=all_mode=normal:all_opacity={max(0.0, strength):.3f}")
    vig = float(grade.get("vignette", 0) or 0)
    if vig:
        parts.append(f"vignette=angle={0.35 + 0.45 * min(1.0, vig):.3f}")
    return ",".join(parts)


# ─────────────────────────── previews & QC ───────────────────────────

def contact_sheet(video: str | Path, dest: Path, cols: int = 4, rows: int = 3, width: int = 400,
                  start: float = 0.0, end: float | None = None, label: bool = True) -> Path:
    """Evenly spaced frames tiled with timestamps (drawn by PIL, so no font needed in ffmpeg)."""
    info = probe(video)
    dur = max(0.05, (end if end else info["duration"]) - start)
    n = cols * rows
    tmp = scratch("sheet-")
    try:
        ims = []
        for i in range(n):
            t = start + dur * (i + 0.5) / n
            f = tmp / f"{i}.png"
            subprocess.run([need("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", str(f)], capture_output=True, timeout=120)
            if f.exists():
                ims.append((t, Image.open(f).convert("RGB")))
        if not ims:
            raise ToolError(f"could not read frames from {Path(video).name}")
        tw, th = ims[0][1].size
        sheet = Image.new("RGB", (cols * (tw + 6) + 6, rows * (th + 6) + 6), (22, 22, 26))
        from PIL import ImageDraw
        d = ImageDraw.Draw(sheet)
        for k, (t, im) in enumerate(ims):
            x, y = 6 + (k % cols) * (tw + 6), 6 + (k // cols) * (th + 6)
            sheet.paste(im.resize((tw, th)), (x, y))
            if label:
                txt = f"{int(t // 60)}:{t % 60:05.2f}"
                d.rectangle([x, y + th - 18, x + 7 * len(txt) + 8, y + th], fill=(0, 0, 0))
                d.text((x + 4, y + th - 16), txt, fill=(255, 255, 255))
        sheet.save(dest)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def frame_at(video: str | Path, t: float, dest: Path, width: int = 0) -> Path:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    subprocess.run([need("ffmpeg"), "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
                    *vf, str(dest)], capture_output=True, timeout=120)
    return dest


def blackness(video: str | Path) -> float:
    """% of the duration that is black (blackdetect)."""
    info = probe(video)
    if not info.get("video") or info["duration"] <= 0:
        return 0.0
    r = subprocess.run([need("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(video), "-vf", "blackdetect=d=0.2:pix_th=0.08",
                        "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    tot = sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", r.stderr))
    return round(100 * tot / info["duration"], 1)


def finish(res: Result, dest: Path, *, expect: dict | None = None, target_lufs: float | None = None,
           sheet: bool = True, check_black: bool = True, sheet_cols: int = 4, sheet_rows: int = 3) -> Result:
    """Measure the output (ffprobe + loudness + black frames), make a contact sheet preview, add warnings."""
    info = probe(dest)
    res.data["output"] = {k: info.get(k) for k in ("duration", "width", "height", "fps", "codec", "pix_fmt", "audio",
                                                   "audio_codec", "sample_rate", "size_bytes") if info.get(k) is not None}
    if info.get("size_bytes"):
        res.data["output"]["size_mb"] = round(info["size_bytes"] / 1e6, 2)
    exp = expect or {}
    if exp.get("width") and (info.get("width"), info.get("height")) != (exp["width"], exp["height"]):
        res.warnings.append(f"output is {info.get('width')}x{info.get('height')}, expected {exp['width']}x{exp['height']}")
    if exp.get("duration") and abs(info["duration"] - exp["duration"]) > max(0.15, 2.0 / max(1.0, info.get("fps") or 30)):
        res.warnings.append(f"duration {info['duration']:.2f}s differs from the planned {exp['duration']:.2f}s")
    if info.get("audio"):
        m = qc.loudness(dest)
        res.data["loudness"] = {k: m.get(k) for k in ("lufs", "true_peak", "max_db", "silence_pct")}
        res.warnings += qc.audio_verdict(m, target_lufs)
    elif exp.get("audio"):
        res.warnings.append("output has no audio stream")
    if check_black and info.get("video"):
        b = blackness(dest)
        res.data["black_pct"] = b
        if b > 30:
            res.warnings.append(f"{b}% of the video is black — check the preview")
    if sheet and info.get("video"):
        sp = sibling(dest, "-sheet", "png")
        contact_sheet(dest, sp, cols=sheet_cols, rows=sheet_rows)
        res.previews.append(str(sp))
    return res


def fmt_t(t: float) -> str:
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}"


def extract_audio(src: Path, dest: Path, sr: int = 48000) -> Path:
    ff(["-i", str(src), "-vn", "-ac", "2", "-ar", str(sr), "-c:a", "pcm_s24le", str(dest)], what="audio extract")
    return dest


def image_stats(p: Path) -> dict:
    a = np.asarray(Image.open(p).convert("L"), dtype=np.float32)
    return {"mean": float(a.mean()), "std": float(a.std())}
