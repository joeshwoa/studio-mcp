"""Driving GIMP headless (GIMP 3.x via Python-Fu, GIMP 2.10 via Script-Fu).

Used to turn our layered PSD into a native XCF whose text layers are REAL GIMP text layers
(editable text, font, size, colour, RTL direction), plus a flattened render of that XCF for QC.
Set STUDIO_GIMP to a specific gimp-console binary to override detection.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ...core.deps import IS_MAC, need
from ...core.result import MissingTool, ToolError

_found: tuple[str, int] | None = None


def find_gimp() -> tuple[str, int]:
    """(executable, major version). Prefers the console build (no GUI)."""
    global _found
    if _found:
        return _found
    cands = []
    if os.environ.get("STUDIO_GIMP"):
        cands.append(os.environ["STUDIO_GIMP"])
    for b in ("gimp-console-3.2", "gimp-console-3.0", "gimp-console-3", "gimp-console", "gimp-console-2.10", "gimp"):
        p = shutil.which(b)
        if p:
            cands.append(p)
    if IS_MAC:
        for app in sorted(glob.glob("/Applications/GIMP*.app"), reverse=True) + sorted(glob.glob(os.path.expanduser("~/Applications/GIMP*.app")), reverse=True):
            cands += sorted(glob.glob(f"{app}/Contents/MacOS/gimp-console*"), reverse=True)
            cands.append(f"{app}/Contents/MacOS/gimp")
    if not cands:
        try:
            cands.append(need("gimp"))
        except MissingTool:
            raise
    for c in cands:
        if not Path(c).exists():
            continue
        try:
            r = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=60)
            m = re.search(r"version (\d+)\.(\d+)", r.stdout + r.stderr)
            if m:
                _found = (c, int(m.group(1)))
                return _found
        except Exception:
            continue
    raise MissingTool("GIMP is not installed (photo editor, needed for the editable .xcf master).",
                      "brew install --cask gimp   (macOS)  ·  sudo apt install gimp   (Linux)")


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIMP2_DIRECTORY": os.environ.get("GIMP2_DIRECTORY", ""), "NO_AT_BRIDGE": "1"}
    env = {k: v for k, v in env.items() if v != ""}
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ToolError(f"GIMP timed out after {timeout}s")


PY3 = r'''
import json, sys
from gi.repository import Gimp, Gio
try:
    from gi.repository import Gegl
except Exception:
    Gegl = None
job = json.load(open(JOBFILE, encoding="utf-8"))
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(job["psd"]))
JUST = {"left": Gimp.TextJustification.LEFT, "right": Gimp.TextJustification.RIGHT,
        "center": Gimp.TextJustification.CENTER}
report = []
def get_font(name, family):
    for getter in (lambda n: Gimp.Font.get_by_name(n), lambda n: (Gimp.fonts_get_by_name(n) or [None])[0]):
        try:
            f = getter(name)
            if f:
                return f
        except Exception:
            pass
    try:
        fl = Gimp.fonts_get_list(family) or []
        if fl:
            return fl[0]
    except Exception:
        pass
    return Gimp.context_get_font()
for t in job["texts"]:
    old = img.get_layer_by_name(t["name"])
    if old is None:
        report.append("missing " + t["name"]); continue
    pos = img.get_item_position(old)
    font = get_font(t["font"], t["family"])
    tl = Gimp.TextLayer.new(img, t["text"], font, float(t["size"]), Gimp.Unit.pixel())
    img.insert_layer(tl, old.get_parent(), pos)
    tl.set_justification(JUST.get(t["align"], Gimp.TextJustification.LEFT))
    tl.set_base_direction(Gimp.TextDirection.RTL if t["direction"] == "rtl" else Gimp.TextDirection.LTR)
    tl.set_line_spacing(float(t["line_spacing"]))
    if t.get("letter_spacing"):
        tl.set_letter_spacing(float(t["letter_spacing"]))
    r, g, b = t["rgb"]
    if Gegl is not None:
        c = Gegl.Color.new("black"); c.set_rgba(r / 255.0, g / 255.0, b / 255.0, 1.0)
        tl.set_color(c)
    tl.resize(float(t["w"]), float(t["h"]))
    tl.set_offsets(int(t["x"]), int(t["y"]))
    tl.set_opacity(float(t["opacity"]) * 100.0)
    tl.set_visible(bool(t["visible"]))
    img.remove_layer(old)
    tl.set_name(t["name"])
    report.append("text " + t["name"])
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, Gio.File.new_for_path(job["xcf"]), None)
if job.get("check_png"):
    dup = img.duplicate()
    dup.flatten()
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup, Gio.File.new_for_path(job["check_png"]), None)
print("STUDIO_REPORT " + json.dumps(report))
'''


def _scm_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rtl_flip(t: dict) -> str:
    """GIMP 2.10 mirrors left/right justification for RTL base direction."""
    a = t["align"]
    if t["direction"] == "rtl":
        return {"left": "right", "right": "left"}.get(a, a)
    return a


def _script_fu(job: dict) -> str:
    j = {"left": 0, "right": 1, "center": 2}
    lines = [f'(let* ((image (car (gimp-file-load RUN-NONINTERACTIVE {_scm_str(job["psd"])} {_scm_str(job["psd"])}))))']
    for t in job["texts"]:
        r, g, b = t["rgb"]
        lines.append(f'''  (let* ((old (car (gimp-image-get-layer-by-name image {_scm_str(t["name"])})))
         (pos (car (gimp-image-get-item-position image old)))
         (tl (car (gimp-text-layer-new image {_scm_str(t["text"])} {_scm_str(t["font"])} {float(t["size"])} PIXELS))))
    (gimp-image-insert-layer image tl 0 pos)
    (gimp-text-layer-set-justification tl {j.get(_rtl_flip(t), 0)})
    (gimp-text-layer-set-base-direction tl {1 if t["direction"] == "rtl" else 0})
    (gimp-text-layer-set-line-spacing tl {float(t["line_spacing"])})
    (gimp-text-layer-set-letter-spacing tl {float(t.get("letter_spacing") or 0)})
    (gimp-text-layer-set-color tl '({r} {g} {b}))
    (gimp-text-layer-resize tl {float(t["w"])} {float(t["h"])})
    (gimp-layer-set-offsets tl {int(t["x"])} {int(t["y"])})
    (gimp-layer-set-opacity tl {float(t["opacity"]) * 100.0})
    (gimp-item-set-visible tl {1 if t["visible"] else 0})
    (gimp-image-remove-layer image old)
    (gimp-item-set-name tl {_scm_str(t["name"])}))''')
    lines.append(f'  (gimp-xcf-save 0 image (car (gimp-image-get-active-drawable image)) {_scm_str(job["xcf"])} {_scm_str(job["xcf"])})')
    if job.get("check_png"):
        lines.append(f'''  (let* ((dup (car (gimp-image-duplicate image)))
         (flat (car (gimp-image-flatten dup))))
    (file-png-save-defaults RUN-NONINTERACTIVE dup flat {_scm_str(job["check_png"])} {_scm_str(job["check_png"])})))''')
    else:
        lines.append(")")
    return "\n".join(lines) + "\n"


def psd_to_xcf(psd: Path, xcf: Path, texts: list[dict], check_png: Path | None = None, timeout: int = 300) -> dict:
    """Convert our PSD to XCF, swapping raster text layers for live GIMP text layers."""
    exe, major = find_gimp()
    job = {"psd": str(psd), "xcf": str(xcf), "texts": texts, "check_png": str(check_png) if check_png else ""}
    tmpd = Path(tempfile.mkdtemp(prefix="studio-gimp-"))
    try:
        if major >= 3:
            jf = tmpd / "job.json"
            jf.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            sf = tmpd / "job.py"
            sf.write_text(f"JOBFILE = {str(jf)!r}\n" + PY3, encoding="utf-8")
            cmd = [exe, "-i", "--no-splash", "--batch-interpreter=python-fu-eval",
                   "-b", f"exec(open({str(sf)!r}, encoding='utf-8').read())", "--quit"]
        else:
            sf = tmpd / "job.scm"
            sf.write_text(_script_fu(job), encoding="utf-8")
            cmd = [exe, "-i", "--no-splash", "-b", f'(load {_scm_str(str(sf))})', "-b", "(gimp-quit 0)"]
        r = _run(cmd, timeout)
        if not xcf.exists():
            tail = (r.stderr + r.stdout)[-1200:]
            raise ToolError(f"GIMP {major} did not write the XCF: {tail}")
        rep = re.findall(r"STUDIO_REPORT (.*)", r.stdout)
        return {"gimp": exe, "gimp_major": major, "report": json.loads(rep[-1]) if rep else [],
                "log_tail": (r.stderr or "")[-400:]}
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
