"""Licence normalisation shared by every stock adapter.

Each item carries: license (short human code), license_url, commercial_ok, modify_ok,
attribution_required, share_alike. CC codes come from Openverse codes, CC URLs, Commons
short names or Freesound's licence names — all funnel through `cc()`."""
from __future__ import annotations

import re

CC_BASE = "https://creativecommons.org/licenses/"


def _flags(code: str) -> dict:
    code = code.lower()
    pd = code in ("cc0", "pdm", "pd")
    return {
        "commercial_ok": "nc" not in code.split("-") and "nc-sampling" not in code,
        "modify_ok": "nd" not in code.split("-"),
        "attribution_required": not pd,
        "share_alike": "sa" in code.split("-"),
    }


def cc(code: str = "", version: str = "", url: str = "") -> dict:
    """Normalise a Creative Commons licence from a code ('by-sa'), Freesound name
    ('Attribution NonCommercial'), Commons short name ('CC BY-SA 4.0') or a CC URL."""
    c = (code or "").strip().lower()
    v = (version or "").strip()
    u = (url or "").strip()
    if u and not c:
        m = re.search(r"creativecommons\.org/licenses/([a-z+\-]+)/?([\d.]+)?", u.lower())
        if m:
            c, v = m.group(1), v or (m.group(2) or "")
        elif "publicdomain/zero" in u.lower():
            c, v = "cc0", v or "1.0"
        elif "publicdomain/mark" in u.lower():
            c, v = "pdm", v or "1.0"
        elif "publicdomain" in u.lower():
            c = "pd"
    names = {"creative commons 0": "cc0", "attribution": "by", "attribution noncommercial": "by-nc",
             "attribution sharealike": "by-sa", "attribution noderivatives": "by-nd",
             "attribution noncommercial sharealike": "by-nc-sa", "sampling+": "sampling+",
             "public domain": "pd", "public domain mark": "pdm", "cc0 1.0": "cc0"}
    if c in names:
        c = names[c]
    m = re.match(r"^cc[\s-]+((?:by|zero|0)[a-z\-\s]*?)\s*([\d.]+)?$", c)
    if m:   # Commons-style "cc by-sa 4.0" / "cc0" / "cc zero"
        body = m.group(1).strip().replace(" ", "-")
        c = "cc0" if body in ("zero", "0") else body
        v = v or (m.group(2) or "")
    if c in ("cc-zero", "cc0-1.0"):
        c = "cc0"
    if c.startswith("public domain") or c in ("pd", "pd-old", "pd-usgov", "pd-self"):
        c = "pd"
    if not c:
        return unknown()
    flags = _flags(c)
    if c == "cc0":
        short, lurl = "CC0", "https://creativecommons.org/publicdomain/zero/1.0/"
    elif c == "pdm":
        short, lurl = "Public Domain Mark", "https://creativecommons.org/publicdomain/mark/1.0/"
    elif c == "pd":
        short, lurl = "Public Domain", u or ""
    elif c in ("sampling+", "nc-sampling+"):
        short, lurl = f"CC {c.upper()} {v or '1.0'}", u or f"{CC_BASE}{c}/{v or '1.0'}/"
        flags.update(commercial_ok=(c == "sampling+"), modify_ok=True)
    elif re.match(r"^by(-nc)?(-nd|-sa)?$", c):
        v = v or "4.0"
        short, lurl = f"CC {c.upper()} {v}", u or f"{CC_BASE}{c}/{v}/"
    else:
        return unknown(c.upper(), u)
    return {"license": short, "license_url": lurl, **flags}


def unknown(name: str = "unknown licence", url: str = "") -> dict:
    """Anything we can't classify is treated as NOT safe (no commercial, attribution required)."""
    return {"license": name or "unknown licence", "license_url": url, "commercial_ok": False, "modify_ok": False,
            "attribution_required": True, "share_alike": False}


def fixed(name: str, url: str, commercial_ok: bool = True, attribution_required: bool = False,
          modify_ok: bool = True) -> dict:
    return {"license": name, "license_url": url, "commercial_ok": commercial_ok, "modify_ok": modify_ok,
            "attribution_required": attribution_required, "share_alike": False}


def spdx(code: str, title: str = "", url: str = "") -> dict:
    """Icon-set licences from Iconify (SPDX ids)."""
    s = (code or "").strip()
    up = s.upper()
    if up.startswith("CC") and ("BY" in up or "0" in up):
        m = re.match(r"CC-?(BY(?:-NC)?(?:-SA|-ND)?|0)-?([\d.]+)?", up)
        if m:
            d = cc("cc0" if m.group(1) == "0" else m.group(1).lower(), m.group(2) or "", url)
            if url:
                d["license_url"] = url
            return d
    gpl = up.startswith(("GPL", "AGPL", "LGPL"))
    return {"license": title or s or "unknown licence", "license_url": url,
            "commercial_ok": bool(s) and "NC" not in up, "modify_ok": bool(s) and "ND" not in up,
            "attribution_required": False, "share_alike": gpl}
