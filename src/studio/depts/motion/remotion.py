"""Remotion (React video) — render an EXISTING Remotion project/composition. Optional engine: needs Node.js,
and Remotion's licence is free only for individuals and companies of ≤ 3 people (otherwise a company
licence is required — remotion.dev/docs/license), so the caller must confirm it with licence_ok=true."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ...config import unique_path
from ...core import deps, qc
from ...core.registry import tool
from ...core.result import MissingTool, Result, ToolError
from . import _out_target, engine

LICENCE = ("Remotion is free for individuals and organisations of up to 3 people; larger companies need a Remotion "
           "company licence (remotion.dev/docs/license).")


def _node() -> tuple[str, str]:
    node, npx = shutil.which("node"), shutil.which("npx")
    if not node or not npx:
        raise MissingTool("Node.js (node + npx) is not installed (needed for motion_remotion).",
                          "brew install node   |   sudo apt install nodejs npm   (Node ≥ 18)")
    return node, npx


@tool("motion", network=True, installs=True)
def motion_remotion(project_dir: str, composition: str, licence_ok: bool = False, install: bool = False, props: dict = {},
                    entry: str = "", codec: str = "h264", transparent: bool = False, frames: str = "", scale: float = 1.0,
                    timeout: int = 3600, project: str = "", out: str = "") -> Result:
    """Render a composition of an EXISTING Remotion (React) project with `npx remotion render` — for
    teams that already build videos in React or batch-render data-driven templates (pass input
    props as props={…}). Returns the video + a contact sheet. For new motion graphics prefer
    motion_compose (no Node, no licence question).

    licence_ok=true is REQUIRED: confirm the user's organisation has ≤ 3 people or holds a Remotion
    company licence (remotion.dev/docs/license). install=true runs `npm install` when node_modules is
    missing (downloads packages). codec: h264 | h265 | vp9 | prores; transparent=true → ProRes 4444
    (or VP9 with alpha when codec=vp9). frames: '0-89' to render a range. entry: the entry file if
    Remotion cannot find it (e.g. src/index.ts)."""
    if not licence_ok:
        raise ToolError("Remotion licence not confirmed. " + LICENCE,
                        "ask the user; if their organisation has ≤ 3 people or a company licence, call again with licence_ok=true — "
                        "or use motion_compose, which needs no licence")
    pdir = Path(project_dir).expanduser().resolve()
    if not (pdir / "package.json").exists():
        raise ToolError(f"{pdir} is not a Node project (no package.json)", "point project_dir at the Remotion project root")
    pkg = json.loads((pdir / "package.json").read_text(encoding="utf-8"))
    alldeps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    if not any(k == "remotion" or k.startswith("@remotion/") for k in alldeps):
        raise ToolError(f"{pdir.name} does not depend on remotion", "this tool only renders existing Remotion projects")
    _node()
    if not (pdir / "node_modules").exists():
        if not install:
            raise ToolError("node_modules is missing", "call again with install=true to run `npm install` (downloads packages) — with the user's OK")
        deps.run([shutil.which("npm") or "npm", "install", "--no-audit", "--no-fund"], timeout=1800, cwd=str(pdir))
    odir, stem = _out_target(out, project, "remotion-" + composition[:30])
    ext = {"h264": "mp4", "h265": "mp4", "vp9": "webm", "prores": "mov"}.get(codec)
    if not ext:
        raise ToolError(f"unknown codec {codec!r}", "h264 | h265 | vp9 | prores")
    if transparent and codec in ("h264", "h265"):
        codec, ext = "prores", "mov"
    dest = unique_path(odir, stem, ext)
    cmd = [shutil.which("npx") or "npx", "remotion", "render"] + ([entry] if entry else []) + [composition, str(dest), f"--codec={codec}"]
    if props:
        pf = engine.scratch_dir("remotion-") / "props.json"
        pf.write_text(json.dumps(props), encoding="utf-8")
        cmd.append(f"--props={pf}")
    if transparent:
        cmd += ["--prores-profile=4444", "--pixel-format=yuva444p10le"] if codec == "prores" else ["--pixel-format=yuva420p"]
    if frames:
        cmd.append(f"--frames={frames}")
    if scale and scale != 1.0:
        cmd.append(f"--scale={scale}")
    deps.run(cmd, timeout=timeout, cwd=str(pdir))
    if not dest.exists():
        raise ToolError("Remotion finished but the output file is missing")
    sheet = unique_path(odir, stem + "-sheet", "png")
    qc.contact_sheet(dest, sheet)
    pr = qc.probe(dest)
    res = Result(f"Remotion composition '{composition}' rendered ({pr.get('duration', 0):.1f}s, {pr.get('width')}x{pr.get('height')}).",
                 files=[str(dest)], previews=[str(sheet)], data={"engine": "remotion", "probe": pr, "licence": LICENCE})
    res.warnings.append("licence confirmed by the caller: " + LICENCE)
    return res
