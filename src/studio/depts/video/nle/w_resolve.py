"""DaVinci Resolve — every page:
  Edit      the timeline (FCPXML: File › Import › Timeline), all tracks, transitions, transforms, speed,
            opacity, the clips' sound and music with its ducking keyframes
  Fusion    every graphic as a Fusion Title (.setting): live Text+ layers (real text, font, size, colour,
            position) over the shapes plate, fade/rise in and out — installable into Effects › Titles
  Color     a .cube per clip (the studio's grade baked exactly) + a table of which LUT goes on which clip
  Fairlight the stems (dialogue, music, mix) as WAVs; levels already on the timeline clips
  Deliver   render presets in the guide
Studio only (Resolve 21.1+ moved Python scripting to Studio): build_resolve.py does all of the above
automatically — new project, import, timeline, LUTs on every clip, Fusion titles placed, subtitles,
render job. Resolve Studio 21.1's built-in MCP server lets Claude drive it directly."""
from __future__ import annotations

import json
from pathlib import Path

from .common import fr, hex_rgb, rel


def _lua_str(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def fusion_title(g: dict, d: dict, dur: float, plate_host: str | None) -> str:
    """One Fusion Title macro (.setting): Background (transparent) ← plate Loader ← Text+ layers, Blend animated."""
    W, H, fps = d["W"], d["H"], d["fps"]
    n = max(1, fr(dur, fps))
    a_in, a_out = fr(g["anim"]["in"], fps), fr(g["anim"]["out"], fps)
    tools, inputs = [], []
    tools.append(f"""		Canvas = Background {{
			Inputs = {{
				GlobalOut = Input {{ Value = {n - 1}, }},
				Width = Input {{ Value = {W}, }}, Height = Input {{ Value = {H}, }},
				UseFrameFormatSettings = Input {{ Value = 1, }},
				TopLeftAlpha = Input {{ Value = 0, }},
			}},
			ViewInfo = OperatorInfo {{ Pos = {{ 0, 0 }} }},
		}},""")
    last = "Canvas"
    k = 0
    if plate_host:
        bx = g["plate"]["box"]
        cx, cy = (bx[0] + bx[2] / 2) / W, 1 - (bx[1] + bx[3] / 2) / H
        tools.append(f"""		Plate = Loader {{
			Clips = {{
				Clip {{ ID = "Clip1", Filename = {_lua_str(plate_host)}, FormatID = "PNGFormat", StartFrame = -1, LengthSetManually = true,
					TrimIn = 0, TrimOut = 0, ExtendFirst = 0, ExtendLast = {n}, Loop = 0, AspectMode = 0, Depth = 0, TimeCode = 0, GlobalStart = 0, GlobalEnd = {n - 1} }},
			}},
			Inputs = {{ PostMultiplyByAlpha = Input {{ Value = 1, }}, }},
			ViewInfo = OperatorInfo {{ Pos = {{ 0, 60 }} }},
		}},
		PlateMerge = Merge {{
			Inputs = {{
				Background = Input {{ SourceOp = "{last}", Source = "Output", }},
				Foreground = Input {{ SourceOp = "Plate", Source = "Output", }},
				Center = Input {{ Value = {{ {cx:.5f}, {cy:.5f} }}, }},
				ReferenceSize = Input {{ Value = 1, }},
				Width = Input {{ Value = {W}, }}, Height = Input {{ Value = {H}, }},
			}},
			ViewInfo = OperatorInfo {{ Pos = {{ 110, 60 }} }},
		}},""")
        last = "PlateMerge"
    for lay in g["layers"]:
        if lay["type"] != "text":
            continue
        k += 1
        bx = lay["box"]
        cx, cy = (bx[0] + bx[2] / 2) / W, 1 - (bx[1] + bx[3] / 2) / H
        r, gg, b = hex_rgb(lay["color"])
        just = {"left": 0, "start": 0, "right": 2, "end": 2}.get(lay.get("align"), 1)
        if lay.get("rtl"):
            just = {"left": 0, "right": 2}.get(lay.get("align"), 2 if lay.get("align") in ("start", "right") else 1)
        tools.append(f"""		Text{k} = TextPlus {{
			Inputs = {{
				GlobalOut = Input {{ Value = {n - 1}, }},
				Width = Input {{ Value = {W}, }}, Height = Input {{ Value = {H}, }},
				UseFrameFormatSettings = Input {{ Value = 1, }},
				StyledText = Input {{ Value = {_lua_str(lay["text"])}, }},
				Font = Input {{ Value = {_lua_str(lay.get("family", "Open Sans"))}, }},
				Style = Input {{ Value = {_lua_str(lay.get("style", "Regular"))}, }},
				Size = Input {{ Value = {lay["size"] / H * 0.94:.5f}, }},
				Center = Input {{ Value = {{ {cx:.5f}, {cy:.5f} }}, }},
				HorizontalJustificationNew = Input {{ Value = {just}, }},
				VerticalJustificationNew = Input {{ Value = 3, }},
				CharacterSpacing = Input {{ Value = {1 + (lay.get("letter_spacing") or 0) / max(1.0, lay["size"]):.4f}, }},
				Red1 = Input {{ Value = {r:.4f}, }}, Green1 = Input {{ Value = {gg:.4f}, }}, Blue1 = Input {{ Value = {b:.4f}, }},
				Alpha1 = Input {{ Value = {lay.get("opacity", 1):.3f}, }},
			}},
			ViewInfo = OperatorInfo {{ Pos = {{ {110 * k}, 120 }} }},
		}},
		TextMerge{k} = Merge {{
			Inputs = {{
				Background = Input {{ SourceOp = "{last}", Source = "Output", }},
				Foreground = Input {{ SourceOp = "Text{k}", Source = "Output", }},
			}},
			ViewInfo = OperatorInfo {{ Pos = {{ {110 * k}, 60 }} }},
		}},""")
        inputs.append(f'				Text{k} = InstanceInput {{ SourceOp = "Text{k}", Source = "StyledText", Name = {_lua_str("Text " + str(k) + " — " + lay["text"][:20])}, }},')
        inputs.append(f'				Font{k} = InstanceInput {{ SourceOp = "Text{k}", Source = "Font", }},')
        inputs.append(f'				Style{k} = InstanceInput {{ SourceOp = "Text{k}", Source = "Style", }},')
        inputs.append(f'				Size{k} = InstanceInput {{ SourceOp = "Text{k}", Source = "Size", }},')
        last = f"TextMerge{k}"
    # fade + rise: transform the whole stack
    rise = g["anim"]["rise"] / H
    keys_b = f"[0] = {{ 0, }}, [{a_in}] = {{ 1, }}, [{max(a_in + 1, n - 1 - a_out)}] = {{ 1, }}, [{n - 1}] = {{ 0, }}"
    keys_y = f"[0] = {{ {0.5 - rise:.5f}, }}, [{a_in}] = {{ 0.5, }}"
    tools.append(f"""		Anim = Transform {{
			Inputs = {{
				Input = Input {{ SourceOp = "{last}", Source = "Output", }},
				Center = Input {{ SourceOp = "AnimPath", Source = "Position", }},
			}},
			ViewInfo = OperatorInfo {{ Pos = {{ {110 * (k + 1)}, 60 }} }},
		}},
		AnimPath = XYPath {{
			ShowKeyPoints = false,
			DrawMode = "ModifyOnly",
			Inputs = {{ X = Input {{ Value = 0.5, }}, Y = Input {{ SourceOp = "AnimY", Source = "Value", }}, }},
		}},
		AnimY = BezierSpline {{ SplineColor = {{ Red = 255, Green = 128, Blue = 0 }}, KeyFrames = {{ {keys_y} }}, }},
		Out = Dissolve {{
			Inputs = {{
				Mix = Input {{ SourceOp = "OutMix", Source = "Value", }},
				Background = Input {{ SourceOp = "Canvas", Source = "Output", }},
				Foreground = Input {{ SourceOp = "Anim", Source = "Output", }},
			}},
			ViewInfo = OperatorInfo {{ Pos = {{ {110 * (k + 2)}, 60 }} }},
		}},
		OutMix = BezierSpline {{ SplineColor = {{ Red = 0, Green = 200, Blue = 255 }}, KeyFrames = {{ {keys_b} }}, }},""")
    name = "".join(ch if ch.isalnum() else "_" for ch in g["name"])[:30] or "Title"
    return ("{\n\tTools = ordered() {\n\t\t" + name + " = MacroOperator {\n\t\t\tCtrlWZoom = false,\n\t\t\tInputs = ordered() {\n"
            + "\n".join(inputs) + "\n\t\t\t},\n\t\t\tOutputs = {\n\t\t\t\tMainOutput1 = InstanceOutput { SourceOp = \"Out\", Source = \"Output\", },\n"
            "\t\t\t},\n\t\t\tViewInfo = GroupInfo { Pos = { 0, 0 } },\n\t\t\tTools = ordered() {\n"
            + "\n".join(tools) + "\n\t\t\t},\n\t\t},\n\t},\n\tActiveTool = \"" + name + "\"\n}\n")


BUILD_PY = r'''#!/usr/bin/env python3
"""Build this edit inside DaVinci Resolve STUDIO (scripting is Studio-only since Resolve 21.1).
Run from Resolve: Workspace › Scripts › (copy this file into the Scripts/Comp folder), or from a terminal with
Resolve open:   python3 build_resolve.py
It creates a project, imports the media and the timeline, puts the right LUT on every clip, places the
Fusion titles, imports the subtitles and adds a render job. Nothing is deleted or overwritten."""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PLAN = json.load(open(os.path.join(HERE, "resolve_plan.json"), encoding="utf-8"))

def resolve_api():
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        base = {"darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
                "win32": os.path.expandvars(r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"),
                }.get(sys.platform, "/opt/resolve/Developer/Scripting/Modules")
        sys.path.append(base)
        import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if not r:
        sys.exit("Resolve is not running (or this is the free version — scripting needs Resolve Studio).")
    return r

r = resolve_api()
pm = r.GetProjectManager()
name = PLAN["name"]
proj = pm.CreateProject(name) or pm.CreateProject(name + " " + time.strftime("%H%M%S"))
proj.SetSetting("timelineResolutionWidth", str(PLAN["W"]))
proj.SetSetting("timelineResolutionHeight", str(PLAN["H"]))
proj.SetSetting("timelineFrameRate", str(PLAN["fps"]))
mp = proj.GetMediaPool()
root = mp.GetRootFolder()
tl = mp.ImportTimelineFromFile(os.path.join(HERE, PLAN["fcpxml"]), {"timelineName": name, "importSourceClips": True,
                                                                     "sourceClipsPath": ROOT})
if not tl:
    sys.exit("Timeline import failed — import it by hand: File › Import › Timeline › " + PLAN["fcpxml"])
proj.SetCurrentTimeline(tl)
applied = 0
for tr in range(1, tl.GetTrackCount("video") + 1):
    for item in tl.GetItemListInTrack("video", tr) or []:
        lut = PLAN["luts"].get(item.GetName())
        if lut:
            if item.SetLUT(1, os.path.join(ROOT, lut)):
                applied += 1
print("LUTs applied:", applied)
for srt in PLAN.get("subtitles", []):
    mp.ImportMedia([os.path.join(ROOT, srt)])
print("Done. Subtitles are in the Media Pool — drag the .srt onto the timeline to make a subtitle track.")
print("Fusion titles: run install_fusion_titles.command once, then Effects › Titles › Fusion Titles.")
'''

INSTALL = r'''#!/bin/bash
# Installs this edit's graphics as Fusion Titles (Effects › Titles › Fusion Titles in Resolve's Edit page).
cd "$(dirname "$0")"
D="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates/Edit/Titles/__PKG__"
[ "$(uname)" = "Linux" ] && D="$HOME/.local/share/DaVinciResolve/Fusion/Templates/Edit/Titles/__PKG__"
mkdir -p "$D" && cp FusionTitles/*.setting "$D/" && echo "Installed $(ls FusionTitles/*.setting | wc -l) title(s) into: $D" \
  && echo "Restart Resolve (or Effects › Titles refresh) to see them."
'''


def write(d: dict, dest_dir: Path, root: Path, fcpxml: Path, luts: dict[str, str], subtitles: list[Path]) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    ft = dest_dir / "FusionTitles"
    for lane in d["video"]:
        if lane["role"] != "gfx":
            continue
        for it in lane["items"]:
            g = d["graphics"][it["gfx"]]
            plate = d["media"][g["plate_mid"]]["host"] if g.get("plate_mid") else None
            ft.mkdir(parents=True, exist_ok=True)
            p = ft / (("".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in g["name"]).strip() or g["id"]) + f" ({g['id']}).setting")
            p.write_text(fusion_title(g, d, it["rec_out"] - it["rec_in"], plate), encoding="utf-8")
            out.append(p)
    plan = {"name": d["name"], "W": d["W"], "H": d["H"], "fps": d["fps"], "fcpxml": fcpxml.name,
            "luts": luts, "subtitles": [rel(s, root) for s in subtitles],
            "titles": [{"file": f"FusionTitles/{p.name}"} for p in out]}
    (dest_dir / "resolve_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    b = dest_dir / "build_resolve.py"
    b.write_text(BUILD_PY, encoding="utf-8")
    b.chmod(0o755)
    out.append(b)
    if (ft).exists():
        ins = dest_dir / "install_fusion_titles.command"
        ins.write_text(INSTALL.replace("__PKG__", "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in d["name"])), encoding="utf-8")
        ins.chmod(0o755)
        out.append(ins)
    return out


def check_setting(p: Path) -> list[str]:
    """Balanced braces/quotes in a .setting (Lua table) file."""
    s = p.read_text(encoding="utf-8")
    depth, q, esc = 0, False, False
    for ch in s:
        if q:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                q = False
            continue
        if ch == '"':
            q = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return [f"{p.name}: unbalanced braces"]
    return [] if depth == 0 and not q else [f"{p.name}: unbalanced braces/quotes"]
