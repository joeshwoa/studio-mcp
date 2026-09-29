"""Audio department tests. Fast ones use synthetic signals; @slow ones hit the network (edge-tts,
Openverse) or run real models (Whisper, Demucs)."""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("soundfile")
pytest.importorskip("pedalboard")
if not shutil.which("ffmpeg"):
    pytest.skip("ffmpeg missing", allow_module_level=True)

from studio.core import qc  # noqa: E402
from studio.core.registry import call  # noqa: E402
from studio.core.result import ToolError  # noqa: E402
from studio.depts.audio import _common as C  # noqa: E402

SR = 48000


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    return tmp_path


def speechlike(sec=6.0, seed=0, gaps=True) -> np.ndarray:
    """Voiced bursts (glottal-ish harmonics + moving formant) with pauses — a speech stand-in."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(sec * SR)) / SR
    f0 = 120 + 20 * np.sin(2 * np.pi * 0.7 * t)
    ph = 2 * np.pi * np.cumsum(f0) / SR
    x = sum(np.sin(k * ph) / k for k in range(1, 30)) * 0.3
    form = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t)
    x = x * (0.4 + 0.6 * form)
    if gaps:
        env = np.zeros_like(t)
        pos = 0.2
        while pos < sec - 0.5:
            L = rng.uniform(0.6, 1.4)
            m = (t >= pos) & (t < pos + L)
            env[m] = np.sin(np.pi * (t[m] - pos) / L) ** 0.3
            pos += L + rng.uniform(0.3, 1.2)
        x = x * env
    return (x * 0.5).astype(np.float32)


def write(p: Path, x, sr=SR) -> str:
    C.save(p, x, sr)
    return str(p)


# ───────────────────────────── helpers ─────────────────────────────

def test_wer_arabic_normalisation():
    assert C.wer("إزيك يا جماعة", "ازيك يا جماعه")["wer"] == 0.0
    assert C.wer("one two three four", "one too three four")["errors"] == 1


def test_cues_respect_limits():
    words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in
             enumerate(("النهارده هنتكلم عن حاجة مهمة جداً، إزاي نستخدم الذكاء الاصطناعي في شغلنا كل يوم. "
                        "الموضوع مش صعب زي ما انتوا فاكرين.").split())]
    cues = C.words_to_cues(words, max_chars=40, max_lines=2)
    assert len(cues) >= 2
    for c in cues:
        assert all(len(line) <= 40 for line in c["text"].split("\n"))
        assert c["text"].count("\n") <= 1
        assert c["end"] - c["start"] <= 6.5
    for a, b in zip(cues, cues[1:]):
        assert a["end"] <= b["start"]
    srt = C.write_srt(cues, Path(os.environ["STUDIO_HOME"]).parent / "x.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 -->" in srt and "الذكاء" in srt


def test_normalize_and_limiter_hit_targets():
    x = np.stack([speechlike(8), speechlike(8, 1)], 1) * 3.0     # way too hot
    y, info = C.normalize_loudness(x, SR, -16, -1.0)
    assert abs(info["lufs_after"] - -16) < 0.6
    assert C.true_peak_db(y, SR) <= -0.9


# ───────────────────────────── processing tools ─────────────────────────────

def test_clean_reduces_noise(home):
    rng = np.random.default_rng(0)
    x = speechlike(8) + 0.02 * rng.standard_normal(8 * SR).astype(np.float32)
    x += 0.02 * np.sin(2 * np.pi * 50 * np.arange(8 * SR) / SR).astype(np.float32)  # mains hum
    src = write(home / "noisy.wav", x)
    r = call("audio_clean", {"path": src, "preset": "podcast"})
    assert r.data["noise_floor_after_same_gain"] < r.data["noise_floor_before"] - 10
    m = r.data["metrics"]
    assert abs(m["lufs"] - -16) < 1.0 and m["true_peak"] <= -0.8
    assert r.previews and Path(r.previews[0]).exists()


def test_master_targets(home):
    src = write(home / "v.wav", speechlike(10))
    for tgt, lufs in (("streaming", -14), ("broadcast", -23), ("-18", -18)):
        r = call("audio_master", {"path": src, "target": tgt})
        assert abs(r.data["metrics"]["lufs"] - lufs) < 0.8, (tgt, r.data["metrics"])
        assert r.data["metrics"]["true_peak"] <= -0.8


def test_mix_ducks_music(home):
    v = speechlike(10)
    t = np.arange(12 * SR) / SR
    music = (0.3 * np.sin(2 * np.pi * 60 * t)).astype(np.float32)   # below the voice's lowest harmonic
    vp, mp = write(home / "voice.wav", v), write(home / "music.wav", music)
    r = call("audio_mix", {"voice": vp, "music": mp, "intro": 2.0, "outro": 1.0, "duck_db": 12,
                           "fade_in": 0.2})
    assert r.data["duck_regions"]
    y, sr = C.load(r.files[0], sr=SR, mono=True)
    # music level at 60 Hz: under speech vs in the intro
    def band(a):
        s = np.abs(np.fft.rfft(a[:, 0] * np.hanning(len(a))))
        f = np.fft.rfftfreq(len(a), 1 / sr)
        return s[(f > 55) & (f < 65)].max()
    s0, e0 = r.data["duck_regions"][0]
    mid = (s0 + e0) / 2
    under = band(y[int((mid - 0.1) * sr):int((mid + 0.1) * sr)])
    intro = band(y[int(1.0 * sr):int(1.2 * sr)])
    assert 20 * np.log10(intro / under) > 6


def test_trim_silence(home):
    x = np.concatenate([np.zeros(SR), speechlike(2, gaps=False), np.zeros(3 * SR), speechlike(2, gaps=False), np.zeros(SR)])
    r = call("audio_trim_silence", {"path": write(home / "p.wav", x), "max_pause": 0.4})
    assert 4.0 < r.data["metrics"]["duration"] < 5.5
    assert len(r.data["cuts"]) == 1


def test_convert_join_analyze(home):
    a = write(home / "a.wav", speechlike(3, gaps=False))
    r = call("audio_convert", {"path": a, "format": "mp3", "sample_rate": 44100})
    assert r.files[0].endswith(".mp3") and qc.probe(r.files[0])["sample_rate"] == 44100
    j = call("audio_join", {"paths": [a, r.files[0]], "crossfade": 0.5})
    assert abs(j.data["metrics"]["duration"] - 5.5) < 0.2 and j.data["offsets"][1] == pytest.approx(2.5, abs=0.05)
    an = call("audio_analyze", {"path": a, "target": "podcast"})
    assert "spectral_balance_pct" in an.data and an.previews


def test_never_overwrites(home):
    a = write(home / "a.wav", speechlike(2, gaps=False))
    f1 = call("audio_convert", {"path": a, "format": "wav", "project": "p"}).files[0]
    f2 = call("audio_convert", {"path": a, "format": "wav", "project": "p"}).files[0]
    assert f1 != f2


def test_missing_file_is_a_toolerror():
    with pytest.raises(ToolError):
        call("audio_clean", {"path": "/nope/missing.wav"})


# ───────────────────────────── generators ─────────────────────────────

@pytest.mark.parametrize("kind", ["whoosh", "riser", "downlifter", "impact", "pop", "click", "ding", "success",
                                  "error", "glitch", "typing", "tick", "shutter"])
def test_sfx_kinds(kind):
    r = call("audio_sfx", {"kind": kind})
    m = r.data.get("duration")
    mm, w = C.measure(r.files[0])
    assert mm["max_db"] > -20 and mm["max_db"] <= -0.5, mm
    assert not any("clipped" in x for x in w)


@pytest.mark.parametrize("genre,extra", [("lofi", {}), ("cinematic", {}), ("corporate", {}),
                                         ("oriental", {"maqam": "hijaz"}), ("oriental", {"maqam": "bayati", "rhythm": "saidi"})])
def test_music_genres(genre, extra):
    mido = pytest.importorskip("mido")
    r = call("audio_music", {"genre": genre, "duration": 20, "seed": 5, **extra})
    m = r.data["metrics"]
    assert abs(m["lufs"] - -14) < 1.0 and m["true_peak"] <= -0.8
    assert 14 < m["duration"] < 34
    assert m["silence_pct"] < 20
    mf = mido.MidiFile(r.data["midi"])
    names = [t.name for t in mf.tracks]
    assert "drums" in names
    if extra.get("maqam") == "bayati":   # quarter tones → pitch bends
        bends = [msg.pitch for t in mf.tracks for msg in t if msg.type == "pitchwheel" and msg.pitch]
        assert bends and all(b == 2048 for b in bends)


def test_music_is_deterministic_and_fit():
    a = call("audio_music", {"genre": "corporate", "duration": 16, "seed": 9, "fit": True})
    b = call("audio_music", {"genre": "corporate", "duration": 16, "seed": 9, "fit": True})
    assert abs(a.data["metrics"]["duration"] - 16) < 0.1
    x, _ = C.load(a.files[0], sr=None)
    y, _ = C.load(b.files[0], sr=None)
    assert np.allclose(x, y, atol=1e-4)


def test_ks_pitch_is_exact():
    from studio.depts.audio import _synth as S
    x = S.pluck_ks(81, 1.0)[2000:2000 + 32768]      # A5 = 880 Hz
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / S.SR)
    peak = f[np.argmax(sp * (f > 500))]
    assert abs(1200 * np.log2(peak / 880)) < 5


def test_stems_center_fallback(home):
    t = np.arange(6 * SR) / SR
    voice = speechlike(6)
    left_inst = 0.2 * np.sin(2 * np.pi * 440 * t)
    st = np.stack([voice + left_inst, voice - left_inst * 0.2], 1).astype(np.float32)
    r = call("audio_separate_stems", {"path": write(home / "song.wav", st), "method": "center"})
    assert set(r.data["stems"]) == {"vocals", "no_vocals"}
    assert r.warnings


# ───────────────────────────── Audacity bridge (fake pipe) ─────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFOs")
def test_audacity_bridge_with_fake_pipe(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDACITY_PIPE_DIR", str(tmp_path))
    from studio.depts.audio import audacity as A
    to_p, from_p = A.pipe_paths()
    r = call("audio_audacity", {"action": "status"})
    assert r.data["reachable"] is False and "mod-script-pipe" in " ".join(r.next_steps) + r.summary
    os.mkfifo(to_p)
    os.mkfifo(from_p)
    got = []

    def server():
        with open(to_p) as fin, open(from_p, "w") as fout:
            for line in fin:
                got.append(line.strip())
                fout.write(f"reply to {line.strip()}\nBatchCommand finished: OK\n\n")
                fout.flush()
    th = threading.Thread(target=server, daemon=True)
    th.start()
    r = call("audio_audacity", {"action": "run", "commands": ["SelectAll:", "Normalize: PeakLevel=-1"]})
    assert r.ok and got == ["SelectAll:", "Normalize: PeakLevel=-1"]


# ───────────────────────────── slow: network + real models ─────────────────────────────

@pytest.mark.slow
def test_voiceover_egyptian_edge():
    pytest.importorskip("edge_tts")
    try:
        r = call("audio_voiceover", {"text": "إزيكم يا جماعة! النهارده هنتكلم عن الذكاء الاصطناعي.", "voice": "egyptian-female"})
    except ToolError as e:
        pytest.skip(f"edge-tts unreachable: {e}")
    assert r.data["engine"] == "edge-tts" and r.data["words"]
    assert abs(r.data["metrics"]["lufs"] - -16) < 1
    assert Path(r.data["srt"]).read_text(encoding="utf-8").count("-->") >= 1


@pytest.mark.slow
def test_roundtrip_english_wer():
    pytest.importorskip("edge_tts")
    pytest.importorskip("faster_whisper")
    from studio.depts.audio.transcribe import _cached
    if not _cached("base.en"):
        pytest.skip("base.en not downloaded")
    text = "Today we are going to build a complete video from scratch using only free tools."
    try:
        vo = call("audio_voiceover", {"text": text, "voice": "english-male"})
    except ToolError as e:
        pytest.skip(f"edge-tts unreachable: {e}")
    tr = call("audio_transcribe", {"path": vo.data["audio"], "model": "base.en", "language": "en"})
    assert tr.data["srt"] and Path(tr.data["srt"]).exists() and tr.data["json"]
    assert C.wer(text, tr.data["text"])["wer"] < 0.1
    assert tr.data["words"][0]["start"] < 1.0


@pytest.mark.slow
def test_library_openverse():
    try:
        r = call("audio_library_search", {"query": "rain", "kind": "sfx", "limit": 3, "source": "openverse"})
    except ToolError as e:
        pytest.skip(f"offline: {e}")
    assert r.data["results"] and all(x["license"] for x in r.data["results"])


@pytest.mark.slow
def test_demucs_separates_voice(home):
    pytest.importorskip("demucs")
    t = np.arange(8 * SR) / SR
    voice = speechlike(8)
    music = 0.15 * np.sin(2 * np.pi * 110 * t) * (np.sin(2 * np.pi * 2 * t) > 0)
    st = np.stack([voice + music, voice + music], 1).astype(np.float32)
    r = call("audio_separate_stems", {"path": write(home / "mix.wav", st), "method": "demucs"})
    assert set(r.data["stems"]) == {"vocals", "no_vocals"}
