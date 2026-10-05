"""API keys the user created for free services — read from the environment first, then
STUDIO_HOME/keys.json (a flat {"NAME": "value"} file the user edits by hand).

Rules: values are never printed, logged, returned by a tool or written by us. The only
thing we ever write is the *template* (empty values for names that are missing), so the
user just pastes keys in. `key_status()` reports set/unset, never values."""
from __future__ import annotations

import json
import os
from pathlib import Path

README = ("Paste your free API keys between the quotes, save, done. Environment variables with the same "
          "names win over this file. Never share this file.")


def keys_file() -> Path:
    from ..config import studio_home
    return studio_home() / "keys.json"


def _load() -> dict:
    try:
        f = keys_file()
        if f.exists():
            j = json.loads(f.read_text(encoding="utf-8") or "{}")
            return j if isinstance(j, dict) else {}
    except Exception:
        pass
    return {}


def get_key(*names: str) -> str:
    """First non-empty value among `names`: environment variables first, then keys.json."""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    j = _load()
    for n in names:
        v = j.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def key_source(name: str) -> str:
    """'env' | 'keys.json' | '' — where a key would be read from (never the value)."""
    if os.environ.get(name, "").strip():
        return "env"
    v = _load().get(name)
    return "keys.json" if isinstance(v, str) and v.strip() else ""


def key_status(names: list[str] | tuple[str, ...]) -> dict[str, str]:
    """name → 'set (env)' | 'set (keys.json)' | 'unset'. Never includes a value."""
    out = {}
    for n in names:
        s = key_source(n)
        out[n] = f"set ({s})" if s else "unset"
    return out


def ensure_keys_template(names: list[str] | tuple[str, ...]) -> tuple[Path, list[str]]:
    """Make sure STUDIO_HOME/keys.json exists and lists every name (missing ones get "").
    Existing values are never touched; an unreadable (hand-broken) file is left alone.
    The file is chmod 600. Returns (path, names that were added)."""
    f = keys_file()
    data: dict = {}
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8") or "{}")
            if not isinstance(data, dict):
                return f, []
        except Exception:
            return f, []   # broken JSON the user is editing: do not clobber it
    added = [n for n in names if n not in data]
    if added or not f.exists():
        if "_readme" not in data:
            data = {"_readme": README, **data}
        for n in added:
            data[n] = ""
        tmp = f.with_suffix(".json.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, f)
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return f, added
