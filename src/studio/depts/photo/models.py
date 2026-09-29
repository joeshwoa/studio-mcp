"""Free offline model files for the photo department, fetched from Hugging Face mirrors.

GitHub release downloads (rembg's default source) are often blocked; these Hugging Face
copies are byte-identical (the rembg files match rembg's own md5 checksums; sha256 below is
checked after every download). Override the mirror with STUDIO_REMBG_MIRROR / STUDIO_UPSCALE_URL
if you host your own copies.
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path

from ...config import models_dir
from ...core.result import ToolError

REMBG_MIRROR = os.environ.get("STUDIO_REMBG_MIRROR", "https://huggingface.co/tomjackson2023/rembg/resolve/main")

REMBG_MODELS = {
    # name: (sha256, MB, what it is)
    "isnet-general-use": ("60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a", 179,
                          "IS-Net general use — best all-round quality (default)"),
    "u2net": ("8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491", 176, "U²-Net general"),
    "u2netp": ("309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8", 5, "U²-Net small — fast, rough edges"),
    "u2net_human_seg": ("01eb6a29a5c4d8edb30b56adad9bb3a2a0535338e480724a213e0acfd2d1c73c", 176, "people / portraits"),
    "silueta": ("75da6c8d2f8096ec743d071951be73b4a8bc7b3e51d9a6625d63644f90ffeedb", 44, "compact general model"),
    "isnet-anime": ("f15622d853e8260172812b657053460e20806f04b9e05147d49af7bed31a6e99", 176, "anime / illustration characters"),
}

UPSCALE_MODELS = {
    "realesr-general-x4v3": (
        os.environ.get("STUDIO_UPSCALE_URL",
                       "https://huggingface.co/CoderViking/realesr-general-x4v3-onnx/resolve/main/realesr-general-x4v3.onnx"),
        "1940a93ee08283a0a7286183186357b1688fe9fa8ede74604b424586aaddf112", 5,
        "Real-ESRGAN realesr-general-x4v3 (SRVGGNetCompact, BSD-3), ONNX export of the official weights"),
}


def rembg_home() -> Path:
    d = models_dir() / "rembg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, sha: str) -> Path:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise ToolError(f"could not download model from {url}: {e}",
                        f"download it manually and place it at {dest}")
    if sha and _sha256(tmp) != sha:
        tmp.unlink(missing_ok=True)
        raise ToolError(f"checksum mismatch for {dest.name} — refusing to use it", "retry, or set the mirror env var")
    tmp.rename(dest)
    return dest


def rembg_model(name: str, allow_download: bool = True) -> Path:
    if name not in REMBG_MODELS:
        raise ToolError(f"unknown background-removal model {name!r}", "one of: " + ", ".join(REMBG_MODELS))
    p = rembg_home() / f"{name}.onnx"
    if p.exists():
        return p
    if not allow_download:
        raise ToolError(f"model {name} not downloaded", f"it is fetched on first use ({REMBG_MODELS[name][1]} MB)")
    return _download(f"{REMBG_MIRROR}/{name}.onnx", p, REMBG_MODELS[name][0])


def upscale_model(name: str = "realesr-general-x4v3") -> Path:
    url, sha, _mb, _ = UPSCALE_MODELS[name]
    d = models_dir() / "upscale"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.onnx"
    return p if p.exists() else _download(url, p, sha)


YUNET = ("https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
         "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4")


def face_model() -> Path | None:
    """YuNet face detector (OpenCV Zoo, MIT, 230 KB). None when it can't be fetched."""
    d = models_dir() / "faces"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "face_detection_yunet_2023mar.onnx"
    if p.exists():
        return p
    try:
        return _download(YUNET[0], p, YUNET[1])
    except ToolError:
        return None
