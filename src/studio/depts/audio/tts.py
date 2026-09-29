"""Voiceover: edge-tts (free Microsoft neural voices, online) with Piper as the offline fallback."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np

from ...config import cache_dir, models_dir
from ...core.deps import need
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C

ALIASES = {
    "egyptian-female": "ar-EG-SalmaNeural", "egyptian-male": "ar-EG-ShakirNeural",
    "saudi-female": "ar-SA-ZariyahNeural", "saudi-male": "ar-SA-HamedNeural",
    "emirati-female": "ar-AE-FatimaNeural", "emirati-male": "ar-AE-HamdanNeural",
    "kuwaiti-female": "ar-KW-NouraNeural", "kuwaiti-male": "ar-KW-FahedNeural",
    "qatari-female": "ar-QA-AmalNeural", "qatari-male": "ar-QA-MoazNeural",
    "english-female": "en-US-AvaMultilingualNeural", "english-male": "en-US-AndrewMultilingualNeural",
    "us-female": "en-US-JennyNeural", "us-male": "en-US-GuyNeural",
    "british-female": "en-GB-SoniaNeural", "british-male": "en-GB-RyanNeural",
    "narrator-male": "en-US-ChristopherNeural", "narrator-female": "en-US-AriaNeural",
}
PIPER_DEFAULTS = {"ar": "ar_JO-kareem-medium", "en": "en_US-lessac-medium"}
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


# ───────────────────────────── edge-tts ─────────────────────────────

def _edge():
    need("edge_tts")
    import edge_tts
    import edge_tts.communicate as ec
    # Behind TLS-inspecting proxies edge-tts only trusts certifi; add the system/proxy bundle if set.
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        f = os.environ.get(var)
        if f and Path(f).exists() and not getattr(ec, "_studio_ca", False):
            try:
                ec._SSL_CTX.load_verify_locations(f)
                import edge_tts.voices as ev
                ev._SSL_CTX.load_verify_locations(f)
                ec._studio_ca = True
            except Exception:
                pass
    return edge_tts


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:  # called from inside an event loop (MCP server)
        return ex.submit(asyncio.run, coro).result()


_VOICES: list[dict] | None = None


def edge_voices() -> list[dict]:
    global _VOICES
    if _VOICES is None:
        et = _edge()
        cache = cache_dir() / "edge_voices.json"
        try:
            _VOICES = _run(et.list_voices())
            if cache:
                cache.write_text(json.dumps(_VOICES))
        except Exception as e:
            if cache and cache.exists():
                _VOICES = json.loads(cache.read_text())
            else:
                raise ToolError(f"could not reach the Edge voice service: {e}", "check the internet connection, or use engine='piper'")
    return _VOICES


def _resolve_voice(voice: str, text: str) -> str:
    v = (voice or "auto").strip()
    if v == "auto":
        return "ar-EG-SalmaNeural" if C.is_arabic(text) else "en-US-AvaMultilingualNeural"
    return ALIASES.get(v.lower(), v)


def _edge_chunk(text: str, voice: str, rate: str, pitch: str, volume: str, tries: int = 3) -> tuple[bytes, list[dict]]:
    et = _edge()
    last = None
    for k in range(tries):
        try:
            async def go():
                c = et.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume, boundary="WordBoundary")
                audio, marks = bytearray(), []
                async for ch in c.stream():
                    if ch["type"] == "audio":
                        audio += ch["data"]
                    elif ch["type"] == "WordBoundary":
                        marks.append({"word": ch["text"], "start": ch["offset"] / 1e7,
                                      "end": (ch["offset"] + ch["duration"]) / 1e7})
                return bytes(audio), marks
            audio, marks = _run(go())
            if not audio:
                raise RuntimeError("no audio returned")
            return audio, marks
        except Exception as e:  # transient websocket errors are common; back off
            last = e
            time.sleep(1.5 * (k + 1))
    msg = str(last)
    if "voice" in msg.lower() or "NoAudioReceived" in type(last).__name__:
        raise ToolError(f"edge-tts returned no audio for voice {voice!r}: {msg[:200]}",
                        "check the voice name with audio_voices (e.g. ar-EG-SalmaNeural)")
    raise ToolError(f"edge-tts failed after {tries} tries: {msg[:300]}", "no internet? use engine='piper' (offline)")


def _decode_mp3(b: bytes, sr: int) -> np.ndarray:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.mp3"
        p.write_bytes(b)
        a, _ = C.load(p, sr=sr, mono=True)
    return a[:, 0]


def _punctuate(marks: list[dict], chunk: str) -> list[dict]:
    """Edge word marks drop punctuation; re-attach the script's own tokens (so captions keep ،.؟!)."""
    toks = chunk.split()
    j = 0
    out = []
    for m in marks:
        key = re.sub(r"\W+", "", m["word"]).lower()
        hit = None
        for k in range(j, min(len(toks), j + 4)):
            if key and key in re.sub(r"\W+", "", toks[k]).lower():
                hit = k
                break
        if hit is None:
            out.append(dict(m))
            continue
        word = toks[hit]
        # tokens skipped between matches (numbers, symbols) ride along with the previous word
        if out and hit > j:
            out[-1]["word"] += " " + " ".join(toks[j:hit])
        out.append({**m, "word": word})
        j = hit + 1
    if out and j < len(toks):
        out[-1]["word"] += " " + " ".join(toks[j:])
    return out


def _chunks(text: str, max_chars: int = 900) -> list[str]:
    """Paragraph/sentence chunks ≤ max_chars (keeps prosody natural, isolates network failures)."""
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        cur = ""
        for s in C.split_sentences(para) or [para]:
            if len(cur) + len(s) + 1 > max_chars and cur:
                out.append(cur)
                cur = s
            else:
                cur = (cur + " " + s).strip()
        if cur:
            out.append(cur + "\n")  # marker: paragraph end
    return out


def _trim_edges(x: np.ndarray, sr: int, thr_db: float = -50) -> tuple[np.ndarray, int]:
    e = C.rms_env(x, sr, 0.01)
    idx = np.where(e > C.undb(thr_db))[0]
    if not len(idx):
        return x, 0
    s = max(0, idx[0] - int(0.03 * sr))
    t = min(len(x), idx[-1] + int(0.08 * sr))
    return x[s:t], s


# ───────────────────────────── piper ─────────────────────────────

def piper_catalog() -> dict:
    f = models_dir() / "piper" / "voices.json"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(f"{PIPER_BASE}/voices.json", timeout=30) as r:
                f.write_bytes(r.read())
        except Exception as e:
            raise ToolError(f"could not fetch the Piper voice list: {e}", "needs internet once; voices then work offline")
    return json.loads(f.read_text())


def piper_voice_path(name: str, allow_download: bool) -> Path:
    d = models_dir() / "piper"
    onnx = d / f"{name}.onnx"
    if onnx.exists() and (d / f"{name}.onnx.json").exists():
        return onnx
    cat = piper_catalog()
    if name not in cat:
        raise ToolError(f"unknown Piper voice {name!r}", "see audio_voices(engine='piper')")
    if not allow_download:
        sz = sum(v.get("size_bytes", 0) for k, v in cat[name]["files"].items() if k.endswith(".onnx")) / 1e6
        raise ToolError(f"Piper voice {name} is not downloaded (~{sz:.0f} MB).",
                        "call again with allow_download=true (after the user agrees)")
    for rel in cat[name]["files"]:
        if rel.endswith((".onnx", ".onnx.json")):
            dest = d / Path(rel).name
            with urllib.request.urlopen(f"{PIPER_BASE}/{rel}", timeout=300) as r:
                dest.write_bytes(r.read())
    return onnx


_PIPER: dict = {}


def _piper_sentences(text: str, voice_name: str, speed: float, allow_download: bool) -> tuple[list[tuple[str, np.ndarray]], int]:
    need("piper")
    from piper import PiperVoice, SynthesisConfig
    onnx = piper_voice_path(voice_name, allow_download)
    if voice_name not in _PIPER:
        _PIPER.clear()
        _PIPER[voice_name] = PiperVoice.load(str(onnx))
    v = _PIPER[voice_name]
    cfg = SynthesisConfig(length_scale=1.0 / max(0.5, min(2.0, speed)))
    out = []
    for s in C.split_sentences(text) or [text]:
        chunks = list(v.synthesize(s, syn_config=cfg))
        if chunks:
            out.append((s, np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)))
    return out, v.config.sample_rate


def _pct(s: str) -> float:
    m = re.match(r"^([+-]?\d+)%$", s.strip())
    return int(m.group(1)) if m else 0


@tool("audio", network=True)
def audio_voiceover(text: str = "", script_path: str = "", voice: str = "auto", rate: str = "+0%",
                    pitch: str = "+0Hz", volume: str = "+0%", engine: str = "auto", pause_ms: int = 350,
                    loudness: float = -16.0, format: str = "wav", max_chars_per_line: int = 42,
                    allow_download: bool = False, project: str = "", out: str = "") -> Result:
    """Text → natural voiceover (Egyptian/Saudi/Gulf Arabic, many English accents) with captions
    aligned to the voice. Long scripts are chunked by sentence/paragraph, synthesised, stitched with
    natural pauses and loudness-normalised. Returns the audio (WAV 48 kHz by default), an SRT and a
    words JSON (data: audio, srt, json, words, duration, voice, lufs).

    voice: 'auto' (Egyptian female for Arabic text, US multilingual female for English), an Edge
    voice id (ar-EG-SalmaNeural, ar-EG-ShakirNeural, ar-SA-HamedNeural, en-US-AndrewMultilingualNeural…),
    an alias (egyptian-female, egyptian-male, saudi-male, british-female, narrator-male…) or a Piper
    voice (ar_JO-kareem-medium, en_US-lessac-medium). rate '-10%'..'+30%', pitch '-5Hz'..'+5Hz'.
    engine: auto (edge online, Piper offline fallback) | edge | piper. pause_ms: silence between
    paragraphs. loudness: LUFS target (-16 voiceover/podcast, -14 reels; 0 = leave as is).
    Tip: write numbers/abbreviations the way they should be SAID (Egyptian: 'تلاتين' not '30')."""
    if script_path:
        text = Path(script_path).expanduser().read_text(encoding="utf-8")
    text = (text or "").strip()
    if not text:
        raise ToolError("no text", "pass text='…' or script_path='script.txt'")
    v = _resolve_voice(voice, text)
    is_piper = bool(re.match(r"^[a-z]{2}_[A-Z]{2}-", v))
    eng = "piper" if is_piper else engine
    sr = 48000
    warnings: list[str] = []
    pieces: list[np.ndarray] = []
    words: list[dict] = []
    cues_piper: list[dict] = []
    t = 0.0
    used_engine = ""
    if eng in ("auto", "edge"):
        try:
            for i, ch in enumerate(_chunks(text)):
                para_end = ch.endswith("\n")
                ch = ch.strip()
                audio, marks = _edge_chunk(ch, v, rate, pitch, volume)
                x = _decode_mp3(audio, sr)
                x, cut = _trim_edges(x, sr)
                for m in _punctuate(marks, ch):
                    words.append({"word": m["word"], "start": round(t + max(0.0, m["start"] - cut / sr), 3),
                                  "end": round(t + max(0.0, m["end"] - cut / sr), 3)})
                pieces.append(x)
                t += len(x) / sr
                gap = (pause_ms if para_end else 180) / 1000
                pieces.append(np.zeros(int(gap * sr), np.float32))
                t += gap
            used_engine = "edge-tts"
        except ToolError as e:
            if eng == "edge" or "voice" in str(e).lower():
                raise
            warnings.append(f"edge-tts unavailable ({str(e)[:120]}); fell back to offline Piper")
            pieces, words, t = [], [], 0.0
            eng = "piper"
            v = PIPER_DEFAULTS["ar" if C.is_arabic(text) else "en"]
    if eng == "piper":
        pv = v if re.match(r"^[a-z]{2}_[A-Z]{2}-", v) else PIPER_DEFAULTS["ar" if C.is_arabic(text) else "en"]
        speed = 1.0 + _pct(rate) / 100
        for para in re.split(r"\n\s*\n", text):
            sents, psr = _piper_sentences(para, pv, speed, allow_download)
            for s, x in sents:
                x = C.resample(x[:, None], psr, sr)[:, 0]
                x, _ = _trim_edges(x, sr)
                d = len(x) / sr
                cues_piper.append({"start": round(t, 3), "end": round(t + d, 3), "text": s})
                pieces.append(x)
                t += d
                pieces.append(np.zeros(int(0.22 * sr), np.float32))
                t += 0.22
            pieces.append(np.zeros(int(max(0, pause_ms - 220) / 1000 * sr), np.float32))
            t += max(0, pause_ms - 220) / 1000
        v, used_engine = pv, "piper"
        if pitch not in ("+0Hz", "0Hz", ""):
            warnings.append("pitch is ignored by Piper")
        if "ar_" in pv:
            warnings.append("Piper's only Arabic voice is Jordanian MSA-ish (ar_JO-kareem), not Egyptian — "
                            "use edge-tts online for Egyptian dialect")
    y = np.concatenate(pieces or [np.zeros(1, np.float32)])
    # strip trailing pause, add short room tone at the head/tail
    y, _ = _trim_edges(y, sr)
    y = np.concatenate([np.zeros(int(0.12 * sr), np.float32), y, np.zeros(int(0.25 * sr), np.float32)])
    words = [{**w, "start": round(w["start"] + 0.12, 3), "end": round(w["end"] + 0.12, 3)} for w in words]
    cues_piper = [{**c, "start": round(c["start"] + 0.12, 3), "end": round(c["end"] + 0.12, 3)} for c in cues_piper]
    y = C.fade(y[:, None], sr, 0.01, 0.05)
    norm = {}
    if loudness:
        y, norm = C.normalize_loudness(y, sr, float(loudness), -1.0)
    stem = f"vo-{v}"
    dest = C.out_path(project, out, stem, "." + format.lstrip("."))
    C.save(dest, y, sr)
    if words:
        cues = C.words_to_cues(words, max_chars=max_chars_per_line, max_lines=2)
    else:
        cues = []
        for c in cues_piper:  # split long sentences into readable cues, time ∝ characters (approximate)
            toks = c["text"].split()
            n = len(c["text"])
            dur = c["end"] - c["start"]
            acc = 0
            fake = []
            for tok in toks:
                s0 = c["start"] + dur * acc / n
                acc += len(tok) + 1
                fake.append({"word": tok, "start": s0, "end": c["start"] + dur * min(1, acc / n)})
            cues += C.words_to_cues(fake, max_chars=max_chars_per_line, max_lines=2, gap_split=9)
        if cues:
            warnings.append("Piper gives no word timings: caption cue timing within a sentence is estimated from "
                            "character counts (sentence boundaries are exact)")
    srt = C.write_srt(cues, C.sibling(dest, ".srt"))
    js = C.write_json({"voice": v, "engine": used_engine, "rate": rate, "pitch": pitch, "text": text,
                       "words": words, "cues": cues, "word_timing": "exact" if words else "estimated"},
                      C.sibling(dest, ".json"))
    m, w = C.measure(dest, float(loudness) if loudness else None)
    warnings += w
    pv = C.preview(dest, C.sibling(dest, ".png"), metrics=m, title=f"voiceover {v}")
    res = Result(f"Voiceover {m['duration']}s with {used_engine} voice {v} ({len(text)} chars, "
                 f"{len(cues)} caption cues) → {m.get('lufs')} LUFS / {m.get('true_peak')} dBTP.",
                 files=[str(dest), str(srt), str(js)], previews=[str(pv)], warnings=warnings,
                 data={"audio": str(dest), "srt": str(srt), "json": str(js), "words": words, "cues": len(cues),
                       "voice": v, "engine": used_engine, "duration": m["duration"], "metrics": m, "normalize": norm})
    res.next_steps += ["Listen-check pronunciation of names/numbers; rephrase in the script rather than post-fixing.",
                       "Optional: audio_transcribe the result to verify words (round-trip WER).",
                       "Add music: audio_mix(voice=…, music=…) with auto-ducking."]
    if used_engine == "edge-tts":
        res.next_steps.append("Edge voices are free Microsoft online voices — check Microsoft's terms for commercial use.")
    return res


@tool("audio", network=True)
def audio_voices(language: str = "ar", gender: str = "", engine: str = "edge", samples: int = 0,
                 sample_text: str = "", project: str = "", out: str = "") -> Result:
    """Voice catalogue for audio_voiceover: lists Edge neural voices (online, free) and/or Piper voices
    (offline) for a language ('ar', 'ar-EG', 'en', 'en-GB', 'all'), with gender, accent and personality.
    samples=N renders the first N voices reading sample_text and one 'audition' file with all of them
    back to back (a 1 s beep between voices, in list order) so the user can pick by ear.
    Returns data.voices [{id, gender, locale, engine, sample}]."""
    lang = language.strip()
    rows = []
    if engine in ("edge", "all"):
        for vv in edge_voices():
            loc = vv["Locale"]
            if lang not in ("all", "") and not (loc.lower() == lang.lower() or loc.lower().startswith(lang.lower() + "-")):
                continue
            if gender and vv["Gender"].lower() != gender.lower():
                continue
            rows.append({"id": vv["ShortName"], "gender": vv["Gender"], "locale": loc, "engine": "edge",
                         "personality": ", ".join(vv.get("VoiceTag", {}).get("VoicePersonalities", []))})
    if engine in ("piper", "all"):
        for k, vv in piper_catalog().items():
            code = vv["language"]["code"]
            if lang not in ("all", "") and not code.lower().replace("_", "-").startswith(lang.lower()):
                continue
            rows.append({"id": k, "gender": "", "locale": code, "engine": "piper", "quality": vv.get("quality"),
                         "downloaded": (models_dir() / "piper" / f"{k}.onnx").exists()})
    # Egyptian first for Arabic
    rows.sort(key=lambda r: (r["engine"] != "edge", not r["locale"].startswith("ar-EG"), r["locale"], r["id"]))
    files, previews, warnings = [], [], []
    if samples > 0 and rows:
        sr = 48000
        beep = 0.15 * np.sin(2 * np.pi * 880 * np.arange(int(0.12 * sr)) / sr).astype(np.float32)
        parts = []
        d = None
        for r in rows[:samples]:
            txt = sample_text or ("أهلاً بيكم! أنا الصوت ده، وممكن أقرا لكم أي نص بالعربي بطريقة طبيعية."
                                  if r["locale"].startswith("ar") else
                                  "Hi there! This is how I sound reading your script, clear and natural.")
            try:
                if r["engine"] == "edge":
                    audio, _ = _edge_chunk(txt, r["id"], "+0%", "+0Hz", "+0%")
                    x = _decode_mp3(audio, sr)
                else:
                    sents, psr = _piper_sentences(txt, r["id"], 1.0, allow_download=False)
                    x = C.resample(np.concatenate([s[1] for s in sents])[:, None], psr, sr)[:, 0]
                x, _ = _trim_edges(x, sr)
                x, _ = C.normalize_loudness(x, sr, -16, -1)
                x = x[:, 0]
                p = C.out_path(project, out, f"voice-sample-{r['id']}", ".mp3", kind="audio/voices")
                d = p.parent
                C.save(p, x, sr)
                r["sample"] = str(p)
                files.append(str(p))
                parts += [x, np.zeros(int(0.4 * sr), np.float32), beep, np.zeros(int(0.5 * sr), np.float32)]
            except ToolError as e:
                warnings.append(f"{r['id']}: {e}")
        if parts:
            aud = C.out_path(project, str(d) if d else out, "voice-audition", ".mp3", kind="audio/voices")
            C.save(aud, np.concatenate(parts), sr)
            files.insert(0, str(aud))
            previews.append(str(C.preview(aud, C.sibling(aud, ".png"), title="audition: voices in list order, beep between")))
    lines = [f"  {r['id']:34} {r['gender']:7} {r['locale']:7} {r['engine']:6} {r.get('personality') or r.get('quality') or ''}"
             for r in rows[:120]]
    summ = f"{len(rows)} voice(s) for '{lang}' ({engine}):\n" + "\n".join(lines)
    if lang.startswith("ar") and engine in ("piper", "all"):
        warnings.append("Piper (offline) has no Egyptian voice — only ar_JO-kareem (Jordanian). Egyptian = Edge ar-EG-*.")
    return Result(summ, files=files, previews=previews, warnings=warnings, data={"voices": rows, "aliases": ALIASES})
