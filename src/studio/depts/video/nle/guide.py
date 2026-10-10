"""OPEN-IN.md — how to open the package in each app, what each app gets, and the checks that ran."""
from __future__ import annotations

from pathlib import Path

from . import APP_NAMES

STEPS = {
    "premiere": """**Adobe Premiere Pro** — `Projects/Premiere/{n}.xml`
1. Premiere › New Project (or open yours) › **File › Import…** › pick `{n}.xml` → a bin with the sequence and all media.
2. Open the sequence. Every track is there: picture, B-roll/PiP, graphics, dialogue, music (with the ducking keyframes), SFX.
3. Captions: **File › Import** `Media/Captions/{n}.srt`, drag it onto the timeline → a native caption track (Window › Text › Captions to restyle).
4. Editable graphics: right-click a graphic clip › *Replace With After Effects Composition* and pick the matching `GFX · …` comp from the After Effects project — or, when the package was built with MOGRTs, drag `Projects/Premiere/MOGRT/*.mogrt` from the Essential Graphics panel.
5. Colour: on a clip, Lumetri Color › Creative › Look › Browse › the clip's LUT in `Media/LUTs` (table below).
6. Save the project (File › Save As › `Projects/Premiere/{n}.prproj`).""",
    "aftereffects": """**Adobe After Effects** — `Projects/AfterEffects/build_project.jsx`
1. Install the fonts in `Fonts/` (double-click › Install) so the text uses the right typeface.
2. After Effects › **File › Scripts › Run Script File…** › `build_project.jsx` (first time: Settings › Scripting & Expressions › *Allow Scripts to Write Files* ON).
3. It imports the media into folders, builds the main comp `{n}` (every clip, move, fade, transition, sound with its levels), one `GFX · …` precomp per graphic with **live text and shape layers**, a `Captions` precomp with live caption text — and saves `{n}.aep` next to the script.
4. The rendered graphics are kept under the editable ones, switched off (eye icon) — turn one on to compare.""",
    "resolve": """**DaVinci Resolve** — `Projects/Resolve/`
1. **Edit page:** File › Import › Timeline… › `{n}.fcpxml` (keep *Automatically import source clips* ON). The FCP7 `.xml` also works (Import › Timeline).
2. **Fusion titles:** double-click `install_fusion_titles.command` once → Effects › Titles › *Fusion Titles* has every graphic of this edit with live Text+ (font, size, colour, position, text) — drag each onto the graphics track at its place (or open `FusionTitles/*.setting` in the Fusion page: File › Import › Setting).
3. **Color page:** apply each clip's LUT (table below) — right-click the clip › LUT › (add `Media/LUTs` in Project Settings › Color Management › Open LUT Folder).
4. **Fairlight:** the timeline's audio clips carry the levels and ducking; the stems are in `Exports/Stems`.
5. **Subtitles:** File › Import › Subtitle… › `Media/Captions/{n}.srt`.
6. **Resolve Studio:** run `build_resolve.py` (Workspace › Scripts, or `python3 build_resolve.py` with Resolve open) — it does steps 1, 3 and 5 for you. Studio 21.1+ also has a built-in MCP server so Claude can drive Resolve directly.""",
    "fcpx": """**Final Cut Pro** — `Projects/FinalCut/{n}.fcpxml`
1. Install the fonts in `Fonts/`.
2. Final Cut › **File › Import › XML…** › `{n}.fcpxml` → a library event with the project.
3. Storyline + connected clips, transitions, transforms with keyframes, volume/ducking keyframes, **every graphic text and caption as an editable Basic Title** over its shapes plate. Rendered graphics sit on a disabled lane as backup.
4. Colour: Effects › Color › *Custom LUT* › the clip's LUT.""",
    "capcut": """**CapCut (desktop)** — `Projects/CapCut/`
1. Quit CapCut. Double-click `Add to CapCut.command` (it copies the draft into CapCut's projects folder and registers it).
2. Open CapCut › the project `{n}` is at the top of the list.
3. Main track with dissolves and the framing/Ken Burns keyframes, overlay tracks, each graphic as a shapes image + **live text** (edit text, font, colour), live captions, music with volume keyframes. Transitions download from CapCut's library the first time (needs internet).""",
    "avid": """**Avid Media Composer / Pro Tools / Logic** — `Projects/Avid-ProTools/{n}.aaf`
Media Composer: File › Import › the AAF (link to the media in `Media/`). Pro Tools: File › Import › Session Data › the AAF. Picture edit + audio tracks; graphics come as the rendered clips.""",
    "kdenlive": """**Kdenlive / Shotcut** — `Projects/Kdenlive-Shotcut/{n}.kdenlive` (Shotcut: the `.mlt`)
Open it — it was checked by rendering it headless and comparing with the MP4.""",
    "otio": """**OpenTimelineIO** — `Projects/Interchange/{n}.otio` — Resolve (Import › Timeline), Kdenlive 23+, Nuke Studio/Hiero, RV, and any OTIO adapter.""",
    "edl": """**Any NLE (EDL)** — `Projects/Interchange/{n}.edl` — the storyline as CMX3600 (cuts, dissolves, V + A).""",
}


def write_guide(root: Path, d: dict, apps: list[str], checks: dict, notes: list[str], fonts: list[str], luts: dict,
                subs: list[Path], name: str) -> Path:
    n = root.name.replace(" — Project", "")
    lines = [f"# {name} — editable project for every pro app", "",
             f"{d['W']}×{d['H']} · {d['fps']:g} fps · {d['duration']:.2f} s · "
             f"{sum(len(l['items']) for l in d['video'])} picture items on {len(d['video'])} video tracks · "
             f"{sum(len(l['items']) for l in d['audio'])} audio clips on {len(d['audio'])} tracks · "
             f"{len(d['graphics'])} editable graphic(s){' · captions' if d.get('captions') else ''}", "",
             "The rendered video is `Exports/" + n + ".mp4`. Everything below rebuilds THAT edit in the app, so you can change "
             "any cut, move, word, colour or level and export again.", "",
             "**Keep the folder together** (media is inside). Moved it to another disk or Mac? Run `python3 relink.py` once.", ""]
    if fonts:
        lines += ["## Fonts (install once)", "Double-click each file in `Fonts/` › Install: " + ", ".join(fonts), ""]
    lines += ["## Open it in…", ""]
    for a in apps:
        if a in STEPS:
            lines += [STEPS[a].format(n=n), ""]
    if luts:
        lines += ["## Colour — LUT per clip", "| Clip | LUT |", "|---|---|"] + [f"| {k} | `{v}` |" for k, v in luts.items()] + [""]
    lines += ["## Tracks", "| Track | Role | Items |", "|---|---|---|"]
    for l in d["video"]:
        lines.append(f"| {l['name']} | {l['role']} | {len(l['items'])} |")
    for l in d["audio"]:
        lines.append(f"| {l['name']} | {l['role']} | {len(l['items'])} |")
    lines += ["", "## Checks that ran"]
    for a in apps:
        iss = checks.get(a)
        lines.append(f"- {APP_NAMES.get(a, a)}: " + ("✅ OK" if iss == [] else ("⚠ " + "; ".join(iss[:4]) if iss else "—")))
    if "graphics" in checks:
        g = checks["graphics"]
        lines.append("- Editable graphics vs the render (Media/Graphics/Native/check-*.png): " + ("✅ match" if not g else "⚠ " + "; ".join(g)))
    if notes:
        lines += ["", "## Notes"] + [f"- {x}" for x in dict.fromkeys(notes)]
    p = root / "OPEN-IN.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
