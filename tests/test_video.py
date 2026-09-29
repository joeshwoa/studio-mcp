"""Video department tests. Media is synthesised with ffmpeg (lavfi) so nothing is downloaded; @slow
tests run full renders that call other departments (motion titles, Whisper, audio_mix)."""
from __future__ import annotations

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
    pytest.skip("ffmpeg missing", allow_module_level=True)

from studio.core.registry import call  # noqa: E402
from studio.core.result import ToolError  # noqa: E402
from studio.depts.video import _common as C  # noqa: E402
from studio.depts.video import captions as K  # noqa: E402
from studio.depts.video import reframe as RF  # noqa: E402
from studio.depts.video import timeline as TL  # noqa: E402


@pytest.fixture(scope="session")
def home(tmp_path_factory):
    h = tmp_path_factory.mktemp("home")
    mp = pytest.MonkeyPatch()
    mp.setenv("STUDIO_HOME", str(h))
    yield h
    mp.undo()


@pytest.fixture(scope="session")
def media(home):
    d = home / "media"
    d.mkdir()

    def mk(name, args):
        p = d / name
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(p)], check=True, timeout=120)
        return p
    # 4 s 640x360 test pattern with a tone that is silent between 1.5 s and 2.5 s (a "pause")
    a = mk("a.mp4", ["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=4", "-f", "lavfi", "-i",
                     "aevalsrc='0.3*sin(2*PI*220*t)*(lt(t,1.5)+gt(t,2.5))':s=48000:d=4",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"])
    b = mk("b.mp4", ["-f", "lavfi", "-i", "smptehdbars=s=1280x720:r=25:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p"])
    img = d / "still.png"
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (800, 600), (30, 60, 120))
    ImageDraw.Draw(im).ellipse([300, 200, 500, 400], fill=(250, 200, 40))
    im.save(img)
    return {"a": a, "b": b, "img": img, "dir": d}


# ─────────────────────────── pure logic ───────────────────────────

def test_xfade_names():
    assert C.xfade_name("dissolve") == "fade"
    assert C.xfade_name("dip") == "fadeblack"
    assert C.xfade_name("slide_left") == "slideleft"
    assert C.xfade_name("circleopen") == "circleopen"
    with pytest.raises(ToolError):
        C.xfade_name("nope")


def test_parse_size():
    assert C.parse_size("9:16") == (1080, 1920)
    assert C.parse_size("1279x721") == (1278, 720)
    assert C.parse_size("", (10, 20)) == (10, 20)


def test_grade_filter_strings(home):
    assert C.grade_filter(None) == ""
    f = C.grade_filter({"exposure": 0.5, "contrast": 0.2, "temperature": 0.5})
    assert "eq=" in f and "colortemperature" in f


def test_timeline_layout_and_jl(home, media):
    spec = {"size": "640x360", "fps": 30, "clips": [
        {"src": str(media["a"]), "in": 0, "out": 2, "l_cut": 0.5},
        {"src": str(media["b"]), "in": 0.5, "out": 2.5, "transition": {"type": "dissolve", "duration": 0.5}},
        {"src": str(media["a"]), "in": 2.5, "out": 4, "j_cut": 0.4}]}
    tl = TL.normalize(spec, home / "assets")
    assert [round(c.start, 3) for c in tl.clips] == [0.0, 1.5, 3.5]
    assert abs(tl.duration - 5.0) < 1e-6
    assert tl.clips[0].l == 0.5 and tl.clips[2].j == 0.4
    assert tl.clips[1].has_audio is False
    ins, graph, lab = TL.build_video_graph(tl)
    assert "xfade=transition=fade:duration=0.5000:offset=1.5000" in graph and "concat=n=2" in graph
    g = TL.build_dialog_graph(tl)
    assert g and "adelay=3100" in g[1]          # j-cut: audio of clip 3 starts 0.4 s before its picture


def test_speed_ramp_segments():
    from studio.depts.video.edit import _ramp_segments
    segs = _ramp_segments(10.0, [{"start": 3, "end": 6, "speed": 4, "ease": 0.6}])
    assert segs[0] == (0.0, 3.0, 1.0) and segs[-1][1] == 10.0
    assert max(s for _, _, s in segs) == 4.0
    assert abs(sum(b - a for a, b, _ in segs) - 10.0) < 1e-3   # covers the source exactly


def test_reframe_path_and_expr():
    an = {"t": [0, 1, 2, 3], "x": [0.2, 0.25, 0.7, 0.72], "y": [0.4] * 4, "kind": ["face"] * 4, "cuts": [], "samples": 4}
    grid, path = RF.camera_path(an, 3.0, 0.3)
    assert path.min() >= 0.15 - 1e-9 and path.max() <= 0.85 + 1e-9
    assert path[-1] > path[0] + 0.2                               # the camera followed the subject
    kf = RF.simplify(grid, path * 1920 - 288, 2.0)
    e = RF.crop_expr(kf, 1920 - 576)
    assert e.startswith("clip(") and "gte(t," in e


def test_captions_ass_rtl_order_and_srt(tmp_path):
    ws = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate("انا هنا الان".split())]
    ass, chunks = K.build_ass(ws, 1080, 1920, "reels")
    assert "[V4+ Styles]" in ass and "Base_ar" in ass
    first_hi = next(l for l in ass.splitlines() if l.startswith("Dialogue: 1"))
    # visual order for RTL: the LAST spoken word is written first
    assert first_hi.index("الان") < first_hi.index("انا")
    en = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate("one two three four five six".split())]
    ass2, ch2 = K.build_ass(en, 1920, 1080, "karaoke")
    assert "\\kf" in ass2
    srt = K.write_srt(K.segments_from_words(en), tmp_path / "x.srt").read_text()
    assert "00:00:00,000 -->" in srt and "one two" in srt


# ─────────────────────────── renders (fast, synthetic) ───────────────────────────

def test_probe_and_trim(home, media):
    r = call("video_probe", {"path": str(media["a"]), "project": "t"})
    assert r.data["info"]["width"] == 640 and r.data["info"]["audio"]
    assert Path(r.previews[0]).exists()
    t = call("video_trim", {"path": str(media["a"]), "start": 1.0, "end": 3.0, "project": "t"})
    assert abs(C.probe(t.files[0])["duration"] - 2.0) < 0.1


def test_split_every(home, media):
    r = call("video_split", {"path": str(media["a"]), "every": 1.5, "project": "t"})
    assert len(r.files) == 3 and not r.files[0].endswith(".")


def test_concat_dissolve_writes_kdenlive(home, media):
    r = call("video_concat", {"paths": [str(media["a"]), str(media["b"])], "transition": "dissolve",
                              "transition_duration": 0.5, "size": "640x360", "project": "t"})
    mp4 = r.files[0]
    info = C.probe(mp4)
    assert (info["width"], info["height"]) == (640, 360)
    assert abs(info["duration"] - 6.5) < 0.1
    kd = next(f for f in r.files if f.endswith(".kdenlive"))
    root = ET.parse(kd).getroot()
    assert root.find("profile").get("width") == "640"
    assert any(t.find("property[@name='mlt_service']").text == "luma" for t in root.iter("transition"))
    if shutil.which("melt"):
        chk = r.data.get("kdenlive_check", {})
        if chk.get("ok") is not None:
            assert chk["ok"], chk
            assert abs(chk["melt_duration"] - info["duration"]) < 0.3


def test_l_cut_audio_under_next_picture(home, media):
    # clip b has no audio: with l_cut the tone of clip a must continue under b's picture
    spec = {"size": "640x360", "fps": 30, "loudness": None, "clips": [
        {"src": str(media["a"]), "in": 0, "out": 1.0, "l_cut": 0.4}, {"src": str(media["b"]), "in": 0, "out": 1.5}]}
    r = call("video_edit", {"timeline": spec, "project": "t", "kdenlive": False, "name": "lcut"})
    wav = Path(r.files[0]).with_suffix(".wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", r.files[0], "-ac", "1", "-ar", "8000", str(wav)], check=True)
    import soundfile as sf
    x, sr = sf.read(str(wav))
    rms = lambda a, b: float(np.sqrt(np.mean(x[int(a * sr):int(b * sr)] ** 2)))
    assert rms(1.05, 1.3) > 0.02          # still sounding after the picture cut
    assert rms(1.6, 2.4) < 0.005          # and gone afterwards


def test_timeline_images_colour_overlays(home, media):
    spec = {"size": "640x360", "fps": 25, "loudness": None, "clips": [
        {"image": str(media["img"]), "duration": 2, "ken_burns": "in"},
        {"color": "#223344", "duration": 1, "transition": {"type": "dip", "duration": 0.4}},
        {"src": str(media["a"]), "in": 0, "out": 2, "transition": {"type": "wipe_left", "duration": 0.4}, "grade": {"saturation": -1}}],
        "overlays": [{"type": "image", "src": str(media["img"]), "at": 0.5, "duration": 2, "position": "top-right", "width": 0.2},
                     {"type": "pip", "src": str(media["b"]), "at": 3, "duration": 1.5, "width": 0.3}],
        "fade_in": 0.3, "fade_out": 0.3}
    r = call("video_edit", {"timeline": spec, "project": "t", "name": "mix"})
    info = C.probe(r.files[0])
    assert abs(info["duration"] - 4.2) < 0.1 and info["fps"] == 25
    assert any(p.endswith("-sheet.png") for p in r.previews)


def test_export_gif_and_prores(home, media):
    g = call("video_export", {"path": str(media["b"]), "preset": "gif", "project": "t"})
    assert g.files[0].endswith(".gif") and C.probe(g.files[0])["width"] <= 480
    p = call("video_export", {"path": str(media["a"]), "preset": "prores", "project": "t"})
    info = C.probe(p.files[0])
    assert info["codec"] == "prores" and p.files[0].endswith(".mov")


def test_loudness_target(home, media):
    r = call("video_loudness", {"path": str(media["a"]), "target": "youtube", "project": "t"})
    assert abs(r.data["loudness"]["lufs"] - (-14)) < 1.0


def test_grade_manual(home, media):
    r = call("video_grade", {"path": str(media["b"]), "saturation": -1.0, "project": "t"})
    fr = C.frame_at(r.files[0], 1.0, home / "g.png")
    a = np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(fr).convert("RGB"), np.float32)
    assert np.abs(a[..., 0] - a[..., 1]).mean() < 6      # desaturated


def test_errors(home, media):
    with pytest.raises(ToolError):
        call("video_trim", {"path": str(media["a"]), "start": 3, "end": 2})
    with pytest.raises(ToolError):
        call("video_edit", {"timeline": {"clips": []}})
    with pytest.raises(ToolError):
        call("video_export", {"path": str(media["a"]), "preset": "nope"})


# ─────────────────────────── slow: cross-department ───────────────────────────

@pytest.mark.slow
def test_remove_silence(home, media):
    r = call("video_remove_silence", {"path": str(media["a"]), "max_pause": 0.2, "project": "t"})
    assert r.data["removed_seconds"] > 0.5
    assert C.probe(r.files[0])["duration"] < 3.5


@pytest.mark.slow
def test_stabilize_runs(home, media):
    shaky = media["dir"] / "shaky.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(media["b"]), "-vf",
                    "crop=1100:620:90+40*sin(t*9):50+25*sin(t*7),scale=640:360", str(shaky)], check=True)
    r = call("video_stabilize", {"path": str(shaky), "project": "t"})
    assert r.data["shake_after"] < r.data["shake_before"]


@pytest.mark.slow
def test_timeline_with_motion_title_and_music(home, media):
    pytest.importorskip("playwright")
    music = media["dir"] / "m.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=330:d=8", "-ar", "48000", str(music)], check=True)
    spec = {"size": "640x360", "fps": 30, "clips": [
        {"title": "Hello", "duration": 1.5},
        {"src": str(media["a"]), "transition": {"type": "dissolve", "duration": 0.4}}],
        "overlays": [{"type": "lower_third", "at": 2, "duration": 2, "name": "Test Name", "role": "Role"}],
        "audio": [{"src": str(music), "volume_db": -18, "duck": True, "loop": True}], "loudness": "youtube"}
    r = call("video_edit", {"timeline": spec, "project": "t", "name": "full"})
    assert abs(r.data["loudness"]["lufs"] + 14) < 1.5
    assert r.data.get("duck_regions")


@pytest.mark.slow
def test_reframe_track(home, media):
    r = call("video_reframe", {"path": str(media["a"]), "aspect": "9:16", "project": "t"})
    info = C.probe(r.files[0])
    assert (info["width"], info["height"]) == (1080, 1920)
