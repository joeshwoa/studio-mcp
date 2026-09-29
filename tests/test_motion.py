"""Motion department tests: the deterministic renderer and every template at a small size.
Needs Playwright + Chromium; web fonts are downloaded once per session (falls back to system fonts
offline, with a warning)."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright")
if not shutil.which("ffmpeg"):
    pytest.skip("ffmpeg missing", allow_module_level=True)

from PIL import Image  # noqa: E402

from studio.core import qc  # noqa: E402
from studio.core.registry import call  # noqa: E402
from studio.core.result import ToolError  # noqa: E402
from studio.depts import motion as M  # noqa: E402
from studio.depts.motion import engine  # noqa: E402

S = "320x180"


@pytest.fixture(scope="session", autouse=True)
def home(tmp_path_factory):
    h = tmp_path_factory.mktemp("mhome")
    mp = pytest.MonkeyPatch()
    mp.setenv("STUDIO_HOME", str(h))
    yield h
    mp.undo()


def _chromium_ok() -> bool:
    try:
        call("motion_render_html", {"html": "<html><body style='background:#000'></body></html>", "duration": 0.1,
                                    "size": "64x36", "fps": 10})
        return True
    except ToolError:
        return False


@pytest.fixture(scope="session")
def chromium():
    if not _chromium_ok():
        pytest.skip("Chromium for Playwright not installed")


def test_parse_size_and_formats():
    assert M.parse_size("9:16") == (1080, 1920)
    assert M.parse_size("641x361") == (640, 360)
    assert M._formats([], True) == ["mov", "webm"]
    with pytest.raises(ToolError):
        M._formats(["avi"], False)


def test_page_defers_template_until_fonts(tmp_path):
    page = M.build_page("title_card", {"title": "x", "colors": {}}, {}, tmp_path, [])
    html = page.read_text()
    assert 'type="text/x-template"' in html and "window.__ready" in html


def test_virtual_clock_is_deterministic(chromium, tmp_path):
    html = ("<html><body style='margin:0;background:#000'><div id=b style='width:20px;height:20px;background:#f00;"
            "position:absolute;top:8px'></div><script>document.getElementById('b').animate([{left:'0px'},{left:'200px'}],"
            "{duration:1000,fill:'both'})</script></body></html>")
    r = call("motion_render_html", {"html": html, "size": "240x36", "fps": 10, "out": str(tmp_path / "a.mp4")})
    assert r.data["frames"] == 10 and r.data["duration"] == 1.0
    # frame 5 (t=0.5 s): the box must be at x≈100 px — exactly, not "whenever the browser got there"
    f = tmp_path / "f5.png"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", r.files[0], "-vf", "select=eq(n\\,5)", "-frames:v", "1", str(f)], check=True)
    a = np.asarray(Image.open(f).convert("RGB"), np.float32)
    xs = np.where(a[18, :, 0] > 128)[0]
    assert abs(xs.min() - 100) <= 2


def _check(r: Result, alpha: bool):
    assert r.ok, r.warnings
    assert Path(r.previews[0]).exists()
    assert r.data["qc"]["blank_frames"] < r.data["qc"]["sampled"]
    assert r.data["qc"]["motion"] > 0.05
    for o in r.data.get("outputs", []):
        assert o["width"] == 320
        if alpha and o["file"].endswith(".mov"):
            assert "a" in (o["pix_fmt"] or "")


from studio.core.result import Result  # noqa: E402


def test_lower_third_alpha(chromium):
    r = call("motion_lower_third", {"name": "Joshua George", "role": "Founder", "size": S, "duration": 2})
    _check(r, True)
    assert any(f.endswith(".mov") for f in r.files) and any(f.endswith(".webm") for f in r.files)


@pytest.mark.slow
@pytest.mark.parametrize("tool,args,alpha", [
    ("motion_title_card", {"title": "Build *faster*", "subtitle": "sub", "kicker": "EP 1", "duration": 2}, False),
    ("motion_title_card", {"title": "عنوان *مهم* جدا", "style": "minimal", "duration": 2}, False),
    ("motion_lower_third", {"name": "جوشوا جورج", "role": "مؤسس", "style": "glass", "duration": 2}, True),
    ("motion_kinetic_text", {"text": "Stop.|Start *now*", "style": "punch"}, False),
    ("motion_kinetic_text", {"text": "سطر أول|سطر *تاني*", "style": "stack"}, False),
    ("motion_kinetic_text", {"text": "Type it", "style": "type"}, False),
    ("motion_captions", {"segments": [{"start": 0, "end": 1.5, "text": "hello captions world"}], "size": "180x320"}, True),
    ("motion_cta", {"kind": "subscribe", "duration": 3}, True),
    ("motion_cta", {"kind": "follow", "handle": "@me", "duration": 2.5}, True),
    ("motion_countdown", {"start": 2, "end_text": "GO"}, False),
    ("motion_transition", {"shape": "circle", "duration": 1}, True),
    ("motion_counter", {"items": [{"value": 1200, "label": "Users", "suffix": "+"}, {"value": 9.5, "label": "Score"}], "duration": 2}, False),
    ("motion_infographic", {"data": [{"label": "A", "value": 3}, {"label": "B", "value": 5}], "kind": "donut", "duration": 2.5}, False),
    ("motion_infographic", {"data": [{"label": "A", "value": 3}, {"label": "B", "value": 5}], "kind": "columns", "duration": 2.5}, False),
])
def test_templates(chromium, tool, args, alpha):
    a = dict(args)
    a.setdefault("size", S)
    r = call(tool, a)
    if a["size"] == S:
        _check(r, alpha)
    else:
        assert r.ok and r.data["qc"]["motion"] > 0.01


@pytest.mark.slow
def test_logo_reveal_svg(chromium, tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80"><circle cx="40" cy="40" r="30" fill="#ff5a36"/>'
                   '<rect x="90" y="20" width="100" height="40" rx="8" fill="#ffffff"/></svg>')
    for style in ("draw", "pop", "mask"):
        r = call("motion_logo_reveal", {"logo": str(svg), "style": style, "size": S, "duration": 2, "tagline": "tag"})
        _check(r, False)


def test_errors():
    with pytest.raises(ToolError):
        call("motion_infographic", {"data": []})
    with pytest.raises(ToolError):
        call("motion_logo_reveal", {})
    with pytest.raises(ToolError):
        call("motion_captions", {})
