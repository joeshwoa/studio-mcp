"""Font resolution shared by the photo and vector departments.

`find_font(family, weight)` returns a real font FILE for Pillow (and makes sure
GIMP/Inkscape/rsvg can see the same family by name). Order:
  1. a path given directly;
  2. fonts already cached in STUDIO_HOME/fonts;
  3. fonts installed on the system (fontconfig on Linux/macOS, plus the macOS
     font folders scanned by name);
  4. a free download from Google Fonts (network), cached in STUDIO_HOME/fonts and
     copied into the user's font folder so desktop apps render it too;
  5. a system fallback (with a warning) — never a crash.

Arabic: Pillow is built with libraqm here, so shaping/RTL is correct as long as
the font has Arabic glyphs. `default_family(text)` picks Cairo (Arabic + Latin)
when the text contains Arabic.
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from ...config import sub

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
IS_MAC = platform.system() == "Darwin"

WEIGHT_NAMES = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
                600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
_FC_WEIGHT = {100: 0, 200: 40, 300: 50, 400: 80, 500: 100, 600: 180, 700: 200, 800: 205, 900: 210}

_cache: dict[tuple, tuple[Path, list[str]]] = {}


def has_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text or ""))


def default_family(text: str = "") -> str:
    return "Cairo" if has_arabic(text) else "Inter"


def fonts_dir() -> Path:
    return sub("fonts")


def user_font_dir() -> Path:
    return Path.home() / ("Library/Fonts" if IS_MAC else ".local/share/fonts/studio")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def weight_value(w) -> int:
    if isinstance(w, str):
        s = _norm(w)
        for k, v in WEIGHT_NAMES.items():
            if _norm(v) == s:
                return k
        if s in ("normal", "book"):
            return 400
        if s.isdigit():
            w = int(s)
        else:
            return 400
    w = int(w or 400)
    return min(WEIGHT_NAMES, key=lambda k: abs(k - w))


def _fc_match(family: str, weight: int, italic: bool, exact: bool = False) -> Path | None:
    fc = shutil.which("fc-match")
    if not fc:
        return None
    pat = f"{family}:weight={_FC_WEIGHT[weight]}" + (":slant=100" if italic else "")
    try:
        r = subprocess.run([fc, "-f", "%{family}\t%{weight}\t%{file}", pat], capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    parts = r.stdout.split("\t")
    if len(parts) < 3:
        return None
    fam, wt, file = parts[0], parts[1], parts[2]
    if not (file and any(_norm(f) == _norm(family) for f in fam.split(","))):
        return None
    if exact and "[" not in wt:  # variable fonts report a range like [0 210]
        try:
            if abs(float(wt) - _FC_WEIGHT[weight]) > 2:
                return None
        except ValueError:
            pass
    return Path(file)


def _scan_dirs(family: str, weight: int, italic: bool, exact: bool = False) -> Path | None:
    dirs = [fonts_dir(), user_font_dir()]
    if IS_MAC:
        dirs += [Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
    fam = _norm(family)
    wname = _norm(WEIGHT_NAMES[weight])
    best = None
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            n = _norm(p.stem)
            if not n.startswith(fam):
                continue
            rest = n[len(fam):]
            if italic != ("italic" in rest):
                continue
            rest = rest.replace("italic", "")
            if rest == wname or (weight == 400 and rest in ("", "regular")):
                return p
            if rest in ("variable", "vf") or rest.startswith("wght"):
                return p
            if best is None and rest in ("", "regular"):
                best = p
    return None if exact else best


def _google_download(family: str, weight: int, italic: bool) -> Path | None:
    fam_q = urllib.parse.quote(family)
    ital = "1" if italic else "0"
    urls = [f"https://fonts.googleapis.com/css2?family={fam_q}:ital,wght@{ital},{weight}",
            f"https://fonts.googleapis.com/css2?family={fam_q}:wght@{weight}",
            f"https://fonts.googleapis.com/css2?family={fam_q}"]
    css = ""
    for u in urls:
        try:
            # an old-style UA makes the API answer with plain TTF links
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 6.1) studio-mcp"})
            with urllib.request.urlopen(req, timeout=20) as r:
                css = r.read().decode("utf-8", "replace")
            if "url(" in css:
                break
        except Exception:
            continue
    m = re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+\.(?:ttf|otf))\)", css)
    if not m:
        return None
    dest = fonts_dir() / f"{family.replace(' ', '')}-{WEIGHT_NAMES[weight]}{'Italic' if italic else ''}{Path(m[-1]).suffix}"
    try:
        with urllib.request.urlopen(m[-1], timeout=60) as r:
            dest.write_bytes(r.read())
    except Exception:
        return None
    # make the family visible to GIMP / Inkscape / rsvg by name as well
    try:
        ud = user_font_dir()
        ud.mkdir(parents=True, exist_ok=True)
        tgt = ud / dest.name
        if not tgt.exists():
            shutil.copy2(dest, tgt)
        if shutil.which("fc-cache"):
            subprocess.run(["fc-cache", "-f", str(ud)], capture_output=True, timeout=60)
    except Exception:
        pass
    return dest


def find_font(family: str = "", weight=400, italic: bool = False, text: str = "",
              allow_download: bool = True) -> tuple[Path, list[str]]:
    """(font file, warnings). Never raises for a missing family — falls back and warns."""
    family = (family or "").strip() or default_family(text)
    w = weight_value(weight)
    key = (family, w, italic, has_arabic(text), allow_download)
    if key in _cache:
        return _cache[key]
    warns: list[str] = []
    p = Path(family).expanduser()
    if p.suffix.lower() in (".ttf", ".otf", ".ttc") and p.exists():
        res = (p, warns)
        _cache[key] = res
        return res
    found = _scan_dirs(family, w, italic, exact=True) or _fc_match(family, w, italic, exact=True)
    if not found and allow_download:
        found = _google_download(family, w, italic)
        if found:
            warns.append(f"downloaded free font '{family}' {WEIGHT_NAMES[w]} from Google Fonts → {found}")
    if not found:
        found = _scan_dirs(family, w, italic) or _fc_match(family, w, italic)
        if found:
            warns.append(f"'{family}' {WEIGHT_NAMES[w]} not available — used {Path(found).name}")
    if not found:
        fb = default_family(text)
        if _norm(fb) != _norm(family):
            found, w2 = find_font(fb, w, italic, text, allow_download)
            warns += w2
            warns.append(f"font '{family}' not found — used {fb} instead")
        else:
            for cand in ("DejaVu Sans", "Arial", "Helvetica", "Noto Sans"):
                found = _fc_match(cand, w, False) or _scan_dirs(cand, w, False)
                if found:
                    break
            warns.append(f"font '{family}' not available — used system fallback {found}")
    if not found:
        from ...core.result import ToolError
        raise ToolError(f"no usable font for '{family}'", "install a TTF/OTF or pass a font file path")
    if has_arabic(text) and not font_has_arabic(found):
        f2, w2 = find_font("Cairo", w, False, text, allow_download)
        warns += w2 + [f"'{family}' has no Arabic glyphs — used Cairo for this text"]
        found = f2
    res = (Path(found), warns)
    _cache[key] = res
    return res


def font_has_arabic(path: Path) -> bool:
    try:
        from PIL import ImageFont
        f = ImageFont.truetype(str(path), 40)
        # a font without Arabic renders the test letter as .notdef (same box as another missing glyph)
        a = f.getmask("ب").getbbox()
        b = f.getmask("￿").getbbox()
        return a is not None and a != b
    except Exception:
        return True


def family_name(path: Path) -> str:
    """The family name stored in a font file (for GIMP/Inkscape which select by name)."""
    try:
        from PIL import ImageFont
        return ImageFont.truetype(str(path), 12).getname()[0]
    except Exception:
        return path.stem.split("-")[0]


def pil_font(family: str = "", size: int = 48, weight=400, italic: bool = False, text: str = ""):
    """(ImageFont with raqm layout, warnings, font path)."""
    from PIL import ImageFont, features
    path, warns = find_font(family, weight, italic, text)
    engine = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    if engine != ImageFont.Layout.RAQM and has_arabic(text):
        warns.append("Pillow lacks libraqm: Arabic will not be shaped correctly (brew install libraqm)")
    f = ImageFont.truetype(str(path), int(size), layout_engine=engine)
    try:  # variable fonts: pick the requested weight axis
        axes = f.get_variation_axes()
        if axes:
            vals = []
            for a in axes:
                nm = a.get("name", b"")
                nm = nm.decode() if isinstance(nm, bytes) else str(nm)
                if nm.lower().startswith("weight") or nm.lower() == "wght":
                    vals.append(max(a["minimum"], min(a["maximum"], weight_value(weight))))
                else:
                    vals.append(a.get("default", a["minimum"]))
            f.set_variation_by_axes(vals)
    except Exception:
        pass
    return f, warns, path
