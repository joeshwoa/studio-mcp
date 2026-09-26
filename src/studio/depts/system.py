"""Studio housekeeping: what's installed, installing the free tools, opening results in apps."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import studio_home
from ..core.deps import DEPS, IS_MAC, doctor, find_bin, have, install_hint
from ..core.registry import tool
from ..core.result import Result, ToolError

GROUPS = {
    "core": ["ffmpeg", "magick", "rsvg", "chromium"],
    "design": ["chromium", "inkscape", "vtracer"],
    "photo": ["gimp", "rembg", "opencv", "magick"],
    "video": ["ffmpeg", "melt", "kdenlive", "chromium"],
    "audio": ["ffmpeg", "sox", "edge_tts", "piper", "faster_whisper", "noisereduce", "pedalboard"],
    "ai": ["httpx", "comfyui"] + (["mflux"] if IS_MAC and platform.machine() == "arm64" else []),
    "apps": ["gimp", "inkscape", "krita", "kdenlive", "blender", "audacity"],
}


@tool("system")
def studio_doctor() -> Result:
    """Start here. Lists every free tool the studio can drive, whether it is installed, what it is
    for, and the exact install command for the missing ones. Also shows STUDIO_HOME (where outputs
    and AI models are kept)."""
    rows = doctor()
    ok = [r for r in rows if r["ok"]]
    miss = [r for r in rows if not r["ok"]]
    lines = [f"STUDIO_HOME: {studio_home()}", f"{len(ok)}/{len(rows)} tools available on {platform.system()} {platform.machine()}."]
    lines += [f"  ok       {r['dep']:15} {r['purpose']}" for r in ok]
    lines += [f"  missing  {r['dep']:15} {r['purpose']}  →  {r['install']}" for r in miss]
    ready = {g: all(have(k) for k in ks) for g, ks in GROUPS.items()}
    lines.append("")
    lines.append("Departments ready: " + ", ".join(f"{g}={'yes' if v else 'partly'}" for g, v in ready.items()))
    return Result("\n".join(lines), data={"home": str(studio_home()), "deps": rows, "ready": ready},
                  next_steps=["studio_install with the group the user needs (after they agree to the download sizes)"] if miss else [])


def _install_cmds(key: str) -> list[list[str]]:
    d = DEPS[key]
    if d.kind == "py":
        cmds = [[sys.executable, "-m", "pip", "install", d.pip]]
        if key == "chromium":
            cmds.append([sys.executable, "-m", "playwright", "install", "chromium"])
        return cmds
    if key == "comfyui":
        return []  # handled by ai.comfyui_setup
    if IS_MAC:
        if not shutil.which("brew"):
            raise ToolError("Homebrew is required to install apps on macOS.", "install it from https://brew.sh")
        parts = d.brew.split()
        return [["brew", "install", *parts]] if parts else []
    if shutil.which("apt-get") and d.apt:
        return [["sudo", "-n", "apt-get", "install", "-y", d.apt]]
    return []


@tool("system", installs=True, network=True)
def studio_install(group: str = "core", confirm: bool = False) -> Result:
    """Install the free tools for a department: core, design, photo, video, audio, ai, apps (or one
    dependency name from studio_doctor). With confirm=false it only lists what WOULD be installed
    and the download sizes — show that to the user; call again with confirm=true only after they agree.
    Python packages go into this studio's own environment; apps via Homebrew (macOS) or apt (Linux)."""
    keys = GROUPS.get(group, [group] if group in DEPS else None)
    if keys is None:
        raise ToolError(f"unknown group {group!r}", f"one of {sorted(GROUPS)} or a dependency name")
    todo = [k for k in dict.fromkeys(keys) if not have(k)]
    if not todo:
        return Result(f"Everything in '{group}' is already installed.")
    plan = [(k, _install_cmds(k)) for k in todo]
    listing = [f"- {k}: {DEPS[k].purpose} ({DEPS[k].size or 'small'}) → " +
               ("; ".join(" ".join(c) for c in cmds) if cmds else install_hint(k)) for k, cmds in plan]
    if not confirm:
        return Result(f"Would install {len(todo)} item(s) for '{group}':\n" + "\n".join(listing),
                      next_steps=["Ask the user; if they agree, call studio_install again with confirm=true."])
    done, failed = [], []
    for k, cmds in plan:
        if not cmds:
            failed.append(f"{k}: {install_hint(k)}")
            continue
        try:
            for c in cmds:
                r = subprocess.run(c, capture_output=True, text=True, timeout=3600)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or r.stdout)[-400:])
            done.append(k)
        except Exception as e:  # keep going; report precisely
            failed.append(f"{k}: {e}")
    res = Result(f"Installed {len(done)}/{len(todo)}: {', '.join(done) or '—'}")
    res.warnings += failed
    res.ok = not failed
    return res


APP_KEYS = {"gimp": "gimp", "inkscape": "inkscape", "krita": "krita", "kdenlive": "kdenlive",
            "blender": "blender", "audacity": "audacity"}


@tool("system")
def open_in_app(path: str, app: str = "auto") -> Result:
    """Open a file the studio made in the free desktop app that edits it, so the user can take over
    by hand: .psd/.xcf → GIMP, .svg/.pdf → Inkscape, .kra → Krita, .kdenlive/.mlt → Kdenlive,
    .blend → Blender, .aup3/.wav → Audacity. app='auto' picks by extension."""
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"no such file: {p}")
    ext_map = {".psd": "gimp", ".xcf": "gimp", ".svg": "inkscape", ".pdf": "inkscape", ".eps": "inkscape",
               ".kra": "krita", ".kdenlive": "kdenlive", ".mlt": "kdenlive", ".blend": "blender",
               ".aup3": "audacity", ".wav": "audacity"}
    key = ext_map.get(p.suffix.lower()) if app == "auto" else APP_KEYS.get(app)
    if not key:
        raise ToolError(f"don't know which app opens {p.suffix}", "pass app= gimp|inkscape|krita|kdenlive|blender|audacity")
    exe = find_bin(DEPS[key])
    if not exe:
        raise ToolError(f"{key} is not installed", install_hint(key))
    if IS_MAC and ".app/" in exe:
        subprocess.Popen(["open", "-a", exe.split(".app/")[0] + ".app", str(p)])
    else:
        subprocess.Popen([exe, str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return Result(f"Opened {p.name} in {key}.")
