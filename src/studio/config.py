"""Where everything lives.

STUDIO_HOME holds projects (outputs), models (AI weights), cache, brand
kits and logs. Resolution order:
  1. $STUDIO_HOME if set;
  2. the folder remembered in ~/.config/ai-skills/creative-studio.root
     (so outputs never split between two drives);
  3. first run: /Volumes/PortableSSD/creative-studio when that drive is
     mounted (models are big), else ~/AI-Projects/creative-studio — and
     remember it.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT_FILE = Path.home() / ".config" / "ai-skills" / "creative-studio.root"
SSD = Path("/Volumes/PortableSSD")


class StudioHomeUnavailable(RuntimeError):
    pass


def studio_home() -> Path:
    env = os.environ.get("STUDIO_HOME", "").strip()
    if env:
        home = Path(env).expanduser()
    elif ROOT_FILE.exists():
        home = Path(ROOT_FILE.read_text().strip()).expanduser()
        if not home.parent.exists():
            raise StudioHomeUnavailable(
                f"Studio folder {home} is not available (external drive not mounted?). "
                f"Mount it, set STUDIO_HOME, or edit {ROOT_FILE}."
            )
    else:
        home = SSD / "creative-studio" if SSD.is_dir() else Path.home() / "AI-Projects" / "creative-studio"
        try:
            ROOT_FILE.parent.mkdir(parents=True, exist_ok=True)
            ROOT_FILE.write_text(str(home) + "\n")
            print(f"(creative-studio home: {home} — remembered in {ROOT_FILE})", file=sys.stderr)
        except OSError:
            pass
    home.mkdir(parents=True, exist_ok=True)
    return home


def sub(name: str) -> Path:
    p = studio_home() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def models_dir() -> Path:
    return sub("models")


def cache_dir() -> Path:
    return sub("cache")


def brands_dir() -> Path:
    return sub("brands")


def slug(text: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9؀-ۿ]+", "-", text.strip().lower()).strip("-")
    return (s[:maxlen].strip("-") or "untitled")


def output_dir(project: str | None = None, kind: str = "") -> Path:
    """projects/<project>/<kind>/ — project defaults to today's date."""
    proj = slug(project) if project else datetime.now().strftime("%Y-%m-%d")
    p = sub("projects") / proj
    if kind:
        p = p / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


def unique_path(directory: Path, stem: str, ext: str) -> Path:
    """Never overwrite: stem.ext, stem-2.ext, stem-3.ext …"""
    ext = ext if ext.startswith(".") else "." + ext
    p = directory / f"{stem}{ext}"
    n = 2
    while p.exists():
        p = directory / f"{stem}-{n}{ext}"
        n += 1
    return p
