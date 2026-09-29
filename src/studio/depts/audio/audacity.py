"""Bridge to a running Audacity through its mod-script-pipe (free, built into Audacity)."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

from ...core.deps import DEPS, IS_MAC, find_bin
from ...core.registry import tool
from ...core.result import Result, ToolError

ENABLE_HELP = ("Enable scripting once: open Audacity → Preferences (macOS: Audacity ▸ Settings) → Modules → set "
               "'mod-script-pipe' to Enabled → quit and restart Audacity. Leave Audacity open while the studio talks to it.")


def pipe_paths() -> tuple[str, str]:
    if sys.platform.startswith("win"):
        return r"\\.\pipe\ToSrvPipe", r"\\.\pipe\FromSrvPipe"
    uid = os.getuid()
    base = os.environ.get("AUDACITY_PIPE_DIR", "/tmp")
    return f"{base}/audacity_script_pipe.to.{uid}", f"{base}/audacity_script_pipe.from.{uid}"


class Pipe:
    """Minimal client: write one command line, read the reply up to the blank line after
    'BatchCommand finished:'. Every blocking open/read runs under a timeout thread so a stuck
    Audacity can never hang the agent."""

    def __init__(self, timeout: float = 5.0):
        self.to_path, self.from_path = pipe_paths()
        if not sys.platform.startswith("win") and not (Path(self.to_path).exists() and Path(self.from_path).exists()):
            raise ToolError("Audacity's scripting pipe is not available (Audacity closed, or mod-script-pipe disabled).",
                            ENABLE_HELP)
        self.to_f = self._with_timeout(lambda: open(self.to_path, "w"), timeout)
        self.from_f = self._with_timeout(lambda: open(self.from_path, "r"), timeout)

    @staticmethod
    def _with_timeout(fn, timeout: float):
        box: dict = {}

        def run():
            try:
                box["v"] = fn()
            except Exception as e:  # pragma: no cover
                box["e"] = e
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            raise ToolError("Audacity did not answer on its scripting pipe (busy with a dialog?)", ENABLE_HELP)
        if "e" in box:
            raise ToolError(f"Audacity pipe error: {box['e']}", ENABLE_HELP)
        return box["v"]

    def send(self, cmd: str, timeout: float = 120.0) -> str:
        self.to_f.write(cmd.strip() + "\n")
        self.to_f.flush()

        def read():
            lines = []
            while True:
                line = self.from_f.readline()
                if line == "":
                    break
                if line == "\n" and any(l.startswith("BatchCommand finished") for l in lines):
                    break
                lines.append(line.rstrip("\n"))
            return "\n".join(lines)
        return self._with_timeout(read, timeout)

    def close(self):
        for f in (getattr(self, "to_f", None), getattr(self, "from_f", None)):
            try:
                f and f.close()
            except Exception:
                pass


def _q(p: str) -> str:
    return '"' + str(p).replace('"', "'") + '"'


def _launch() -> str:
    exe = find_bin(DEPS["audacity"])
    if not exe:
        raise ToolError("Audacity is not installed", "brew install --cask audacity (macOS) / apt install audacity")
    if IS_MAC and ".app/" in exe:
        subprocess.Popen(["open", "-a", exe.split(".app/")[0] + ".app"])
    else:
        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return exe


@tool("audio")
def audio_audacity(action: str = "status", path: str = "", commands: list[str] = [], export: str = "",
                   launch: bool = False, timeout: float = 120.0) -> Result:
    """Drive a running Audacity (free Audition replacement) through mod-script-pipe so the user can
    watch/continue by hand. action: status (is Audacity reachable?) | open (import `path` into a new
    project window) | run (send raw scripting `commands`, e.g. ["SelectAll:", "Normalize: PeakLevel=-1",
    "Compressor: Threshold=-18 Ratio=3"]) | process (open `path`, run `commands` on all audio, export
    to `export`, close the project). launch=true starts Audacity first if it is installed.
    Scripting reference: https://manual.audacityteam.org/man/scripting_reference.html.
    If the pipe is missing, the result explains how to enable mod-script-pipe (one-time setting)."""
    if launch:
        _launch()
        for _ in range(40):
            to_p, from_p = pipe_paths()
            if sys.platform.startswith("win") or (Path(to_p).exists() and Path(from_p).exists()):
                break
            time.sleep(0.5)
    installed = find_bin(DEPS["audacity"])
    try:
        pipe = Pipe()
    except ToolError as e:
        if action == "status":
            return Result(f"Audacity is {'installed' if installed else 'NOT installed'} but not reachable: {e}",
                          data={"installed": bool(installed), "reachable": False, "pipe": pipe_paths()},
                          next_steps=[ENABLE_HELP] if installed else
                          ["Install Audacity: brew install --cask audacity (macOS)", ENABLE_HELP])
        raise
    log = []
    try:
        if action == "status":
            r = pipe.send("GetInfo: Type=Tracks Format=JSON", timeout=10)
            return Result("Audacity is running and scriptable.", data={"reachable": True, "tracks": r[:2000]})
        cmds: list[str] = []
        if action in ("open", "process"):
            if not path:
                raise ToolError("path is required for open/process")
            p = Path(path).expanduser().resolve()
            if not p.exists():
                raise ToolError(f"no such file: {p}")
            if action == "process":
                cmds.append("New:")
            cmds.append(f"Import2: Filename={_q(p)}")
        if action in ("run", "process"):
            if action == "process":
                cmds.append("SelectAll:")
            cmds += list(commands)
        if action == "process":
            if not export:
                raise ToolError("export (output file path) is required for process")
            e = Path(export).expanduser()
            if e.exists():
                raise ToolError(f"{e} exists — refusing to overwrite", "choose a new export path")
            e.parent.mkdir(parents=True, exist_ok=True)
            cmds += ["SelectAll:", f"Export2: Filename={_q(e)} NumChannels=2"]
        if action not in ("open", "run", "process"):
            raise ToolError(f"unknown action {action!r}", "status | open | run | process")
        failed = []
        for c in cmds:
            reply = pipe.send(c, timeout)
            ok = "BatchCommand finished: OK" in reply
            log.append({"command": c, "ok": ok, "reply": reply[-300:]})
            if not ok:
                failed.append(c)
                break
        res = Result(f"Audacity: ran {len(log)} command(s)" + (f"; FAILED at {failed[0]!r}" if failed else " OK") + ".",
                     data={"log": log})
        res.ok = not failed
        if action == "process" and not failed:
            e = Path(export).expanduser()
            if e.exists():
                res.files.append(str(e))
                try:
                    from . import _common as C
                    m, w = C.measure(e)
                    res.warnings += w
                    res.data["metrics"] = m
                    res.previews.append(str(C.preview(e, C.sibling(e, ".png"), metrics=m)))
                except Exception:
                    pass
                pipe.send("Close:", 10)  # may prompt to save in some versions; user can dismiss
            else:
                res.warnings.append("Audacity reported success but the export file is missing")
        return res
    finally:
        pipe.close()
