"""Final Cut Pro (and DaVinci Resolve's FCPXML import): FCPXML 1.10 — a library › event › project with
the storyline on the spine, everything else as connected clips (lanes), transitions on the cuts,
speed via timeMap, Transform (position/scale) with Ken Burns keyframes, opacity fades, volume with the
ducking keyframes, the clips' sound as connected audio (J/L cuts), editable titles for every graphic
text and every caption (Basic Title: real text, font, size, colour, position) over a transparent
"plate" PNG carrying the shapes, and the rendered graphics on a disabled lane as a backup."""
from __future__ import annotations

from pathlib import Path

from . import doc as D
from .common import clip_kf, file_url, fr, hex_rgb, x

CROSS_DISSOLVE = "FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"
BASIC_TITLE = ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"
TITLE_POS_KEY = "9999/999166631/999166633/1/100/101"
TITLE_ALIGN_KEY = "9999/999166631/999166633/2/354/999169573/401"


class _T:
    def __init__(self, d: dict):
        num, den = d["rate"]
        self.num, self.den = num, den
        self.fps = num / den

    def s(self, t: float) -> str:
        f = fr(t, self.fps)
        if f == 0:
            return "0s"
        n, dd = f * self.den, self.num
        from math import gcd
        g = gcd(n, dd)
        n, dd = n // g, dd // g
        return f"{n}s" if dd == 1 else f"{n}/{dd}s"


def write(d: dict, dest: Path, for_resolve: bool = False) -> Path:
    T = _T(d)
    W, H = d["W"], d["H"]
    res: list[str] = []
    rid = [1]

    def new_id() -> str:
        rid[0] += 1
        return f"r{rid[0]}"
    fmt_id = "r1"
    res.append(f'  <format id="{fmt_id}" name="FFVideoFormatRateUndefined" frameDuration="{T.den}/{T.num}s" width="{W}" height="{H}" '
               f'colorSpace="1-1-1 (Rec. 709)"/>')
    assets: dict[str, str] = {}
    fmts: dict[tuple, str] = {}

    def asset(mid: str) -> str:
        if mid in assets:
            return assets[mid]
        m = d["media"][mid]
        aid = new_id()
        attrs = f'id="{aid}" name="{x(m["name"])}" uid="{x(mid)}" start="0s"'
        if m["kind"] == "image":
            key = (m.get("w"), m.get("h"), "still")
            if key not in fmts:
                fmts[key] = new_id()
                res.append(f'  <format id="{fmts[key]}" name="FFVideoFormatRateUndefined" width="{m.get("w")}" height="{m.get("h")}" colorSpace="1-13-1"/>')
            attrs += f' duration="0s" hasVideo="1" format="{fmts[key]}" videoSources="1"'
        else:
            dur = T.s(m.get("dur") or 0)
            attrs += f' duration="{dur}"'
            if m.get("has_video") and m["kind"] != "audio":
                key = (m.get("w"), m.get("h"), round(m.get("fps") or d["fps"], 3))
                if key not in fmts:
                    fmts[key] = new_id()
                    fps = m.get("fps") or d["fps"]
                    from fractions import Fraction
                    fdur = Fraction(1 / fps).limit_denominator(100000) if fps else Fraction(T.den, T.num)
                    for nominal in (24, 30, 60):
                        if abs(fps - nominal * 1000 / 1001) < 0.005:
                            fdur = Fraction(1001, nominal * 1000)
                    res.append(f'  <format id="{fmts[key]}" name="FFVideoFormatRateUndefined" frameDuration="{fdur.numerator}/{fdur.denominator}s" '
                               f'width="{m.get("w")}" height="{m.get("h")}" colorSpace="1-1-1 (Rec. 709)"/>')
                attrs += f' hasVideo="1" format="{fmts[key]}" videoSources="1"'
            if m.get("has_audio"):
                attrs += f' hasAudio="1" audioSources="1" audioChannels="{int(m.get("channels") or 2)}" audioRate="{int(m.get("rate") or 48000)}"'
        res.append(f'  <asset {attrs}>\n   <media-rep kind="original-media" src="{x(file_url(m["host"]))}"/>\n  </asset>')
        assets[mid] = aid
        return aid
    fx_ids = {}

    def effect(name: str, uid: str) -> str:
        if name not in fx_ids:
            fx_ids[name] = new_id()
            res.append(f'  <effect id="{fx_ids[name]}" name="{x(name)}" uid="{x(uid)}"/>')
        return fx_ids[name]

    # ── spine = the storyline (abutted, transitions on the cuts)
    pic = next(l for l in d["video"] if l["role"] == "picture")
    spine_items = D.abutted([i for i in pic["items"]])
    spine: list[dict] = []
    t = 0.0
    for it in spine_items:
        if it["rec_in"] > t + 1e-3:
            spine.append({"gap": True, "rec_in": t, "rec_out": it["rec_in"]})
        spine.append(it)
        t = it["rec_out"]
    if t < d["duration"] - 1e-3:
        spine.append({"gap": True, "rec_in": t, "rec_out": d["duration"]})
    # each spine element gets its connected children
    children: dict[int, list[str]] = {i: [] for i in range(len(spine))}

    def host_of(t0: float) -> int:
        for i, e in enumerate(spine):
            if e["rec_in"] - 1e-6 <= t0 < e["rec_out"] - 1e-6:
                return i
        return len(spine) - 1

    def local(e: dict, t0: float) -> str:
        """Programme time → the parent's local time (its start + elapsed)."""
        start = 0.0 if e.get("gap") else _start(e)
        return T.s(start + (t0 - e["rec_in"]) * (1.0 if e.get("gap") else e.get("speed", 1.0)))

    def _start(e):
        m = d["media"].get(e.get("mid"), {})
        return 0.0 if m.get("kind") == "image" else e["src_in"]

    def transform(it: dict, m: dict, ind: str) -> str:
        sw, sh = (m.get("w") or W), (m.get("h") or H)
        fit = min(W / sw, H / sh)

        def vals(b):
            sc = b[2] / (sw * fit)
            cx, cy = b[0] + b[2] / 2 - W / 2, b[1] + b[3] / 2 - H / 2
            return f"{cx / H * 100:.4f} {-cy / H * 100:.4f}", f"{sc:.5f} {sc:.5f}"
        kf = it.get("kf", {}).get("box")
        p0, s0 = vals(kf[0][1] if kf else it["box"])
        if not kf and p0 == "0.0000 -0.0000" or (not kf and p0 in ("0.0000 0.0000", "-0.0000 -0.0000") and s0 == "1.00000 1.00000"):
            return ""
        out = f'{ind}<adjust-transform position="{p0}" scale="{s0}">\n'
        if kf:
            base = _start(it)
            span = it["rec_out"] - it["rec_in"]
            ks = clip_kf(kf, 0.0, span)
            out += f'{ind} <param name="position">\n{ind}  <keyframeAnimation>\n'
            out += "".join(f'{ind}   <keyframe time="{T.s(base + tt)}" value="{vals(b)[0]}"/>\n' for tt, b in ks)
            out += f'{ind}  </keyframeAnimation>\n{ind} </param>\n{ind} <param name="scale">\n{ind}  <keyframeAnimation>\n'
            out += "".join(f'{ind}   <keyframe time="{T.s(base + tt)}" value="{vals(b)[1]}"/>\n' for tt, b in ks)
            out += f"{ind}  </keyframeAnimation>\n{ind} </param>\n"
        return out + f"{ind}</adjust-transform>\n"

    def opacity(it: dict, ind: str) -> str:
        kf = it.get("kf", {}).get("opacity")
        op = it.get("opacity", 1.0)
        if not kf and op >= 0.999:
            return ""
        s = f'{ind}<adjust-blend amount="{op:.3f}">\n'
        if kf:
            base = _start(it) if it.get("mid") else 0.0
            s += f'{ind} <param name="amount">\n{ind}  <keyframeAnimation>\n'
            s += "".join(f'{ind}   <keyframe time="{T.s(base + tt)}" value="{v:.3f}"/>\n'
                         for tt, v in clip_kf(kf, 0.0, it["rec_out"] - it["rec_in"]))
            s += f"{ind}  </keyframeAnimation>\n{ind} </param>\n"
        return s + f"{ind}</adjust-blend>\n"

    def volume(a: dict, ind: str) -> str:
        span = a["rec_out"] - a["rec_in"]
        base = a.get("gain_db", 0.0)
        pts = list(a.get("kf") or [])
        if a.get("fade_in"):
            pts += [(0.0, -96.0), (min(a["fade_in"], span), base)]
        if a.get("fade_out"):
            pts += [(max(0.0, span - a["fade_out"]), base), (span, -96.0)]
        if not pts and abs(base) < 0.01:
            return ""
        s = f'{ind}<adjust-volume amount="{base:.2f}dB">\n'
        if pts:
            st = a["src_in"]
            s += f'{ind} <param name="amount">\n{ind}  <keyframeAnimation>\n'
            seen = set()
            for tt, v in sorted(pts):
                k = T.s(st + tt * a.get("speed", 1.0))
                if k in seen:
                    continue
                seen.add(k)
                s += f'{ind}   <keyframe time="{k}" value="{v:.2f}dB"/>\n'
            s += f"{ind}  </keyframeAnimation>\n{ind} </param>\n"
        return s + f"{ind}</adjust-volume>\n"

    def timemap(it: dict, ind: str) -> str:
        sp = it.get("speed", 1.0)
        if abs(sp - 1) < 1e-6:
            return ""
        dur = it["rec_out"] - it["rec_in"]
        st = it["src_in"]
        return (f'{ind}<timeMap>\n{ind} <timept time="{T.s(st)}" value="{T.s(st)}" interp="linear"/>\n'
                f'{ind} <timept time="{T.s(st + dur)}" value="{T.s(st + dur * sp)}" interp="linear"/>\n{ind}</timeMap>\n')

    def clip_xml(it: dict, ind: str, lane: int | None = None, offset: str = "", audio_only: bool = False, video_only: bool = False,
                 kids: str = "") -> str:
        m = d["media"][it["mid"]]
        aid = asset(it["mid"])
        dur = T.s(it["rec_out"] - it["rec_in"])
        en = "" if it.get("enabled", True) else ' enabled="0"'
        ln = f' lane="{lane}"' if lane is not None else ""
        off = offset or T.s(it["rec_in"])
        if m["kind"] == "image":
            return (f'{ind}<video ref="{aid}"{ln} offset="{off}" name="{x(it["name"])}" start="0s" duration="{dur}"{en}>\n'
                    + transform(it, m, ind + " ") + opacity(it, ind + " ") + kids + f"{ind}</video>\n")
        src = ' srcEnable="audio"' if audio_only else (' srcEnable="video"' if video_only and m.get("has_audio") else "")
        body = timemap(it, ind + " ")
        if audio_only:
            body += volume(it, ind + " ")
        else:
            body += transform(it, m, ind + " ") + opacity(it, ind + " ")
        start = T.s(it["src_in"])
        return (f'{ind}<asset-clip ref="{aid}"{ln} offset="{off}" name="{x(it["name"])}" start="{start}" duration="{dur}"'
                f' tcFormat="NDF"{src}{en}>\n' + body + kids + f"{ind}</asset-clip>\n")

    def title_xml(text: str, ind: str, lane: int, off: str, dur: str, name: str, font: str, face: str, size: float,
                  color: str, cx: float, cy: float, align: str, ts_id: str, stroke=None, enabled=True) -> str:
        r, g, b = hex_rgb(color)
        tid = effect("Basic Title", BASIC_TITLE)
        al = {"left": "left", "right": "right", "start": "left", "end": "right"}.get(align, "center")
        extra = ""
        if stroke:
            extra = f' strokeColor="0 0 0 1" strokeWidth="{stroke:.1f}"'
        dis = "" if enabled else ' enabled="0"'
        return (f'{ind}<title ref="{tid}" lane="{lane}" offset="{off}" name="{x(name)}" start="3600s" duration="{dur}"'
                f'{dis}>\n'
                f'{ind} <param name="Position" key="{TITLE_POS_KEY}" value="{cx:.1f} {cy:.1f}"/>\n'
                f'{ind} <text>\n{ind}  <text-style ref="{ts_id}">{x(text)}</text-style>\n{ind} </text>\n'
                f'{ind} <text-style-def id="{ts_id}">\n{ind}  <text-style font="{x(font)}" fontSize="{size:.1f}" fontFace="{x(face)}"'
                f' fontColor="{r:.4f} {g:.4f} {b:.4f} 1" alignment="{al}"{extra}/>\n{ind} </text-style-def>\n{ind}</title>\n')

    # connected items, by lane number
    lane_no = 0
    ts_n = [0]
    picture_seen = False
    for lane in d["video"]:
        if lane["role"] == "picture":
            picture_seen = True
            continue
        if lane["role"] == "backdrop":
            ln = -1     # below the storyline
        else:
            lane_no += 1
            ln = lane_no
        if lane["role"] == "gfx":
            for it in lane["items"]:
                g = d["graphics"][it["gfx"]]
                h = host_of(it["rec_in"])
                base_lane = ln
                if g.get("plate_mid"):
                    p_it = {"mid": g["plate_mid"], "name": f"{g['name']} · shapes", "rec_in": it["rec_in"], "rec_out": it["rec_out"],
                            "src_in": 0.0, "box": g["plate"]["box"], "kf": {}, "opacity": 1.0}
                    children[h].append(clip_xml(p_it, "      ", base_lane, local(spine[h], it["rec_in"])))
                for k, lay in enumerate([l for l in g["layers"] if l["type"] == "text"]):
                    ts_n[0] += 1
                    bx = lay["box"]
                    cx, cy = bx[0] + bx[2] / 2 - W / 2, -(bx[1] + bx[3] / 2 - H / 2)
                    children[h].append(title_xml(lay["text"], "      ", base_lane + 1 + k, local(spine[h], it["rec_in"]),
                                                 T.s(it["rec_out"] - it["rec_in"]), lay["text"][:40], lay.get("family", "Helvetica"),
                                                 lay.get("style", "Regular"), lay["size"], lay["color"], cx, cy, lay.get("align", "center"),
                                                 f"ts{ts_n[0]}"))
                lane_no = max(lane_no, base_lane + 1 + len(g["layers"]))
            continue
        if lane["role"] == "captions":
            cs = d["captions"]["spec"]
            for it in lane["items"]:
                h = host_of(it["rec_in"])
                rtl = any("؀" <= ch <= "ۿ" for ch in it["text"])
                f = cs["font_ar"] if rtl else cs["font"]
                text = it["text"] if (rtl or not cs["uppercase"]) else it["text"].upper()
                cy = H / 2 - cs["anchor_y"] + (cs["size"] * 0.6 if cs["valign"] == "bottom" else 0)
                ts_n[0] += 1
                children[h].append(title_xml(text, "      ", ln, local(spine[h], it["rec_in"]), T.s(it["rec_out"] - it["rec_in"]),
                                             "Caption", f.get("family", "Helvetica"), f.get("style", "Bold"),
                                             cs["size_ar"] if rtl else cs["size"], cs["color"], 0.0, cy, "center", f"ts{ts_n[0]}",
                                             stroke=cs["outline"]))
            continue
        for it in lane["items"]:
            if "mid" not in it:
                continue
            h = host_of(it["rec_in"])
            children[h].append(clip_xml(it, "      ", ln, local(spine[h], it["rec_in"]),
                                        video_only=True))
    # audio lanes → negative lanes
    for k, lane in enumerate(d["audio"], start=1):
        for a in lane["items"]:
            h = host_of(a["rec_in"])
            children[h].append(clip_xml(a, "      ", -k - (1 if any(l["role"] == "backdrop" for l in d["video"]) else 0),
                                        local(spine[h], a["rec_in"]), audio_only=True))

    body = []
    for i, e in enumerate(spine):
        if e.get("trans_in") and i > 0 and not spine[i - 1].get("gap"):
            tr = e["trans_in"]
            tid = effect("Cross Dissolve", CROSS_DISSOLVE)
            body.append(f'     <transition name="Cross Dissolve" offset="{T.s(e["rec_in"] - tr["dur"] / 2)}" duration="{T.s(tr["dur"])}">\n'
                        f'      <filter-video ref="{tid}" name="Cross Dissolve"/>\n     </transition>\n')
        kids = "".join(children[i])
        if e.get("gap"):
            body.append(f'     <gap name="Gap" offset="{T.s(e["rec_in"])}" start="0s" duration="{T.s(e["rec_out"] - e["rec_in"])}">\n'
                        + kids + "     </gap>\n")
            continue
        body.append(clip_xml(e, "     ", None, video_only=True, kids=kids))
    fades = ""
    fi, fo = d.get("fades", {}).get("in") or 0, d.get("fades", {}).get("out") or 0
    if fi or fo:
        tid = effect("Cross Dissolve", CROSS_DISSOLVE)
        if fi:
            body.insert(0, f'     <transition name="Fade In" offset="0s" duration="{T.s(fi)}">\n      <filter-video ref="{tid}" name="Cross Dissolve"/>\n     </transition>\n')
        if fo:
            body.append(f'     <transition name="Fade Out" offset="{T.s(d["duration"] - fo)}" duration="{T.s(fo)}">\n      <filter-video ref="{tid}" name="Cross Dissolve"/>\n     </transition>\n')
    seq = (f'    <sequence format="{fmt_id}" duration="{T.s(d["duration"])}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">\n'
           f"     <spine>\n" + "".join(body) + "     </spine>\n    </sequence>\n")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10">\n <resources>\n' + "\n".join(res) +
           f'\n </resources>\n <library>\n  <event name="{x(d["name"])}">\n   <project name="{x(d["name"])}">\n' + seq +
           "   </project>\n  </event>\n </library>\n</fcpxml>\n")
    dest.write_text(xml, encoding="utf-8")
    return dest


def check(path: Path, d: dict) -> list[str]:
    import xml.etree.ElementTree as ET
    issues = []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return [f"FCPXML does not parse: {e}"]
    ids = {e.get("id") for e in root.iter() if e.get("id")}
    for e in root.iter():
        r = e.get("ref")
        if r and r not in ids:
            issues.append(f"<{e.tag}> refers to missing resource {r}")
    dtd = _dtd()
    if dtd:
        import subprocess
        r = subprocess.run(["xmllint", "--noout", "--dtdvalid", str(dtd), str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            issues += ["DTD: " + ln for ln in r.stderr.strip().splitlines()[:6]]
    return issues


def _dtd() -> Path | None:
    """Apple's FCPXML 1.10 DTD (cached; fetched once from the CommandPost project) — validation like FCP's own."""
    import shutil
    if not shutil.which("xmllint"):
        return None
    from ....config import cache_dir
    p = cache_dir() / "dtd" / "FCPXMLv1_10.dtd"
    if p.exists() and p.stat().st_size > 10000:
        return p
    try:
        import urllib.request
        p.parent.mkdir(parents=True, exist_ok=True)
        url = "https://raw.githubusercontent.com/CommandPost/CommandPost/develop/src/extensions/cp/apple/fcpxml/dtd/FCPXMLv1_10.dtd"
        with urllib.request.urlopen(url, timeout=20) as r:
            p.write_bytes(r.read())
        return p if p.stat().st_size > 10000 else None
    except Exception:
        return None
