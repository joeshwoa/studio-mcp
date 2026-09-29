"""Google Fonts on demand: download static TTFs once into STUDIO_HOME/cache/fonts, embed them with
@font-face so renders are identical offline and on any machine.

Mixed Arabic/Latin text uses one CSS family per role ("sf-head", "sf-body") built from TWO faces:
the Latin family for everything, then the Arabic family restricted (unicode-range) to Arabic
code points, with a per-family size-adjust so both scripts sit at the same optical size.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ...config import cache_dir
from ...core.result import ToolError

API = "https://fonts.googleapis.com/css2?family="
META_URL = "https://fonts.google.com/metadata/fonts"
AR_RANGE = ("U+0600-06FF, U+0750-077F, U+0870-08FF, U+FB50-FDFF, U+FE70-FEFF, U+200C-200F, "
            "U+2010-2011, U+204F, U+2E41, U+10E60-10E7E, U+1EE00-1EEFF")

# Curated families with the weights Google serves (offline fallback when metadata is unavailable)
# and an optical size-adjust for Arabic faces (so Arabic sits with Latin at the same visual size).
CURATED: dict[str, dict] = {
    # Arabic (all also carry Latin glyphs unless noted)
    "Cairo": {"w": [300, 400, 500, 600, 700, 800, 900], "ar": 1.0, "kind": "ar-sans", "note": "modern geometric Kufi-inspired sans; the safe default"},
    "Tajawal": {"w": [300, 400, 500, 700, 800, 900], "ar": 1.08, "kind": "ar-sans", "note": "clean, light, friendly; great body text"},
    "Almarai": {"w": [300, 400, 700, 800], "ar": 1.0, "kind": "ar-sans", "note": "neutral, very legible (Saudi-designed), corporate"},
    "IBM Plex Sans Arabic": {"w": [300, 400, 500, 600, 700], "ar": 1.02, "kind": "ar-sans", "note": "technical, corporate, pairs with IBM Plex"},
    "Noto Kufi Arabic": {"w": [300, 400, 500, 600, 700, 800, 900], "ar": 0.96, "kind": "ar-kufi", "note": "geometric Kufi, strong headlines"},
    "Noto Naskh Arabic": {"w": [400, 500, 600, 700], "ar": 1.12, "kind": "ar-naskh", "note": "classic Naskh, long reading, editorial"},
    "Noto Sans Arabic": {"w": [300, 400, 500, 600, 700, 800, 900], "ar": 1.02, "kind": "ar-sans", "note": "neutral UI sans"},
    "Readex Pro": {"w": [300, 400, 500, 600, 700], "ar": 1.0, "kind": "ar-sans", "note": "tech/startup, wide and open"},
    "El Messiri": {"w": [400, 500, 600, 700], "ar": 1.08, "kind": "ar-display", "note": "elegant, slightly calligraphic display"},
    "Amiri": {"w": [400, 700], "ar": 1.2, "kind": "ar-naskh", "note": "classical Naskh (Bulaq press) — heritage, luxury, certificates"},
    "Alexandria": {"w": [300, 400, 500, 600, 700, 800, 900], "ar": 1.0, "kind": "ar-sans", "note": "bold geometric, Egyptian-designed, very modern"},
    "Rubik": {"w": [300, 400, 500, 600, 700, 800, 900], "ar": 1.0, "kind": "ar-sans", "note": "rounded-corner sans, friendly/playful"},
    "Changa": {"w": [300, 400, 500, 600, 700, 800], "ar": 1.0, "kind": "ar-display", "note": "condensed Kufi, sporty, energetic"},
    "Lalezar": {"w": [400], "ar": 1.0, "kind": "ar-display", "note": "fat display, posters, loud promos"},
    "Lemonada": {"w": [300, 400, 500, 600, 700], "ar": 0.92, "kind": "ar-display", "note": "round and playful display"},
    "Reem Kufi": {"w": [400, 500, 600, 700], "ar": 1.05, "kind": "ar-kufi", "note": "geometric Fatimid Kufi, logos and headings"},
    "Aref Ruqaa": {"w": [400, 700], "ar": 1.15, "kind": "ar-callig", "note": "Ruqaa calligraphy — traditional, food, heritage"},
    "Baloo Bhaijaan 2": {"w": [400, 500, 600, 700, 800], "ar": 1.0, "kind": "ar-display", "note": "chunky rounded — kids, food, fun"},
    "Markazi Text": {"w": [400, 500, 600, 700], "ar": 1.2, "kind": "ar-naskh", "note": "editorial Naskh with a Latin serif"},
    "Vazirmatn": {"w": [300, 400, 500, 600, 700, 800, 900], "ar": 1.02, "kind": "ar-sans", "note": "solid UI sans, many weights"},
    "Zain": {"w": [300, 400, 700, 800, 900], "ar": 1.1, "kind": "ar-sans", "note": "contemporary, soft"},
    "Mada": {"w": [300, 400, 500, 600, 700, 900], "ar": 1.05, "kind": "ar-sans", "note": "clean contemporary"},
    "Harmattan": {"w": [400, 500, 600, 700], "ar": 1.2, "kind": "ar-naskh", "note": "simple Naskh"},
    # Latin
    "Inter": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "sans", "note": "neutral workhorse UI/body"},
    "Manrope": {"w": [300, 400, 500, 600, 700, 800], "kind": "sans", "note": "modern geometric-grotesk, corporate/tech"},
    "Plus Jakarta Sans": {"w": [300, 400, 500, 600, 700, 800], "kind": "sans", "note": "friendly modern sans"},
    "DM Sans": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "sans", "note": "low-contrast geometric"},
    "Space Grotesk": {"w": [300, 400, 500, 600, 700], "kind": "sans", "note": "techy quirky grotesk"},
    "Sora": {"w": [300, 400, 500, 600, 700, 800], "kind": "sans", "note": "wide tech display"},
    "Outfit": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "sans", "note": "clean geometric"},
    "Poppins": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "sans", "note": "geometric, very popular"},
    "Montserrat": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "sans", "note": "urban geometric"},
    "Archivo": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "sans", "note": "grotesk, bold news/sport"},
    "Bricolage Grotesque": {"w": [300, 400, 500, 600, 700, 800], "kind": "sans", "note": "characterful contemporary grotesk"},
    "Syne": {"w": [400, 500, 600, 700, 800], "kind": "display", "note": "art/creative, extended bold"},
    "Unbounded": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "display", "note": "rounded wide display"},
    "Anton": {"w": [400], "kind": "display", "note": "condensed impact headlines, thumbnails"},
    "Bebas Neue": {"w": [400], "kind": "display", "note": "all-caps condensed display"},
    "Oswald": {"w": [300, 400, 500, 600, 700], "kind": "display", "note": "condensed gothic"},
    "Fredoka": {"w": [300, 400, 500, 600, 700], "kind": "display", "note": "rounded, playful"},
    "Nunito": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "sans", "note": "rounded friendly body"},
    "Playfair Display": {"w": [400, 500, 600, 700, 800, 900], "kind": "serif", "note": "high-contrast luxury serif"},
    "Fraunces": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "serif", "note": "soft editorial serif"},
    "Cormorant Garamond": {"w": [300, 400, 500, 600, 700], "kind": "serif", "note": "refined classical serif"},
    "DM Serif Display": {"w": [400], "kind": "serif", "note": "bold editorial display serif"},
    "Lora": {"w": [400, 500, 600, 700], "kind": "serif", "note": "readable text serif"},
    "Source Serif 4": {"w": [300, 400, 500, 600, 700, 800, 900], "kind": "serif", "note": "editorial text serif"},
    "Libre Baskerville": {"w": [400, 700], "kind": "serif", "note": "classic book serif"},
    "Great Vibes": {"w": [400], "kind": "script", "note": "formal script (certificates, signatures)"},
    "JetBrains Mono": {"w": [300, 400, 500, 600, 700, 800], "kind": "mono", "note": "code/tech accents"},
}

# Arabic + Latin pairings: display/body per script, with the moods they suit.
PAIRS: list[dict] = [
    {"id": "modern", "moods": ["modern", "tech", "startup", "clean", "confident", "innovative"],
     "head": "Sora", "body": "Inter", "head_ar": "Alexandria", "body_ar": "IBM Plex Sans Arabic", "head_weight": 700},
    {"id": "corporate", "moods": ["corporate", "trustworthy", "professional", "finance", "consulting", "serious", "reliable"],
     "head": "Manrope", "body": "Inter", "head_ar": "IBM Plex Sans Arabic", "body_ar": "IBM Plex Sans Arabic", "head_weight": 700},
    {"id": "friendly", "moods": ["friendly", "warm", "approachable", "health", "community", "education", "kind"],
     "head": "Plus Jakarta Sans", "body": "Plus Jakarta Sans", "head_ar": "Tajawal", "body_ar": "Tajawal", "head_weight": 800},
    {"id": "playful", "moods": ["playful", "fun", "kids", "young", "cheerful", "food", "casual"],
     "head": "Fredoka", "body": "Nunito", "head_ar": "Baloo Bhaijaan 2", "body_ar": "Rubik", "head_weight": 600},
    {"id": "luxury", "moods": ["luxury", "elegant", "premium", "fashion", "beauty", "refined", "sophisticated", "jewelry"],
     "head": "Playfair Display", "body": "DM Sans", "head_ar": "El Messiri", "body_ar": "Almarai", "head_weight": 600},
    {"id": "editorial", "moods": ["editorial", "intellectual", "culture", "literary", "media", "thoughtful", "calm"],
     "head": "Fraunces", "body": "Source Serif 4", "head_ar": "Noto Naskh Arabic", "body_ar": "Noto Naskh Arabic", "head_weight": 600},
    {"id": "heritage", "moods": ["heritage", "traditional", "authentic", "craft", "cultural", "classic", "oriental"],
     "head": "Cormorant Garamond", "body": "Lora", "head_ar": "Aref Ruqaa", "body_ar": "Amiri", "head_weight": 700},
    {"id": "bold", "moods": ["bold", "energetic", "sport", "loud", "fitness", "street", "powerful", "dynamic"],
     "head": "Archivo", "body": "Inter", "head_ar": "Changa", "body_ar": "Cairo", "head_weight": 800},
    {"id": "geometric", "moods": ["geometric", "architecture", "minimal", "design", "precise", "structured"],
     "head": "Outfit", "body": "DM Sans", "head_ar": "Reem Kufi", "body_ar": "Almarai", "head_weight": 600},
    {"id": "creative", "moods": ["creative", "artsy", "agency", "experimental", "youthful", "edgy"],
     "head": "Syne", "body": "DM Sans", "head_ar": "Noto Kufi Arabic", "body_ar": "Readex Pro", "head_weight": 700},
    {"id": "news", "moods": ["news", "impact", "urgent", "media", "headline", "youtube"],
     "head": "Anton", "body": "Inter", "head_ar": "Lalezar", "body_ar": "Cairo", "head_weight": 400},
    {"id": "egypt-classic", "moods": ["egyptian", "local", "arabic-first", "cairo", "popular", "everyday"],
     "head": "Montserrat", "body": "Inter", "head_ar": "Cairo", "body_ar": "Cairo", "head_weight": 800},
]

UA = "curl/8"  # plain UA → Google serves one full static TTF per weight (no unicode-range subsets)


def fonts_dir() -> Path:
    p = cache_dir() / "fonts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def fslug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_META: dict | None = None


def metadata(refresh: bool = False) -> dict:
    """{family: {"weights": [...], "italic": bool, "category": str, "subsets": [...], "popularity": int}}."""
    global _META
    if _META is not None and not refresh:
        return _META
    cache = fonts_dir() / "metadata.json"
    raw = None
    if cache.exists() and not refresh and time.time() - cache.stat().st_mtime < 60 * 86400:
        try:
            raw = json.loads(cache.read_text())
        except Exception:
            raw = None
    if raw is None:
        try:
            full = json.loads(_get(META_URL, 60).decode("utf-8"))
            raw = {}
            for f in full.get("familyMetadataList", []):
                ws = sorted({int(k.rstrip("i")) for k in f.get("fonts", {}) if k.rstrip("i").isdigit()})
                raw[f["family"]] = {"weights": ws, "italic": any(k.endswith("i") for k in f.get("fonts", {})),
                                    "category": f.get("category", ""), "subsets": f.get("subsets", []),
                                    "popularity": f.get("popularity", 9999)}
            cache.write_text(json.dumps(raw))
        except Exception:
            raw = None
    if raw is None:  # offline, never fetched: curated list only
        raw = {k: {"weights": v["w"], "italic": False, "category": v["kind"],
                   "subsets": ["arabic", "latin"] if "ar" in v else ["latin"], "popularity": 1}
               for k, v in CURATED.items()}
    _META = raw
    return raw


def family_info(family: str) -> dict:
    m = metadata()
    if family in m:
        return m[family]
    low = {k.lower(): k for k in m}
    if family.lower() in low:
        return m[low[family.lower()]]
    raise ToolError(f"font family {family!r} is not on Google Fonts",
                    "use design_fonts to search, e.g. {\"query\": \"kufi\"} — names are case-sensitive like 'IBM Plex Sans Arabic'")


def canonical(family: str) -> str:
    m = metadata()
    if family in m:
        return family
    for k in m:
        if k.lower() == family.lower():
            return k
    return family


def nearest_weight(family: str, want: int) -> int:
    ws = family_info(family)["weights"] or [400]
    return min(ws, key=lambda w: (abs(w - want), -w))


def is_arabic_family(family: str) -> bool:
    try:
        return "arabic" in family_info(family).get("subsets", [])
    except ToolError:
        return False


def ar_scale(family: str) -> float:
    return CURATED.get(family, {}).get("ar", 1.0)


def ensure(family: str, weights: list[int] | tuple[int, ...] = (400, 700)) -> dict[int, Path]:
    """Download (once) the static TTFs for these weights → {weight: path}. Unavailable weights map to
    the nearest existing weight. Raises ToolError when offline and not cached."""
    family = canonical(family)
    d = fonts_dir() / fslug(family)
    d.mkdir(parents=True, exist_ok=True)
    want = sorted({nearest_weight(family, int(w)) for w in weights})
    have = {w: d / f"{fslug(family)}-{w}.ttf" for w in want}
    missing = [w for w, p in have.items() if not p.exists() or p.stat().st_size < 1000]
    if missing:
        url = API + urllib.parse.quote_plus(family) + ":wght@" + ";".join(str(w) for w in missing) + "&display=swap"
        try:
            css = _get(url).decode("utf-8")
        except Exception as e:
            raise ToolError(f"could not download font {family!r} ({e})",
                            "check the internet connection once — fonts are cached forever after the first download")
        for block in re.findall(r"@font-face\s*{(.*?)}", css, re.S):
            wm = re.search(r"font-weight:\s*(\d+)", block)
            um = re.search(r"url\((https://[^)]+)\)", block)
            if not (wm and um):
                continue
            w = int(wm.group(1))
            if w in have and not have[w].exists():
                tmp = have[w].with_suffix(".part")
                tmp.write_bytes(_get(um.group(1), 60))
                tmp.rename(have[w])
    out = {w: p for w, p in have.items() if p.exists()}
    if not out:
        raise ToolError(f"font {family!r}: no files downloaded")
    # map every requested weight to its nearest downloaded file
    return {int(w): out[min(out, key=lambda x: abs(x - int(w)))] for w in weights} | out


def role_css(role: str, latin: str, arabic: str | None, weights: list[int], url_for, ar_adjust: float | None = None) -> str:
    """@font-face rules for a combined role family (e.g. 'sf-head'): Latin face for all code points,
    Arabic face (declared later → wins) only for Arabic code points. url_for(path) → CSS url."""
    rules = []
    lat = ensure(latin, weights)
    ar = ensure(arabic, weights) if arabic and canonical(arabic) != canonical(latin) else None
    adj = ar_adjust if ar_adjust is not None else (ar_scale(canonical(arabic)) if arabic else 1.0)
    for w in sorted(set(weights)):
        rules.append(f"@font-face{{font-family:'{role}';font-weight:{w};font-style:normal;font-display:block;"
                     f"src:url('{url_for(lat[w])}') format('truetype');}}")
        if ar:
            rules.append(f"@font-face{{font-family:'{role}';font-weight:{w};font-style:normal;font-display:block;"
                         f"src:url('{url_for(ar[w])}') format('truetype');unicode-range:{AR_RANGE};"
                         f"size-adjust:{adj * 100:.0f}%;}}")
    return "\n".join(rules)


def copy_into(paths: list[Path], dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for p in paths:
        t = dest_dir / p.name
        if not t.exists():
            shutil.copy2(p, t)


def pair_for(moods: list[str] | str) -> dict:
    """Best curated Arabic+Latin pairing for a list of mood words."""
    if isinstance(moods, str):
        moods = re.split(r"[,\s/]+", moods)
    words = {m.strip().lower() for m in moods if m.strip()}
    best, score = PAIRS[0], -1
    for p in PAIRS:
        s = sum(1 for m in p["moods"] if m in words) * 2 + sum(1 for m in p["moods"] for w in words if len(w) > 3 and (w in m or m in w))
        if s > score:
            best, score = p, s
    return dict(best)


def search(query: str = "", arabic: bool | None = None, category: str = "", limit: int = 30) -> list[dict]:
    m = metadata()
    q = query.lower().strip()
    rows = []
    for fam, info in m.items():
        if arabic is True and "arabic" not in info.get("subsets", []):
            continue
        if category and category.lower() not in (info.get("category", "") or "").lower():
            continue
        cur = CURATED.get(fam, {})
        hay = (fam + " " + info.get("category", "") + " " + cur.get("note", "") + " " + cur.get("kind", "")).lower()
        if q and q not in hay:
            continue
        rows.append({"family": fam, "category": info.get("category"), "weights": info.get("weights"),
                     "arabic": "arabic" in info.get("subsets", []), "curated": bool(cur), "note": cur.get("note", ""),
                     "popularity": info.get("popularity", 9999)})
    rows.sort(key=lambda r: (not r["curated"], r["popularity"]))
    return rows[:limit]
