"""Open a studio project package in a professional app ON THIS MAC (the studio-mcp server runs locally):
Premiere Pro (imports the XML), After Effects (runs the builder script → native .aep), Final Cut Pro
(imports the FCPXML), DaVinci Resolve (Studio: full automatic build; free: opens Resolve + the guide),
CapCut (registers the draft, opens CapCut), Kdenlive/Shotcut. Plus relink after moving a package."""
from __future__ import annotations

import glob
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from ...core.registry import tool
from ...core.result import Result, ToolError

APP_GLOBS = {
    "premiere": ["/Applications/Adobe Premiere Pro*/Adobe Premiere Pro*.app"],
    "aftereffects": ["/Applications/Adobe After Effects*/Adobe After Effects*.app"],
    "fcpx": ["/Applications/Final Cut Pro*.app"],
    "resolve": ["/Applications/DaVinci Resolve/DaVinci Resolve.app", "/Applications/DaVinci Resolve*.app"],
    "capcut": ["/Applications/CapCut.app"],
    "kdenlive": ["/Applications/kdenlive.app", "/Applications/Kdenlive.app"],
    "shotcut": ["/Applications/Shotcut.app"],
}


def find_app(key: str) -> Path | None:
    for g in APP_GLOBS.get(key, []):
        hits = sorted(glob.glob(g))
        if hits:
            return Path(hits[-1])         # newest version last (e.g. 2025 < 2026)
    return None


def _pkg(package: str) -> Path:
    p = Path(package).expanduser()
    if p.is_file():
        p = p.parent
    while p != p.parent and not (p / "manifest.json").exists():
        p = p.parent
    if not (p / "manifest.json").exists():
        raise ToolError(f"not a studio project package: {package}", "pass the '<name> — Project' folder video_edit wrote")
    return p


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


@tool("video")
def video_project_apps() -> Result:
    """Which professional editing/motion apps are installed on this Mac (Premiere, After Effects, Final Cut,
    Resolve free/Studio, CapCut, Kdenlive, Shotcut) — so the AI knows which projects it can open for you."""
    if platform.system() != "Darwin":
        return Result("Not macOS — open the package's projects by hand (OPEN-IN.md).", data={"apps": {}})
    found = {k: str(find_app(k)) for k in APP_GLOBS if find_app(k)}
    if "resolve" in found:
        found["resolve_studio"] = str(Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting").exists())
    return Result("Installed: " + (", ".join(found) or "none of the pro apps"), data={"apps": found})


@tool("video")
def video_project_open(package: str, app: str, save: bool = True) -> Result:
    """Open a project package (the '<name> — Project' folder video_edit writes) in one app on this Mac and
    build the native project there:
      premiere      opens Premiere with the edit's XML (all tracks, transitions, motion, levels) — save as .prproj
      aftereffects  runs Projects/AfterEffects/build_project.jsx inside After Effects → saves the native .aep
                    (needs AE › Settings › Scripting & Expressions › 'Allow Scripts to Write Files')
      fcpx          imports the FCPXML into Final Cut Pro (library + project)
      resolve       Resolve Studio: runs build_resolve.py (timeline, LUTs, subtitles); free: opens Resolve and the guide
      capcut        adds the draft to CapCut's project list (CapCut must be closed) and opens CapCut
      kdenlive | shotcut  opens the .kdenlive / .mlt
    Nothing in the user's existing projects is changed."""
    if platform.system() != "Darwin":
        raise ToolError("video_project_open runs on macOS (the studio server on the user's Mac)", "open the files as OPEN-IN.md explains")
    root = _pkg(package)
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    name = next((p.stem for p in (root / "Projects" / "Premiere").glob("*.xml")), man["name"])
    if man.get("host_root") and man["host_root"] != str(root):
        _run([sys.executable, str(root / "relink.py")])
    key = app.lower().replace(" ", "").replace("_", "")
    key = {"pr": "premiere", "premierepro": "premiere", "ae": "aftereffects", "aftereffect": "aftereffects", "finalcut": "fcpx",
           "finalcutpro": "fcpx", "fcp": "fcpx", "davinci": "resolve", "davinciresolve": "resolve"}.get(key, key)
    exe = find_app("kdenlive" if key == "kdenlive" else key)
    if not exe:
        raise ToolError(f"{app} is not installed on this Mac", "video_project_apps lists what is installed")
    P = root / "Projects"
    res = Result("")
    if key == "premiere":
        r = _run(["open", "-a", str(exe), str(P / "Premiere" / f"{name}.xml")])
        res.summary = (f"Opened the edit in {exe.stem}: it converts the XML into a new project (all tracks). Then: File › Import "
                       f"Media/Captions/{name}.srt → drag to the timeline for live captions; File › Save As → Projects/Premiere/{name}.prproj.")
    elif key == "aftereffects":
        jsx = P / "AfterEffects" / "build_project.jsx"
        app_name = exe.stem
        _run(["open", "-a", str(exe)])
        script = f'tell application "{app_name}"\n activate\n DoScriptFile "{jsx}"\nend tell'
        r = _run(["osascript", "-e", script], timeout=1800)
        log = P / "AfterEffects" / "build-log.txt"
        if r.returncode != 0:
            raise ToolError(f"After Effects did not run the script: {(r.stderr or r.stdout).strip()[:300]}",
                            "AE › Settings › Scripting & Expressions › tick 'Allow Scripts to Write Files and Access Network', "
                            "or run it by hand: File › Scripts › Run Script File… › build_project.jsx")
        res.summary = log.read_text(encoding="utf-8") if log.exists() else "After Effects ran the builder."
        res.files.append(str(P / "AfterEffects" / f"{name}.aep"))
    elif key == "fcpx":
        r = _run(["open", "-a", str(exe), str(P / "FinalCut" / f"{name}.fcpxml")])
        res.summary = "Final Cut Pro is importing the FCPXML (choose or create a library when it asks)."
    elif key == "resolve":
        _run(["open", "-a", str(exe)])
        studio = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules").exists()
        if studio:
            import time
            time.sleep(25)
            r = _run([sys.executable, str(P / "Resolve" / "build_resolve.py")], timeout=900)
            res.summary = (r.stdout or r.stderr).strip()[-1500:] or "Ran build_resolve.py"
            if r.returncode != 0:
                res.warnings.append("build_resolve.py needs DaVinci Resolve STUDIO (scripting is Studio-only since 21.1) — "
                                    "import by hand: File › Import › Timeline › Projects/Resolve/*.fcpxml")
        else:
            res.summary = "Opened Resolve. Import: File › Import › Timeline › Projects/Resolve/*.fcpxml (see OPEN-IN.md)."
        inst = P / "Resolve" / "install_fusion_titles.command"
        if inst.exists():
            r = _run(["bash", str(inst)])
            res.summary += "\n" + r.stdout.strip()
    elif key == "capcut":
        cmd = P / "CapCut" / "Add to CapCut.command"
        r = _run(["bash", str(cmd)])
        if r.returncode != 0:
            raise ToolError("CapCut draft not added: " + (r.stdout + r.stderr).strip()[-300:], "quit CapCut and run again")
        _run(["open", "-a", str(exe)])
        res.summary = r.stdout.strip()
    elif key in ("kdenlive", "shotcut"):
        f = next((P / "Kdenlive-Shotcut").glob("*.kdenlive" if key == "kdenlive" else "*.mlt"))
        _run(["open", "-a", str(exe), str(f)])
        res.summary = f"Opened {f.name} in {exe.stem}."
    else:
        raise ToolError(f"unknown app {app!r}", "premiere | aftereffects | fcpx | resolve | capcut | kdenlive | shotcut")
    res.data["package"] = str(root)
    return res


@tool("video")
def video_project_relink(package: str) -> Result:
    """After moving a project package (another disk, another Mac): rewrite the media and font paths inside
    every project file to the folder's new location."""
    root = _pkg(package)
    r = _run([sys.executable, str(root / "relink.py")])
    if r.returncode != 0:
        raise ToolError("relink failed: " + r.stderr[-300:])
    return Result(r.stdout.strip(), data={"package": str(root)})
