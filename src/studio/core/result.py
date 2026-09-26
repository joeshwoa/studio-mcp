"""The one return shape every tool uses, for both MCP and the CLI."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Result:
    summary: str
    files: list[str] = field(default_factory=list)
    previews: list[str] = field(default_factory=list)  # PNG/contact sheets the agent should LOOK at
    warnings: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    ok: bool = True

    def add_file(self, p: str | Path) -> "Result":
        self.files.append(str(p))
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "summary": self.summary, "files": self.files, "previews": self.previews,
            "warnings": self.warnings, "next_steps": self.next_steps, "data": self.data,
        }

    def to_text(self) -> str:
        lines = [self.summary]
        if self.files:
            lines += ["", "Files:"] + [f"- {f}" for f in self.files]
        if self.previews:
            lines += ["", "Look at before presenting:"] + [f"- {f}" for f in self.previews]
        if self.warnings:
            lines += ["", "Warnings:"] + [f"- {w}" for w in self.warnings]
        if self.next_steps:
            lines += ["", "Next steps:"] + [f"- {s}" for s in self.next_steps]
        if self.data:
            lines += ["", "```json", json.dumps(self.data, ensure_ascii=False, indent=1, default=str), "```"]
        return "\n".join(lines)


class ToolError(Exception):
    """A user-facing failure: bad input, missing program, refused action."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class MissingTool(ToolError):
    """An external program or Python package is not installed."""
