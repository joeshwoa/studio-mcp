"""Small helpers shared by the NLE writers."""
from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape


def fr(t: float, fps: float) -> int:
    return int(math.floor(t * fps + 0.5))


def file_url(p: str) -> str:
    """Absolute path → file:// URL (FCPXML, xmeml pathurl)."""
    return "file://" + quote(str(p).replace("\\", "/") if str(p).startswith("/") else "/" + str(p).replace("\\", "/"), safe="/:._-~()")


def xmeml_url(p: str) -> str:
    """FCP7/Premiere style pathurl: file://localhost/abs/path"""
    return "file://localhost" + quote(str(p).replace("\\", "/"), safe="/:._-~()")


def x(s) -> str:
    return escape(str(s), {'"': "&quot;"})


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = (h or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20.0)


def tc(frames: int, fps_nominal: int) -> str:
    s, f = divmod(frames, fps_nominal)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def kf_at(kf: list, t: float):
    """Linear interpolation inside a [(t, value)] list (scalar or list values)."""
    if not kf:
        return None
    if t <= kf[0][0]:
        return kf[0][1]
    for (t0, v0), (t1, v1) in zip(kf, kf[1:]):
        if t <= t1:
            u = (t - t0) / max(1e-9, t1 - t0)
            if isinstance(v0, (list, tuple)):
                return [a + (b - a) * u for a, b in zip(v0, v1)]
            return v0 + (v1 - v0) * u
    return kf[-1][1]


def clip_kf(kf: list, a: float, b: float) -> list:
    """Keyframes restricted to [a, b] (item-relative), with interpolated end points, re-based to a."""
    if not kf:
        return []
    out = [(0.0, kf_at(kf, a))]
    out += [(round(t - a, 4), v) for t, v in kf if a + 1e-6 < t < b - 1e-6]
    out.append((round(b - a, 4), kf_at(kf, b)))
    return out


def rel(p: str | Path, root: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(root.resolve()))
    except ValueError:
        return str(p)
