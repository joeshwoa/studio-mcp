"""Pro-NLE project packages (video/nle): one edit → editable projects for Premiere, After Effects, Resolve,
Final Cut, CapCut, Avid/Pro Tools, Kdenlive, OTIO, EDL — every writer's own check must pass, and the
geometry/LUT/native-graphics maths is checked against the render."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

if not shutil.which("ffmpeg"):
    pytest.skip("ffmpeg missing", allow_module_level=True)

from studio.depts.video import timeline as TL  # noqa: E402
from studio.depts.video.nle import doc as D  # noqa: E402
from studio.depts.video.nle import luts as L  # noqa: E402


def _ff(args, dest):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(dest)], check=True, timeout=180)
    return dest


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("nlemedia")
    _ff(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-f", "lavfi", "-i", "sine=f=440:d=6", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"], d / "a.mp4")
    _ff(["-f", "lavfi", "-i", "testsrc=s=480x360:r=30:d=6", "-f", "lavfi", "-i", "sine=f=660:d=6", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"], d / "b.mp4")
    _ff(["-f", "lavfi", "-i", "sine=f=220:d=20"], d / "music.wav")
    _ff(["-f", "lavfi", "-i", "gradients=s=800x600:d=1", "-frames:v", "1"], d / "photo.png")
    return d


def test_geometry_matches_render_maths():
    c = TL.Clip("video", Path("x.mp4"), 2.0, src_w=1280, src_h=720, zoom=1.2, focus=(0.3, 0.5))
    b = D.cover_box(1280, 720, 1920, 1080, 1.2, (0.3, 0.5))
    assert b[2] == pytest.approx(1920 * 1.2) and b[0] == pytest.approx(-(1920 * 1.2 - 1920) * 0.3)
    assert D.contain_box(1080, 1920, 1920, 1080) == [656.25, 0.0, 607.5, 1080.0]
    c.kind, c.ken_burns, c.src_w, c.src_h = "image", "in", 1920, 1080
    b0, b1 = D.kenburns_boxes(c, 1920, 1080)
    assert b0 == [0.0, 0.0, 1920.0, 1080.0] and b1[2] == pytest.approx(1920 * 1.12)


def test_abutted_transitions_centre_on_the_cut():
    items = [{"id": "a", "rec_in": 0.0, "rec_out": 4.0, "src_in": 1.0, "speed": 1.0},
             {"id": "b", "rec_in": 3.0, "rec_out": 7.0, "src_in": 2.0, "speed": 1.0, "trans_in": {"type": "dissolve", "dur": 1.0}}]
    a, b = D.abutted(items)
    assert a["rec_out"] == 3.5 and b["rec_in"] == 3.5 and b["src_in"] == 2.5


def test_duck_keyframes_dip_under_speech():
    kf = D.duck_keyframes(-18, 10, [(2.0, 4.0)], 0.0, 10.0)
    vals = dict(kf)
    assert vals[2.0] == -28 and vals[0.0] == -18 and max(t for t, _ in kf) <= 10


def test_lut_reproduces_the_grade(tmp_path):
    p, dropped = L.bake(["teal_orange", {"saturation": 0.3, "vignette": 0.5}], tmp_path / "luts", "t", tmp_path / "w")
    assert p and not L.check_cube(p) and dropped == ["vignette"]
    src = _ff(["-f", "lavfi", "-i", "testsrc2=s=160x90", "-frames:v", "1"], tmp_path / "s.png")
    ch = L.chain_for("teal_orange")[0] + "," + L.chain_for({"saturation": 0.3})[0]
    a = _ff(["-i", str(src), "-vf", "format=gbrp," + ch], tmp_path / "a.png")
    b = _ff(["-i", str(src), "-vf", f"format=gbrp,lut3d=file={p}:interp=tetrahedral"], tmp_path / "b.png")
    from PIL import Image
    diff = np.abs(np.asarray(Image.open(a).convert("RGB"), float) - np.asarray(Image.open(b).convert("RGB"), float))
    assert diff.mean() < 1.0


@pytest.mark.slow
def test_package_every_app(media, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    from studio.core.registry import call
    tl = {"size": "1280x720", "fps": 30, "clips": [
        {"title": "Chapter One", "subtitle": "Start", "duration": 2, "style": "bold"},
        {"src": str(media / "a.mp4"), "in": 0, "out": 4, "transition": {"type": "dip", "duration": 0.5}, "grade": "teal_orange", "j_cut": 0.3},
        {"src": str(media / "b.mp4"), "in": 1, "out": 5, "speed": 1.5, "transition": {"type": "dissolve", "duration": 0.5}},
        {"image": str(media / "photo.png"), "duration": 2, "ken_burns": "in"}],
        "overlays": [{"type": "lower_third", "at": 2.5, "duration": 3, "name": "Joshua George", "role": "Founder"}],
        "audio": [{"src": str(media / "music.wav"), "volume_db": -18, "duck": True, "loop": True}],
        "captions": {"words": [{"word": "hello", "start": 2.6, "end": 3.0}, {"word": "world", "start": 3.0, "end": 3.5}], "style": "clean"},
        "fade_in": 0.3, "fade_out": 0.5}
    r = call("video_edit", {"timeline": tl, "out": str(tmp_path / "out"), "name": "Pkg Test"})
    pk = r.data["project_package"]
    assert all(v == [] for v in pk["checks"].values()), pk["checks"]
    root = Path(pk["dir"])
    for rel in ("Projects/Premiere/Pkg Test.xml", "Projects/FinalCut/Pkg Test.fcpxml", "Projects/AfterEffects/build_project.jsx",
                "Projects/Resolve/build_resolve.py", "Projects/CapCut/Pkg Test/draft_content.json", "Projects/Avid-ProTools/Pkg Test.aaf",
                "Projects/Interchange/Pkg Test.otio", "Projects/Kdenlive-Shotcut/Pkg Test.kdenlive", "OPEN-IN.md", "relink.py"):
        assert (root / rel).exists(), rel
    assert pk["graphics_native"] >= 2 and pk["captions_native"]
    assert list((root / "Media" / "LUTs").glob("*.cube"))
    d = json.loads((root / "edit-document.json").read_text())
    lt = next(g for g in d["graphics"].values() if "Lower" in g["name"])
    assert {l["text"] for l in lt["layers"] if l["type"] == "text"} >= {"Joshua George", "Founder"}
