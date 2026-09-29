"""Search/download free-licensed music & sound effects: Openverse (no key) and Freesound (free key)."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C

UA = {"User-Agent": "studio-mcp/0.1 (free creative tools)"}


def _get(url: str, headers: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:200]
        if e.code in (401, 403):
            raise ToolError(f"{urllib.parse.urlparse(url).netloc} refused the request ({e.code}): {body}",
                            "check the API key (FREESOUND_API_KEY)")
        raise ToolError(f"search failed ({e.code}): {body}")
    except Exception as e:
        raise ToolError(f"network error: {e}", "check the internet connection")


def _download(url: str, dest, headers: dict | None = None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.write_bytes(r.read())
    return dest


def _openverse(q: str, kind: str, commercial: bool, n: int, min_dur: float, max_dur: float) -> list[dict]:
    params = {"q": q, "page_size": min(50, max(n * 3, 10))}
    # Openverse's `category` is mostly empty, so filter by provider instead
    if kind == "sfx":
        params["source"] = "freesound"
    elif kind == "music":
        params["source"] = "jamendo,ccmixter"
    if commercial:  # commercial use AND usable in a video (ND forbids sync/adaptation)
        params["license"] = "cc0,pdm,by,by-sa"
    j = _get("https://api.openverse.org/v1/audio/?" + urllib.parse.urlencode(params))
    out = []
    for r in j.get("results", []):
        dur = (r.get("duration") or 0) / 1000
        if (min_dur and dur and dur < min_dur) or (max_dur and dur and dur > max_dur):
            continue
        out.append({"source": f"openverse/{r.get('source')}", "id": r.get("id"), "title": r.get("title"),
                    "creator": r.get("creator"), "license": f"{r.get('license', '').upper()} {r.get('license_version') or ''}".strip(),
                    "license_url": r.get("license_url"), "duration": round(dur, 1), "url": r.get("url"),
                    "page": r.get("foreign_landing_url"), "attribution": r.get("attribution"),
                    "tags": [t.get("name") for t in (r.get("tags") or [])][:8]})
    return out[:n]


def _freesound(q: str, kind: str, commercial: bool, n: int, min_dur: float, max_dur: float, key: str) -> list[dict]:
    flt = []
    if commercial:
        flt.append('license:("Creative Commons 0" OR "Attribution")')
    if min_dur or max_dur:
        flt.append(f"duration:[{min_dur or 0} TO {max_dur or '*'}]")
    if kind == "music":
        flt.append("tag:music")
    params = {"query": q, "page_size": min(50, max(n, 5)), "token": key,
              "fields": "id,name,username,license,duration,previews,url,tags,avg_rating,num_downloads",
              "sort": "rating_desc"}
    if flt:
        params["filter"] = " ".join(flt)
    j = _get("https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params))
    out = []
    for r in j.get("results", []):
        lic = r.get("license", "")
        short = "CC0" if "zero" in lic or "/publicdomain/" in lic else ("CC BY-NC" if "-nc" in lic else ("CC BY" if "/by/" in lic else lic))
        out.append({"source": "freesound", "id": r["id"], "title": r.get("name"), "creator": r.get("username"),
                    "license": short, "license_url": lic, "duration": round(r.get("duration") or 0, 1),
                    "url": (r.get("previews") or {}).get("preview-hq-mp3"), "page": r.get("url"),
                    "attribution": f"\"{r.get('name')}\" by {r.get('username')} (freesound.org) — {short}",
                    "tags": (r.get("tags") or [])[:8], "rating": r.get("avg_rating"), "downloads": r.get("num_downloads")})
    return out[:n]


@tool("audio", network=True)
def audio_library_search(query: str, kind: str = "any", source: str = "auto", commercial: bool = True,
                         limit: int = 10, download: int = 0, min_duration: float = 0.0, max_duration: float = 0.0,
                         project: str = "", out: str = "") -> Result:
    """Search FREE-licensed recorded music and sound effects and optionally download the top results
    with an attribution/licence file. kind: any | music | sfx. source: auto (Freesound if a key is set,
    plus Openverse) | openverse (no key; aggregates Freesound, Jamendo, Wikimedia… CC-licensed) |
    freesound (needs a free API key in FREESOUND_API_KEY or STUDIO_HOME/keys.json). commercial=true
    keeps only licences that allow commercial use (CC0 / CC BY — attribution still required for BY).
    download=N saves the first N (Freesound gives its HQ MP3 preview without OAuth; originals need
    OAuth login). Pixabay Music/SFX has NO public audio API, so it cannot be searched here — the user
    can browse pixabay.com/music by hand. Returns data.results with licence + attribution for each."""
    fs_key = C.env_key("FREESOUND_API_KEY", "FREESOUND_TOKEN")
    srcs = []
    if source in ("auto", "freesound"):
        if fs_key:
            srcs.append("freesound")
        elif source == "freesound":
            raise ToolError("Freesound needs a free API key", "create one at https://freesound.org/apiv2/apply and set "
                            "FREESOUND_API_KEY (or add it to STUDIO_HOME/keys.json)")
    if source in ("auto", "openverse"):
        srcs.append("openverse")
    if not srcs:
        raise ToolError(f"unknown source {source!r}", "auto | openverse | freesound")
    results, warns = [], []
    for s in srcs:
        try:
            if s == "freesound":
                results += _freesound(query, kind, commercial, limit, min_duration, max_duration, fs_key)
            else:
                results += _openverse(query, kind, commercial, limit, min_duration, max_duration)
        except ToolError as e:
            warns.append(f"{s}: {e}")
    if not results and warns:
        raise ToolError("; ".join(warns))
    results = results[: max(limit, 1) * len(srcs)]
    files, previews = [], []
    for r in results[:download]:
        if not r.get("url"):
            continue
        ext = re.search(r"\.(mp3|wav|ogg|flac|m4a|aiff?|opus)(\?|$)", r["url"].lower())
        ext = "." + (ext.group(1) if ext else "mp3")
        dest = C.out_path(project, out, f"{r['source'].split('/')[-1]}-{r['id']}-{r['title'] or 'audio'}", ext, kind="audio/library")
        try:
            _download(r["url"], dest)
        except Exception as e:
            warns.append(f"download failed for {r['title']}: {e}")
            continue
        lic = C.sibling(dest, ".license.txt")
        lic.write_text(f"{r['title']}\nby {r['creator']}\nsource: {r['page']}\nlicence: {r['license']} {r['license_url']}\n\n"
                       f"Attribution line to credit:\n{r['attribution']}\n", encoding="utf-8")
        r["file"] = str(dest)
        files += [str(dest), str(lic)]
        try:
            m, w = C.measure(dest)
            r["metrics"] = m
            warns += [f"{dest.name}: {x}" for x in w]
            previews.append(str(C.preview(dest, C.sibling(dest, ".png"), metrics=m, title=f"{r['title']} — {r['license']}")))
        except Exception as e:
            warns.append(f"{dest.name}: could not measure ({e})")
    lines = [f"  [{r['source']}] {r['title'][:50]!s:52} {r['duration']:>6}s  {r['license']:10} by {r['creator']}" for r in results]
    if not fs_key and source == "auto":
        warns.append("no Freesound key set — searched Openverse only (add FREESOUND_API_KEY for Freesound's full catalogue)")
    sa = [r for r in results if "SA" in (r["license"] or "").upper()]
    if sa:
        warns.append(f"{len(sa)} result(s) are ShareAlike (BY-SA): a video using them may have to be CC BY-SA too — prefer CC0/BY for client work")
    nc = [r for r in results if "NC" in (r["license"] or "").upper()]
    if nc:
        warns.append(f"{len(nc)} result(s) are Non-Commercial — not for client/paid work")
    return Result(f"{len(results)} free-licensed result(s) for “{query}” ({', '.join(srcs)}):\n" + "\n".join(lines),
                  files=files, previews=previews, warnings=warns, data={"results": results},
                  next_steps=["Credit CC BY items with their attribution line (in the video description/credits).",
                              "Pixabay music: browse https://pixabay.com/music/ manually (no API)."])
