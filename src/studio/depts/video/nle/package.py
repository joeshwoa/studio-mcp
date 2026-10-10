"""Build the portable project folder:

<Name> — Project/
  Exports/      the rendered master (.mp4) + Stems/ (dialogue, music, full mix WAVs)
  Media/        Footage · Stills · Audio · Graphics (rendered alpha + native images/plates) · Generated ·
                LUTs (one .cube per graded clip) · Captions (.srt .vtt .ass)
  Fonts/        every font the graphics and captions use (install them once)
  Projects/     Premiere/  AfterEffects/  Resolve/  FinalCut/  CapCut/  Avid-ProTools/  Kdenlive-Shotcut/  Interchange/
  OPEN-IN.md    how to open it in each app (and what each app gets)
  timeline.json the studio timeline (re-render or change it with video_edit)
  relink.py     after moving the folder to another disk/computer: python3 relink.py  (rewrites every project's paths)
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .. import _common as C
from .. import timeline as TL
from . import APP_NAMES, APPS
from . import doc as D

FOLDERS = ["Exports/Stems", "Media/Footage", "Media/Stills", "Media/Audio", "Media/Graphics", "Media/Generated", "Media/LUTs",
           "Media/Captions", "Fonts", "Projects"]


def host_mapper(host_map: dict | str | None):
    """Local path → the path the user's apps will see (e.g. a VM mount → /Volumes/SSD/…)."""
    pairs: list[tuple[str, str]] = []
    env = os.environ.get("STUDIO_HOST_PATHMAP", "")
    if isinstance(host_map, str):
        env = host_map + (";" + env if env else "")
        host_map = None
    for part in env.split(";"):
        if "=" in part:
            a, b = part.split("=", 1)
            pairs.append((a.rstrip("/"), b.rstrip("/")))
    for a, b in (host_map or {}).items():
        pairs.append((str(a).rstrip("/"), str(b).rstrip("/")))
    pairs.sort(key=lambda p: -len(p[0]))

    def f(p) -> str:
        s = str(Path(p).resolve())
        for a, b in pairs:
            if s == a or s.startswith(a + "/"):
                return b + s[len(a):]
        return s
    return f


def _safe(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]+", "-", s).strip()
    return s[:80] or "edit"


def _put(src: Path, dest_dir: Path, mode: str, used: set[str]) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem, ext = src.stem, src.suffix
    name = f"{stem}{ext}"
    k = 2
    while name.lower() in used or ((dest_dir / name).exists() and (dest_dir / name).stat().st_size != src.stat().st_size):
        name = f"{stem}-{k}{ext}"
        k += 1
    used.add(name.lower())
    dest = dest_dir / name
    if dest.exists():
        return dest
    if mode == "reference":
        return src
    try:
        os.link(src, dest)            # same disk: instant, no extra space
    except OSError:
        shutil.copy2(src, dest)
    return dest


def build(tl: TL.Timeline, *, master: Path, name: str, side: dict, out_dir: Path, apps=None, media: str = "copy",
          host_map=None, make_mogrts: bool = False, spec: dict | None = None, work: Path | None = None) -> dict:
    """→ {"dir", "files", "checks": {app: [issues]}, "notes", "preview"}"""
    apps = [a for a in (apps or APPS) if a in APPS]
    root = out_dir / f"{_safe(name)} — Project"
    for f in FOLDERS:
        (root / f).mkdir(parents=True, exist_ok=True)
    host = host_mapper(host_map)
    work = work or C.scratch("pkg-")
    notes: list[str] = []
    checks: dict[str, list[str]] = {}
    files: list[str] = []
    used: set[str] = set()

    # ── native graphics from the motion pages
    gfx_specs = {}
    from . import gfx as G
    gdir = root / "Media" / "Graphics" / "Native"
    sources = [(f"clip{n}", c.meta, c.dur, c.src) for n, c in enumerate(tl.clips) if c.meta.get("html")]
    sources += [(f"ov{k}", o.meta, o.dur, o.src) for k, o in enumerate(tl.overlays) if o.meta.get("html")]
    gfx_checks = []
    for key, meta, dur, rendered in sources:
        if meta.get("tool") not in NATIVE_OK:
            continue          # stateful animations (CTA clicks, counters, countdowns, reveals) stay as rendered clips
        try:
            fonts = _fonts_for(meta.get("args") or {})
            label = _gfx_label(meta)
            spec_g = G.extract(Path(meta["html"]), tl.W, tl.H, dur, gdir, fonts, label)
            why = G.unsafe(spec_g)
            if why:
                notes.append(f"{label}: kept as the rendered graphic ({why})")
                continue
            gfx_specs[key] = spec_g
            pv = G.preview(spec_g, rendered, gdir / f"check-{G._slug(label)}.png")
            gfx_checks.append((label, pv))
        except Exception as e:
            notes.append(f"{meta.get('tool')}: native rebuild skipped ({str(e).splitlines()[0][:140]}) — the rendered graphic is used")
    side = {**side, "gfx": gfx_specs}
    d = D.build(tl, name=name, side=side)
    d["file_name"] = _safe(name)
    notes += d["notes"]

    # ── generated media: backdrops, colour cards, graphic plates/images
    for lane in d["video"]:
        for it in lane["items"]:
            if it.get("kind") == "backdrop":
                p = _backdrop(Path(it["src"]), it, tl, root / "Media" / "Generated", len(used))
                mid = f"mb{it['id']}"
                d["media"][mid] = {"src": str(p), "kind": "video", "name": p.name, "w": tl.W, "h": tl.H, "dur": it["rec_out"] - it["rec_in"] + 0.0,
                                   "fps": tl.fps, "has_video": True, "has_audio": False, "generated": True}
                it.update({"mid": mid, "src_in": 0.0, "speed": 1.0, "kind": "video", "name": f"Blurred fill · {Path(it['src']).name}"})
            elif it.get("kind") == "color":
                p = root / "Media" / "Generated" / f"colour-{it['color'].lstrip('#').upper()}-{tl.W}x{tl.H}.png"
                if not p.exists():
                    from PIL import Image
                    Image.new("RGB", (tl.W, tl.H), it["color"]).save(p)
                mid = f"mc{it['color'].lstrip('#')}"
                d["media"][mid] = {"src": str(p), "kind": "image", "name": p.name, "w": tl.W, "h": tl.H, "dur": 0.0,
                                   "has_video": True, "has_audio": False, "generated": True}
                it.update({"mid": mid, "kind": "image"})
    for gid, g in d["graphics"].items():
        if g.get("plate"):
            mid = f"mp{gid}"
            d["media"][mid] = {"src": g["plate"]["png"], "kind": "image", "name": Path(g["plate"]["png"]).name,
                               "w": int(g["plate"]["box"][2]), "h": int(g["plate"]["box"][3]), "dur": 0.0, "has_video": True,
                               "has_audio": False, "alpha": True, "generated": True}
            g["plate_mid"] = mid
        for lay in g["layers"]:
            if lay["type"] == "image" and lay.get("png"):
                mid = f"mi{gid}{lay['z']}"
                from PIL import Image
                with Image.open(lay["png"]) as im:
                    w_, h_ = im.size
                d["media"][mid] = {"src": lay["png"], "kind": "image", "name": Path(lay["png"]).name, "w": w_, "h": h_, "dur": 0.0,
                                   "has_video": True, "has_audio": False, "alpha": True, "generated": True}
                lay["mid"] = mid

    # ── copy media into the package
    for mid, m in d["media"].items():
        src = Path(m["src"])
        if m.get("generated") and str(src).startswith(str(root)):
            p = src
        else:
            sub = {"audio": "Media/Audio", "image": "Media/Stills", "graphic": "Media/Graphics"}.get(m["kind"], "Media/Footage")
            if m.get("generated"):
                sub = "Media/Graphics" if m["kind"] == "graphic" else "Media/Generated"
            p = _put(src, root / sub, media, used)
        m["path"] = str(p)
        m["host"] = host(p)
        m["rel"] = os.path.relpath(p, root)

    # ── grades → LUTs
    from . import luts as L
    lut_map: dict[str, str] = {}
    for lane in d["video"]:
        for it in lane["items"]:
            g = it.get("grade")
            if not g:
                continue
            grades = [x for x in (g.get("clip"), g.get("programme")) if x]
            try:
                p, dropped = L.bake(grades, root / "Media" / "LUTs", _safe(it["name"])[:30], work / "luts")
            except Exception as e:
                notes.append(f"LUT for {it['name']}: {e}")
                continue
            if p:
                it["lut"] = os.path.relpath(p, root)
                lut_map[it["name"]] = it["lut"]
                if dropped:
                    notes.append(f"{it['name']}: {', '.join(set(dropped))} is not a colour transform — add it in the app (Resolve: Window › Vignette)")

    # ── fonts, captions
    fonts_used = G.collect_fonts(list(gfx_specs.values()), None, root / "Fonts")
    font_host = {f: host(root / "Fonts" / f) for f in fonts_used}
    for g in d["graphics"].values():
        for lay in g["layers"]:
            if lay.get("font_file"):
                lay["font_file"] = font_host.get(Path(lay["font_file"]).name, lay["font_file"])
    subs = []
    cap = d.get("captions")
    if cap:
        cs = G.caption_spec(cap, tl.W, tl.H)
        fonts_used += G.collect_fonts([], cs, root / "Fonts")
        for k in ("font", "font_ar"):
            if cs[k].get("file"):
                cs[k]["file_host"] = host(root / "Fonts" / Path(cs[k]["file"]).name)
        cap["spec"] = cs
        for k in ("srt", "ass"):
            if cap.get(k) and Path(cap[k]).exists():
                p = root / "Media" / "Captions" / f"{_safe(name)}.{k}"
                shutil.copy2(cap[k], p)
                subs.append(p)
        if subs and subs[0].suffix == ".srt":
            vtt = subs[0].with_suffix(".vtt")
            vtt.write_text("WEBVTT\n\n" + re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", subs[0].read_text(encoding="utf-8")),
                           encoding="utf-8")
            subs.append(vtt)

    # ── exports + stems
    mp4 = _put(master, root / "Exports", "copy", set())
    files.append(str(mp4))
    for k, p in (side.get("stems") or {}).items():
        if p and Path(p).exists():
            dst = root / "Exports" / "Stems" / f"{_safe(name)} - {k}.wav"
            shutil.copy2(p, dst)

    # ── writers
    P = root / "Projects"
    fcpxml_path = None
    if "premiere" in apps or "resolve" in apps:
        from . import w_premiere
        (P / "Premiere").mkdir(exist_ok=True)
        pp = w_premiere.write(d, P / "Premiere" / f"{_safe(name)}.xml")
        checks["premiere"] = w_premiere.check(pp, d)
        files.append(str(pp))
    if "fcpx" in apps or "resolve" in apps:
        from . import w_fcpxml
        (P / "FinalCut").mkdir(exist_ok=True)
        fcpxml_path = w_fcpxml.write(d, P / "FinalCut" / f"{_safe(name)}.fcpxml")
        checks["fcpx"] = w_fcpxml.check(fcpxml_path, d)
        files.append(str(fcpxml_path))
    if "aftereffects" in apps:
        from . import w_ae
        jsx = w_ae.write(d, P / "AfterEffects", root, make_mogrts)
        checks["aftereffects"] = w_ae.check(jsx)
        files.append(str(jsx))
    if "resolve" in apps:
        from . import w_resolve
        rd = P / "Resolve"
        rd.mkdir(exist_ok=True)
        r_fcp = rd / f"{_safe(name)}.fcpxml"
        if fcpxml_path:
            shutil.copy2(fcpxml_path, r_fcp)
        r_xml = rd / f"{_safe(name)} (FCP7).xml"
        if (P / "Premiere" / f"{_safe(name)}.xml").exists():
            shutil.copy2(P / "Premiere" / f"{_safe(name)}.xml", r_xml)
        outs = w_resolve.write(d, rd, root, r_fcp, lut_map, [s for s in subs if s.suffix == ".srt"])
        checks["resolve"] = [i for p in outs if p.suffix == ".setting" for i in w_resolve.check_setting(p)]
        files += [str(p) for p in outs]
    if "capcut" in apps:
        from . import w_capcut
        cd = P / "CapCut"
        dd = w_capcut.write(d, cd / _safe(name), _safe(name))
        ins = cd / "Add to CapCut.command"
        ins.write_text(w_capcut.INSTALL.replace("__DRAFT__", _safe(name)), encoding="utf-8")
        ins.chmod(0o755)
        checks["capcut"] = w_capcut.check(dd, d)
        files.append(str(dd / "draft_content.json"))
    if "otio" in apps or "avid" in apps:
        from . import w_otio
        (P / "Interchange").mkdir(exist_ok=True)
        (P / "Avid-ProTools").mkdir(exist_ok=True)
        outs = w_otio.write(d, P / "Interchange" / f"{_safe(name)}.otio",
                            P / "Avid-ProTools" / f"{_safe(name)}.aaf" if "avid" in apps else None)
        checks["otio"] = w_otio.check(outs[0], d)
        if len(outs) > 1:
            checks["avid"] = _aaf_check(outs[1], d)
        elif "avid" in apps:
            checks["avid"] = ["AAF not written — see Avid-ProTools/*.aaf-error.txt"]
        files += [str(p) for p in outs]
    if "edl" in apps:
        from ..pro_interchange import write_edl
        pic = next(l for l in d["video"] if l["role"] == "picture")
        items = [{"kind": d["media"][i["mid"]]["kind"] if d["media"][i["mid"]]["kind"] != "graphic" else "video",
                  "src": d["media"][i["mid"]]["host"], "name": d["media"][i["mid"]]["name"], "rec_in": i["rec_in"], "rec_out": i["rec_out"],
                  "src_in": i["src_in"], "speed": i.get("speed", 1.0), "trans": i.get("trans_in", {}).get("dur", 0.0) if i.get("trans_in") else 0.0,
                  "has_audio": bool(i.get("link"))} for i in pic["items"] if "mid" in i]
        (P / "Interchange").mkdir(exist_ok=True)
        e = write_edl(items, d["fps"], P / "Interchange" / f"{_safe(name)}.edl", _safe(name)[:60], channels="B")
        files.append(str(e))
        checks["edl"] = []
    if "kdenlive" in apps:
        checks["kdenlive"] = _kdenlive(tl, d, side, P / "Kdenlive-Shotcut", _safe(name), root, notes)

    checks["graphics"] = [f"{lb}: native rebuild differs from the render (score {pv['diff']})" for lb, pv in gfx_checks
                          if pv.get("diff") is not None and pv["diff"] > 12]
    # ── guide, manifest, relink
    (root / "timeline.json").write_text(json.dumps(spec or {}, ensure_ascii=False, indent=1), encoding="utf-8")
    from .guide import write_guide
    write_guide(root, d, apps, checks, notes, fonts_used, lut_map, subs, name)
    (root / "relink.py").write_text(RELINK, encoding="utf-8")
    manifest = {"name": name, "apps": apps, "duration": d["duration"], "size": [d["W"], d["H"]], "fps": d["fps"],
                "media": {mid: m["rel"] for mid, m in d["media"].items()}, "host_root": host(root), "built_root": str(root),
                "graphics": len(d["graphics"]), "captions": bool(cap), "luts": lut_map, "checks": checks, "notes": notes}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "edit-document.json").write_text(json.dumps(d, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    prev = _layout(d, root / "Projects" / "tracks.png")
    return {"dir": str(root), "files": files, "checks": checks, "notes": notes, "preview": str(prev), "doc": d,
            "gfx_previews": [pv["preview"] for _, pv in gfx_checks]}


NATIVE_OK = {"motion_title_card", "motion_lower_third"}


def _fonts_for(args: dict):
    from ...motion import apply_brand
    w: list[str] = []
    try:
        _, fonts, _ = apply_brand(args.get("brand", ""), args.get("colors") or {}, args.get("fonts") or {}, w)
    except Exception:
        fonts = args.get("fonts") or {}
    return fonts


def _gfx_label(meta: dict) -> str:
    a = meta.get("args") or {}
    t = meta.get("type") or meta.get("tool", "graphic").replace("motion_", "")
    txt = a.get("name") or a.get("title") or a.get("text") or a.get("kind") or ""
    return (t.replace("_", " ").title() + (f" – {txt}" if txt else ""))[:60]


def _backdrop(src: Path, it: dict, tl: TL.Timeline, dest_dir: Path, n: int) -> Path:
    """The blurred fill behind a fit=blur clip, as its own clip (same look as the render)."""
    W, H = tl.W, tl.H
    span = it["rec_out"] - it["rec_in"]
    dest = dest_dir / f"blurfill-{src.stem[:40]}-{it['src_in']:.2f}-{span:.2f}.mp4"
    if dest.exists():
        return dest
    sp = it.get("speed", 1.0)
    C.ff(["-ss", f"{it['src_in']:.4f}", "-t", f"{span * sp + 0.1:.4f}", "-i", str(src), "-an", "-vf",
          (f"setpts=(PTS-STARTPTS)/{sp:.5f}," if abs(sp - 1) > 1e-6 else "") +
          f"scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},gblur=sigma=12,"
          f"eq=brightness=-0.06:saturation=1.1,scale={W}:{H},fps={TL.fps_str(tl.fps)},format=yuv420p",
          "-t", f"{span:.4f}", *C.v_encode_args(20, "fast"), str(dest)], what="blurred fill")
    return dest


def _aaf_check(p: Path, d: dict) -> list[str]:
    try:
        import aaf2
        with aaf2.open(str(p), "r") as f:
            comps = list(f.content.compositionmobs())
            if not comps:
                return ["AAF has no composition"]
    except ImportError:
        return []
    except Exception as e:
        return [f"AAF does not open: {str(e)[:150]}"]
    return []


def _kdenlive(tl, d, side, dest_dir: Path, name: str, root: Path, notes: list[str]) -> list[str]:
    from .. import kdenlive as KD
    dest_dir.mkdir(parents=True, exist_ok=True)
    kd = dest_dir / f"{name}.kdenlive"
    try:
        KD.write(tl, kd, audio_duck=side.get("duck_regions"))
    except Exception as e:
        return [f"Kdenlive project not written: {e}"]
    txt = kd.read_text(encoding="utf-8")
    for m in d["media"].values():
        if m["src"] != m["path"]:
            txt = txt.replace(m["src"], m["host"])
    kd.write_text(txt, encoding="utf-8")
    shutil.copy2(kd, dest_dir / f"{name} (Shotcut).mlt")
    return []


def _layout(d: dict, dest: Path) -> Path:
    """Track layout picture: every lane, every item, colour by role."""
    from PIL import Image, ImageDraw
    lanes = [(l["name"], l["role"], l["items"]) for l in reversed(d["video"])] + [(l["name"], l["role"], l["items"]) for l in d["audio"]]
    Wd, lh, lw = 1600, 34, 230
    img = Image.new("RGB", (Wd, 40 + lh * len(lanes)), "#15171C")
    dr = ImageDraw.Draw(img)
    col = {"picture": "#4F7CFF", "backdrop": "#3B4A6B", "overlay": "#22B07D", "gfx_render": "#6B5B95", "gfx": "#C04BFF",
           "captions": "#F2B705", "dialog": "#2BB3C0", "broll": "#2B8FC0", "music": "#E0703A", "sfx": "#D8A31A"}
    scale = (Wd - lw - 10) / max(0.01, d["duration"])
    dr.text((10, 10), f"{d['name']} — {d['W']}x{d['H']} {d['fps']:g} fps, {d['duration']:.2f}s", fill="#FFFFFF")
    for i, (nm, role, items) in enumerate(lanes):
        y = 36 + i * lh
        dr.text((10, y + 9), nm[:30], fill="#C9CED8")
        for it in items:
            x0, x1 = lw + it["rec_in"] * scale, lw + it["rec_out"] * scale
            c = col.get(role, "#888888")
            if it.get("enabled") is False:
                c = "#3A3D45"
            dr.rectangle([x0, y + 4, max(x0 + 2, x1 - 1), y + lh - 4], fill=c)
            if x1 - x0 > 40:
                dr.text((x0 + 4, y + 9), str(it.get("name", ""))[: int((x1 - x0) / 7)], fill="#FFFFFF")
    img.save(dest)
    return dest


RELINK = r'''#!/usr/bin/env python3
"""After moving this project folder (another disk, another computer): python3 relink.py
Rewrites the media/font paths inside every project file (Premiere XML, FCPXML, Resolve, CapCut draft,
OTIO, Kdenlive) from the old location to this folder's current location. The After Effects builder needs
no relink (it finds the media next to itself)."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
man = json.load(open(os.path.join(HERE, "manifest.json"), encoding="utf-8"))
old = man["host_root"]
new = HERE
if old == new:
    print("Paths already point here:", new); sys.exit(0)
from urllib.parse import quote
pairs = [(old, new), (quote(old, safe="/:._-~()"), quote(new, safe="/:._-~()"))]
n = 0
for dp, _, fs in os.walk(os.path.join(HERE, "Projects")):
    for f in fs:
        if f.endswith((".xml", ".fcpxml", ".otio", ".kdenlive", ".mlt", ".json", ".setting", ".edl")):
            p = os.path.join(dp, f)
            s = open(p, encoding="utf-8", errors="surrogateescape").read()
            t = s
            for a, b in pairs:
                t = t.replace(a, b)
            if t != s:
                open(p, "w", encoding="utf-8", errors="surrogateescape").write(t); n += 1
man["host_root"] = new
json.dump(man, open(os.path.join(HERE, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"Relinked {n} file(s): {old} → {new}")
'''
