"""Quality checks every department uses before handing a file back.

The rule of the studio: nothing is presented that wasn't measured and,
for anything visual, rendered to a preview PNG the agent looks at."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from .deps import find_bin, DEPS


def _ff(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True, timeout=timeout)


def probe(path: str | Path) -> dict:
    """ffprobe summary: duration, streams (w/h/fps/codec), audio presence."""
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return {"error": r.stderr.strip()[-300:]}
    j = json.loads(r.stdout)
    out: dict = {"duration": float(j.get("format", {}).get("duration", 0) or 0)}
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and "width" not in out:
            num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
            out.update(width=s.get("width"), height=s.get("height"), codec=s.get("codec_name"),
                       fps=round(float(num) / float(den or 1), 3) if float(den or 1) else 0,
                       pix_fmt=s.get("pix_fmt"))
        if s.get("codec_type") == "audio":
            out["audio"] = True
            out.setdefault("audio_codec", s.get("codec_name"))
            out.setdefault("sample_rate", int(s.get("sample_rate", 0) or 0))
            out.setdefault("channels", s.get("channels"))
    out.setdefault("audio", False)
    return out


def loudness(path: str | Path) -> dict:
    """Integrated loudness (LUFS), true peak (dBTP), and % of silence."""
    info = probe(path)
    dur = info.get("duration", 0) or 0
    res: dict = {"duration": round(dur, 3)}
    if dur >= 0.5:
        r = _ff(["-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"])
        m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
        p = re.findall(r"Peak:\s+(-?[\d.inf]+) dBFS", r.stderr)
        res["lufs"] = float(m[-1]) if m else None
        try:
            res["true_peak"] = float(p[-1]) if p else None
        except ValueError:
            res["true_peak"] = None
    r = _ff(["-i", str(path), "-af", "volumedetect", "-f", "null", "-"])
    mx = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
    res["max_db"] = float(mx.group(1)) if mx else None
    if dur > 0:
        r = _ff(["-i", str(path), "-af", "silencedetect=n=-50dB:d=0.5", "-f", "null", "-"])
        sil = sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", r.stderr))
        res["silence_pct"] = round(100 * sil / dur, 1)
    return res


def audio_verdict(m: dict, target_lufs: float | None = None) -> list[str]:
    w = []
    if m.get("max_db") is not None and m["max_db"] < -40:
        w.append("audio is silent or nearly silent")
    if m.get("true_peak") is not None and m["true_peak"] > -0.5:
        w.append(f"true peak {m['true_peak']} dBTP — clipping risk")
    if target_lufs is not None and m.get("lufs") is not None and abs(m["lufs"] - target_lufs) > 1.5:
        w.append(f"loudness {m['lufs']} LUFS vs target {target_lufs}")
    if m.get("silence_pct", 0) > 60:
        w.append(f"{m['silence_pct']}% silence")
    return w


def image_preview(src: str | Path, dest: str | Path, max_side: int = 1024, background: str | None = None) -> Path:
    """PNG preview of an image/SVG/PDF page for the agent to LOOK at."""
    src, dest = Path(src), Path(dest)
    ext = src.suffix.lower()
    if ext == ".svg":
        rsvg = find_bin(DEPS["rsvg"])
        if rsvg:
            cmd = [rsvg, "-w", str(max_side), "-a", str(src), "-o", str(dest)]
            if background:
                cmd[1:1] = ["-b", background]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            return dest
    if ext == ".pdf":
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-frames:v", "1", str(dest)],
                       capture_output=True, timeout=120)
        if dest.exists():
            return dest
    im = Image.open(src)
    im.thumbnail((max_side, max_side))
    if background and im.mode in ("RGBA", "LA"):
        bg = Image.new("RGBA", im.size, background)
        bg.alpha_composite(im.convert("RGBA"))
        im = bg
    im.save(dest)
    return dest


def contact_sheet(video: str | Path, dest: str | Path, cols: int = 4, rows: int = 3, width: int = 320) -> Path:
    """Evenly spaced frames tiled into one PNG."""
    info = probe(video)
    dur = max(info.get("duration", 1), 0.1)
    n = cols * rows
    step = dur / n
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
                    f"fps=1/{step:.4f},scale={width}:-2,tile={cols}x{rows}", "-frames:v", "1", str(dest)],
                   capture_output=True, timeout=300)
    return Path(dest)


def sheet_of_images(images: list[str | Path], dest: str | Path, tile: int = 320, cols: int = 4,
                    background: str = "#e5e7eb") -> Path:
    """Tile several images (e.g. a logo pack, social set) into one preview."""
    ims = []
    for p in images:
        try:
            if str(p).lower().endswith(".svg"):
                tmp = Path(dest).with_suffix(f".tmp{len(ims)}.png")
                image_preview(p, tmp, max_side=tile)
                im = Image.open(tmp).convert("RGBA")
                tmp.unlink(missing_ok=True)
            else:
                im = Image.open(p).convert("RGBA")
            im.thumbnail((tile, tile))
            ims.append(im)
        except Exception:
            continue
    if not ims:
        raise ValueError("no images to tile")
    rows = (len(ims) + cols - 1) // cols
    c = min(cols, len(ims))
    sheet = Image.new("RGBA", (c * (tile + 16) + 16, rows * (tile + 16) + 16), background)
    for i, im in enumerate(ims):
        x = 16 + (i % c) * (tile + 16) + (tile - im.width) // 2
        y = 16 + (i // c) * (tile + 16) + (tile - im.height) // 2
        sheet.alpha_composite(im, (x, y))
    sheet.save(dest)
    return Path(dest)


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None
