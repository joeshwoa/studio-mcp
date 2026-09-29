"""Speech → text with word timestamps (faster-whisper; mlx-whisper on Apple Silicon)."""
from __future__ import annotations

import os
import platform
import re
import tempfile
import time
from pathlib import Path

import numpy as np

from ...core import qc
from ...core.deps import IS_MAC, need
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C

# name → (approx download, note). "cached" is checked at run time.
MODELS = {
    "large-v3": ("3.1 GB", "best accuracy, esp. Arabic/Egyptian; slow on CPU (~4x realtime on 2 cores)"),
    "large-v3-turbo": ("1.6 GB", "≈large-v2 accuracy at ~3-4x the speed; weaker on dialect than large-v3"),
    "medium": ("1.5 GB", "middle ground"),
    "small": ("480 MB", "fast, noticeably worse on Arabic"),
    "base": ("150 MB", "fastest multilingual, rough on Arabic"),
    "base.en": ("150 MB", "fast English-only; fine for clean English speech"),
    "small.en": ("480 MB", "English-only, better than base.en"),
    "medium.en": ("1.5 GB", "English-only"),
    "distil-large-v3": ("1.5 GB", "English-focused distilled large-v3; fast"),
}
MLX_REPOS = {"large-v3": "mlx-community/whisper-large-v3-mlx", "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
             "medium": "mlx-community/whisper-medium-mlx", "small": "mlx-community/whisper-small-mlx",
             "base": "mlx-community/whisper-base-mlx", "base.en": "mlx-community/whisper-base.en-mlx"}

# Phrases Whisper is known to hallucinate on silence/music (from subtitle-laden training data).
HALLUCINATIONS = [
    "اشتركوا في القناة", "اشترك في القناة", "ترجمة نانسي قنقر", "شكرا للمشاهدة", "لا تنسوا الاشتراك",
    "thanks for watching", "thank you for watching", "subscribe to", "please subscribe", "subtitles by",
    "amara.org", "like and subscribe", "see you in the next video",
]

EGYPTIAN_PROMPT = "إزيك؟ عامل إيه؟ النهارده هنتكلم عن حاجات كتير، يعني إيه وإزاي وليه. تمام كده."

_MODEL_CACHE: dict = {}


def _cached(name: str) -> bool:
    try:
        from faster_whisper.utils import download_model
        download_model(name, local_files_only=True)
        return True
    except Exception:
        return False


def _pick(model: str, speed: str, language: str) -> str:
    if model and model != "auto":
        if model not in MODELS and "/" not in model and not Path(model).exists():
            raise ToolError(f"unknown model {model!r}", f"one of {sorted(MODELS)} (or a local CTranslate2 folder / HF repo id)")
        return model
    en = language in ("en", "english")
    order = (["base.en", "small.en", "large-v3-turbo", "small", "base"] if en else ["large-v3-turbo", "small", "base"]) \
        if speed == "fast" else ["large-v3", "large-v3-turbo", "medium", "small", "base"]
    if speed == "fast" and not en:
        order += ["large-v3"]  # nothing lighter cached → still work
    if en and speed != "fast":
        order += ["medium.en", "small.en", "base.en"]
    for m in order:
        if _cached(m):
            return m
    return order[0]


def _mlx_ok() -> bool:
    import importlib.util
    return IS_MAC and platform.machine() == "arm64" and importlib.util.find_spec("mlx_whisper") is not None


def _extract(src: Path, td: str) -> Path:
    wav = Path(td) / "speech16k.wav"
    a, _ = C.load(src, sr=16000, mono=True)
    peak = float(np.abs(a).max()) if a.size else 0.0
    if peak > 0.99:
        a = a * (0.99 / peak)
    C.save(wav, a, 16000)
    return wav


def _run_faster_whisper(wav: Path, model_name: str, lang: str | None, prompt: str, hotwords: str,
                        beam: int, vad: bool, allow_download: bool) -> tuple[list[dict], dict]:
    need("faster_whisper")
    from faster_whisper import WhisperModel
    if not _cached(model_name) and "/" not in model_name and not Path(model_name).exists():
        if not allow_download:
            size = MODELS.get(model_name, ("?", ""))[0]
            raise ToolError(f"Whisper model '{model_name}' is not downloaded yet ({size}).",
                            "ask the user, then call again with allow_download=true — or pass model='auto' to use a cached one")
    key = (model_name, "int8")
    if key not in _MODEL_CACHE:
        _MODEL_CACHE.clear()  # keep at most one model in RAM
        _MODEL_CACHE[key] = WhisperModel(model_name, device="auto", compute_type="int8",
                                         cpu_threads=max(1, (os.cpu_count() or 2)))
    m = _MODEL_CACHE[key]
    kw = dict(language=lang, beam_size=beam, word_timestamps=True, vad_filter=vad,
              condition_on_previous_text=False, initial_prompt=prompt or None)
    if vad:
        kw["vad_parameters"] = {"min_silence_duration_ms": 500, "speech_pad_ms": 300}
    if hotwords:
        kw["hotwords"] = hotwords
    segs, info = m.transcribe(str(wav), **kw)
    out = []
    for s in segs:
        out.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip(),
                    "avg_logprob": float(s.avg_logprob), "no_speech_prob": float(s.no_speech_prob),
                    "compression_ratio": float(s.compression_ratio), "temperature": float(s.temperature or 0),
                    "words": [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end),
                               "probability": float(w.probability)} for w in (s.words or [])]})
    return out, {"language": info.language, "language_probability": float(info.language_probability),
                 "duration": float(info.duration), "engine": "faster-whisper", "model": model_name}


def _run_mlx(wav: Path, model_name: str, lang: str | None, prompt: str) -> tuple[list[dict], dict]:
    import mlx_whisper  # type: ignore
    repo = MLX_REPOS.get(model_name, model_name)
    r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=repo, language=lang, word_timestamps=True,
                               condition_on_previous_text=False, initial_prompt=prompt or None)
    out = []
    for s in r.get("segments", []):
        out.append({"start": s["start"], "end": s["end"], "text": s["text"].strip(), "avg_logprob": s.get("avg_logprob", 0),
                    "no_speech_prob": s.get("no_speech_prob", 0), "compression_ratio": s.get("compression_ratio", 1),
                    "temperature": s.get("temperature", 0),
                    "words": [{"word": w["word"].strip(), "start": w["start"], "end": w["end"],
                               "probability": w.get("probability", 1.0)} for w in s.get("words", [])]})
    return out, {"language": r.get("language"), "language_probability": None, "engine": "mlx-whisper", "model": repo}


def _flag(segs: list[dict], low_word: float) -> None:
    seen = []
    for s in segs:
        f = []
        low = [w for w in s["words"] if w["probability"] < low_word]
        s["low_confidence_words"] = [w["word"] for w in low]
        if s["avg_logprob"] < -1.0:
            f.append("low_confidence")
        if s["no_speech_prob"] > 0.6 and s["avg_logprob"] < -0.6:
            f.append("maybe_not_speech")
        if s["compression_ratio"] > 2.4:
            f.append("repetitive_text")
        t = s["text"].lower()
        if any(h in t for h in HALLUCINATIONS):
            f.append("known_hallucination_phrase")
        if s["words"] and len(low) / len(s["words"]) > 0.4:
            f.append("many_uncertain_words")
        norm = re.sub(r"\W+", "", t)
        if norm and seen.count(norm) >= 2:
            f.append("repeated_segment")
        seen.append(norm)
        if s["words"] and (s["end"] - s["start"]) > 0 and len(s["words"]) / (s["end"] - s["start"]) > 7:
            f.append("implausibly_fast")
        s["flags"] = f


@tool("audio")
def audio_transcribe(path: str, language: str = "auto", model: str = "auto", speed: str = "accurate",
                     formats: list[str] = ["srt", "vtt", "txt", "json"], max_chars_per_line: int = 42,
                     max_lines: int = 2, dialect: str = "", prompt: str = "", hotwords: str = "",
                     vad: bool = True, engine: str = "auto", allow_download: bool = False,
                     project: str = "", out: str = "") -> Result:
    """Transcribe speech (audio OR video file) to text with word-level timestamps: SRT + VTT + TXT +
    JSON (segments, words, per-word probability). Use for captions, subtitles, transcripts, finding
    cut points. Returns files and data {srt, vtt, txt, json, language, words:[{word,start,end,probability}],
    confidence, flagged_segments}. Output is Whisper's text verbatim — NOTHING is guessed or 'repaired';
    uncertain words/segments are flagged for human review instead.

    language: 'auto' | 'ar' | 'en' | any Whisper code. model: 'auto' (best cached: large-v3) |
    large-v3 | large-v3-turbo | medium | small | base | base.en … ; speed='fast' prefers lighter models
    (base.en for English). dialect='egyptian' biases spelling toward Egyptian colloquial (prompt only;
    it never changes words that were not said). prompt: domain context; hotwords: names/terms to favour.
    max_chars_per_line 42 (use 40 for Arabic reels), max_lines 2. engine: auto | faster-whisper | mlx
    (mlx-whisper on Apple Silicon, much faster there). allow_download: permit fetching a missing model."""
    src = C.src_path(path)
    lang = None if language in ("", "auto") else language.lower()
    if lang in ("arabic", "egyptian"):
        lang = "ar"
    if lang == "english":
        lang = "en"
    chosen = _pick(model, speed, lang or "")
    ptxt = prompt
    if dialect.lower().startswith("egy"):
        ptxt = (EGYPTIAN_PROMPT + " " + prompt).strip()
        lang = lang or "ar"
    use_mlx = engine == "mlx" or (engine == "auto" and _mlx_ok())
    if engine == "mlx" and not _mlx_ok():
        raise ToolError("mlx-whisper needs an Apple Silicon Mac with `pip install mlx-whisper`", "use engine='faster-whisper'")
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        wav = _extract(src, td)
        dur = qc.probe(wav).get("duration", 0)
        if dur < 0.3:
            raise ToolError("audio is shorter than 0.3 s — nothing to transcribe")
        if use_mlx:
            segs, info = _run_mlx(wav, chosen, lang, ptxt)
        else:
            segs, info = _run_faster_whisper(wav, chosen, lang, ptxt, hotwords, 5, vad, allow_download)
    elapsed = time.time() - t0
    low_word = 0.45
    _flag(segs, low_word)
    words = [{"word": w["word"], "start": round(w["start"], 3), "end": round(w["end"], 3),
              "probability": round(w["probability"], 3)} for s in segs for w in s["words"] if w["word"]]
    all_p = [w["probability"] for w in words]
    conf = {
        "mean_word_probability": round(sum(all_p) / len(all_p), 3) if all_p else None,
        "low_confidence_words": sum(p < low_word for p in all_p),
        "low_confidence_pct": round(100 * sum(p < low_word for p in all_p) / len(all_p), 1) if all_p else None,
        "flagged_segments": sum(bool(s["flags"]) for s in segs),
    }
    lang_out = info.get("language") or lang or ""
    is_ar = lang_out == "ar"
    cues = C.words_to_cues(words, max_chars=max_chars_per_line, max_lines=max_lines) if words else \
        [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segs]
    stem = f"{src.stem}-transcript"
    base = C.out_path(project, out, stem, ".srt")
    files, data = [], {}
    fm = [f.lower().lstrip(".") for f in formats] or ["srt"]
    if "srt" not in fm:
        fm.append("srt")  # the video builder always needs it
    if "json" not in fm:
        fm.append("json")
    text_full = "\n".join(s["text"] for s in segs)
    for f in fm:
        p = base if f == "srt" else C.sibling(base, "." + f)
        if f == "srt":
            C.write_srt(cues, p)
        elif f == "vtt":
            C.write_vtt(cues, p)
        elif f == "txt":
            p.write_text(text_full + "\n", encoding="utf-8")
        elif f == "json":
            C.write_json({"source": str(src), "language": lang_out, "language_probability": info.get("language_probability"),
                          "engine": info.get("engine"), "model": info.get("model"), "duration": round(dur, 3),
                          "confidence": conf, "segments": [{**s, "start": round(s["start"], 3), "end": round(s["end"], 3)}
                                                           for s in segs], "words": words, "cues": cues}, p)
        else:
            continue
        files.append(str(p))
        data[f] = str(p)
    flagged = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"], "flags": s["flags"],
                "uncertain_words": s["low_confidence_words"]} for s in segs if s["flags"] or s["low_confidence_words"]]
    data.update(language=lang_out, language_probability=info.get("language_probability"), model=info.get("model"),
                engine=info.get("engine"), duration=round(dur, 2), processing_seconds=round(elapsed, 1),
                realtime_factor=round(elapsed / dur, 2) if dur else None, confidence=conf, cue_count=len(cues),
                flagged_segments=flagged[:50], words=words, text=text_full if len(text_full) < 4000 else text_full[:4000] + " …")
    res = Result(f"Transcribed {src.name} ({dur:.1f}s, language={lang_out}) with {info.get('engine')} {chosen}: "
                 f"{len(words)} words, {len(cues)} caption cues, mean word confidence "
                 f"{conf['mean_word_probability']}. Text is verbatim model output — not corrected.",
                 files=files, data=data)
    # preview: waveform with flagged regions marked
    try:
        pv = C.preview(src, C.sibling(base, ".png"), title=f"{src.name} — flagged segments marked in yellow",
                       marks=[(s["start"], s["end"], "") for s in segs if s["flags"]])
        res.previews.append(str(pv))
    except Exception:
        pass
    if info.get("language_probability") and info["language_probability"] < 0.7 and not lang:
        res.warnings.append(f"language detection unsure ({info['language_probability']:.2f}) — pass language='ar' or 'en'")
    if conf["low_confidence_pct"] and conf["low_confidence_pct"] > 10:
        res.warnings.append(f"{conf['low_confidence_pct']}% of words are low-confidence (<{low_word}) — review flagged_segments "
                            "against the audio; do NOT auto-correct by guessing")
    for s in flagged[:8]:
        if s["flags"]:
            res.warnings.append(f"{C.ts(s['start'])}–{C.ts(s['end'])} {','.join(s['flags'])}: “{s['text'][:80]}”")
    if not words:
        res.warnings.append("no speech detected (VAD removed everything?) — try vad=false")
    if is_ar:
        res.next_steps.append("Arabic: Whisper writes Egyptian words phonetically (e.g. 'إيزيك'); spelling may differ from "
                              "a human transcript while the words are right. dialect='egyptian' nudges spelling.")
    if chosen in ("base.en", "small.en") and is_ar is False and lang_out != "en":
        res.warnings.append("English-only model used on non-English audio")
    if not use_mlx and IS_MAC and platform.machine() == "arm64":
        res.next_steps.append("On this Mac, `pip install mlx-whisper` makes transcription several times faster (engine='mlx').")
    res.next_steps.append("Burn captions: video dept (uses data.srt / data.json words).")
    return res


@tool("audio")
def audio_transcribe_models() -> Result:
    """List Whisper models for audio_transcribe: which are already downloaded (cached), their size,
    and the speed/accuracy trade-off, plus whether the fast Apple-Silicon engine (mlx-whisper) is
    available. Use before choosing `model=` or before asking the user to allow a download."""
    rows = [{"model": k, "cached": _cached(k), "download": v[0], "note": v[1]} for k, v in MODELS.items()]
    lines = [f"  {'cached ' if r['cached'] else 'missing'}  {r['model']:16} {r['download']:>7}  {r['note']}" for r in rows]
    mlx = _mlx_ok()
    return Result("Whisper models (faster-whisper, int8 on CPU):\n" + "\n".join(lines) +
                  f"\n\nmlx-whisper (Apple Silicon): {'available' if mlx else 'not installed / not Apple Silicon'}",
                  data={"models": rows, "mlx_whisper": mlx, "default": _pick('auto', 'accurate', '')})
