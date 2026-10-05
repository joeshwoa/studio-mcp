"""Stock department tools: find and download free stock media from every free stock site that
has an official API, with licence + attribution carried automatically."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from ...config import output_dir, slug, unique_path
from ...core import qc
from ...core.keys import ensure_keys_template, key_status
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _sheet
from ._http import HTTPFail, download, stock_cache
from .providers import KEY_NAMES, KINDS, PROVIDERS, WEIGHTS, Opts, fmt_dur, orient_of

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_items_lock = threading.Lock()
MAX_CACHED_ITEMS = 4000


# ─────────────────────────── item cache (ids resolve without re-searching) ───────────────────────────

def _items_path() -> Path:
    return stock_cache() / "items.json"


def _load_items() -> dict:
    p = _items_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _save_items(new: list[dict], meta: dict, last: list[str] | None = None) -> None:
    with _items_lock:
        db = _load_items()
        items = db.setdefault("items", {})
        now = time.time()
        for it in new:
            items[it["id"]] = {**it, "_search": {**meta, "at": now}}
        if len(items) > MAX_CACHED_ITEMS:
            for k in sorted(items, key=lambda k: items[k].get("_search", {}).get("at", 0))[: len(items) - MAX_CACHED_ITEMS]:
                items.pop(k, None)
        if last is not None:
            db["last"] = last
        p = _items_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)


def lookup(ref) -> dict | None:
    """An item by id ('pexels:123'), or by number of the last search ('3', '#3', 3)."""
    db = _load_items()
    s = str(ref).strip()
    m = re.fullmatch(r"#?(\d+)", s)
    if m and ":" not in s:
        last = db.get("last") or []
        n = int(m.group(1))
        if 1 <= n <= len(last):
            s = last[n - 1]
        else:
            return None
    return (db.get("items") or {}).get(s)


# ─────────────────────────── search + ranking ───────────────────────────

def _providers_for(kind: str, providers: str) -> tuple[list, list[str]]:
    warns = []
    order = list(WEIGHTS.get(kind, {}))
    if providers and providers != "auto":
        want = [p.strip().lower() for p in re.split(r"[,\s]+", providers) if p.strip()]
        bad = [p for p in want if p not in PROVIDERS]
        if bad:
            raise ToolError(f"unknown provider(s) {bad}", f"choose from {', '.join(PROVIDERS)}")
        chosen = []
        for p in want:
            prov = PROVIDERS[p]
            if kind not in prov.kinds:
                warns.append(f"{prov.label} has no {kind} — skipped (it serves: {', '.join(prov.kinds)})")
            elif not prov.active():
                warns.append(f"{prov.label} needs a free key {prov.key_names[0]} — run stock_sources for the 2-minute steps")
            else:
                chosen.append(prov)
        return chosen, warns
    chosen = [PROVIDERS[n] for n in order if PROVIDERS[n].active()]
    missing = [PROVIDERS[n].label for n in order if not PROVIDERS[n].active()]
    if missing:
        warns.append(f"not searched (no key yet): {', '.join(missing)} — stock_sources shows how to add free keys")
    return chosen, warns


STOP = {"a", "an", "the", "of", "and", "in", "on", "with", "for", "to", "at", "by", "from"}


def _stem(w: str) -> str:
    for suf in ("ing", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[^\W_]+", (text or "").lower())} - STOP


def _relevance(it: dict, qwords: set[str]) -> float:
    """Share of query words found in the title/tags (0..1) — evens out providers' own relevance."""
    if not qwords:
        return 0.0
    have = _words(it.get("title", "") + " " + " ".join(it.get("tags") or []))
    return len(qwords & have) / len(qwords)


def _score(it: dict, pos: int, kind: str, o: Opts, qwords: set[str] | None = None) -> float | None:
    """Higher is better; None = filtered out."""
    w, h, dur = it.get("width") or 0, it.get("height") or 0, it.get("duration") or 0
    s = WEIGHTS.get(kind, {}).get(it["provider"], 0.5) / (1 + 0.07 * pos)
    s += 0.3 * _relevance(it, qwords or set())
    if kind in ("video", "photo", "illustration"):
        short = min(w, h) if w and h else 0
        if o.min_width and w and w < o.min_width:
            return None
        s += 0.18 if short >= 2160 else 0.14 if short >= 1080 else 0.05 if short >= 720 else (-0.3 if short and short < 480 else 0)
    if o.orientation and kind not in ("music", "sfx", "icon"):
        ori = orient_of(w, h)
        if not ori and (it.get("extra") or {}).get("thumb_aspect"):
            a = it["extra"]["thumb_aspect"]
            ori = orient_of(int(a * 1000), 1000)
        s += 0.2 if ori == o.orientation else (-0.03 if not ori else -0.7)
    if kind in ("video", "music", "sfx") and dur:
        if (o.min_duration and dur < o.min_duration) or (o.max_duration and dur > o.max_duration):
            return None
        if kind == "video":
            s += 0.05 if 4 <= dur <= 60 else (-0.15 if dur > 300 else 0)
        elif kind == "sfx":
            s += 0.08 if dur <= 20 else (-0.25 if dur > 120 else 0)
        elif kind == "music":
            s += 0.08 if dur >= 45 else (-0.3 if dur < 15 else 0)
    if not it.get("files") and it["provider"] not in ("nasa", "archive"):
        return None
    if not it.get("attribution_required"):
        s += 0.06
    if it.get("share_alike"):
        s -= 0.04
    return s


def _dedupe_key(it: dict) -> list[str]:
    keys = []
    f = (it.get("files") or [{}])[0].get("url") or ""
    if f:
        keys.append(urllib.parse.urlsplit(f)._replace(query="", fragment="").geturl().lower())
    t = re.sub(r"[^a-z0-9]+", "", (it.get("title") or "").lower())
    c = re.sub(r"[^a-z0-9]+", "", (it.get("creator") or "").lower())
    if len(t) > 6 and c:
        keys.append(f"{t}|{c}")
    return keys


def search_kind(query: str, kind: str, o: Opts, providers: str = "auto") -> tuple[list[dict], list[str], dict]:
    provs, warns = _providers_for(kind, providers)
    if not provs:
        return [], warns, {}
    per = max(o.count, 8) if len(provs) > 1 else o.count
    po = Opts(**{**o.__dict__, "count": per})
    got: dict[str, list] = {}
    stats: dict[str, int | str] = {}

    def run(p):
        return p.search(query, kind, po)

    with ThreadPoolExecutor(max_workers=len(provs)) as ex:
        futs = {ex.submit(run, p): p for p in provs}
        done, pending = wait(futs, timeout=60)
        for f in pending:
            warns.append(f"{futs[f].label}: no answer within 60 s — skipped")
            stats[futs[f].name] = "timeout"
        for f in done:
            p = futs[f]
            try:
                got[p.name] = f.result()
                stats[p.name] = len(got[p.name])
            except HTTPFail as e:
                hint = ""
                if p.key_names and (e.status in (401, 403) or (e.status == 400 and "key" in str(e).lower())):
                    hint = f" — check {p.key_names[0]} (stock_sources shows how to get one)"
                warns.append(f"{p.label}: {e}{hint}")
                stats[p.name] = f"error {e.status or ''}".strip()
            except Exception as e:  # one provider failing never sinks the search
                warns.append(f"{p.label}: {type(e).__name__}: {str(e)[:200]}")
                stats[p.name] = "error"
    scored = []
    dropped_nc = 0
    qwords = _words(query)
    for name, items in got.items():
        for i, it in enumerate(items):
            if o.commercial and not (it.get("commercial_ok") and it.get("modify_ok", True)):
                dropped_nc += 1
                continue
            sc = _score(it, i, kind, o, qwords)
            if sc is not None:
                it["_score"] = round(sc, 4)
                scored.append(it)
    scored.sort(key=lambda it: -it["_score"])
    seen: set[str] = set()
    out = []
    for it in scored:
        ks = _dedupe_key(it)
        if any(k in seen for k in ks):
            continue
        seen.update(ks)
        out.append(it)
    if dropped_nc:
        warns.append(f"{dropped_nc} result(s) hidden because their licence forbids commercial use or edits (commercial=true)")
    return out, warns, stats


def _compact(n: int, it: dict) -> dict:
    d = {"n": n, "id": it["id"], "provider": it["provider"], "kind": it["kind"], "title": it["title"],
         "size": f"{it['width']}x{it['height']}" if it.get("width") else "", "orientation": orient_of(it.get("width"), it.get("height")),
         "duration": it.get("duration") or None, "fps": it.get("fps") or None, "license": it["license"],
         "commercial_ok": it["commercial_ok"], "attribution_required": it["attribution_required"],
         "attribution": it["attribution"], "page_url": it["page_url"], "creator": it.get("creator", "")}
    if it.get("source") and it["source"] != it["provider"]:
        d["source"] = it["source"]
    if it.get("notes"):
        d["notes"] = it["notes"]
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _tile(n: int, it: dict, img) -> dict:
    w, h = it.get("width"), it.get("height")
    bits = [it.get("source", it["provider"]).replace("openverse/", "openverse·")]
    if w and h:
        bits.append(f"{w}×{h}")
    elif it["kind"] in ("video", "photo"):
        bits.append("size known at download")
    if it.get("fps"):
        bits.append(f"{it['fps']:g}fps")
    if it["kind"] in ("music", "sfx", "icon", "vector", "illustration"):
        bits.append(it["kind"])
    return {"n": n, "img": img, "kind": it["kind"], "provider": it["provider"], "line1": " · ".join(bits),
            "line2": _sheet.licence_label(it), "colour": _sheet.licence_colour(it), "title": it["title"],
            "badge": fmt_dur(it.get("duration")), "play": it["kind"] == "video"}


def _preview_dir(project: str) -> Path:
    d = output_dir(project or None, "stock") / "_previews"
    d.mkdir(parents=True, exist_ok=True)
    return d


@tool("stock", network=True)
def stock_search(query: str, kind: str = "video", orientation: str = "", count: int = 12, min_duration: float = 0,
                 max_duration: float = 0, min_width: int = 0, commercial: bool = True, providers: str = "auto",
                 page: int = 1, project: str = "", out: str = "") -> Result:
    """Search FREE stock media across every free stock site with an official API and show a numbered,
    labelled contact sheet to pick from (LOOK at it). kind: video | photo | illustration | vector | music |
    sfx | icon | any. Providers fan out in parallel: videos from Pexels, Coverr, Pixabay, Wikimedia Commons,
    NASA, Internet Archive; photos from Unsplash, Pexels, Pixabay, Openverse, Wikimedia, NASA; music from
    Jamendo, Freesound, Openverse, Internet Archive; sfx from Freesound, Openverse, NASA…; icons from Iconify.
    Keyless providers always work; keyed ones (free keys) join once the user adds them (see stock_sources).
    orientation: landscape | portrait | square. min/max_duration in seconds, min_width in px.
    commercial=true (default) keeps only licences allowing commercial use AND edits. providers: "auto" or
    a comma list (e.g. "pexels,wikimedia"). Write queries in ENGLISH (Arabic is passed through but most
    APIs index English). Returns data.results (n, id, size, duration, licence, attribution…) — pass ids
    (or the numbers) to stock_download."""
    q = (query or "").strip()
    if not q:
        raise ToolError("query is empty", "describe the shot in English, e.g. 'coffee pouring slow motion'")
    kind = (kind or "video").lower().strip()
    kind = {"photos": "photo", "image": "photo", "images": "photo", "videos": "video", "footage": "video",
            "sound": "sfx", "sounds": "sfx", "sound effect": "sfx", "svg": "vector", "icons": "icon"}.get(kind, kind)
    if kind not in KINDS + ("any",):
        raise ToolError(f"unknown kind {kind!r}", "video | photo | illustration | vector | music | sfx | icon | any")
    orientation = (orientation or "").lower().strip()
    orientation = {"horizontal": "landscape", "wide": "landscape", "16:9": "landscape", "vertical": "portrait",
                   "tall": "portrait", "9:16": "portrait", "1:1": "square"}.get(orientation, orientation)
    if orientation not in ("", "landscape", "portrait", "square"):
        raise ToolError(f"unknown orientation {orientation!r}", "landscape | portrait | square (or empty)")
    count = max(1, min(int(count or 12), 60))
    o = Opts(orientation=orientation, count=count, page=max(1, int(page or 1)), min_duration=float(min_duration or 0),
             max_duration=float(max_duration or 0), min_width=int(min_width or 0), commercial=bool(commercial))
    warns: list[str] = []
    if ARABIC_RE.search(q):
        warns.append("query contains Arabic: most stock APIs index English only (Openverse/Wikimedia handle some Arabic) — "
                     "for better results search again in English")
    stats: dict = {}
    if kind == "any":
        results = []
        sub = max(2, count // 4 + 1)
        for k in ("video", "photo", "music", "sfx"):
            r, w, st = search_kind(q, k, Opts(**{**o.__dict__, "count": sub}), providers if providers != "auto" else "auto")
            results += r[:sub]
            warns += [x for x in w if x not in warns]
            stats.update({f"{k}:{n}": v for n, v in st.items()})
        results = results[:count]
    else:
        results, w, stats = search_kind(q, kind, o, providers)
        warns += w
        results = results[:count]
    if not results:
        msg = f"no {kind} results for {q!r}"
        res = Result(msg, warnings=warns or ["try simpler English keywords"], next_steps=[
            "try broader English keywords (e.g. 'coffee' instead of 'barista pouring latte art')",
            "set commercial=false only if the project is non-commercial", "add free keys: stock_sources"],
            data={"results": [], "providers": stats})
        res.ok = False
        return res
    for it in results:
        it.pop("_score", None)
    _save_items(results, {"query": q, "kind": kind, "commercial": o.commercial}, last=[it["id"] for it in results])
    thumbs = _sheet.fetch_thumbs(results)
    tiles = [_tile(i + 1, it, im) for i, (it, im) in enumerate(zip(results, thumbs))]
    broken = sum(1 for t, it in zip(thumbs, results) if t is None and it["kind"] not in ("music", "sfx"))
    if broken:
        warns.append(f"{broken} thumbnail(s) could not be loaded (shown as 'no preview')")
    pdir = Path(out).expanduser() if out else _preview_dir(project)
    pdir.mkdir(parents=True, exist_ok=True)
    sheet = unique_path(pdir, f"search-{slug(q, 40)}-{kind}", ".png")
    used = ", ".join(f"{k} {v}" for k, v in stats.items())
    _sheet.render(tiles, sheet, f"Stock search: “{q}” — {kind}, {len(results)} results"
                  + (f", {orientation}" if orientation else "") + (" · commercial-safe" if commercial else " · ALL licences"),
                  f"providers: {used}")
    rows = [_compact(i + 1, it) for i, it in enumerate(results)]
    lines = [f"{r['n']:>2}. {r['id']} · {r.get('size') or ('audio' if r['kind'] in ('music', 'sfx') else 'size at download')} · {fmt_dur(r.get('duration', 0)) or '-'} · {r['license']}"
             f"{' (credit)' if r['attribution_required'] else ''} — {r['title'][:60]}" for r in rows]
    return Result(
        f"{len(results)} free {kind} results for “{q}” from {len([v for v in stats.values() if isinstance(v, int) and v])} "
        f"provider(s). Look at the contact sheet, then download by number or id.\n" + "\n".join(lines),
        files=[str(sheet)], previews=[str(sheet)], warnings=warns,
        next_steps=[f"stock_download(ids=[\"{results[0]['id']}\"], quality=\"hd\", project=\"{project}\")",
                    "page=2 for more; providers=\"…\" to narrow; stock_sources to add free keys (Pexels, Pixabay, Unsplash…)"],
        data={"results": rows, "providers": stats, "query": q, "kind": kind})


# ─────────────────────────── download ───────────────────────────

TARGET_SHORT = {"best": 10 ** 6, "4k": 2160, "uhd": 2160, "hd": 1080, "1080": 1080, "720": 720, "sd": 720}
PHOTO_MAXW = {"best": 0, "4k": 3840, "uhd": 3840, "hd": 1920, "1080": 1920, "720": 1280, "sd": 1280}


def choose_files(it: dict, quality: str, max_width: int) -> list[dict]:
    """Candidate files in preference order (first = chosen; the rest are fallbacks)."""
    files = [dict(f) for f in it.get("files") or [] if f.get("url")]
    if not files:
        return []
    kind = it["kind"]
    q = (quality or "best").lower()
    if kind == "video":
        tgt = TARGET_SHORT.get(q, 1080)

        def key(f):
            w, h = f.get("width") or 0, f.get("height") or 0
            short = min(w, h) if w and h else 0
            fits = (short <= tgt or not short) and (not max_width or not w or w <= max_width)
            mp4 = 1 if "mp4" in (f.get("mime") or "") or f["url"].split("?")[0].lower().endswith(".mp4") else 0
            return (fits, short if fits else -short, mp4, f.get("fps") or 0, -(f.get("rank") or 0))
        return sorted(files, key=key, reverse=True)
    if kind in ("music", "sfx"):
        def akey(f):
            u = f["url"].split("?")[0].lower()
            lossless = u.endswith((".flac", ".wav"))
            pref = {"download": 3, "hq-mp3": 3, "original": 2}.get(f.get("quality", ""), 1)
            return ((lossless if q == "best" else not lossless), pref, "mp3" in u)
        return sorted(files, key=akey, reverse=True)
    if kind in ("icon", "vector"):
        return sorted(files, key=lambda f: ("svg" in (f.get("mime") or "") or f["url"].split("?")[0].lower().endswith(".svg"),
                                            f.get("width") or 0), reverse=True)
    mw = max_width or PHOTO_MAXW.get(q, 0)
    ex = it.get("extra") or {}
    W = it.get("width") or max([f.get("width") or 0 for f in files] or [0])
    if mw and ex.get("resize") and W and W > mw:
        steps = ex.get("resize_steps")
        w = max([s for s in steps if s <= mw] or [min(steps)]) if steps else mw
        H = it.get("height") or 0
        files.insert(0, {"url": ex["resize"].replace("{w}", str(w)), "width": w, "height": int(H * w / W) if H else 0,
                         "quality": f"w{w}", "mime": "image/jpeg", "size": 0})
        return files[:1] + sorted(files[1:], key=lambda f: f.get("width") or 0, reverse=True)
    if mw:
        fit = sorted([f for f in files if (f.get("width") or 0) <= mw], key=lambda f: f.get("width") or 0, reverse=True)
        rest = sorted([f for f in files if (f.get("width") or 0) > mw], key=lambda f: f.get("width") or 0)
        return fit + rest
    return sorted(files, key=lambda f: f.get("width") or 0, reverse=True)


EXT_BY_MIME = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm", "video/ogg": ".ogv",
               "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
               "image/svg+xml": ".svg", "image/tiff": ".tif", "audio/mpeg": ".mp3", "audio/ogg": ".ogg",
               "audio/flac": ".flac", "audio/wav": ".wav", "audio/mp4": ".m4a", "application/ogg": ".ogg"}


def _ext(f: dict, it: dict) -> str:
    path = urllib.parse.urlsplit(f["url"]).path.lower()
    m = re.search(r"\.(mp4|m4v|mov|webm|ogv|jpe?g|png|webp|gif|svg|tiff?|mp3|ogg|oga|flac|wav|m4a|opus)$", path)
    if m:
        e = "." + m.group(1)
        return {".jpeg": ".jpg", ".m4v": ".mp4", ".tiff": ".tif", ".oga": ".ogg"}.get(e, e)
    mime = (f.get("mime") or "").split(";")[0]
    if mime in EXT_BY_MIME:
        return EXT_BY_MIME[mime]
    return {"video": ".mp4", "music": ".mp3", "sfx": ".mp3", "icon": ".svg", "vector": ".svg"}.get(it["kind"], ".jpg")


def verify(path: Path, kind: str) -> tuple[bool, dict, str]:
    """(ok, measured, problem). Video/audio via ffprobe, images via Pillow, SVG via XML parse."""
    try:
        if path.suffix.lower() == ".svg":
            import xml.etree.ElementTree as ET
            root = ET.parse(path).getroot()
            return (root.tag.endswith("svg"), {}, "" if root.tag.endswith("svg") else "not an SVG document")
        if kind in ("video", "music", "sfx"):
            info = qc.probe(path)
            if info.get("error"):
                return False, info, f"ffprobe: {info['error'][:160]}"
            if kind == "video" and not info.get("width"):
                return False, info, "no video stream"
            if kind != "video" and not info.get("audio"):
                return False, info, "no audio stream"
            return True, info, ""
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            return True, {"width": im.width, "height": im.height, "mode": im.mode, "format": im.format}, ""
    except Exception as e:
        return False, {}, f"{type(e).__name__}: {e}"


def ledger_path(project: str) -> Path:
    return output_dir(project or None, "stock") / "stock_credits.json"


def append_ledger(project: str, entry: dict) -> Path:
    p = ledger_path(project)
    with _items_lock:
        try:
            db = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:
            db = {}
        items = db.setdefault("items", [])
        for e in items:
            if e.get("id") == entry["id"]:
                e["files"] = sorted(set(e.get("files", [])) | set(entry.get("files", [])))
                e["downloaded_at"] = entry["downloaded_at"]
                break
        else:
            items.append(entry)
        db["updated"] = entry["downloaded_at"]
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    return p


LIC_FIELDS = ("license", "license_url", "commercial_ok", "modify_ok", "attribution_required", "share_alike", "attribution",
              "creator", "creator_url", "page_url", "notes")


def _sidecar(dest: Path, it: dict, f: dict, measured: dict, when: str) -> Path:
    side = dest.with_name(dest.name + ".license.json")
    data = {"file": dest.name, "id": it["id"], "provider": it["provider"], "source": it.get("source", it["provider"]),
            "kind": it["kind"], "title": it["title"], **{k: it.get(k) for k in LIC_FIELDS},
            "source_file_url": f["url"].split("?token=")[0], "downloaded_at": when,
            "measured": {k: measured.get(k) for k in ("width", "height", "duration", "fps", "codec", "audio_codec") if measured.get(k)},
            "terms": {"provider_licence_note": PROVIDERS[it["provider"]].licence_note,
                      "attribution_rule": PROVIDERS[it["provider"]].attribution_rule}}
    side.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return side


@tool("stock", network=True)
def stock_download(ids: list[str], quality: str = "best", max_width: int = 0, allow_noncommercial: bool = False,
                   project: str = "", out: str = "") -> Result:
    """Download stock items found by stock_search (ids like "pexels:123", or the result numbers of the last
    search like "3") into projects/<project>/stock/<kind>/<provider>-<title>.<ext>, each with a
    <file>.license.json sidecar (licence, attribution, source URLs, date) and an entry in the project's
    stock_credits.json (feed stock_credits). quality: best | 4k | hd (≤1080p / ≤1920 px photos) | sd (≤720p).
    For video the closest rendition is chosen (mp4/H.264 preferred); for photos the largest ≤ max_width.
    Every file is verified (ffprobe for video/audio, Pillow for images) and shown on a contact sheet (LOOK).
    Unsplash download pings are sent as its API requires. Non-commercial items from a commercial=true search
    are refused unless allow_noncommercial=true."""
    if isinstance(ids, (str, int)):
        ids = [x for x in re.split(r"[,\s]+", str(ids)) if x]
    if not ids:
        raise ToolError("no ids given", "pass ids from stock_search data.results, e.g. [\"pexels:123\"] or [\"1\",\"3\"]")
    q = (quality or "best").lower()
    if q not in TARGET_SHORT:
        raise ToolError(f"unknown quality {quality!r}", "best | 4k | hd | sd")
    warns, files, done, tiles, problems = [], [], [], [], []
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for ref in ids:
        it = lookup(ref)
        if not it:
            problems.append(f"{ref}: unknown id — run stock_search first (ids are remembered across searches)")
            continue
        meta = it.pop("_search", {})
        prov = PROVIDERS.get(it["provider"])
        if not it.get("commercial_ok"):
            if meta.get("commercial", True) and not allow_noncommercial:
                problems.append(f"{it['id']}: licence {it['license']} does NOT allow commercial use — refused "
                                "(pass allow_noncommercial=true only for a non-commercial project)")
                continue
            warns.append(f"{it['id']}: {it['license']} is NON-COMMERCIAL — only for non-commercial projects")
        if it.get("modify_ok") is False:
            warns.append(f"{it['id']}: {it['license']} forbids derivatives (ND) — use unedited only")
        try:
            it = prov.resolve(it)
        except Exception as e:
            problems.append(f"{it['id']}: could not list files ({str(e)[:160]})")
            continue
        cands = choose_files(it, q, int(max_width or 0))
        if not cands:
            problems.append(f"{it['id']}: provider offers no downloadable file")
            continue
        base = Path(out).expanduser() if out else output_dir(project or None, "stock") / it["kind"]
        base.mkdir(parents=True, exist_ok=True)
        stem = slug(f"{it['provider']}-{it['title']}", 60)
        dest = None
        chosen = None
        err = ""
        fam = "wikimedia" if it["provider"] == "wikimedia" else ("archive" if it["provider"] == "archive" else "")
        for f in cands[:3]:
            d = unique_path(base, stem, _ext(f, it))
            try:
                download(f["url"], d, family=fam, timeout=90)
            except HTTPFail as e:
                err = str(e)
                continue
            ok, measured, why = verify(d, it["kind"])
            if not ok:
                err = f"downloaded file failed verification: {why}"
                d.rename(d.with_name(d.name + ".bad"))
                continue
            dest, chosen = d, (f, measured)
            break
        if not dest:
            problems.append(f"{it['id']}: download failed ({err})" +
                            (" — Pixabay/Coverr links can expire: run stock_search again" if it["provider"] in ("pixabay", "coverr") else ""))
            continue
        f, measured = chosen
        note = prov.on_download(it)
        if note:
            warns.append(note)
        side = _sidecar(dest, it, f, measured, when)
        led = append_ledger(project, {"id": it["id"], "provider": it["provider"], "source": it.get("source"),
                                      "kind": it["kind"], "title": it["title"], "files": [str(dest)],
                                      "downloaded_at": when, **{k: it.get(k) for k in LIC_FIELDS}})
        files += [str(dest), str(side)]
        mw, mh = measured.get("width") or 0, measured.get("height") or 0
        dur = measured.get("duration") or it.get("duration") or 0
        rec = {"id": it["id"], "file": str(dest), "license_file": str(side), "license": it["license"],
               "attribution_required": it["attribution_required"], "attribution": it["attribution"],
               "size": f"{mw}x{mh}" if mw else "", "duration": round(dur, 2) if dur else None,
               "rendition": f.get("quality", ""), "bytes": dest.stat().st_size}
        if measured.get("fps"):
            rec["fps"] = measured["fps"]
        if measured.get("codec"):
            rec["codec"] = measured["codec"]
        done.append({k: v for k, v in rec.items() if v not in (None, "")})
        if it["kind"] == "video" and measured.get("codec") in ("theora", "mpeg4", "vp8", "mpeg2video", "mpeg1video"):
            warns.append(f"{dest.name}: {measured['codec']} video — fine for ffmpeg-based tools; transcode to H.264 if a desktop editor balks")
        if it["kind"] == "video" and mw and min(mw, mh) < 720 and q in ("best", "4k", "hd"):
            warns.append(f"{it['id']}: only {mw}x{mh} available — below HD")
        img = _sheet.media_frame(dest, it["kind"], dur)
        bits = [it["provider"]]
        if mw:
            bits.append(f"{mw}×{mh}")
        if measured.get("fps"):
            bits.append(f"{measured['fps']:g}fps")
        if measured.get("codec"):
            bits.append(measured["codec"])
        bits.append(f"{dest.stat().st_size / 1e6:.1f} MB")
        tiles.append({"n": len(done), "img": img, "kind": it["kind"], "provider": it["provider"], "line1": " · ".join(bits),
                      "line2": _sheet.licence_label(it), "colour": _sheet.licence_colour(it), "title": dest.name,
                      "badge": fmt_dur(dur), "play": False})
    if not done:
        raise ToolError("nothing downloaded: " + "; ".join(problems), "run stock_search again and pass its ids")
    pdir = Path(out).expanduser() if out else _preview_dir(project)
    sheet = unique_path(pdir, "downloaded", ".png")
    _sheet.render(tiles, sheet, f"Downloaded {len(done)} stock item(s) — verified", "measured from the files themselves (ffprobe / Pillow)",
                  cols=min(4, len(tiles)))
    need_credit = [d for d in done if d["attribution_required"]]
    summary = f"Downloaded {len(done)} item(s) (verified):\n" + "\n".join(
        "- " + " · ".join(x for x in (Path(d['file']).name, d.get('size', ''), fmt_dur(d.get('duration', 0)), d['license']) if x)
        for d in done)
    if need_credit:
        summary += f"\n{len(need_credit)} need a credit — run stock_credits(project) for a ready CREDITS.md / credits.txt."
    return Result(summary, files=files + [str(ledger_path(project))], previews=[str(sheet)], warnings=warns + problems,
                  next_steps=["stock_credits(project=…) → CREDITS.md + credits.txt for the video description",
                              "use the files in the video/motion tools (timeline, reframe, captions)"],
                  data={"downloaded": done, "failed": problems, "ledger": str(ledger_path(project))})


# ─────────────────────────── credits ───────────────────────────

@tool("stock")
def stock_credits(project: str = "", out: str = "") -> Result:
    """Write CREDITS.md (human-readable) and credits.txt (paste into a video/post description) from the
    project's stock_credits.json — every stock item downloaded with stock_download/stock_icon, grouped
    by provider, with REQUIRED attributions first, licence names + links, and any special terms (share-alike,
    NASA insignia rules…). Regenerates both files each time from the ledger. Returns the credit text in data."""
    led = ledger_path(project)
    if not led.exists():
        raise ToolError(f"no stock_credits.json for project {project or '(today)'}",
                        "download something first with stock_download / stock_icon using the same project")
    db = json.loads(led.read_text(encoding="utf-8"))
    items = db.get("items") or []
    if not items:
        raise ToolError("stock_credits.json is empty")
    by_prov: dict[str, list] = {}
    for e in items:
        by_prov.setdefault(e.get("provider", "?"), []).append(e)
    req = [e for e in items if e.get("attribution_required")]
    md = [f"# Credits — stock media", "", f"Project: {project or led.parent.parent.name} · {len(items)} item(s) · "
          f"generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    if req:
        md += ["## Required attributions", "", "These licences require a visible credit (video description, end card, or caption):", ""]
        md += [f"- {e['attribution']}" for e in req] + [""]
    md += ["## All items by provider", ""]
    for prov, es in by_prov.items():
        p = PROVIDERS.get(prov)
        md += [f"### {p.label if p else prov}", ""]
        if p:
            md += [f"_{p.licence_note}_", ""]
        for e in es:
            files = ", ".join(Path(f).name for f in e.get("files", []))
            flags = []
            if not e.get("commercial_ok"):
                flags.append("**NON-COMMERCIAL ONLY**")
            if e.get("share_alike"):
                flags.append("share-alike")
            if e.get("modify_ok") is False:
                flags.append("no derivatives")
            md += [f"- **{e['title']}** — {e.get('creator') or 'unknown'} · [{e['license']}]({e.get('license_url') or e.get('page_url')})"
                   f" · [source]({e.get('page_url')})" + (f" · {' · '.join(flags)}" if flags else ""),
                   f"  - file(s): {files}", f"  - credit: {e.get('attribution')}"]
            for n in e.get("notes") or []:
                md.append(f"  - note: {n}")
        md.append("")
    txt = []
    if req:
        txt += ["Credits:"] + [f"• {e['attribution']}" for e in req]
    other = [e for e in items if not e.get("attribution_required")]
    if other:
        provs = sorted({(PROVIDERS[e['provider']].label if e.get('provider') in PROVIDERS else e.get('provider', '')) for e in other})
        names = sorted({e.get("creator") for e in other if e.get("creator") and e.get("creator") not in ("Coverr",)})
        line = f"Additional stock media from {', '.join(provs)}"
        if names:
            line += f" (thanks to {', '.join(names[:12])}{'…' if len(names) > 12 else ''})"
        txt += (["", line + "."] if txt else [line + "."])
    d = Path(out).expanduser() if out else led.parent
    d.mkdir(parents=True, exist_ok=True)
    mdp, txp = d / "CREDITS.md", d / "credits.txt"
    mdp.write_text("\n".join(md).rstrip() + "\n", encoding="utf-8")
    txp.write_text("\n".join(txt).rstrip() + "\n", encoding="utf-8")
    warns = []
    nc = [e["id"] for e in items if not e.get("commercial_ok")]
    if nc:
        warns.append(f"non-commercial items in this project: {', '.join(nc)} — don't use them in paid/commercial work")
    sa = [e["id"] for e in items if e.get("share_alike")]
    if sa:
        warns.append(f"share-alike items: {', '.join(sa)} — a video adapting them must be released under the same licence")
    return Result(f"Credits for {len(items)} item(s) ({len(req)} require attribution). credits.txt is ready to paste:\n\n"
                  + "\n".join(txt), files=[str(mdp), str(txp)], warnings=warns,
                  data={"credits_txt": "\n".join(txt), "required": len(req), "items": len(items)})


# ─────────────────────────── icons ───────────────────────────

@tool("stock", network=True)
def stock_icon(query: str, color: str = "", size: int = 512, count: int = 1, project: str = "", out: str = "") -> Result:
    """Find and download open-source icons from Iconify (200 000+ icons, 150+ sets: Material, Tabler,
    Phosphor, Lucide, MDI…) as SVG plus a transparent PNG at `size` px, with the icon set's licence in a
    sidecar and the project's stock_credits.json. query: words ("coffee cup") or an exact "prefix:name"
    ("mdi:coffee"). color: hex like "#C8102E" (applies to single-colour icons). count: how many of the best
    matches to download. Returns the files, a preview, and (for word queries) a numbered sheet of other
    matches with their exact names (data.alternatives) so you can pick a different style."""
    from .providers import Iconify
    ic: Iconify = PROVIDERS["iconify"]  # type: ignore[assignment]
    qv = (query or "").strip()
    if not qv:
        raise ToolError("query is empty", "e.g. 'coffee' or 'mdi:coffee'")
    size = max(16, min(int(size or 512), 4096))
    col = (color or "").strip()
    if col and not re.fullmatch(r"#?[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", col):
        raise ToolError(f"color {color!r} is not a hex colour", "use e.g. '#C8102E'")
    if col and not col.startswith("#"):
        col = "#" + col
    warns, previews, alternatives = [], [], []
    if re.fullmatch(r"[a-z0-9-]+:[a-z0-9-]+", qv):
        prefix, nm = qv.split(":")
        try:
            colinfo = ic.collection(prefix)
        except HTTPFail as e:
            raise ToolError(f"Iconify: {e}")
        if not colinfo:
            raise ToolError(f"unknown icon set {prefix!r}", "search with words first, e.g. stock_icon('coffee')")
        picks = [ic.icon_item(prefix, nm, colinfo)]
    else:
        try:
            found = ic.search(qv, "icon", Opts(count=max(24, count), commercial=True))
        except HTTPFail as e:
            raise ToolError(f"Iconify search failed: {e}")
        if not found:
            raise ToolError(f"no icons for {qv!r}", "use one simple English word (e.g. 'cup', 'phone', 'location')")
        # prefer variety: best match per set first
        seen, ordered = set(), []
        for it in found:   # one per style family first (material-symbols / -light / ic all look alike)
            root = it["extra"]["prefix"].split("-")[0]
            fam = {"ic": "material"}.get(root, root)
            if fam not in seen:
                ordered.append(it)
                seen.add(fam)
        ordered += [it for it in found if it not in ordered]
        picks = ordered[:max(1, count)]
        alts = ordered[:24]
        _save_items(alts, {"query": qv, "kind": "icon", "commercial": True}, last=[a["id"] for a in alts])
        thumbs = _sheet.fetch_thumbs(alts)
        tiles = [{"n": i + 1, "img": im, "kind": "icon", "provider": a["extra"]["prefix"], "line1": a["id"].split(":", 1)[1],
                  "line2": _sheet.licence_label(a), "colour": _sheet.licence_colour(a), "title": a["extra"]["set"]}
                 for i, (a, im) in enumerate(zip(alts, thumbs))]
        sheet = unique_path(Path(out).expanduser() if out else _preview_dir(project), f"icons-{slug(qv, 30)}", ".png")
        _sheet.render(tiles, sheet, f"Icon matches for “{qv}” (Iconify)", "download another with stock_icon(\"prefix:name\")", cols=6)
        previews.append(str(sheet))
        alternatives = [{"n": i + 1, "id": a["id"].split(":", 1)[1], "set": a["extra"]["set"], "license": a["license"]}
                        for i, a in enumerate(alts)]
    files, done, tiles = [], [], []
    base = Path(out).expanduser() if out else output_dir(project or None, "stock") / "icon"
    base.mkdir(parents=True, exist_ok=True)
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for it in picks:
        prefix, nm = it["extra"]["prefix"], it["extra"]["name"]
        params = {"height": size}
        if col and not it["extra"].get("palette"):
            params["color"] = col
        elif col:
            warns.append(f"{it['id']} is a multi-colour icon — color ignored")
        url = f"https://api.iconify.design/{prefix}/{nm}.svg?" + urllib.parse.urlencode(params)
        stem = slug(f"{prefix}-{nm}" + (f"-{col.lstrip('#')}" if col else ""), 60)
        svg = unique_path(base, stem, ".svg")
        try:
            download(url, svg, timeout=30)
        except HTTPFail as e:
            warns.append(f"{it['id']}: {e}")
            continue
        head = svg.read_text(encoding="utf-8", errors="replace")[:200]
        if not head.lstrip().startswith("<svg"):
            svg.unlink(missing_ok=True)
            warns.append(f"{it['id']}: not found on Iconify (check the exact name)")
            continue
        ok, _m, why = verify(svg, "icon")
        if not ok:
            warns.append(f"{it['id']}: invalid SVG ({why})")
            continue
        f = {"url": url, "quality": "svg"}
        png = None
        try:
            from ...core.deps import DEPS, find_bin
            import subprocess
            rs = find_bin(DEPS["rsvg"])
            if rs:
                png = unique_path(base, stem + f"-{size}", ".png")
                src = svg
                if it["extra"].get("animated"):
                    from .providers import static_svg
                    src = svg.with_name(svg.stem + ".static.svg")
                    src.write_text(static_svg(svg.read_text(encoding="utf-8")), encoding="utf-8")
                subprocess.run([rs, "-w", str(size), "-h", str(size), "-a", str(src), "-o", str(png)],
                               capture_output=True, timeout=60, check=True)
                if src != svg:
                    files.append(str(src))
            else:
                warns.append("PNG not made: rsvg-convert missing (brew install librsvg / apt install librsvg2-bin)")
        except Exception as e:
            warns.append(f"PNG render failed: {e}")
            png = None
        side = _sidecar(svg, it, f, {}, when)
        out_files = [str(svg)] + ([str(png)] if png else [])
        append_ledger(project, {"id": it["id"], "provider": "iconify", "source": "iconify", "kind": "icon",
                                "title": it["title"], "files": out_files, "downloaded_at": when,
                                **{k: it.get(k) for k in LIC_FIELDS}})
        files += out_files + [str(side)]
        done.append({"id": it["id"], "svg": str(svg), "png": str(png) if png else "", "license": it["license"],
                     "license_url": it["license_url"], "set": it["extra"]["set"], "attribution": it["attribution"],
                     "attribution_required": it["attribution_required"]})
        from PIL import Image
        img = Image.open(png).convert("RGBA") if png else _sheet.media_frame(svg, "icon")
        if img is not None and img.mode == "RGBA":
            bg = Image.new("RGBA", img.size, "white")
            bg.alpha_composite(img)
            img = bg.convert("RGB")
        tiles.append({"n": len(done), "img": img, "kind": "icon", "provider": prefix, "line1": f"{nm} · {size}px" + (f" · {col}" if col else ""),
                      "line2": _sheet.licence_label(it), "colour": _sheet.licence_colour(it), "title": it["extra"]["set"]})
    if not done:
        raise ToolError("no icon downloaded: " + "; ".join(warns), "check the name, or search with words")
    sheet = unique_path(Path(out).expanduser() if out else _preview_dir(project), "icons-downloaded", ".png")
    _sheet.render(tiles, sheet, f"Downloaded {len(done)} icon(s)", cols=min(4, len(tiles)))
    previews.insert(0, str(sheet))
    return Result(f"{len(done)} icon(s) saved as SVG" + (" + PNG" if any(d['png'] for d in done) else "") + ":\n"
                  + "\n".join(f"- {d['id']} ({d['set']}, {d['license']})" for d in done),
                  files=files + [str(ledger_path(project))], previews=previews, warnings=warns,
                  next_steps=["pick another style from the matches sheet: stock_icon(\"prefix:name\", color=…)",
                              "recolour/compose further with the vector tools"],
                  data={"icons": done, "alternatives": alternatives})


# ─────────────────────────── sources / keys ───────────────────────────

@tool("stock")
def stock_sources() -> Result:
    """Which free stock providers are active right now (keyless ones always; keyed ones once the user adds a
    free key), what each gives, licence + attribution rules, rate limits, and step-by-step "get a free key"
    instructions for each keyed provider (Pexels, Pixabay, Unsplash, Coverr, Freesound, Jamendo). Also creates
    STUDIO_HOME/keys.json with EMPTY entries for missing names, so the user only pastes keys in (existing
    values are never touched or shown). Call this before telling the user a provider is unavailable."""
    path, added = ensure_keys_template(KEY_NAMES)
    status = key_status(KEY_NAMES)
    rows, lines, how = [], [], []
    for p in PROVIDERS.values():
        st = "active (no key needed)" if not p.key_names else (f"active ({status[p.key_names[0]]})" if p.active()
                                                               else f"needs free key {p.key_names[0]}")
        rows.append({"provider": p.name, "label": p.label, "status": st, "active": p.active(), "kinds": list(p.kinds),
                     "gives": p.gives, "licence": p.licence_note, "attribution": p.attribution_rule, "rate_limit": p.rate_note,
                     "key_name": p.key_names[0] if p.key_names else "", "untested": p.untested})
        lines.append(f"- {p.label} [{', '.join(p.kinds)}]: {st}" + (" (adapter untested live)" if p.untested else ""))
        if p.key_names and not p.active():
            how.append(f"{p.label} → {p.key_names[0]}:\n" + "\n".join(f"   {i + 1}. {s}" for i, s in enumerate(p.get_key_steps)))
    active = sum(1 for r in rows if r["active"])
    summary = (f"{active}/{len(rows)} stock providers active.\n" + "\n".join(lines) +
               f"\n\nKeys file: {path} (chmod 600; values never shown)" + (f" — added empty entries: {', '.join(added)}" if added else ""))
    if how:
        summary += "\n\nGet free keys (each takes ~2 minutes), then paste into the keys file:\n" + "\n".join(how)
    summary += ("\n\nNo official API (browse by hand, then pass the file to the video tools): Mixkit, Videvo, "
                "Pixabay Music/SFX pages, Uppbeat, YouTube Audio Library.")
    return Result(summary, files=[str(path)], data={"providers": rows, "keys_file": str(path), "keys": status},
                  next_steps=["add keys to the file above (or as environment variables), then stock_search again"] if how else [])
