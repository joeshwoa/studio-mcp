# Conventions for department modules

- One module (or package) per department under `src/studio/depts/`. Declare tools with
  `@tool("<dept>", network=…, spends=…, installs=…)` from `studio.core.registry`.
  Tool names are global: prefix nothing, but make them specific (`photo_remove_background`
  style names are fine; check `studio list` for clashes).
- Parameters: plain JSON-able types (str, int, float, bool, list[str], dict) with defaults.
  First paragraph of the docstring = when to use it + what it returns (agents read this).
- Return `studio.core.result.Result`: summary, files (absolute paths), previews (PNGs/contact
  sheets the agent must LOOK at), warnings, next_steps, data.
- Raise `ToolError` (user-facing message + hint) / `MissingTool` via `deps.need(key)` —
  never a bare traceback. Add new external deps to `core/deps.py` DEPS with brew/apt/pip hints.
- Outputs: `config.output_dir(project, kind)` + `config.unique_path()` — never overwrite.
  Every tool takes `project: str = ""` (empty = today's date folder) and optional `out: str = ""`.
- Quality: measure and preview every output with `core.qc` (image_preview, contact_sheet,
  loudness, probe). Put findings in warnings. Nothing is "done" without a preview/metric.
- Honesty: say in the summary what is synthetic/placeholder/approximate.
- Editable masters: whenever a free desktop app exists, also write its native editable file
  (PSD/XCF for GIMP, SVG/PDF for Inkscape, .kdenlive for Kdenlive, .blend for Blender).
- Tests: `tests/test_<dept>.py` with pytest; mark heavy ones `@pytest.mark.slow`; skip cleanly
  when an external program is missing (`pytest.importorskip` / `shutil.which`).
