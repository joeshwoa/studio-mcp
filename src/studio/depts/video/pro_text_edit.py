"""Text-based editing (Descript-style): edit a talking video by editing its transcript.

video_transcript   → a readable, indexed transcript (sentences with word numbers + timecodes, pauses,
                     likely fillers marked) the AI reads to decide what to cut.
video_transcript_edit → keep/remove by quoted text, word-index ranges or times; remove fillers
                     (English + Egyptian/MSA Arabic, only when they are standalone hesitations),
                     shorten silences; cut points snapped to the quietest 10 ms near each word boundary,
                     15 ms audio crossfades at every cut; renders MP4 + .kdenlive + EDL + review list."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _pro as P

# ─────────────────────────── fillers ───────────────────────────
# hes: hesitation sounds — removed whenever remove_fillers is on.
# ctx: words/phrases that are fillers ONLY when standalone (pause or comma on both sides in 'standard',
#      either side in 'aggressive'); inside a sentence they carry meaning ("I like it", "يعني كده").
FILLERS = {
    "en": {"hes": {"um", "uh", "er", "erm", "ah", "eh", "hm", "mhm", "mm", "uhm", "umm", "hmm", "uhh", "ahm"},
           "ctx": [("you", "know"), ("i", "mean"), ("like",), ("sort", "of"), ("kind", "of"), ("basically",),
                   ("actually",), ("you", "see"), ("okay", "so")],
           "aggr": [("so",), ("well",), ("right",), ("literally",), ("just",)]},
    "ar": {"hes": {"ام", "امم", "مم", "م", "اا", "ا", "اه اه", "ء", "ف", "اي", "اممم", "ممم", "ايي"},
           "ctx": [("يعني", "ايه"), ("يعني",), ("اه",), ("بص",), ("ايه", "ده"), ("طب",), ("بقي",), ("فا",)],
           "aggr": [("خلاص",), ("تمام",), ("طيب",), ("اصل",), ("والله",)]},
}
FILLER_PROMPT = {
    "en": "Umm, so, uh, I was like, you know, thinking... Hmm, well, er, okay, I mean, basically.",
    "ar": "اممم، يعني، آه، بص، يعني إيه... إممم، فـ، طب، يعني كده.",
}
_AR_DIAC = re.compile(r"[ً-ٰٟـ]")
_PUNCT = re.compile(r"[\s\.,!?;:…\"'“”‘’«»()\[\]{}\-–—،؛؟]+")


def norm(w: str) -> str:
    """Lowercase, strip punctuation/diacritics/tatweel, unify alef/ya/ta-marbuta, collapse repeated letters
    (so 'Ummmm' → 'um', 'يعنييي' → 'يعني', 'اممممم' → 'ام')."""
    s = unicodedata.normalize("NFKC", str(w)).lower()
    s = _AR_DIAC.sub("", s)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").replace("ة", "ه")
    s = _PUNCT.sub("", s)
    s = re.sub(r"(.)\1+", r"\1", s)
    return s


def _norm_set(xs) -> set[str]:
    return {re.sub(r"(.)\1+", r"\1", x) for x in xs}


def lang_of(words: list[dict], language: str = "") -> str:
    if language and language not in ("auto", ""):
        return "ar" if language.lower().startswith(("ar", "egy")) else "en"
    txt = " ".join(w["word"] for w in words[:200])
    return "ar" if len(re.findall(r"[؀-ۿ]", txt)) > len(re.findall(r"[A-Za-z]", txt)) else "en"


def find_fillers(words: list[dict], lang: str, mode: str = "standard", pause: float = 0.15) -> dict[int, str]:
    """{word index: reason} for filler words. mode: safe (hesitation sounds only) | standard (+ standalone
    discourse fillers) | aggressive (+ fillers isolated on ONE side, + so/well/just…)."""
    F = FILLERS.get(lang, FILLERS["en"])
    hes = _norm_set(F["hes"])
    out: dict[int, str] = {}
    n = len(words)
    toks = [norm(w["word"]) for w in words]
    for i, t in enumerate(toks):
        if t and t in hes:
            out[i] = f"hesitation “{words[i]['word'].strip()}”"
    if mode == "safe":
        return out
    phrases = [tuple(norm(x) for x in p) for p in F["ctx"]] + ([tuple(norm(x) for x in p) for p in F["aggr"]] if mode == "aggressive" else [])
    phrases.sort(key=len, reverse=True)
    i = 0
    while i < n:
        hit = None
        for ph in phrases:
            L = len(ph)
            if tuple(toks[i:i + L]) == ph and not any(j in out for j in range(i, i + L)):
                hit = L
                break
        if not hit:
            i += 1
            continue
        a, b = i, i + hit - 1
        raw_prev = words[a - 1]["word"] if a > 0 else ""
        gap_b = words[a]["start"] - words[a - 1]["end"] if a > 0 else 9.0
        gap_a = words[b + 1]["start"] - words[b]["end"] if b + 1 < n else 9.0
        before = a == 0 or gap_b >= pause or bool(re.search(r"[,،.!?؟…;:]\s*$", raw_prev)) or bool(re.match(r"^\s*[,،]", words[a]["word"]))
        after = b == n - 1 or gap_a >= pause or bool(re.search(r"[,،.!?؟…;:]\s*$", words[b]["word"]))
        # a filler repeated back-to-back ("like, like") is a hesitation even mid-sentence
        rep = b + hit < n and tuple(toks[b + 1:b + 1 + hit]) == tuple(toks[a:b + 1])
        ok = (before and after) if mode == "standard" else (before or after)
        if ok or rep:
            phrase = " ".join(words[k]["word"].strip() for k in range(a, b + 1))
            why = "standalone" if (before and after) else ("repeated" if rep else "isolated one side")
            for k in range(a, b + 1):
                out[k] = f"filler “{phrase}” ({why})"
        i = b + 1
    return out


def find_repeats(words: list[dict], skip: set[int]) -> dict[int, str]:
    """Stutters / restarts: the same word (or 2-word phrase) said twice in a row → drop the first."""
    out = {}
    idx = [i for i in range(len(words)) if i not in skip]
    toks = {i: norm(words[i]["word"]) for i in idx}
    for k in range(len(idx) - 1):
        i, j = idx[k], idx[k + 1]
        if toks[i] and toks[i] == toks[j] and words[j]["start"] - words[i]["end"] < 0.8 and len(toks[i]) > 0:
            out[i] = f"repeated “{words[i]['word'].strip()}”"
    for k in range(len(idx) - 3):
        a, b, c, d = idx[k:k + 4]
        if toks[a] and (toks[a], toks[b]) == (toks[c], toks[d]) and a not in out and b not in out:
            out[a] = out[b] = f"repeated “{words[a]['word'].strip()} {words[b]['word'].strip()}”"
    return out


# ─────────────────────────── transcript ───────────────────────────

def get_words(p: Path, language: str, model: str, speed: str, transcript: str, verbatim: bool,
              allow_download: bool, work: Path) -> tuple[list[dict], dict]:
    if transcript:
        tp = Path(transcript).expanduser()
        if not tp.exists():
            raise ToolError(f"transcript not found: {tp}")
        j = json.loads(tp.read_text(encoding="utf-8"))
        ws = j.get("words") or [w for s in j.get("segments", []) for w in s.get("words", [])]
        if not ws:
            raise ToolError(f"{tp.name} has no word timings", "use the .json from video_transcript or audio_transcribe")
        return [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"]),
                 "probability": w.get("probability")} for w in ws], {"language": j.get("language", ""), "source": str(tp)}
    from ...core import registry
    lg = (language or "auto").lower()
    args = {"path": str(p), "language": lg, "formats": ["json"], "out": str(work), "allow_download": allow_download,
            "speed": speed}
    if model and model != "auto":
        args["model"] = model
    if verbatim:
        key = "ar" if lg.startswith(("ar", "egy")) else ("en" if lg.startswith("en") else "")
        if key:
            args["prompt"] = FILLER_PROMPT[key]
        if lg.startswith("egy"):
            args["dialect"] = "egyptian"
            args["language"] = "ar"
    r = registry.call("audio_transcribe", args)
    ws = r.data.get("words") or []
    if not ws:
        raise ToolError("no speech found in the audio", "check the file has speech; try language='en'/'ar'")
    jp = next((f for f in r.files if f.endswith(".json")), "")
    return [dict(w) for w in ws], {"language": r.data.get("language", ""), "model": r.data.get("model"), "json": jp,
                                  "confidence": r.data.get("confidence")}


def sentences(words: list[dict], gap: float = 0.9, max_words: int = 40) -> list[dict]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        nx = words[i + 1] if i + 1 < len(words) else None
        if not nx or re.search(r"[.!?؟]\s*$", w["word"]) or nx["start"] - w["end"] > gap or len(cur) >= max_words:
            out.append({"i0": cur[0], "i1": cur[-1], "start": round(words[cur[0]]["start"], 3), "end": round(words[cur[-1]]["end"], 3),
                        "text": " ".join(words[k]["word"].strip() for k in cur)})
            cur = []
    return out


@tool("video")
def video_transcript(path: str, language: str = "auto", model: str = "auto", speed: str = "accurate", verbatim: bool = True,
                     allow_download: bool = False, project: str = "", out: str = "") -> Result:
    """Readable, INDEXED transcript of a video/audio for text-based editing: one sentence per line with
    its timecode and word-index range ([00:12.30 | w45–w61] …), pauses ≥ 0.6 s shown as ⏸, likely fillers
    in {braces}. Writes .md (read it) + .json (words with index/start/end — pass it as transcript= to
    video_transcript_edit so Whisper does not run twice). verbatim=true prompts Whisper to keep
    um/uh/يعني (it tends to drop them). language: auto | en | ar | egyptian."""
    p = C.src_path(path)
    d = C.out_folder(project, out, p.stem + "-transcript")
    words, meta = get_words(p, language, model, speed, "", verbatim, allow_download, d / "whisper")
    lang = lang_of(words, meta.get("language") or language)
    fill = find_fillers(words, lang, "standard")
    sents = sentences(words)
    lines = [f"# Transcript — {p.name}", "", f"language: {meta.get('language') or lang} · {len(words)} words · "
             f"{len(sents)} sentences · word indices are what video_transcript_edit takes ({{…}} = likely filler)", ""]
    for s in sents:
        toks = []
        for k in range(s["i0"], s["i1"] + 1):
            w = words[k]["word"].strip()
            toks.append("{" + w + "}" if k in fill else w)
            if k < s["i1"] and words[k + 1]["start"] - words[k]["end"] >= 0.6:
                toks.append(f"⏸{words[k + 1]['start'] - words[k]['end']:.1f}s")
        lines.append(f"[{P.tc(s['start'])} | w{s['i0']}–w{s['i1']}] " + " ".join(toks))
        nx = next((t for t in sents if t["i0"] == s["i1"] + 1), None)
        if nx and nx["start"] - s["end"] >= 0.6:
            lines.append(f"    ⏸ {nx['start'] - s['end']:.1f}s pause")
    md = d / f"{p.stem}-transcript.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js = d / f"{p.stem}-words.json"
    js.write_text(json.dumps({"source": str(p), "language": meta.get("language") or lang,
                              "words": [{"i": i, **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in w.items()}}
                                        for i, w in enumerate(words)], "sentences": sents}, ensure_ascii=False, indent=1), encoding="utf-8")
    pv = d / "transcript.png"
    transcript_image(words, {k: ("filler", v) for k, v in fill.items()}, pv, f"{p.name} — transcript (orange = likely filler)", lang)
    txt = "\n".join(lines[4:])
    res = Result(f"Transcript of {p.name}: {len(words)} words in {len(sents)} sentences ({len(fill)} likely filler word(s)).\n\n"
                 + (txt if len(txt) < 6000 else txt[:6000] + "\n…(full text in the .md)"),
                 files=[str(md), str(js), str(pv)], previews=[str(pv)],
                 data={"transcript_md": str(md), "transcript_json": str(js), "language": meta.get("language") or lang,
                       "sentences": sents[:400], "fillers": {str(k): v for k, v in fill.items()}, "confidence": meta.get("confidence")})
    res.next_steps.append(f"video_transcript_edit(path, transcript='{js}', remove=[\"exact words to cut\" | [i0, i1]], "
                          "remove_fillers=true, remove_silences=true)")
    return res


# ─────────────────────────── the edit ───────────────────────────

def _resolve_ranges(items, words: list[dict], what: str) -> list[tuple[int, int]]:
    """Text / [i0, i1] / {"from","to"} / {"start","end"} (seconds) → inclusive word-index ranges."""
    if not items:
        return []
    if isinstance(items, (str, dict)) or (isinstance(items, list) and len(items) == 2 and all(isinstance(x, int) for x in items)):
        items = [items]
    toks = [norm(w["word"]) for w in words]
    out = []
    for it in items:
        if isinstance(it, (list, tuple)) and len(it) == 2:
            a, b = int(it[0]), int(it[1])
        elif isinstance(it, dict) and ("from" in it or "i0" in it):
            a, b = int(it.get("from", it.get("i0"))), int(it.get("to", it.get("i1", it.get("from", it.get("i0")))))
        elif isinstance(it, dict) and "start" in it:
            s, e = float(it["start"]), float(it["end"])
            idx = [i for i, w in enumerate(words) if w["end"] > s + 0.02 and w["start"] < e - 0.02]
            if not idx:
                raise ToolError(f"{what}: no words between {s}s and {e}s")
            a, b = idx[0], idx[-1]
        elif isinstance(it, int):
            a = b = it
        elif isinstance(it, str):
            q = [norm(x) for x in it.split() if norm(x)]
            if not q:
                continue
            hits = [i for i in range(len(toks) - len(q) + 1) if toks[i:i + len(q)] == q]
            if not hits:
                raise ToolError(f"{what}: text not found in the transcript: “{it[:80]}”",
                                "quote the words exactly as video_transcript shows them, or pass word indices [i0, i1]")
            if len(hits) > 1:
                pass  # first occurrence; warn by caller via data
            a, b = hits[0], hits[0] + len(q) - 1
        else:
            raise ToolError(f"{what}: cannot read {it!r}", "use \"exact words\", [i0, i1], {\"from\": i0, \"to\": i1} or {\"start\": s, \"end\": e}")
        if not (0 <= a <= b < len(words)):
            raise ToolError(f"{what}: word range {a}–{b} is outside 0–{len(words) - 1}")
        out.append((a, b))
    return out


def plan_cuts(words: list[dict], removed: dict[int, str], order: list[tuple[int, int]], rms: np.ndarray, hop: float,
              src_dur: float, max_pause: float, remove_silences: bool, unlabelled: list[tuple[float, float]],
              remove_unlabelled: bool) -> tuple[list[tuple[float, float]], list[dict]]:
    """→ (kept source segments in output order, review list of every cut)."""
    cuts: list[dict] = []
    segs: list[tuple[float, float]] = []

    sm = np.convolve(rms, np.ones(3) / 3, mode="same") if rms.size else rms
    ref = float(np.median(sm[sm > 0])) if (sm > 0).any() else 1.0

    def quiet(t: float, lo: float, hi: float, reach: float = 0.15) -> float:
        """The quietest point near t (Whisper word times are often ±0.1 s off, so look up to `reach` s
        either side, never past lo..hi): smoothed 10 ms energy plus a small penalty for moving away."""
        a, b = max(lo, t - reach), min(hi, t + reach)
        if b - a < hop or sm.size == 0:
            return min(max(t, lo), hi)
        i0, i1 = int(a / hop), max(int(a / hop) + 1, int(b / hop))
        ks = np.arange(i0, min(i1, sm.size))
        if ks.size == 0:
            return min(max(t, lo), hi)
        cost = sm[ks] / (ref + 1e-9) + 0.35 * np.abs((ks + 0.5) * hop - t) / reach
        k = int(ks[int(np.argmin(cost))])
        return min(max((k + 0.5) * hop, lo), hi)

    def mid(w: dict) -> float:
        return (w["start"] + w["end"]) / 2

    half = max_pause / 2
    for (a, b) in order:
        keep = [i for i in range(a, b + 1) if i not in removed]
        if not keep:
            continue
        runs: list[list[int]] = [[keep[0]]]
        for i in keep[1:]:
            prev = runs[-1][-1]
            gap = words[i]["start"] - words[prev]["end"]
            contiguous = i == prev + 1
            voiced_gap = any(s < words[i]["start"] and e > words[prev]["end"] for s, e in unlabelled) and remove_unlabelled
            if contiguous and not voiced_gap and (not remove_silences or gap <= max_pause):
                runs[-1].append(i)
            else:
                runs.append([i])
        for r in runs:
            w0, w1 = words[r[0]], words[r[-1]]
            # how far we may extend into the neighbouring pause without touching another word
            prev_end = words[r[0] - 1]["end"] if r[0] > 0 else 0.0
            next_start = words[r[-1] + 1]["start"] if r[-1] + 1 < len(words) else src_dur
            lead = min(half, max(0.0, w0["start"] - prev_end) * (0.5 if (r[0] - 1) in removed or r[0] - 1 < a else 1.0))
            tail = min(half, max(0.0, next_start - w1["end"]) * (0.5 if (r[-1] + 1) in removed or r[-1] + 1 > b else 1.0))
            lead = max(lead, 0.03) if r[0] > 0 else lead
            tail = max(tail, 0.05)
            pw = words[r[0] - 1] if r[0] > 0 else None
            nw = words[r[-1] + 1] if r[-1] + 1 < len(words) else None
            p_dropped = pw is not None and ((r[0] - 1) in removed or r[0] - 1 < a)
            n_dropped = nw is not None and ((r[-1] + 1) in removed or r[-1] + 1 > b)
            s_lo = mid(pw) if p_dropped else (pw["end"] if pw else 0.0)
            e_hi = mid(nw) if n_dropped else (nw["start"] if nw else src_dur)
            s = quiet(w0["start"] - lead, max(0.0, s_lo), w0["start"] + 0.35 * (w0["end"] - w0["start"]))
            e = quiet(w1["end"] + tail, w1["start"] + 0.65 * (w1["end"] - w1["start"]), min(src_dur, e_hi))
            segs.append((round(max(0.0, s), 3), round(min(src_dur, e), 3)))
    # merge segments that touch in source order (silence shortening inside one sentence)
    merged: list[tuple[float, float]] = []
    for s, e in segs:
        if merged and abs(s - merged[-1][1]) < 0.02:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    # review list: what fell between consecutive kept segments (in source order)
    srt = sorted(merged)
    covered = 0.0
    for s, e in srt + [(src_dur, src_dur)]:
        if s - covered > 0.04:
            inside = [i for i, w in enumerate(words) if w["start"] >= covered - 0.01 and w["end"] <= s + 0.01]
            reasons = sorted({removed.get(i, "") for i in inside} - {""})
            txt = " ".join(words[i]["word"].strip() for i in inside)
            kind = "words" if inside else ("voiced gap (untranscribed — breath/um?)" if any(
                us >= covered - 0.05 and ue <= s + 0.05 for us, ue in unlabelled) else "silence")
            cuts.append({"src_start": round(covered, 3), "src_end": round(s, 3), "duration": round(s - covered, 3),
                         "kind": kind, "text": txt, "reasons": reasons or ([("leading silence trimmed" if covered == 0 else
                                                                               "trailing silence trimmed" if s >= src_dur else
                                                                               f"pause shortened by {s - covered:.2f}s")] if not inside else [])})
        covered = max(covered, e)
    return merged, cuts


def voiced_gaps(words: list[dict], rms: np.ndarray, hop: float, min_gap: float = 0.3) -> list[tuple[float, float]]:
    """Gaps between transcribed words that still contain voice energy — usually an 'um', breath or laugh
    Whisper left out."""
    if rms.size == 0:
        return []
    sp = []
    for w in words:
        i0, i1 = int(w["start"] / hop), int(w["end"] / hop)
        if i1 > i0:
            sp.append(np.median(rms[i0:i1]))
    if not sp:
        return []
    ref = float(np.median(sp))
    out = []
    for a, b in zip(words, words[1:]):
        g0, g1 = a["end"] + 0.05, b["start"] - 0.05
        if g1 - g0 < min_gap:
            continue
        seg = rms[int(g0 / hop):int(g1 / hop)]
        if seg.size and (seg > ref * 0.35).mean() > 0.4:
            out.append((round(g0, 3), round(g1, 3)))
    return out


def transcript_image(words: list[dict], marks: dict[int, tuple[str, str]], dest: Path, title: str, lang: str = "en",
                     max_words: int = 700) -> Path:
    """Words laid out like a document: removed words struck through (orange = filler, red = cut by request,
    grey = repeat), long pauses as ⏸ chips. RTL for Arabic."""
    W = 1500
    pad = 30
    f = P.font(22, False, "يعني" if lang == "ar" else "")
    fb = P.font(22, True, "يعني" if lang == "ar" else "")
    col = {"filler": (235, 140, 20), "request": (215, 50, 50), "repeat": (130, 130, 140), "keep": (25, 25, 32)}
    probe = Image.new("RGB", (10, 10))
    dp = ImageDraw.Draw(probe)
    items = []
    for i, w in enumerate(words[:max_words]):
        if i and w["start"] - words[i - 1]["end"] >= 0.6:
            items.append(("pause", f"{w['start'] - words[i - 1]['end']:.1f}s", None))
        items.append(("word", w["word"].strip(), marks.get(i)))
    lines, cur, x = [], [], 0
    sp = 9
    for it in items:
        fw = dp.textlength(it[1], font=P.font(16) if it[0] == "pause" else (fb if it[2] else f)) + (28 if it[0] == "pause" else 0)
        if x + fw > W - 2 * pad and cur:
            lines.append(cur)
            cur, x = [], 0
        cur.append((it, fw))
        x += fw + sp
    if cur:
        lines.append(cur)
    lh = 40
    H = 90 + lh * len(lines) + 70
    im = Image.new("RGB", (W, H), (252, 252, 250))
    d = ImageDraw.Draw(im)
    d.text((pad, 20), title, font=P.font(22, True, title), fill=(20, 20, 30))
    y = 70
    rtl = lang == "ar"
    for ln in lines:
        x = W - pad if rtl else pad
        for (kind, txt, mk), fw in ln:
            x0 = x - fw if rtl else x
            if kind == "pause":
                d.rounded_rectangle([x0, y + 4, x0 + fw, y + 30], 8, fill=(225, 228, 235))
                d.rectangle([x0 + 8, y + 10, x0 + 11, y + 24], fill=(90, 95, 110))      # ‖ pause icon
                d.rectangle([x0 + 15, y + 10, x0 + 18, y + 24], fill=(90, 95, 110))
                d.text((x0 + 24, y + 7), txt, font=P.font(16), fill=(90, 95, 110))
            else:
                c = col["keep"] if not mk else col.get(mk[0], col["request"])
                d.text((x0, y), txt, font=fb if mk else f, fill=c)
                if mk:
                    d.line([(x0, y + 16), (x0 + fw, y + 16)], fill=c, width=3)
            x = x - fw - sp if rtl else x + fw + sp
        y += lh
    leg = "struck orange = filler · red = cut by request · grey = repeat · grey chips = pauses ≥ 0.6 s"
    d.text((pad, H - 40), leg, font=P.font(16), fill=(110, 110, 120))
    if len(words) > max_words:
        d.text((W - 420, H - 40), f"(first {max_words} of {len(words)} words shown)", font=P.font(16), fill=(110, 110, 120))
    im.save(dest)
    return dest


def _verify(mp4: Path, words_out: list[dict], removed: dict[int, str], words: list[dict], lang: str, d: Path) -> dict:
    """Re-transcribe the edited file and diff it with the words we meant to keep → leaked cut words (with
    their time in the OUTPUT) and kept words that went missing."""
    import difflib
    from ...core import registry
    r = registry.call("audio_transcribe", {"path": str(mp4), "language": lang, "speed": "fast", "formats": ["json"],
                                           "prompt": FILLER_PROMPT.get(lang, ""), "out": str(d / "verify")})
    gw = [w for w in (r.data.get("words") or []) if norm(w["word"])]
    got = [norm(w["word"]) for w in gw]
    want = [norm(w["word"]) for w in words_out if norm(w["word"])]
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    extra, missing = [], []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "replace" and difflib.SequenceMatcher(None, "".join(want[a0:a1]), "".join(got[b0:b1])).ratio() >= 0.7:
            continue   # a mis-hearing of the same words ("works"/"worked", "it is"/"its"), not a cut problem
        if op in ("insert", "replace"):
            extra += [{"word": got[k], "start": float(gw[k]["start"]), "end": float(gw[k]["end"])} for k in range(b0, b1)]
        if op in ("delete", "replace"):
            missing += want[a0:a1]
    cut_toks = {norm(words[i]["word"]) for i in removed}
    leaked = [x for x in extra if x["word"] in cut_toks]
    return {"match_ratio": round(sm.ratio(), 3), "extra_words": [x["word"] for x in extra][:40], "leaked": leaked,
            "missing": missing[:40], "model": r.data.get("model")}


def _remap(words: list[dict], removed: dict[int, str], segs: list[tuple[float, float]]) -> list[dict]:
    """Kept words in programme (output) time — for captions without re-transcribing."""
    out, t = [], 0.0
    for s, e in segs:
        for i, w in enumerate(words):
            if i in removed or w["start"] < s - 0.12 or w["end"] > e + 0.12 or (w["start"] + w["end"]) / 2 < s or (w["start"] + w["end"]) / 2 > e:
                continue
            out.append({"word": w["word"].strip(), "start": round(t + max(0.0, w["start"] - s), 3),
                        "end": round(t + max(0.05, min(e, w["end"]) - s), 3), "src_index": i})
        t += e - s
    return out


def repair(segs: list[tuple[float, float]], leaked: list[dict]) -> tuple[list[tuple[float, float]], list[str]]:
    """Move the cut next to each leaked word past it: a leak near a segment's start moves its in-point
    later, near its end moves the out-point earlier (times from the verify transcript)."""
    out = [list(x) for x in segs]
    starts, t = [], 0.0
    for s0, e0 in segs:
        starts.append(t)
        t += e0 - s0
    notes = []
    for lk in leaked:
        for k, (s0, e0) in enumerate(segs):
            o0, o1 = starts[k], starts[k] + (e0 - s0)
            if o0 - 0.05 <= lk["start"] < o1:
                if lk["start"] - o0 < 0.5 and lk["end"] - o0 < (o1 - o0) * 0.6:
                    nin = s0 + max(lk["end"] - o0 + 0.02, 0.06)
                    if nin < out[k][1] - 0.1:
                        out[k][0] = max(out[k][0], round(nin, 3))
                        notes.append(f"segment {k + 1}: in-point moved past “{lk['word']}” ({s0:.2f} → {out[k][0]:.2f}s)")
                elif o1 - lk["end"] < 0.5:
                    nout = s0 + min(lk["start"] - o0 - 0.02, (o1 - o0) - 0.06)
                    if nout > out[k][0] + 0.1:
                        out[k][1] = min(out[k][1], round(nout, 3))
                        notes.append(f"segment {k + 1}: out-point moved before “{lk['word']}” ({e0:.2f} → {out[k][1]:.2f}s)")
                break
    return [tuple(x) for x in out], notes


def write_review(cuts: list[dict], segs: list[tuple[float, float]], src: Path, dest_md: Path, fps: float) -> None:
    lines = [f"# Cut list — {src.name}", "", f"{len(cuts)} cut(s), {sum(c['duration'] for c in cuts):.2f}s removed; "
             f"{len(segs)} kept segment(s). Source timecodes.", "",
             "| # | source in | source out | dur | what | text |", "|---|---|---|---|---|---|"]
    for k, c in enumerate(cuts):
        lines.append(f"| {k + 1} | {P.smpte(c['src_start'], fps)} | {P.smpte(c['src_end'], fps)} | {c['duration']:.2f}s | "
                     f"{'; '.join(c['reasons']) or c['kind']} | {c['text'][:80]} |")
    lines += ["", "## Kept segments (record order)", "", "| # | source in | source out | record in |", "|---|---|---|---|"]
    t = 0.0
    for k, (s, e) in enumerate(segs):
        lines.append(f"| {k + 1} | {P.smpte(s, fps)} | {P.smpte(e, fps)} | {P.smpte(t, fps)} |")
        t += e - s
    dest_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


@tool("video")
def video_transcript_edit(path: str, keep: list = [], remove: list = [], remove_fillers: bool = True,
                          filler_mode: str = "standard", remove_silences: bool = True, max_pause: float = 0.35,
                          remove_repeats: bool = False, remove_unlabelled: bool = False, language: str = "auto",
                          transcript: str = "", captions: str = "", crossfade_ms: float = 15.0, model: str = "auto",
                          speed: str = "accurate", allow_download: bool = False, dry_run: bool = False,
                          verify: bool = True, project: str = "", out: str = "") -> Result:
    """Edit a talking video by its transcript (Descript-style). Cut what you remove from the text:
    remove=["exact words to cut", [i0, i1], {"start": 12.0, "end": 15.5}] and/or keep only some parts
    (keep=[…] in the ORDER you want them — reorders the story). Word indices come from video_transcript
    (pass its .json as transcript= so Whisper does not run again). remove_fillers: English um/uh/er/hmm
    always, 'like / you know / I mean / basically…' only when standalone (pause or comma on both sides);
    Egyptian/MSA Arabic امم/ممم/فـ always, 'يعني / آه / بص / يعني إيه / إيه ده / طب' only when standalone —
    filler_mode safe (hesitation sounds only) | standard | aggressive. remove_silences shortens every pause
    to max_pause s. remove_repeats drops stutters ('I I think'). remove_unlabelled also cuts voiced gaps
    Whisper did not transcribe (often 'um'). Cut points snap to the quietest 10 ms; audio crossfades
    (crossfade_ms, 10–20) at every cut. captions: '' or a style (reels/clean/bold…) to burn captions from
    the edited words. dry_run: only the plan. verify: re-transcribe the result (fast model) and warn if a
    cut word is still audible (Whisper word times are ±0.1 s). Returns MP4 + .kdenlive + timeline .json + cut list (.md/
    .json, source timecodes) + CMX3600 .edl + a transcript image with every cut struck through (LOOK)."""
    p = C.src_path(path)
    info = C.probe(p)
    if not info.get("audio"):
        raise ToolError(f"{p.name} has no audio to transcribe")
    if filler_mode not in ("safe", "standard", "aggressive"):
        raise ToolError(f"unknown filler_mode {filler_mode!r}", "safe | standard | aggressive")
    d = C.out_folder(project, out, p.stem + "-textedit")
    words, meta = get_words(p, language, model, speed, transcript, True, allow_download, d / "whisper")
    lang = lang_of(words, meta.get("language") or language)
    removed: dict[int, str] = {}
    marks: dict[int, tuple[str, str]] = {}
    warns: list[str] = []
    order = _resolve_ranges(keep, words, "keep") if keep else [(0, len(words) - 1)]
    for a, b in _resolve_ranges(remove, words, "remove"):
        for i in range(a, b + 1):
            removed[i] = "removed by request"
            marks[i] = ("request", removed[i])
    if keep:
        kept_idx = {i for a, b in order for i in range(a, b + 1)}
        for i in range(len(words)):
            if i not in kept_idx:
                marks.setdefault(i, ("request", "not kept"))
    fillers = find_fillers(words, lang, filler_mode) if remove_fillers else {}
    for i, why in fillers.items():
        if i not in removed:
            removed[i] = why
            marks[i] = ("filler", why)
    if remove_repeats:
        for i, why in find_repeats(words, set(removed)).items():
            removed[i] = why
            marks[i] = ("repeat", why)
    a16 = P.audio_mono(p, 16000)
    hop = 0.01
    n = a16.size // 160
    rms = np.sqrt((a16[:n * 160].reshape(n, 160).astype(np.float64) ** 2).mean(axis=1)) if n else np.zeros(0)
    gaps = voiced_gaps(words, rms, hop)
    D = info["duration"]
    segs, cuts = plan_cuts(words, removed, order, rms, hop, D, max_pause, remove_silences, gaps, remove_unlabelled)
    if not segs:
        raise ToolError("everything was removed — nothing left to render")
    fps = info.get("fps") or 30.0
    kept_s = sum(e - s for s, e in segs)
    words_out = _remap(words, removed, segs)
    clips = []
    ax = max(0.0, min(0.03, crossfade_ms / 1000.0))
    for k, (s, e) in enumerate(segs):
        c = {"src": str(p), "in": s, "out": e, "label": f"seg {k + 1}"}
        if k and ax:
            c["audio_crossfade"] = ax
        clips.append(c)
    spec: dict = {"clips": clips, "loudness": None}
    if info.get("video"):
        spec["size"] = f"{info['width']}x{info['height']}"
        spec["fps"] = fps
    if captions:
        spec["captions"] = {"words": words_out, "style": captions}
    tl_json = d / f"{p.stem}-textedit.json"
    tl_json.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    review_md = d / f"{p.stem}-cuts.md"
    write_review(cuts, segs, p, review_md, fps)
    (d / f"{p.stem}-cuts.json").write_text(json.dumps({"source": str(p), "cuts": cuts, "kept": segs,
                                                        "removed_words": {str(k): v for k, v in sorted(removed.items())}},
                                                       ensure_ascii=False, indent=1), encoding="utf-8")
    edl = d / f"{p.stem}-textedit.edl"
    from .pro_interchange import edl_from_segments
    edl_from_segments([(str(p), s, e) for s, e in segs], fps, edl, title=f"{p.stem} text edit")
    pv = d / "transcript-cuts.png"
    transcript_image(words, marks, pv, f"{p.name} — {len(cuts)} cut(s), {D:.1f}s → {kept_s:.1f}s", lang)
    removed_list = [{"i": i, "word": words[i]["word"].strip(), "start": round(words[i]["start"], 3), "why": why}
                    for i, why in sorted(removed.items())]
    nf = sum(1 for v in removed.values() if v.startswith(("hesitation", "filler")))
    summary = (f"Text edit of {p.name}: {D:.2f}s → {kept_s:.2f}s ({D - kept_s:.2f}s cut) in {len(segs)} segment(s); "
               f"{nf} filler word(s), {sum(1 for v in removed.values() if v == 'removed by request')} requested word(s), "
               f"{sum(1 for c in cuts if c['kind'] == 'silence')} pause(s) shortened.")
    data = {"segments": [{"in": s, "out": e} for s, e in segs], "cuts": cuts, "removed_words": removed_list,
            "voiced_gaps": gaps, "words_out": words_out, "language": lang, "timeline_json": str(tl_json),
            "cut_list": str(review_md), "edl": str(edl), "transcript_source": meta.get("json") or meta.get("source")}
    if gaps and not remove_unlabelled:
        warns.append(f"{len(gaps)} voiced gap(s) not in the transcript (possible um/breath Whisper skipped) at "
                     + ", ".join(f"{P.tc(a)}" for a, _ in gaps[:6]) + " — listen, or pass remove_unlabelled=true")
    files = [str(tl_json), str(review_md), str(d / f"{p.stem}-cuts.json"), str(edl), str(pv)]
    if dry_run:
        return Result("DRY RUN — " + summary, files=files, previews=[str(pv)], warnings=warns, data=data,
                      next_steps=["run again with dry_run=false to render"])
    from . import render as R
    res = R.render(spec, project=project, out=str(d), name=f"{p.stem}-edited", summary=summary)
    res.files += files
    res.previews.insert(0, str(pv))
    res.warnings = warns + res.warnings
    res.data.update(data)
    if verify:
        try:
            v = _verify(Path(res.files[0]), words_out, removed, words, lang, d)
            all_notes: list[str] = []
            for _pass in range(2):   # up to two repair passes: move the offending cut points and render again
                if not v["leaked"]:
                    break
                segs2, notes = repair(segs, v["leaked"])
                if not notes:
                    break
                spec2 = dict(spec, clips=[dict(c, **{"in": s2, "out": e2}) for c, (s2, e2) in zip(spec["clips"], segs2)])
                if captions:
                    spec2["captions"] = {"words": _remap(words, removed, segs2), "style": captions}
                tl_json.write_text(json.dumps(spec2, indent=1, ensure_ascii=False), encoding="utf-8")
                edl_from_segments([(str(p), x, y) for x, y in segs2], fps, edl, title=f"{p.stem} text edit")
                res2 = R.render(spec2, project=project, out=str(d), name=f"{p.stem}-edited", summary=summary)
                v2 = _verify(Path(res2.files[0]), _remap(words, removed, segs2), removed, words, lang, d)
                bad = lambda vv: len(vv["leaked"]) + 1.5 * len(vv["missing"])  # noqa: E731
                if bad(v2) >= bad(v):   # the repair did not help (it clipped kept words) — keep the earlier pass
                    for f in res2.files + res2.previews:
                        if f not in files:
                            Path(f).unlink(missing_ok=True)
                    spec1 = dict(spec, clips=[dict(c, **{"in": x, "out": y}) for c, (x, y) in zip(spec["clips"], segs)])
                    if captions:
                        spec1["captions"] = {"words": _remap(words, removed, segs), "style": captions}
                    tl_json.write_text(json.dumps(spec1, indent=1, ensure_ascii=False), encoding="utf-8")
                    edl_from_segments([(str(p), x, y) for x, y in segs], fps, edl, title=f"{p.stem} text edit")
                    v.setdefault("repair_rejected", []).extend(notes)
                    break
                for f in res.files + res.previews:   # the earlier pass is superseded — no two edits side by side
                    if f not in files:
                        Path(f).unlink(missing_ok=True)
                first_leaks = [x["word"] for x in v["leaked"]]
                v = v2
                all_notes += notes
                v["repair"] = all_notes
                v.setdefault("earlier_leaks", []).extend(first_leaks)
                res2.files += files
                res2.previews.insert(0, str(pv))
                res2.warnings = warns + res2.warnings
                res2.data.update(data)
                res2.data["segments"] = [{"in": x, "out": y} for x, y in segs2]
                res2.next_steps = res.next_steps
                res, segs = res2, segs2
            res.warnings += [f"auto-repair: {n}" for n in all_notes]
            res.data["verify"] = v
            if v.get("leaked"):
                res.warnings.insert(0, "verify: cut word(s) may still be audible: " + ", ".join(
                    f"“{x['word']}” at {P.tc(x['start'])}" for x in v["leaked"][:8]) + " — listen there; nudge that segment's "
                    "in/out in the timeline .json and re-render with video_edit")
            if len(v.get("missing", [])) > max(2, 0.08 * len(words_out)):
                res.warnings.append(f"verify: {len(v['missing'])} kept word(s) not heard in the result (or mis-heard by the fast "
                                    f"model): {' '.join(v['missing'][:12])}")
        except ToolError as e:
            res.warnings.append(f"verify skipped: {e}")
    res.next_steps.insert(0, f"review {review_md.name} (every cut with source timecodes); tweak {tl_json.name} and re-render "
                             "with video_edit, or open the .kdenlive / import the .edl in Resolve/Premiere")
    return res
