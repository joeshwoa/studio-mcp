"""CLI for agents without MCP (and for humans).

    studio list [dept]                 catalog of tools (name, flags, first line)
    studio help <tool>                 full description + arguments
    studio <tool> '<json-args>'        run a tool → readable report
    studio <tool> @args.json           arguments from a file (long briefs, Arabic, quotes)
    studio --json <tool> ...           machine-readable result
    studio doctor                      what is installed and how to install the rest
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

from .core.registry import TOOLS, call, load_all
from .core.result import ToolError


def _params(fn) -> list[str]:
    out = []
    for p in inspect.signature(fn).parameters.values():
        ann = p.annotation
        t = getattr(ann, "__name__", None) or str(ann).replace("typing.", "")
        req = p.default is inspect.Parameter.empty
        out.append(f"{p.name}: {t}" + ("" if req else f" = {p.default!r}"))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    raw = False
    if argv[:1] == ["--json"]:
        raw, argv = True, argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    load_all()
    cmd = argv[0]
    if cmd == "list":
        dept = argv[1] if len(argv) > 1 else None
        for name, s in sorted(TOOLS.items(), key=lambda kv: (kv[1].dept, kv[0])):
            if dept and s.dept != dept:
                continue
            flags = "".join(f for f, on in (("N", s.network), ("$", s.spends), ("I", s.installs)) if on)
            first = s.description.split("\n")[0]
            print(f"{s.dept:9} {name:28} {flags:3} {first}")
        print("\nflags: N network · $ may spend (needs consent) · I installs (needs consent)")
        return 0
    if cmd == "help":
        s = TOOLS.get(argv[1]) if len(argv) > 1 else None
        if not s:
            print("usage: studio help <tool>  (see studio list)")
            return 2
        print(f"{s.name}  [{s.dept}]\n\n{s.description}\n\nArguments:")
        for line in _params(s.fn):
            print(f"  {line}")
        return 0
    args: dict = {}
    if len(argv) > 1:
        src = argv[1]
        text = Path(src[1:]).read_text(encoding="utf-8") if src.startswith("@") else src
        try:
            args = json.loads(text or "{}")
        except json.JSONDecodeError as e:
            print(f"arguments are not valid JSON: {e}", file=sys.stderr)
            return 2
    if cmd == "doctor":
        cmd = "studio_doctor"
    try:
        res = call(cmd, args)
    except ToolError as e:
        msg = {"ok": False, "error": str(e), "fix": getattr(e, "hint", "")}
        print(json.dumps(msg, ensure_ascii=False, indent=1) if raw else f"ERROR: {e}" + (f"\nFix: {e.hint}" if e.hint else ""),
              file=sys.stderr if not raw else sys.stdout)
        return 1
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=1, default=str) if raw else res.to_text())
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
