"""External programs and Python packages the departments use.

Every dependency is free/open-source. `need()` raises MissingTool with the
exact install command for this OS, so an agent can tell the user (and,
with consent, run `studio install`)."""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .result import MissingTool

IS_MAC = platform.system() == "Darwin"


@dataclass
class Dep:
    key: str
    kind: str                     # "bin" | "py" | "app"
    purpose: str
    brew: str = ""                # formula or "--cask name"
    apt: str = ""
    pip: str = ""                 # pip requirement (installed into the studio venv)
    bins: list[str] = field(default_factory=list)   # executable names to look for
    mac_paths: list[str] = field(default_factory=list)  # app bundle binaries
    module: str = ""              # python import name
    size: str = ""                # rough download size


DEPS: dict[str, Dep] = {d.key: d for d in [
    Dep("ffmpeg", "bin", "video/audio engine", brew="ffmpeg", apt="ffmpeg", bins=["ffmpeg"], size="80 MB"),
    Dep("ffprobe", "bin", "media inspection", brew="ffmpeg", apt="ffmpeg", bins=["ffprobe"]),
    Dep("magick", "bin", "ImageMagick raster engine", brew="imagemagick", apt="imagemagick", bins=["magick", "convert"], size="40 MB"),
    Dep("rsvg", "bin", "SVG → PNG", brew="librsvg", apt="librsvg2-bin", bins=["rsvg-convert"]),
    Dep("inkscape", "app", "vector editor (Illustrator replacement), headless export/outline/boolean",
        brew="--cask inkscape", apt="inkscape", bins=["inkscape"],
        mac_paths=["/Applications/Inkscape.app/Contents/MacOS/inkscape"], size="250 MB"),
    Dep("gimp", "app", "photo editor (Photoshop replacement), headless layered PSD/XCF",
        brew="--cask gimp", apt="gimp", bins=["gimp-console", "gimp", "gimp-2.10"],
        mac_paths=["/Applications/GIMP.app/Contents/MacOS/gimp", "/Applications/GIMP-2.10.app/Contents/MacOS/gimp"], size="400 MB"),
    Dep("krita", "app", "painting / illustration app (opens the files we make)", brew="--cask krita", apt="krita",
        bins=["krita"], mac_paths=["/Applications/krita.app/Contents/MacOS/krita"], size="300 MB"),
    Dep("melt", "bin", "MLT renderer behind Kdenlive/Shotcut (renders our editable timelines)", brew="mlt", apt="melt",
        bins=["melt"], size="60 MB"),
    Dep("kdenlive", "app", "video editor (Premiere replacement) — opens the .kdenlive projects we write",
        brew="--cask kdenlive", apt="kdenlive", bins=["kdenlive"],
        mac_paths=["/Applications/kdenlive.app/Contents/MacOS/kdenlive"], size="500 MB"),
    Dep("blender", "app", "3D, motion graphics, video sequencer (After Effects/C4D replacement)",
        brew="--cask blender", apt="blender", bins=["blender"],
        mac_paths=["/Applications/Blender.app/Contents/MacOS/Blender"], size="900 MB"),
    Dep("audacity", "app", "audio editor (Audition replacement)", brew="--cask audacity", apt="audacity",
        bins=["audacity"], mac_paths=["/Applications/Audacity.app/Contents/MacOS/Audacity"], size="100 MB"),
    Dep("sox", "bin", "audio effects CLI", brew="sox", apt="sox", bins=["sox"]),
    Dep("chromium", "py", "headless browser for HTML → PNG/PDF/video (layout, motion graphics)",
        pip="playwright", module="playwright", size="150 MB (browser)"),
    Dep("rembg", "py", "AI background removal (local)", pip="rembg[cpu]", module="rembg", size="180 MB model"),
    Dep("opencv", "py", "face/subject detection for smart crop & reframe", pip="opencv-python-headless", module="cv2"),
    Dep("vtracer", "py", "raster → vector tracing", pip="vtracer", module="vtracer"),
    Dep("poppler", "bin", "PDF → PNG previews (pdftoppm), PDF font checks", brew="poppler", apt="poppler-utils",
        bins=["pdftoppm"], size="20 MB"),
    Dep("harfbuzz", "py", "text shaping for logo wordmarks (Arabic joining, kerning)", pip="uharfbuzz", module="uharfbuzz"),
    Dep("fonttools", "py", "font outlines → SVG paths for logos", pip="fonttools", module="fontTools"),
    Dep("segno", "py", "QR codes (SVG) for posters, flyers, business cards", pip="segno", module="segno"),
    Dep("ghostscript", "bin", "optional CMYK conversion of print PDFs", brew="ghostscript", apt="ghostscript", bins=["gs"], size="50 MB"),
    Dep("edge_tts", "py", "neural TTS incl. Egyptian Arabic (free Microsoft Edge voices, online)", pip="edge-tts", module="edge_tts"),
    Dep("piper", "py", "offline neural TTS", pip="piper-tts", module="piper", size="60 MB per voice"),
    Dep("faster_whisper", "py", "speech → text (captions, transcripts)", pip="faster-whisper", module="faster_whisper", size="150 MB–3 GB model"),
    Dep("noisereduce", "py", "voice clean-up", pip="noisereduce", module="noisereduce"),
    Dep("pedalboard", "py", "studio audio effects (EQ, comp, reverb, limiter)", pip="pedalboard", module="pedalboard"),
    Dep("demucs", "py", "stem separation (vocals/drums/bass/other)", pip="demucs", module="demucs", size="2 GB with torch"),
    Dep("mlx_whisper", "py", "fast Whisper transcription on Apple Silicon (MLX)", pip="mlx-whisper", module="mlx_whisper", size="1.6–3 GB model"),
    Dep("soundfile", "py", "WAV/FLAC read/write (libsndfile)", pip="soundfile", module="soundfile"),
    Dep("mido", "py", "MIDI files (editable master of generated music)", pip="mido", module="mido"),
    Dep("httpx", "py", "HTTP client for AI backends", pip="httpx", module="httpx"),
    Dep("mflux", "py", "FLUX image generation on Apple Silicon (MLX, local)", pip="mflux", module="mflux", size="6–12 GB model"),
    Dep("comfyui", "app", "local AI image/video generation server (FLUX, SDXL, LTX-Video, Wan)",
        bins=[], size="2 GB + models"),
]}


def find_bin(dep: Dep) -> str | None:
    for b in dep.bins:
        p = shutil.which(b)
        if p:
            return p
    for p in dep.mac_paths:
        if Path(p).exists():
            return p
    return None


def have(key: str) -> bool:
    d = DEPS[key]
    if d.kind == "py":
        return importlib.util.find_spec(d.module) is not None
    if key == "comfyui":
        return comfy_url() is not None
    return find_bin(d) is not None


def install_hint(key: str) -> str:
    d = DEPS[key]
    if d.kind == "py":
        extra = " && python -m playwright install chromium" if key == "chromium" else ""
        return f"pip install '{d.pip}'{extra}   (or: studio install {key})"
    if key == "comfyui":
        return "comfyui_setup (dry run lists sizes/licences) then comfyui_start, or set COMFYUI_URL to an existing server"
    if IS_MAC:
        return f"brew install {d.brew}" if d.brew else f"see the {d.key} website"
    return f"sudo apt install {d.apt}" if d.apt else f"see the {d.key} website"


def need(key: str) -> str:
    """Path of the executable (or module name) or MissingTool with the fix."""
    d = DEPS[key]
    if d.kind == "py":
        if importlib.util.find_spec(d.module) is None:
            raise MissingTool(f"{key} is not installed ({d.purpose}).", install_hint(key))
        return d.module
    p = find_bin(d)
    if not p:
        raise MissingTool(f"{key} is not installed ({d.purpose}).", install_hint(key))
    return p


def comfy_url() -> str | None:
    url = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    try:
        import urllib.request
        with urllib.request.urlopen(url + "/system_stats", timeout=1.5) as r:
            return url if r.status == 200 else None
    except Exception:
        return None


def run(cmd: list[str], timeout: int = 900, cwd: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a program; raise ToolError with the tail of stderr on failure."""
    from .result import ToolError
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           env={**os.environ, **(env or {})})
    except subprocess.TimeoutExpired:
        raise ToolError(f"{Path(cmd[0]).name} timed out after {timeout}s")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-1500:]
        raise ToolError(f"{Path(cmd[0]).name} failed (exit {r.returncode}): {tail}")
    return r


def doctor() -> list[dict]:
    rows = []
    for k, d in DEPS.items():
        ok = have(k)
        rows.append({"dep": k, "ok": ok, "purpose": d.purpose, "install": "" if ok else install_hint(k), "size": d.size})
    return rows
