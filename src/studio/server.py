"""MCP server: every registered studio tool, over stdio.

    studio-mcp            # run the server (clients: Claude, Cursor, Antigravity…)
"""
from __future__ import annotations

import functools
import inspect

from mcp.server.fastmcp import FastMCP

from .core.registry import load_all
from .core.result import Result, ToolError

INSTRUCTIONS = """creative-studio: a free, local creative team — graphic design & layout,
branding, vector (Illustrator-like), photo editing (Photoshop-like, layered PSD),
video editing (Premiere-like, editable Kdenlive projects), motion graphics,
AI image/video generation (local or free services) and audio (voice, music,
clean-up, captions). Every tool returns file paths plus PREVIEW images — look
at the previews before presenting results. Tools flagged SPENDS or INSTALLS
need the user's explicit OK. Start with `studio_doctor` to see what is installed."""


def _wrap(fn):
    @functools.wraps(fn)
    def inner(*a, **kw):
        try:
            res = fn(*a, **kw)
        except ToolError as e:
            msg = f"ERROR: {e}"
            if getattr(e, "hint", ""):
                msg += f"\nFix: {e.hint}"
            return msg
        return res.to_text() if isinstance(res, Result) else str(res)
    inner.__signature__ = inspect.signature(fn).replace(return_annotation=str)
    inner.__annotations__ = {**getattr(fn, "__annotations__", {}), "return": str}
    return inner


def build() -> FastMCP:
    mcp = FastMCP("creative-studio", instructions=INSTRUCTIONS)
    for spec in load_all().values():
        mcp.add_tool(_wrap(spec.fn), name=spec.name, description=spec.description)
    return mcp


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
