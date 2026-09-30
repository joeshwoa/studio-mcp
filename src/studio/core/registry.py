"""One registry feeds both the MCP server and the CLI.

A department module declares tools with @tool. Parameters come from the
function signature (type hints + defaults); the docstring's first paragraph
becomes the description. Flags that matter to the user are declared, and
appended to the description so every client sees them:

  network=True   calls the internet (free services or the user's keys)
  spends=True    can spend money / paid quota → needs explicit user consent
  installs=True  installs software → needs explicit user consent
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass
from typing import Callable

from .result import Result, ToolError


@dataclass
class ToolSpec:
    name: str
    dept: str
    fn: Callable[..., Result]
    description: str
    network: bool = False
    spends: bool = False
    installs: bool = False


TOOLS: dict[str, ToolSpec] = {}


def tool(dept: str, *, network: bool = False, spends: bool = False, installs: bool = False, name: str | None = None):
    def wrap(fn: Callable[..., Result]):
        doc = inspect.getdoc(fn) or ""
        desc = doc.strip()
        flags = []
        if network:
            flags.append("NETWORK: uses the internet")
        if spends:
            flags.append("SPENDS: may use paid credits/quota — only with the user's explicit OK")
        if installs:
            flags.append("INSTALLS software — only with the user's explicit OK")
        if flags:
            desc += "\n\n" + " · ".join(flags)
        tname = name or fn.__name__
        if tname in TOOLS:
            raise ValueError(f"duplicate tool name {tname}")
        TOOLS[tname] = ToolSpec(tname, dept, fn, desc, network, spends, installs)
        return fn
    return wrap


_loaded = False
LOAD_ERRORS: dict[str, str] = {}   # module → why it could not be imported (shown by studio_doctor)


def load_all() -> dict[str, ToolSpec]:
    """Import every department module (studio.depts.*) so their @tool calls run."""
    global _loaded
    if not _loaded:
        from studio import depts
        for m in pkgutil.walk_packages(depts.__path__, depts.__name__ + ".", onerror=lambda n: LOAD_ERRORS.setdefault(n, "import failed")):
            try:
                importlib.import_module(m.name)
            except ImportError as e:  # an optional extra is missing: keep every other department working
                LOAD_ERRORS[m.name] = f"{type(e).__name__}: {e}"
        _loaded = True
    return TOOLS


def call(name: str, args: dict) -> Result:
    load_all()
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name!r}", "run `studio list` for the catalog")
    spec = TOOLS[name]
    sig = inspect.signature(spec.fn)
    unknown = set(args) - set(sig.parameters)
    if unknown:
        raise ToolError(f"{name}: unknown argument(s) {sorted(unknown)}; expected {list(sig.parameters)}")
    try:
        bound = sig.bind(**args)
    except TypeError as e:
        raise ToolError(f"{name}: {e}")
    return spec.fn(*bound.args, **bound.kwargs)
