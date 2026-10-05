"""Blender engine: physically based 3D motion graphics (Cycles path tracing or EEVEE), real depth of field,
motion blur, glass/caustics, curved studio cyclorama — rendered headless from a bundled bpy script
(assets/blender/studio_bpy.py) and saved as an editable .blend master."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from ...config import unique_path
from ...core import deps
from ...core.registry import tool
from ...core.result import MissingTool, Result, ToolError
from . import engine
from . import ASSETS, _out_target, apply_brand, deliver_frames, parse_size

SCRIPT = ASSETS.parent / "blender" / "studio_bpy.py"
PRESETS = ("title", "logo", "turntable", "abstract")
QUALITY = {"draft": {"samples": 16, "scale": 0.5}, "preview": {"samples": 32, "scale": 0.75}, "final": {"samples": 128, "scale": 1.0}}


def blender_cmd() -> list[str] | None:
    """The Blender app (blender -b … -P) or, if set, a Python with the `bpy` module (STUDIO_BLENDER_PYTHON)."""
    try:
        return [deps.need("blender"), "-b", "--factory-startup", "-noaudio", "-P"]
    except MissingTool:
        py = os.environ.get("STUDIO_BLENDER_PYTHON", "").strip()
        if py and Path(py).exists():
            return [py]
        return None


def run_job(job: dict, timeout: int) -> str:
    cmd = blender_cmd()
    if not cmd:
        raise MissingTool("Blender is not installed (needed for motion_blender).", deps.install_hint("blender"))
    work = Path(job["frames_dir"]).parent
    jf = work / "job.json"
    jf.write_text(json.dumps(job), encoding="utf-8")
    t0 = time.time()
    try:
        r = subprocess.run(cmd + [str(SCRIPT), "--", str(jf)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError(f"Blender timed out after {timeout}s", "use quality='draft', fewer seconds, EEVEE instead of Cycles, or a smaller size")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "STUDIO: ERROR" in out:
        tail = "\n".join(l for l in out.splitlines() if l.strip())[-1500:]
        raise ToolError(f"Blender job failed (exit {r.returncode}): {tail}")
    return f"{time.time() - t0:.0f}s"


@tool("motion", network=True)
def motion_blender(preset: str = "title", text: str = "", logo: str = "", model: str = "", material: str = "", engine_name: str = "EEVEE",
                   quality: str = "preview", camera: str = "", animation: str = "", duration: float = 4.0, size: str = "1920x1080",
                   fps: int = 30, transparent: bool = False, dof: bool = True, motion_blur: bool = True, color: str = "",
                   accent: str = "", background: str = "", depth: float = 0.35, font: str = "", weight: int = 800, brand: str = "",
                   fallback: bool = True, timeout: int = 3600, formats: list[str] = [], project: str = "", out: str = "") -> Result:
    """Physically based 3D motion graphics in Blender (headless): 3D title (any language — text is
    shaped with HarfBuzz into outlines first, so Arabic joins correctly), 3D logo from SVG, product
    turntable from a .glb, abstract glass/chrome loop — on a curved studio cyclorama with area lights,
    real depth of field and motion blur. Returns the video, a timecoded contact sheet + key frames AND
    the editable .blend master. Slow (minutes); for fast real-time 3D use motion_3d (three.js).

    engine_name: EEVEE (fast raster, good for most titles) | CYCLES (path traced: glass, caustics,
    soft GI — slowest). quality: draft (½ res, 16 samples) | preview (¾ res, 32) | final (full, 128).
    material: metal | chrome | gold | glass | plastic | matte | clay | neon | satin. camera: push-in |
    orbit | dolly | static. animation: rise | spin-in | flip | turntable. transparent=true → alpha
    (shadow catcher in Cycles). If Blender is not installed and fallback=true, the same preset is
    rendered with three.js instead and the result says FALLBACK clearly (no .blend then)."""
    if preset not in PRESETS:
        raise ToolError(f"unknown Blender preset {preset!r}", f"one of {', '.join(PRESETS)}")
    if quality not in QUALITY:
        raise ToolError(f"unknown quality {quality!r}", "draft | preview | final")
    warnings: list[str] = []
    if not blender_cmd():
        if not fallback:
            raise MissingTool("Blender is not installed (needed for motion_blender).", deps.install_hint("blender"))
        from .three_d import motion_3d
        res = motion_3d(preset={"turntable": "product"}.get(preset, preset), text=text, logo=logo, model=model,
                        material=material if material != "glass" else "glass", camera={"static": "static", "dolly": "dolly", "orbit": "orbit"}.get(camera, ""),
                        animation=animation, duration=duration, size=size, fps=fps, transparent=transparent,
                        color=color, accent=accent, depth=depth, font=font, weight=weight, brand=brand, formats=formats,
                        project=project, out=out, background=background)
        res.summary = ("FALLBACK — Blender is not installed, so this was rendered in real time with three.js (motion_3d), "
                       "not path-traced: no Cycles glass/caustics, no optical depth of field, no .blend master. " + res.summary)
        res.warnings.insert(0, f"Blender missing → three.js fallback. Install Blender for the physically based render: {deps.install_hint('blender')}")
        res.data["engine"] = "three.js (fallback for motion_blender)"
        res.data["fallback"] = True
        return res
    cols, fnt, theme = apply_brand(brand, {}, {}, warnings)
    q = QUALITY[quality]
    w, h = parse_size(size)
    rw, rh = int(w * q["scale"]) // 2 * 2, int(h * q["scale"]) // 2 * 2
    work = engine.scratch_dir("blender-")
    try:
        odir, stem = _out_target(out, project, f"blender-{preset}")
        job = {"preset": preset, "frames_dir": str(work / "frames"), "width": rw, "height": rh, "fps": fps, "duration": float(duration),
               "engine": engine_name.upper(), "samples": q["samples"], "transparent": bool(transparent),
               "material": material or {"title": "metal", "logo": "chrome", "abstract": "plastic"}.get(preset, "plastic"),
               "color": color or cols.get("primary", "#FF5A36"), "accent": accent or cols.get("accent", "#FFC53D"),
               "background": background or cols.get("background", "#0E0F1A"), "camera": camera or ("orbit" if preset == "logo" else "push-in"),
               "animation": animation, "depth": depth, "dof": dof, "motion_blur": motion_blur,
               "blend_path": str(unique_path(odir, stem, "blend"))}
        if preset == "title":
            from .three_d import text_svg
            svgf = work / "title.svg"
            svgf.write_text(text_svg(text or "Studio", font, weight, job["color"], (theme or {}).get("fonts") or (fnt if isinstance(fnt, dict) else {})),
                            encoding="utf-8")
            job["svg"] = str(svgf)
        elif preset == "logo":
            src = logo or (theme or {}).get("logo_svg") or (theme or {}).get("icon_svg")
            if not src or not Path(src).expanduser().exists() or not str(src).lower().endswith(".svg"):
                raise ToolError("3D logo needs logo=<path.svg> (or brand=<slug> with an SVG logo)")
            job["svg"] = str(Path(src).expanduser().resolve())
        elif preset == "turntable":
            p = Path(model).expanduser()
            if not model or not p.exists():
                raise ToolError("turntable needs model=<path to .glb/.gltf>")
            job["glb"] = str(p.resolve())
        took = run_job(job, timeout)
        frames = sorted((work / "frames").glob("f_*.png"))
        if not frames:
            raise ToolError("Blender finished but wrote no frames")
        if (rw, rh) != (w, h):
            warnings.append(f"quality={quality}: rendered at {rw}x{rh} (use quality='final' for full {w}x{h})")
        r = {"frames": frames, "duration": len(frames) / fps, "console": []}
        res = deliver_frames(r, work / "frames", page=None, name=stem, width=rw, height=rh, fps=fps, transparent=transparent,
                             formats=formats, project=project, out=out, warnings=warnings,
                             summary=f"Blender {job['engine']} {preset} ({job['material']}, {job['camera']} camera, {quality}) rendered in {took}.",
                             data={"engine": "blender " + job["engine"], "samples": q["samples"], "blend": job["blend_path"]},
                             background=job["background"], grain=0, keyframes=3, extra_files=[job["blend_path"]])
        res.next_steps.append("open the .blend in Blender to tweak lights/materials/camera and re-render at final quality")
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
