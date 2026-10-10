"""Bake the studio's grades into .cube LUTs (33³) that every grading app reads — Resolve (Color page /
clip LUT), Premiere & After Effects (Lumetri › Creative › Look / Input LUT), Final Cut (Custom LUT
effect), CapCut (Adjust › LUT), Kdenlive. The LUT is made by running the SAME ffmpeg colour chain the
render used over an identity image, so the look matches the MP4 (spatial parts — vignette — are not
colour transforms and are listed separately)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from .. import _common as C

N = 33


def _identity(dest: Path) -> Path:
    """33³ identity colours, red fastest (the .cube order), as a 16-bit PNG N wide × N² tall."""
    if dest.exists():
        return dest
    import subprocess
    i = np.arange(N, dtype=np.float64) / (N - 1)
    b, g, r = np.meshgrid(i, i, i, indexing="ij")
    rgb = np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)
    raw = dest.with_suffix(".rgb48")
    raw.write_bytes((rgb * 65535 + 0.5).astype("<u2").tobytes())
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{N}x{N * N}",
                    "-i", str(raw), str(dest)], check=True)
    raw.unlink()
    return dest


def chain_for(grade) -> tuple[str, list[str]]:
    """ffmpeg colour chain for a grade dict/str, minus spatial filters → (chain, dropped)."""
    if not grade:
        return "", []
    g = {"preset": grade} if isinstance(grade, str) else dict(grade)
    dropped = []
    if g.get("vignette"):
        dropped.append("vignette")
        g.pop("vignette")
    return C.grade_filter(g), dropped


def bake(grades: list, dest_dir: Path, name: str, work: Path) -> tuple[Path | None, list[str]]:
    """Apply the grades in order (clip grade, then programme grade) → one .cube (cached by content)."""
    chains, dropped = [], []
    for g in grades:
        ch, dr = chain_for(g)
        if ch:
            chains.append(ch.replace("_ga", "_ga%d" % len(chains)).replace("_gb", "_gb%d" % len(chains)).replace("_gc", "_gc%d" % len(chains)))
        dropped += dr
    if not chains:
        return None, dropped
    key = hashlib.sha1(json.dumps(chains).encode()).hexdigest()[:8]
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{name}-{key}.cube"
    if out.exists():
        return out, dropped
    work.mkdir(parents=True, exist_ok=True)
    ident = _identity(work / "identity33.png")
    graded = work / f"graded-{key}.png"
    C.ff(["-i", str(ident), "-vf", "format=gbrp," + ",".join(chains) + ",format=rgb48le", "-frames:v", "1", str(graded)],
         what="LUT bake")
    import subprocess
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(graded), "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"],
                         capture_output=True, check=True).stdout
    vals = np.frombuffer(raw, "<u2").reshape(-1, 3).astype(np.float64) / 65535.0
    lines = [f'TITLE "{name}"', f"LUT_3D_SIZE {N}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in vals]
    out.write_text("\n".join(lines) + "\n")
    return out, dropped


def check_cube(p: Path) -> list[str]:
    issues = []
    rows = [l for l in p.read_text().splitlines() if l and l[0] in "0123456789-."]
    if len(rows) != N ** 3:
        issues.append(f"{p.name}: {len(rows)} rows (expected {N ** 3})")
    return issues
