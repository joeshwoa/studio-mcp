"""Pro video tools: scene detection, takes ranking, beats, text-based editing, interchange, colour, QC,
auto-edit. Media is synthesised with ffmpeg lavfi; @slow tests use Whisper / TTS / stock (network)."""
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
from studio.depts.video import pro_auto as A  # noqa: E402
from studio.depts.video import pro_beats as B  # noqa: E402
from studio.depts.video import pro_color as PC  # noqa: E402
from studio.depts.video import pro_qc as Q  # noqa: E402
from studio.depts.video import pro_text_edit as T  # noqa: E402
from studio.depts.video import timeline as TL  # noqa: E402


@pytest.fixture(scope="session")
def home(tmp_path_factory):
    h = tmp_path_factory.mktemp("prohome")
    mp = pytest.MonkeyPatch()
    mp.setenv("STUDIO_HOME", str(h))
    yield h
    mp.undo()


def _ff(args, dest):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(dest)], check=True, timeout=180)
    return dest


@pytest.fixture(scope="session")
def media(home):
    d = home / "media"
    d.mkdir()
    m = {}
    m["shots3"] = _ff(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2", "-f", "lavfi", "-i", "smptehdbars=s=640x360:r=30:d=2",
                       "-f", "lavfi", "-i", "mandelbrot=s=640x360:r=30,trim=duration=2", "-f", "lavfi", "-i", "sine=f=440:d=6",
                       "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]", "-map", "[v]", "-map", "3:a",
                       "-c:v", "libx264", "-c:a", "aac", "-shortest"], d / "shots3.mp4")
    m["blurry"] = _ff(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2", "-vf", "gblur=sigma=6,format=yuv420p",
                       "-c:v", "libx264"], d / "blurry.mp4")
    # 120 BPM click track starting at 0.3 s, accented every 4th beat
    m["click"] = _ff(["-f", "lavfi", "-i", "aevalsrc='(lt(mod(t-0.3,0.5),0.03)*gte(t,0.3))*sin(2*PI*(if(lt(mod(t-0.3,2),0.1),80,1500))*t)"
                      "*exp(-mod(t-0.3,0.5)*60)*0.8':s=44100:d=12"], d / "click120.wav")
    m["cast"] = _ff(["-f", "lavfi", "-i", "smptehdbars=s=640x360:r=30:d=2", "-vf",
                     "colorbalance=rs=0.2:bs=-0.2:rm=0.15:bm=-0.15,format=yuv420p", "-c:v", "libx264"], d / "cast.mp4")
    m["bars"] = _ff(["-f", "lavfi", "-i", "smptehdbars=s=640x360:r=30:d=2", "-vf", "format=yuv420p", "-c:v", "libx264"], d / "bars.mp4")
    m["bad"] = _ff(["-f", "lavfi", "-i", "testsrc2=s=640x270:r=30:d=4", "-f", "lavfi", "-i", "color=white:s=640x270:r=30:d=1",
                    "-f", "lavfi", "-i", "aevalsrc='1.4*sin(2*PI*330*t)':s=48000:d=5",
                    "-filter_complex", "[1:v]geq=lum='if(lt(mod(N,6),3),235,16)':cb=128:cr=128[s];[0:v][s]concat=n=2:v=1:a=0,"
                    "pad=640:360:0:45,format=yuv420p[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", "-shortest"],
                   d / "bad.mp4")
    return m


# ─────────────────────────── pure logic ───────────────────────────

def test_filler_detection_english():
    def W(txt):
        return [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(txt.split())]
    w = W("I like this, like, a lot. So um I mean it works.")
    f = T.find_fillers(w, "en")
    assert 7 in f and "hesitation" in f[7]          # um
    assert 3 in f                                    # standalone "like,"
    assert 1 not in f                                # "I like this" keeps its meaning
    assert T.find_fillers(w, "en", "safe") == {7: f[7]}


def test_filler_detection_arabic():
    def W(txt):
        return [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(txt.split())]
    w = W("رايح الشغل امممم وبعدين يعني كده. آه، بص يعني إيه الموضوع")
    f = T.find_fillers(w, "ar")
    assert 2 in f                                    # اممم hesitation
    assert 4 not in f                                # "يعني كده" mid-sentence stays
    assert 6 in f                                    # standalone "آه،"
    assert 8 not in f and 9 not in f                 # "يعني إيه الموضوع" is a real question
    assert T.norm("Ummmm,") == "um" and T.norm("يعنييي") == "يعني"


def test_resolve_ranges_text_and_indices():
    words = [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate("the quick brown fox jumps".split())]
    assert T._resolve_ranges(["brown fox"], words, "remove") == [(2, 3)]
    assert T._resolve_ranges([[0, 1]], words, "remove") == [(0, 1)]
    assert T._resolve_ranges([{"start": 3.0, "end": 4.6}], words, "remove") == [(3, 4)]


def test_plan_slots_pacing():
    words, t = [], 0.0
    for i in range(30):
        words.append({"word": "word." if i % 6 == 5 else "word", "start": t, "end": t + 0.3})
        t += 0.35
    slots = A.plan_slots(words, t + 1, (1.4, 2.8))
    assert slots[0]["start"] == 0 and abs(slots[-1]["end"] - (t + 1)) < 1e-6
    assert all(b["start"] == a["end"] for a, b in zip(slots, slots[1:]))
    assert all(s["end"] - s["start"] <= 2.8 * 1.4 for s in slots[:-1])


def test_keywords():
    assert "coffee" in A.keywords("Every great morning starts with coffee.")


def test_cube_roundtrip(tmp_path):
    g = PC.lut_grid(5)
    assert np.abs(PC.lab_to_rgb(PC.rgb_to_lab(g)) - g).max() < 1e-6
    p = PC.write_cube(g, tmp_path / "id.cube", "identity", 5)
    n, tab = PC.read_cube(p)
    img = np.random.default_rng(0).random((8, 8, 3))
    assert n == 5 and np.abs(PC.apply_lut_np(img, n, tab) - img).max() < 1e-4


def test_flash_counter():
    fps = 30
    sig = np.array([0.9 if (k // 3) % 2 else 0.05 for k in range(60)])   # 5 Hz full-field strobe
    worst, _, _ = Q.flashes(sig, fps)
    assert worst >= 4
    calm = np.full(60, 0.4)
    assert Q.flashes(calm, fps)[0] == 0


def test_timeline_audio_crossfade(home, media):
    spec = {"clips": [{"src": str(media["shots3"]), "in": 0, "out": 2},
                      {"src": str(media["shots3"]), "in": 3, "out": 5, "audio_crossfade": 0.015}]}
    tl = TL.normalize(spec, home / "assets")
    assert tl.clips[1].ax == 0.015
    _, graph, _ = TL.build_dialog_graph(tl)
    assert "adelay=1985" in graph            # second clip's sound starts 15 ms before its picture
    assert "afade=t=in:st=0:d=0.015" in graph


# ─────────────────────────── tools (fast) ───────────────────────────

def test_scene_detect(home, media):
    r = call("video_scene_detect", {"path": str(media["shots3"]), "project": "t"})
    shots = r.data["shots"]
    assert len(shots) == 3
    assert abs(shots[1]["start"] - 2.0) < 0.07 and abs(shots[2]["start"] - 4.0) < 0.07
    assert all(Path(p).exists() for p in r.previews)
    assert all("sharpness" in s and "audio_db" in s for s in shots)


def test_select_takes_prefers_sharp(home, media):
    r = call("video_select_takes", {"paths": [str(media["blurry"]), str(media["bars"])], "per_shot": False, "want": "sharp",
                                    "count": 2, "project": "t"})
    assert Path(r.data["picks"][0]["src"]).name == "bars.mp4"
    assert Path(r.previews[0]).exists()


def test_beats_click_120(home, media):
    r = call("video_beats", {"path": str(media["click"]), "project": "t"})
    assert abs(r.data["tempo"] - 120) < 1.5
    b = np.array(r.data["beats"])
    assert abs(b[0] - 0.3) < 0.05 and abs(np.median(np.diff(b)) - 0.5) < 0.03
    assert Path(r.previews[0]).exists()


def test_cut_to_beats_spec(home, media):
    spec = {"clips": [str(media["shots3"])] * 3, "cut_to_beats": {"music": str(media["click"]), "every": 2, "align": "beat"}}
    out, info = B.apply_cut_to_beats(spec)
    assert [round(c["duration"], 2) for c in out["clips"]] == [1.0, 1.0, 1.0]
    assert out["audio"][-1]["in"] == pytest.approx(info["music_in"], abs=1e-3)
    assert "cut_to_beats" not in out


def test_interchange(home, media):
    tl = {"size": "640x360", "fps": 30, "clips": [{"src": str(media["shots3"]), "in": 0, "out": 2},
                                                  {"src": str(media["bars"]), "in": 0, "out": 2, "transition": {"type": "dissolve", "duration": 0.5}},
                                                  {"src": str(media["shots3"]), "in": 2, "out": 4, "speed": 2}],
          "overlays": [{"type": "broll", "src": str(media["bars"]), "at": 0.5, "duration": 1}],
          "audio": [{"src": str(media["click"]), "at": 0.2, "duration": 3, "volume_db": -10}]}
    r = call("video_export_interchange", {"timeline": tl, "project": "t", "name": "x"})
    assert all(not v for k, v in r.data["checks"].items() if isinstance(v, list)), r.data["checks"]
    fcp = next(f for f in r.files if f.endswith(".fcpxml"))
    root = ET.parse(fcp).getroot()
    assert root.tag == "fcpxml" and root.find(".//transition") is not None and root.find(".//timeMap") is not None
    otio = json.loads(Path(next(f for f in r.files if f.endswith(".otio"))).read_text())
    v1 = otio["tracks"]["children"][0]["children"]
    assert [c["OTIO_SCHEMA"] for c in v1] == ["Clip.2", "Transition.1", "Clip.2", "Clip.2"]
    tot = sum(c["source_range"]["duration"]["value"] for c in v1 if c["OTIO_SCHEMA"] == "Clip.2") / 30
    assert tot == pytest.approx(r.data["duration"], abs=0.05)
    edl = Path(next(f for f in r.files if f.endswith("x.edl"))).read_text()
    assert "D    015" in edl and "M2" in edl


def test_color_match_and_auto(home, media):
    r = call("video_color_match", {"path": str(media["cast"]), "reference": str(media["bars"]), "samples": 3, "project": "t"})
    assert r.data["delta_after"] < r.data["delta_before"] * 0.5
    assert Path(r.data["cube"]).read_text().count("\n") > 33 ** 3
    r2 = call("video_auto_color", {"path": str(media["cast"]), "samples": 3, "project": "t", "apply": False})
    g = r2.data["wb_gains"]
    assert g[2] > g[0]                       # the warm cast is cooled
    assert Path(r2.previews[0]).exists()


def test_qc_catches_faults(home, media):
    r = call("video_qc", {"path": str(media["bad"]), "platform": "reels", "project": "t"})
    st = {c["check"]: c["status"] for c in r.data["checks"]}
    assert r.data["verdict"] == "FAIL"
    assert st["Flash / strobe risk"] == "FAIL"
    assert st["Audio clipping"] == "FAIL"
    assert st["Resolution"] == "FAIL"
    assert st["Letterbox / pillarbox"] == "WARN"
    assert Path(r.previews[0]).exists()
    ok = call("video_qc", {"path": str(media["bars"]), "project": "t"})
    assert ok.data["checks"][0]["status"] in ("INFO", "PASS")


# ─────────────────────────── slow: whisper / tts / stock ───────────────────────────

@pytest.mark.slow
def test_transcript_edit_removes_fillers(home, media):
    pytest.importorskip("faster_whisper")
    try:
        vo = call("audio_voiceover", {"text": "So today I want to show you, um, how this works. Uh, the first step is easy.",
                                      "voice": "en-US-AndrewMultilingualNeural", "project": "t"})
    except Exception as e:  # offline
        pytest.skip(f"no TTS: {e}")
    wav = next(f for f in vo.files if f.endswith(".wav"))
    mp4 = _ff(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30", "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-shortest"], home / "media" / "talk.mp4")
    tr = call("video_transcript", {"path": str(mp4), "language": "en", "speed": "fast", "project": "t"})
    assert tr.data["fillers"], "verbatim prompt should keep um/uh"
    r = call("video_transcript_edit", {"path": str(mp4), "transcript": tr.data["transcript_json"], "project": "t"})
    assert any("hesitation" in w["why"] for w in r.data["removed_words"])
    assert r.data["output"]["duration"] < C_dur(mp4) - 0.5
    assert not r.data["verify"]["leaked"]


def C_dur(p):
    from studio.depts.video import _common as C
    return C.probe(p)["duration"]


@pytest.mark.slow
def test_auto_edit_plan_only_footage(home, media):
    brief = {"footage": [str(media["shots3"]), str(media["bars"])], "style": "youtube", "duration": 6, "music": "none",
             "broll": "footage", "captions": False}
    r = call("video_auto_edit", {"brief": brief, "project": "t", "render": False})
    assert r.data["shots"] and all(s.get("kind") == "own" for s in r.data["shots"])
    spec = json.loads(Path(r.data["timeline_json"]).read_text())
    assert spec["size"] == "1920x1080" and spec["clips"]
