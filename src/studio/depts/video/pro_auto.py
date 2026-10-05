"""Auto-edit: from a brief to a finished, editable edit (video_auto_edit).

Plan → source → assemble → render → check, the way an editor works:
  1. voice: script → audio_voiceover (exact word timings) | voiceover file / talking head → transcript
  2. beats: sentences split into shots at the style's pace (reel 1.5–3 s … documentary 4–8 s)
  3. b-roll per shot: own footage (shot detection + quality ranking + filename keywords) and/or free stock
     (stock_search kind=video in the right orientation → stock_download, licences kept)
  4. music: file | stock:<query> | generate (audio_music by style), ducked under the voice; cuts snapped
     to the beat for reels/ads/promos
  5. captions from the voice's words, hook title, lower thirds, CTA (motion department), grade, loudness
  6. timeline JSON (re-render/tweak with video_edit), MP4 + .kdenlive, EDIT REPORT (shot list with sources
     and licences), storyboard sheet, delivery QC.
Every department is called through the registry so they stay decoupled; stock is optional."""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from ...core import registry
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _pro as P

STYLES = {
    #            shot length (min, max) s, size, music genre, music dB under voice, captions, transition, snap to beat
    "reel":        {"shot": (1.4, 2.8), "size": "9:16", "genre": "corporate", "music_db": -16, "captions": "reels", "trans": "cut", "beat": True, "platform": "reels", "loud": "reels"},
    "ad":          {"shot": (1.2, 2.5), "size": "9:16", "genre": "corporate", "music_db": -15, "captions": "bold", "trans": "cut", "beat": True, "platform": "reels", "loud": "reels"},
    "promo":       {"shot": (1.8, 3.5), "size": "16:9", "genre": "cinematic", "music_db": -15, "captions": "clean", "trans": "mixed", "beat": True, "platform": "youtube", "loud": "youtube"},
    "youtube":     {"shot": (3.0, 6.0), "size": "16:9", "genre": "lofi", "music_db": -20, "captions": "clean", "trans": "cut", "beat": False, "platform": "youtube", "loud": "youtube"},
    "documentary": {"shot": (4.0, 8.0), "size": "16:9", "genre": "cinematic", "music_db": -21, "captions": "clean", "trans": "dissolve", "beat": False, "platform": "youtube", "loud": "youtube"},
}
STOP = set("""a an the and or but if then so to of in on at by for with from as is are was were be been being it its this that these
those i you he she we they me my your our their his her them us do does did done have has had not no yes very really just
can could will would should may might must about into over under again more most some any all each every than too also only
here there when where why how what which who whom whose up down out off own same such both few other own s t don now get got
let lets make makes made like want wants show shows thing things way ways one two first second today going go know new
step steps easy simple lot will great good best better every come comes came start starts started meet meets right
whole change changes changed then right left feel feels look looks take takes need needs give gives keep keeps day days
time times way find found tell told think thought say says said see seen use used try tried work works start ever never
always still even much many well""".split())
STOP_AR = set("في من على إلى الى عن مع هذا هذه ده دي اللي التي الذي و او أو ثم بس يعني كده كدا انا أنا انت إنت احنا إحنا هو هي هم كل ما لا لم لن مش هنا هناك".split())
PROVIDER_RANK = {"pexels": 0, "pixabay": 1, "coverr": 1, "wikimedia": 3, "nasa": 4, "archive": 6, "openverse": 4, "unsplash": 2}


def keywords(text: str, n: int = 2) -> list[str]:
    toks = re.findall(r"[A-Za-z][A-Za-z'-]+|[؀-ۿ]+", text.lower())
    good = [t.strip("'-") for t in toks if t not in STOP and t not in STOP_AR and len(t) > 2]
    seen, out = set(), []
    for t in sorted(good, key=lambda x: -len(x)):
        if t not in seen:
            seen.add(t)
            out.append(t)
    # keep the original order of the strongest words (reads like a query: "coffee beans", not "beans coffee")
    top = set(out[:n])
    return [t for t in dict.fromkeys(good) if t in top][:n]


def load_brief(brief) -> dict:
    if isinstance(brief, dict):
        return dict(brief)
    s = str(brief or "").strip()
    if s.startswith("{"):
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise ToolError(f"brief JSON is invalid: {e}")
    p = Path(s).expanduser()
    if s and p.exists() and p.suffix.lower() == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    if s:
        return {"script": s}
    raise ToolError("empty brief", "brief={'script': '…', 'style': 'reel'} — see the tool description")


# ─────────────────────────── 1. voice ───────────────────────────

def make_voice(b: dict, project: str, work: Path, warns: list[str]) -> dict:
    """→ {audio, words, duration, kind: tts|vo|talking|none, video (talking head)}"""
    if b.get("talking_head"):
        p = C.src_path(b["talking_head"])
        from .pro_text_edit import get_words
        words, meta = get_words(p, b.get("language", "auto"), b.get("model", "auto"), b.get("speed", "accurate"),
                                b.get("transcript", ""), False, bool(b.get("allow_download", False)), work / "tr")
        return {"audio": p, "video": p, "words": words, "duration": C.probe(p)["duration"], "kind": "talking",
                "language": meta.get("language")}
    if b.get("voiceover"):
        p = C.src_path(b["voiceover"])
        from .pro_text_edit import get_words
        words, meta = get_words(p, b.get("language", "auto"), b.get("model", "auto"), b.get("speed", "accurate"),
                                b.get("transcript", ""), False, bool(b.get("allow_download", False)), work / "tr")
        return {"audio": p, "words": words, "duration": C.probe(p)["duration"], "kind": "vo", "language": meta.get("language")}
    if b.get("script"):
        args = {"text": b["script"], "voice": b.get("voice", "auto"), "loudness": -16.0, "project": project}
        if b.get("rate"):
            args["rate"] = b["rate"]
        r = registry.call("audio_voiceover", args)
        wav = next(f for f in r.files if f.endswith((".wav", ".mp3")))
        warns += [f"voiceover: {w}" for w in r.warnings]
        words = [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])} for w in r.data.get("words", [])]
        return {"audio": Path(wav), "words": words, "duration": C.probe(wav)["duration"], "kind": "tts",
                "voice": r.data.get("voice"), "language": "ar" if re.search(r"[؀-ۿ]", b["script"]) else "en"}
    return {"audio": None, "words": [], "duration": float(b.get("duration") or 20), "kind": "none"}


# ─────────────────────────── 2. shot plan ───────────────────────────

def plan_slots(words: list[dict], total: float, shot: tuple[float, float]) -> list[dict]:
    """Shots that follow the narration: one per phrase, phrases split at punctuation/pauses so each shot
    is within the style's shot length; long phrases split evenly; gaps closed (cut at the next phrase)."""
    lo, hi = shot
    if not words:
        n = max(1, int(round(total / ((lo + hi) / 2))))
        return [{"start": total * i / n, "end": total * (i + 1) / n, "text": ""} for i in range(n)]
    phrases, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nx = words[i + 1] if i + 1 < len(words) else None
        dur = w["end"] - cur[0]["start"]
        brk = (not nx or re.search(r"[.!?؟,،;:]\s*$", w["word"]) and dur >= lo * 0.8 or (nx and nx["start"] - w["end"] > 0.45)
               or dur >= hi)
        if brk:
            phrases.append(cur)
            cur = []
    slots = []
    for ph in phrases:
        a, b2 = ph[0]["start"], ph[-1]["end"]
        k = max(1, math.ceil((b2 - a) / hi - 1e-6))
        for j in range(k):
            sub = [w for w in ph if a + (b2 - a) * j / k - 1e-6 <= w["start"] < a + (b2 - a) * (j + 1) / k]
            slots.append({"start": a + (b2 - a) * j / k, "end": a + (b2 - a) * (j + 1) / k,
                          "text": " ".join(w["word"] for w in (sub or ph))})
    # merge too-short shots into their neighbour (a 0.6 s flash of b-roll reads as a mistake)
    merged = []
    for s in slots:
        if merged and (s["end"] - s["start"] < lo * 0.7 or merged[-1]["end"] - merged[-1]["start"] < lo * 0.7) and \
                (s["end"] - merged[-1]["start"]) <= hi * 1.35:
            merged[-1] = {"start": merged[-1]["start"], "end": s["end"], "text": merged[-1]["text"] + " " + s["text"]}
        else:
            merged.append(dict(s))
    # contiguous coverage 0 → total
    merged[0]["start"] = 0.0
    for a, b2 in zip(merged, merged[1:]):
        a["end"] = b2["start"]
    merged[-1]["end"] = total
    return merged


def snap_to_beats(slots: list[dict], beats: list[float], tol: float = 0.3) -> int:
    """Move each cut to the nearest beat within tol (keeps every shot ≥ 0.8 s). Returns cuts moved."""
    if not beats:
        return 0
    moved = 0
    for i in range(1, len(slots)):
        t = slots[i]["start"]
        b = min(beats, key=lambda x: abs(x - t))
        if abs(b - t) <= tol and b - slots[i - 1]["start"] >= 0.8 and slots[i]["end"] - b >= 0.8:
            slots[i]["start"] = slots[i - 1]["end"] = round(b, 3)
            moved += 1
    return moved


# ─────────────────────────── 3. b-roll sources ───────────────────────────

def own_footage(paths: list[Path], work: Path, warns: list[str]) -> list[dict]:
    """Every shot of every own clip with a quality score and filename keywords."""
    from .pro_detect import analyse, measure_shots, score_shot
    pool = []
    for k, f in enumerate(paths[:40]):
        try:
            an = analyse(f, threshold=0.3, min_scene=0.8)
            wk = work / f"own{k}"
            wk.mkdir(parents=True, exist_ok=True)
            for s in measure_shots(f, an, wk):
                if s["duration"] < 0.8:
                    continue
                sc, why = score_shot(s, "broll", 1.5)
                tags = set(re.findall(r"[a-z]{3,}", f.stem.lower().replace("_", " ").replace("-", " "))) - STOP
                pool.append({"src": str(f), "in": s["start"], "out": s["end"], "dur": s["duration"], "score": sc, "why": why,
                             "tags": tags, "kind": "own", "w": an["info"]["width"], "h": an["info"]["height"], "used": 0})
        except ToolError as e:
            warns.append(f"footage {f.name}: skipped ({e})")
    return pool


def stock_pick(query: str, kind: str, orientation: str, min_dur: float, used_ids: set, cache: dict, project: str,
               warns: list[str], must: str = "") -> dict | None:
    key = (query, kind, orientation)
    if key not in cache:
        try:
            args = {"query": query, "kind": kind, "count": 10, "project": project}
            if orientation:
                args["orientation"] = orientation
            if kind == "video":
                args["max_duration"] = 120
            r = registry.call("stock_search", args)
            cache[key] = r.data.get("results") or []
            if not cache[key] and orientation:   # try again any shape (cover-cropped later)
                args.pop("orientation")
                r = registry.call("stock_search", args)
                cache[key] = r.data.get("results") or []
        except ToolError as e:
            warns.append(f"stock_search '{query}': {e}")
            cache[key] = []
    best, bs = None, -1e9
    qstems = {t[:5] for t in re.findall(r"[a-z]{3,}", query.lower()) if t not in STOP}
    for it in cache[key]:
        if it["id"] in used_ids or not it.get("commercial_ok", True):
            continue
        # relevance: keyless archives return loosely related items — the title must mention the query
        tstems = {t[:5] for t in re.findall(r"[a-z]{3,}", str(it.get("title", "")).lower() + " " + " ".join(it.get("tags", []) or []).lower())}
        rel = len(qstems & tstems)
        if qstems and rel == 0:
            continue
        mstems = {t[:5] for t in re.findall(r"[a-z]{3,}", must.lower()) if t not in STOP}
        if mstems and not (mstems & tstems):   # on-topic only: "morning" alone finds leopards at dawn
            continue
        try:
            w, h = (int(x) for x in str(it.get("size", "0x0")).split("x"))
        except ValueError:
            w = h = 0
        dur = float(it.get("duration") or 0)
        sc = -PROVIDER_RANK.get(it.get("provider", ""), 5) + 3 * rel
        if orientation and it.get("orientation") == orientation:
            sc += 3
        sc += min(2.0, min(w, h) / 540) if w else 0
        if kind == "video":
            sc += 2 if dur >= min_dur + 0.5 else -4
            sc -= 2 if dur > 90 else 0
        if sc > bs:
            best, bs = it, sc
    return best


def _has_prominent_face(path: Path, work: Path) -> bool:
    """True when a face larger than ~1.5% of the frame shows in any of 3 sampled frames (stills: the image)."""
    try:
        from PIL import Image
        from ..photo.saliency import detect_faces
    except Exception:
        return False
    frames = []
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        frames = [path]
    else:
        try:
            dur = float(C.probe(path).get("duration") or 0)
        except Exception:
            dur = 0
        for i, f in enumerate((0.2, 0.5, 0.8)):
            out = work / f"face-{path.stem[:30]}-{i}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur * f:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=640:-2", str(out)], capture_output=True, timeout=60)
            if out.exists():
                frames.append(out)
    for fr in frames:
        try:
            im = Image.open(fr).convert("RGB")
            for (x, y, w, h) in detect_faces(im) or []:
                if w * h > 0.015 * im.width * im.height:
                    return True
        except Exception:
            continue
    return False


def best_range(path: Path, need: float) -> tuple[float, float]:
    """In/out of the steadiest, sharpest stretch of `need` s inside one shot of a stock/own clip."""
    from .pro_detect import analyse
    try:
        an = analyse(path, threshold=0.3, min_scene=0.8)
    except ToolError:
        return 0.0, need
    D = an["info"]["duration"]
    cands = [s for s in an["shots"] if s["duration"] >= need + 0.3] or an["shots"]
    s = min(cands, key=lambda x: (x["shake"], -x["duration"]))
    start = s["start"] + min(0.5, max(0.0, (s["duration"] - need) / 3))
    if start + need > D:
        start = max(0.0, D - need - 0.05)
    return round(start, 3), round(min(D, start + need), 3)


# ─────────────────────────── the tool ───────────────────────────

@tool("video", network=True)
def video_auto_edit(brief: dict | str, project: str = "", out: str = "", render: bool = True) -> Result:
    """Make a video from a brief — the whole edit planned and assembled automatically, then rendered AND
    left fully editable. brief = {script: "text to voice" | voiceover: "vo.wav" | talking_head: "cam.mp4",
    footage: ["a.mp4", …] | "folder", style: reel | ad | promo | youtube | documentary, size ("9:16"
    default for reel/ad), duration (montage length without voice), music: "song.mp3" | "stock:<query>" |
    "generate" (default) | "none", music_genre, broll: stock | footage | both, broll_queries: ["English
    query per shot/sentence", …] (needed for Arabic scripts; stock APIs index English), topic: fallback
    query, captions: style | false, voice (audio_voiceover id), brand, title: "hook" | {text, subtitle},
    lower_thirds: [{name, role, at}], cta: "follow" | {kind, handle, text}, grade: preset, transitions:
    cut | mixed | dissolve, language}. Pacing per style (reel 1.4–2.8 s shots cut on the beat … documentary
    4–8 s), b-roll per phrase from own footage (shot detection + quality ranking + filename keywords) and/or
    free stock in the right orientation (licences kept), music ducked under the voice. Writes the timeline
    JSON (tweak + re-render with video_edit), MP4 + .kdenlive, EDIT-REPORT.md (shot list: time, words,
    source, licence/attribution) and a storyboard sheet + QC table to LOOK at. render=false: plan only."""
    b = load_brief(brief)
    style = str(b.get("style", "reel")).lower()
    if style not in STYLES:
        raise ToolError(f"unknown style {style!r}", " | ".join(STYLES))
    S = STYLES[style]
    size = C.parse_size(b.get("size") or S["size"])
    W, H = size
    orientation = "portrait" if H > W * 1.05 else ("landscape" if W > H * 1.05 else "square")
    proj = project or b.get("project", "")
    nm = b.get("name") or "-".join(keywords(str(b.get("topic") or b.get("script") or ""), 3)) or "edit"
    d = C.out_folder(proj, out, f"auto-{style}-{nm}")
    work = d / "work"
    work.mkdir()
    warns: list[str] = []
    report: list[str] = []
    # 1. voice
    voice = make_voice(b, proj, work, warns)
    words = voice["words"]
    outro = 2.5 if b.get("cta") else 1.2
    total = voice["duration"] + (outro if voice["kind"] != "talking" else 0.3)
    if voice["kind"] == "none":
        total = float(b.get("duration") or 20)
    # 4a. music first (cuts snap to its beats)
    music, music_meta, beats = None, {}, []
    mspec = b.get("music", "generate")
    if isinstance(mspec, str) and mspec.lower() not in ("none", "", "false", "no"):
        if mspec.lower().startswith("stock:"):
            q = mspec.split(":", 1)[1].strip() or "upbeat"
            it = stock_pick(q, "music", "", total, set(), {}, proj, warns)
            if it:
                try:
                    r = registry.call("stock_download", {"ids": [it["id"]], "project": proj})
                    dl = (r.data.get("downloaded") or [{}])[0]
                    if dl.get("file"):
                        music = Path(dl["file"])
                        music_meta = {"source": "stock", "id": it["id"], "title": it.get("title"), "license": dl.get("license"),
                                      "attribution": dl.get("attribution"), "license_file": dl.get("license_file")}
                except ToolError as e:
                    warns.append(f"music download failed: {e}")
            if music is None:
                warns.append(f"no stock music for '{q}' — generated an original bed instead")
                mspec = "generate"
        elif mspec.lower() != "generate":
            music = C.src_path(mspec)
            music_meta = {"source": "file", "file": str(music)}
        if music is None and mspec.lower() == "generate":
            genre = b.get("music_genre") or S["genre"]
            r = registry.call("audio_music", {"genre": genre, "duration": round(total + 2, 1), "project": proj,
                                              "seed": int(b.get("seed", 0) or 0), "fit": True})
            music = Path(next(f for f in r.files if f.endswith(".wav")))
            music_meta = {"source": "generated", "genre": genre, "license": "original, royalty-free (studio audio_music)",
                          "bpm": r.data.get("bpm")}
    if music is not None and S["beat"]:
        try:
            from .pro_beats import analyse_beats
            an = analyse_beats(music)
            beats = an["beats"]
            music_meta["tempo"] = an["tempo"]
        except ToolError as e:
            warns.append(f"beat analysis: {e}")
    # 2. shot plan
    if voice["kind"] == "none" and beats:
        per = 60.0 / max(60.0, music_meta.get("tempo", 120))
        every = max(1, int(round(((S["shot"][0] + S["shot"][1]) / 2) / per)))
        cuts = [0.0] + [x for x in beats[every::every] if x < total - 0.8] + [total]
        slots = [{"start": a, "end": b2, "text": ""} for a, b2 in zip(cuts, cuts[1:])]
    else:
        slots = plan_slots(words, total, S["shot"])
        if beats:
            moved = snap_to_beats(slots, beats)
            report.append(f"{moved} of {len(slots) - 1} cuts snapped to the music's beat ({music_meta.get('tempo', 0):.0f} BPM).")
    queries = b.get("broll_queries") or []
    topic = str(b.get("topic", "") or "")
    for i, s in enumerate(slots):
        if i < len(queries) and queries[i]:
            s["query"] = str(queries[i])
        else:
            kw = keywords(s["text"], 2)
            if kw and re.search(r"[؀-ۿ]", " ".join(kw)):
                kw = []
            s["query"] = " ".join(kw) or topic
    if not any(s.get("query") for s in slots) and b.get("broll", "both") != "footage":
        warns.append("no English b-roll queries (Arabic script or no keywords) — pass broll_queries or topic; using own footage only")
    # 3. b-roll
    footage: list[Path] = []
    fsrc = b.get("footage") or []
    if isinstance(fsrc, str):
        fp = Path(fsrc).expanduser()
        footage = sorted(f for f in fp.iterdir() if f.suffix.lower() in C.VIDEO_EXTS) if fp.is_dir() else [C.src_path(fsrc)]
    else:
        footage = [C.src_path(x) for x in fsrc]
    mode = b.get("broll") or ("both" if footage else "stock")
    pool = own_footage(footage, work, warns) if footage and mode in ("footage", "both") else []
    used_ids: set = set()
    cache: dict = {}
    stock_ok = 0
    reuse = bool(b.get("reuse_stock", True))
    head_slots = set()
    if voice["kind"] == "talking":
        # face first: the first sentence and every other slot stay on the speaker; b-roll covers the rest
        head_slots = {i for i in range(len(slots)) if i == 0 or i % 2 == 0}
    for i, s in enumerate(slots):
        if i in head_slots:
            s["source"] = {"kind": "talking_head"}
            continue
        need = s["end"] - s["start"] + 0.15
        q = s.get("query", "")
        choice = None
        if pool:
            qt = set(q.lower().split())
            cands = [u for u in pool if u["dur"] >= min(need, 1.2)]
            if cands:
                def rank(u):
                    return (len(qt & u["tags"]) * 2 + u["score"] - 0.6 * u["used"] + (0.3 if u["dur"] >= need else -0.5))
                u = max(cands, key=rank)
                if mode == "footage" or rank(u) >= 0.55 or not q:
                    choice = u
        if choice is not None:
            choice["used"] += 1
            span = min(choice["dur"], need)
            off = min(choice["dur"] - span, 0.3 * (choice["used"] - 1)) if choice["used"] > 1 else min(0.2, choice["dur"] - span)
            s["source"] = {"kind": "own", "src": choice["src"], "in": round(choice["in"] + max(0.0, off), 3),
                           "why": ", ".join(choice["why"][:3])}
            continue
        if mode in ("stock", "both") and q:
            it = None
            # topic-anchored query first ("coffee beans roasted"), then the phrase alone, then the topic
            for qq in dict.fromkeys(x for x in ((f"{topic} {q}".strip() if topic and topic.lower() not in q.lower() else ""), q, topic) if x):
                it = stock_pick(qq, "video", orientation, need, used_ids, cache, proj, warns, must=topic)
                if it is not None:
                    q = qq
                    break
            if it is None and reuse:   # catalogue exhausted: reuse a relevant clip from another range
                for qq in (q, topic):
                    if qq:
                        it = stock_pick(qq, "video", orientation, need, set(), cache, proj, warns, must=topic)
                        if it is not None:
                            break
            if it is not None:
                used_ids.add(it["id"])
                s["source"] = {"kind": "stock", "id": it["id"], "provider": it.get("provider"), "title": it.get("title"),
                               "query": q, "license": it.get("license"), "attribution": it.get("attribution")}
                stock_ok += 1
                continue
            itp = None
            for qq in dict.fromkeys(x for x in (q, topic) if x):
                itp = stock_pick(qq, "photo", orientation, 0, used_ids, cache, proj, warns, must=topic)
                if itp is not None:
                    break
            if itp is not None:
                used_ids.add(itp["id"])
                s["source"] = {"kind": "stock_photo", "id": itp["id"], "provider": itp.get("provider"), "title": itp.get("title"),
                               "query": q, "license": itp.get("license"), "attribution": itp.get("attribution")}
                continue
        if pool:  # anything usable from own footage beats an empty slot
            u = max(pool, key=lambda u: u["score"] - 0.6 * u["used"])
            u["used"] += 1
            s["source"] = {"kind": "own", "src": u["src"], "in": u["in"], "why": "fallback (no match)"}
        else:
            s["source"] = {"kind": "none"}
    # download the chosen stock in one go
    ids = list(dict.fromkeys(s["source"]["id"] for s in slots if s.get("source", {}).get("kind") in ("stock", "stock_photo")))
    files_by_id: dict = {}
    if ids:
        try:
            r = registry.call("stock_download", {"ids": ids, "quality": "hd", "project": proj})
            for it in r.data.get("downloaded") or []:
                files_by_id[it["id"]] = it
            warns += [f"stock: {w}" for w in r.warnings if "NON-COMMERCIAL" in w or "failed" in w or "expire" in w][:6]
        except ToolError as e:
            warns.append(f"stock_download: {e}")
    # identifiable people in stock b-roll (interviews, public figures, uploaders' family videos) are a rights
    # risk in ads and promos: by default reject clips with a prominent face and reuse a clean shot instead
    if files_by_id and str(b.get("stock_people", "avoid")).lower() != "allow":
        bad = set()
        for sid, it in files_by_id.items():
            pth = it.get("file") or it.get("path")
            if pth and _has_prominent_face(Path(pth), work):
                bad.add(sid)
        if bad:
            clean = [s for s in slots if s.get("source", {}).get("id") in files_by_id and s["source"]["id"] not in bad]
            for k, s in enumerate(slots):
                if s.get("source", {}).get("id") in bad:
                    alt = min(clean, key=lambda c: abs(slots.index(c) - k)) if clean else None
                    if alt:
                        s["source"] = dict(alt["source"], why="reused: original stock showed an identifiable person")
                    elif pool:
                        u = max(pool, key=lambda u: u["score"] - 0.6 * u["used"]); u["used"] += 1
                        s["source"] = {"kind": "own", "src": u["src"], "in": u["in"], "why": "stock clip showed a person"}
                    else:
                        s["source"] = {"kind": "none"}
            warns.append(f"{len(bad)} stock clip(s) showed an identifiable person and were not used (people need a model "
                         "release for ads; brief stock_people='allow' to keep them)")
    if mode in ("stock", "both") and not stock_ok and not pool:
        warns.append("no stock providers returned usable video (keyless providers have small catalogues; add free Pexels/"
                     "Pixabay keys with stock_sources) — empty shots became title colour cards")
    # 5. assemble the timeline
    clips, overlays = [], []
    reused: dict = {}
    bg = "#101018"
    for i, s in enumerate(slots):
        dur = round(s["end"] - s["start"], 3)
        src = s.get("source", {})
        c: dict = {}
        if src.get("kind") == "talking_head":
            c = {"src": str(voice["video"]), "in": round(s["start"], 3), "out": round(s["end"], 3), "fit": "cover", "focus": [0.5, 0.4]}
        elif src.get("kind") == "own":
            c = {"src": src["src"], "in": src["in"], "duration": dur, "fit": "cover", "mute": True}
        elif src.get("kind") in ("stock", "stock_photo") and src["id"] in files_by_id:
            fi = files_by_id[src["id"]]
            src["file"] = fi["file"]
            src["license_file"] = fi.get("license_file")
            if src["kind"] == "stock":
                a0, a1 = best_range(Path(fi["file"]), dur + 0.1)
                k = reused.get(src["id"], 0)
                reused[src["id"]] = k + 1
                if k:   # the same clip again: take a later stretch so the shot does not repeat
                    D = C.probe(Path(fi["file"]))["duration"]
                    a0 = round(max(0.0, min(D - dur - 0.1, a0 + k * (dur + 0.4))), 3)
                c = {"src": fi["file"], "in": a0, "duration": dur, "fit": "cover", "mute": True}
            else:
                c = {"image": fi["file"], "duration": dur, "ken_burns": ["in", "left", "out", "right"][i % 4], "fit": "cover"}
        else:
            src["kind"] = "card"
            c = {"color": bg, "duration": dur}
        if voice["kind"] == "talking" and c.get("src") != str(voice.get("video")) and "color" not in c:
            # b-roll over the talking head: the speaker's audio continues underneath (classic cutaway)
            overlays.append({"type": "broll", "src": c.get("src") or c.get("image"), "at": round(s["start"], 3),
                             "in": c.get("in", 0), "duration": dur, "fade": 0.12})
            c = {"src": str(voice["video"]), "in": round(s["start"], 3), "out": round(s["end"], 3), "fit": "cover", "focus": [0.5, 0.4]}
        tr = b.get("transitions", S["trans"])
        if i and tr in ("dissolve",) and "color" not in c:
            c["transition"] = {"type": "dissolve", "duration": 0.5}
        elif i and tr == "mixed" and i % 4 == 0:
            c["transition"] = {"type": "zoom", "duration": 0.3}
        if c.get("transition") and "duration" in c:   # the transition overlaps: give the clip that much more
            c["duration"] = round(c["duration"] + c["transition"]["duration"], 3)
        c["label"] = f"shot {i + 1}"
        clips.append(c)
    # talking-head: merge consecutive head segments back into one clip (no needless cuts)
    if voice["kind"] == "talking":
        merged = []
        for c in clips:
            if merged and merged[-1].get("src") == c.get("src") and abs(merged[-1].get("out", -1) - c.get("in", -2)) < 1e-3:
                merged[-1]["out"] = c["out"]
            else:
                merged.append(c)
        clips = merged
    audio = []
    if voice["kind"] in ("tts", "vo"):
        audio.append({"src": str(voice["audio"]), "at": 0.0, "volume_db": 0})
    if music is not None:
        audio.append({"src": str(music), "at": 0.0, "volume_db": S["music_db"] if voice["kind"] != "none" else 0,
                      "duck": voice["kind"] != "none", "duck_db": 8, "fade_in": 0.3, "fade_out": min(2.0, outro), "loop": True})
    # titles / lower thirds / CTA (motion department)
    title = b.get("title")
    if title:
        t = {"text": title} if isinstance(title, str) else dict(title)
        overlays.append({"type": "title", "at": 0.0, "duration": float(t.get("duration", 2.6 if style in ("reel", "ad") else 3.5)),
                         "text": t.get("text", ""), "subtitle": t.get("subtitle", ""),
                         "style": t.get("style", "bold")})
    for lt in b.get("lower_thirds") or []:
        overlays.append({"type": "lower_third", "at": float(lt.get("at", 1.0)), "duration": float(lt.get("duration", 4.5)),
                         "name": lt.get("name", ""), "role": lt.get("role", ""), "style": lt.get("style", "modern")})
    cta = b.get("cta")
    if cta:
        cc = {"kind": cta} if isinstance(cta, str) else dict(cta)
        dur_cta = min(3.5, max(2.2, outro + 1.0))
        ov = {"type": "cta", "at": round(max(0.0, total - dur_cta), 3), "duration": dur_cta, "kind": cc.get("kind", "follow")}
        for k in ("handle", "text", "position"):
            if cc.get(k):
                ov[k] = cc[k]
        overlays.append(ov)
    spec: dict = {"size": f"{W}x{H}", "fps": int(b.get("fps", 30)), "background": bg, "clips": clips, "overlays": overlays,
                  "audio": audio, "loudness": S["loud"], "fade_out": 0.4, "extend_to_audio": True}
    if b.get("brand"):
        spec["brand"] = b["brand"]
    if b.get("grade"):
        spec["grade"] = b["grade"]
    cap = b.get("captions", S["captions"])
    if cap and words:
        cw = [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words]
        spec["captions"] = {"words": cw, "style": cap if isinstance(cap, str) else S["captions"]}
        if title and orientation == "portrait":
            # keep the hook title and the first captions apart: captions start after the title
            tdur = overlays[0]["duration"] if overlays and overlays[0]["type"] == "title" else 0
            spec["captions"]["words"] = [w for w in cw if w["start"] >= tdur - 0.05] or cw
    tl_path = d / "timeline.json"
    tl_path.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    # EDIT REPORT
    rep = write_report(d, b, style, voice, slots, music_meta, report, warns, W, H, total)
    files = [str(tl_path), str(rep)]
    data = {"style": style, "size": f"{W}x{H}", "duration": round(total, 3), "voice": {k: str(v) for k, v in voice.items() if k in ("kind", "audio", "voice", "language")},
            "shots": [{"n": i + 1, "start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"], "query": s.get("query", ""),
                       **{k: v for k, v in s.get("source", {}).items()}} for i, s in enumerate(slots)],
            "music": music_meta, "timeline_json": str(tl_path), "report": str(rep)}
    if not render:
        shutil.rmtree(work, ignore_errors=True)
        return Result(f"Planned a {style} edit ({len(slots)} shots, {total:.1f}s) — not rendered.", files=files, warnings=warns,
                      data=data, next_steps=[f"video_edit(timeline='{tl_path}') to render"])
    from . import render as R
    res = R.render(spec, project=proj, out=str(d), name=f"{style}-edit", brand=b.get("brand", ""),
                   summary=f"Auto-edited a {style} ({W}x{H}, {total:.1f}s, {len(slots)} shots: "
                           f"{sum(1 for s in slots if s.get('source', {}).get('kind') == 'stock')} stock, "
                           f"{sum(1 for s in slots if s.get('source', {}).get('kind') == 'own')} own footage, "
                           f"{sum(1 for s in slots if s.get('source', {}).get('kind') == 'talking_head')} on the speaker, "
                           f"{sum(1 for s in slots if s.get('source', {}).get('kind') == 'card')} colour cards).")
    res.files += files
    res.warnings = warns + res.warnings
    res.data.update(data)
    # storyboard: a frame from the middle of every shot of the RENDER, with words + source
    try:
        mp4 = Path(res.files[0])
        tiles = []
        for i, s in enumerate(slots[:40]):
            f = work / f"sb{i}.jpg"
            C.frame_at(mp4, (s["start"] + s["end"]) / 2, f, 360)
            if not f.exists():
                continue
            src = s.get("source", {})
            lab = {"stock": f"stock {src.get('provider', '')}: {src.get('title', '')}", "stock_photo": f"photo {src.get('provider', '')}: {src.get('title', '')}",
                   "own": f"own: {Path(src.get('src', '')).name}", "talking_head": "speaker", "card": "colour card (no b-roll found)"}.get(src.get("kind"), "?")
            tiles.append({"img": Image.open(f), "head": f"{i + 1}. {P.tc(s['start'])}–{P.tc(s['end'])}",
                          "lines": [f"“{s['text'][:60]}”" if s["text"] else "(music)", lab, (src.get("license") or "") + (f" · q: {s.get('query')}" if s.get("query") else "")],
                          "colour": (215, 60, 60) if src.get("kind") == "card" else (46, 170, 90)})
        sb = d / "storyboard.png"
        P.tile_sheet(tiles, sb, title=f"Auto edit — {style} storyboard", subtitle="one frame from the middle of each shot of the render",
                     cols=5 if orientation == "portrait" else 4, tile_w=220 if orientation == "portrait" else 320)
        res.previews.insert(0, str(sb))
        res.files.append(str(sb))
    except Exception as e:  # the storyboard is a convenience; never fail the edit for it
        res.warnings.append(f"storyboard not made: {e}")
    try:
        q = registry.call("video_qc", {"path": res.files[0], "platform": S["platform"] if (W, H) in ((1080, 1920), (1920, 1080)) else "",
                                       "out": str(d)})
        res.previews.append(q.previews[0])
        res.data["qc"] = {"verdict": q.data.get("verdict"), "issues": [w for w in q.warnings][:10]}
        if q.data.get("verdict") == "FAIL":
            res.warnings.append("QC FAIL: " + "; ".join(q.warnings[:4]))
    except ToolError as e:
        res.warnings.append(f"QC skipped: {e}")
    shutil.rmtree(work, ignore_errors=True)
    res.next_steps = [f"read {rep.name} (every shot's source + licence; credits for the description)",
                      f"tweak {tl_path.name} (swap a shot's src/in, change music level) → video_edit(timeline='{tl_path}')",
                      "video_export_interchange(timeline) for Resolve/Premiere/FCP"] + res.next_steps
    return res


def write_report(d: Path, b: dict, style: str, voice: dict, slots: list[dict], music: dict, notes: list[str], warns: list[str],
                 W: int, H: int, total: float) -> Path:
    L = [f"# EDIT REPORT — {style}", "", f"{W}x{H}, {total:.2f}s, {len(slots)} shots. Voice: {voice['kind']}"
         + (f" ({voice.get('voice')})" if voice.get("voice") else "") + (f" — {voice.get('audio')}" if voice.get("audio") else ""), ""]
    L += [f"- {n}" for n in notes]
    L += ["", "## Shot list", "", "| # | time | words | source | licence |", "|---|---|---|---|---|"]
    credits = []
    for i, s in enumerate(slots):
        src = s.get("source", {})
        k = src.get("kind")
        if k in ("stock", "stock_photo"):
            where = f"{src.get('provider')} `{src.get('id')}` “{src.get('title', '')[:50]}” (query: {s.get('query')})"
            if src.get("attribution"):
                credits.append(src["attribution"])
        elif k == "own":
            where = f"own `{Path(src.get('src', '')).name}` from {src.get('in', 0):.2f}s ({src.get('why', '')})"
        elif k == "talking_head":
            where = "speaker (A-roll)"
        else:
            where = "colour card — no b-roll found (query: " + (s.get("query") or "none") + ")"
        L.append(f"| {i + 1} | {P.tc(s['start'])}–{P.tc(s['end'])} | {s['text'][:70]} | {where} | {src.get('license', '') or ''} |")
    L += ["", "## Music", ""]
    if music:
        L.append(", ".join(f"{k}: {v}" for k, v in music.items() if v))
        if music.get("attribution"):
            credits.append(music["attribution"])
    else:
        L.append("none")
    L += ["", "## Credits (paste into the description)", ""]
    L += [f"- {c}" for c in dict.fromkeys(credits)] or ["- none required"]
    if warns:
        L += ["", "## Warnings", ""] + [f"- {w}" for w in warns]
    p = d / "EDIT-REPORT.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p
