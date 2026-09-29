"""Background runner for comfyui_setup / mflux_setup (so a 10 GB download never blocks the agent).

    python -m studio.depts.ai.setup_worker <plan.json>
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    from .comfy import execute_plan
    plan = json.loads(open(argv[0], encoding="utf-8").read())
    st = execute_plan(plan)
    return 0 if st.get("state") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
