"""Vector department tests. Inkscape-based ones skip when Inkscape is missing."""
from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from studio.core.registry import call, load_all
from studio.core.result import ToolError

needs_rsvg = pytest.mark.skipif(not shutil.which("rsvg-convert"), reason="librsvg (rsvg-convert) missing")
needs_inkscape = pytest.mark.skipif(not shutil.which("inkscape"), reason="Inkscape missing")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    load_all()


SPEC = {"width": 600, "height": 400, "background": {"type": "linear", "colors": ["#0f172a", "#1e3a8a"], "angle": 0},
        "elements": [
            {"type": "rect", "id": "a", "x": 40, "y": 40, "width": 200, "height": 200, "rx": 30, "fill": "#e11d48"},
            {"type": "circle", "id": "b", "cx": 220, "cy": 220, "r": 110, "fill": {"type": "radial", "colors": ["#fde68a", "#f59e0b"]},
             "shadow": {"dy": 6, "blur": 10}},
            {"type": "group", "id": "g", "transform": "translate(300 60)", "elements": [
                {"type": "path", "d": "M0 100 L60 0 L120 100 Z", "fill": "#fff"}]},
            {"type": "text", "text": "مرحبا بيك", "x": 560, "y": 330, "anchor": "end", "font": "DejaVu Sans", "size": 40, "fill": "#fff"},
            {"type": "text", "text": "Hello\nWorld", "x": 40, "y": 320, "font": "DejaVu Sans", "size": 28, "fill": "#cbd5e1"}]}


def _compose(**kw):
    return call("vector_compose", {"spec": SPEC, "name": "art", **kw})


@needs_rsvg
def test_compose_outputs():
    r = _compose()
    svg = [f for f in r.files if f.endswith(".svg")][0]
    root = ET.parse(svg).getroot()
    assert root.get("viewBox") == "0 0 600 400"
    txt = [e for e in root.iter() if e.tag.endswith("text")]
    assert any(e.get("direction") == "rtl" and e.get("text-anchor") == "start" for e in txt)  # visual 'end' for RTL
    assert any(f.endswith(".pdf") for f in r.files) and any(f.endswith(".png") for f in r.files)
    assert Image.open([f for f in r.files if f.endswith(".png")][0]).size == (1200, 800)
    with pytest.raises(ToolError):
        call("vector_compose", {"spec": {"elements": [{"type": "blob"}]}})


@needs_rsvg
def test_info_optimize_recolor():
    svg = [f for f in _compose(pdf=False, png=False).files if f.endswith(".svg")][0]
    info = call("vector_info", {"svg": svg})
    assert "#e11d48" in info.data["colors"] and info.data["live_text"] >= 2
    opt = call("vector_optimize", {"svg": svg})
    assert opt.data["visual_diff"] < 1.0
    rec = call("vector_recolor", {"svg": svg, "mapping": {"#e11d48": "#10b981"}})
    assert "#10b981" in Path(rec.files[0]).read_text()
    rec2 = call("vector_recolor", {"svg": svg, "palette": ["#111111", "#777777", "#eeeeee"]})
    assert set(rec2.data["mapping"].values()) <= {"#111111", "#777777", "#eeeeee"}


@needs_rsvg
def test_export_presets(tmp_path):
    svg = [f for f in _compose(pdf=False, png=False).files if f.endswith(".svg")][0]
    r = call("vector_export", {"svg": svg, "preset": "favicon", "background": "#0f172a"})
    names = {Path(f).name for f in r.files}
    assert {"favicon.ico", "apple-touch-icon.png", "site.webmanifest", "icon.svg"} <= names
    assert Image.open([f for f in r.files if f.endswith("apple-touch-icon.png")][0]).mode == "RGB"
    r = call("vector_export", {"svg": svg, "preset": "app_icon", "background": "#0f172a"})
    cj = [f for f in r.files if f.endswith("Contents.json")][0]
    assert json.loads(Path(cj).read_text())["images"][0]["size"] == "1024x1024"
    r = call("vector_export", {"svg": svg, "preset": "icons", "sizes": [16, 64]})
    assert sorted(Image.open(f).size for f in r.files) == [(16, 16), (64, 64)]
    r = call("vector_export", {"svg": svg, "formats": ["png", "pdf"], "dpi": 150})
    assert Image.open([f for f in r.files if f.endswith(".png")][0]).width == 938  # 600 px @ 96 → 150 dpi


@needs_inkscape
@pytest.mark.slow
def test_outlines_and_boolean():
    svg = [f for f in _compose(pdf=False, png=False).files if f.endswith(".svg")][0]
    r = call("vector_text_to_outlines", {"svg": svg})
    assert r.data["text_after"] == 0 and r.data["visual_diff"] < 3
    for op in ("union", "difference", "intersection"):
        r = call("vector_boolean", {"svg": svg, "operation": op, "ids": ["a", "b"]})
        root = ET.parse(r.files[0]).getroot()
        assert not [e for e in root.iter() if e.get("id") == "b"]  # merged away
    with pytest.raises(ToolError):
        call("vector_boolean", {"svg": svg, "operation": "melt"})


@needs_rsvg
def test_trace(tmp_path):
    pytest.importorskip("vtracer")
    im = Image.new("RGB", (400, 300), "#f5f0e6")
    d = ImageDraw.Draw(im)
    d.ellipse((60, 50, 220, 210), fill="#e11d48")
    d.rectangle((230, 90, 350, 250), fill="#1d4ed8")
    p = tmp_path / "logo.jpg"
    im.save(p, quality=60)
    r = call("vector_trace", {"image": str(p), "colors": 3})
    assert r.data["background_removed"] and r.data["paths"] < 40 and r.data["diff"] < 10
    sk = tmp_path / "sketch.png"
    im2 = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(im2).line((20, 20, 380, 280), fill="black", width=8)
    im2.save(sk)
    r = call("vector_trace", {"image": str(sk), "black_and_white": True})
    assert r.data["paths"] >= 1 and r.data["diff"] < 10
