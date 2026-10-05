"""Delivery QC (what a post house runs before a file goes out): picture faults (black, frozen, flashes,
letterboxing), sound faults (clipping, loudness, true peak, silence gaps), stream/format compliance
for the target platform, caption safe zones, file size/length limits. One PASS/WARN/FAIL table."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ...core.deps import need
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _pro as P

# Platform delivery specs (sizes from the studio presets; limits are the platforms' published upload
# limits as of 2025 and change — treat as guidance). safe = UI-free zone as fractions (top, bottom, left, right).
PLATFORMS = {
    "reels":    {"size": (1080, 1920), "fps": (23, 60), "max_s": 180, "max_mb": 4000, "lufs": -14, "tp": -1.0, "safe": (0.12, 0.20, 0.05, 0.12), "label": "Instagram Reels"},
    "tiktok":   {"size": (1080, 1920), "fps": (23, 60), "max_s": 600, "max_mb": 287, "lufs": -14, "tp": -1.0, "safe": (0.08, 0.20, 0.05, 0.14), "label": "TikTok"},
    "shorts":   {"size": (1080, 1920), "fps": (23, 60), "max_s": 180, "max_mb": 4000, "lufs": -14, "tp": -1.0, "safe": (0.12, 0.20, 0.05, 0.12), "label": "YouTube Shorts"},
    "youtube":  {"size": (1920, 1080), "fps": (23, 60), "max_s": 43200, "max_mb": 256000, "lufs": -14, "tp": -1.0, "safe": (0.05, 0.05, 0.05, 0.05), "label": "YouTube"},
    "linkedin": {"size": (1920, 1080), "fps": (10, 60), "max_s": 900, "max_mb": 5000, "lufs": -14, "tp": -1.0, "safe": (0.05, 0.05, 0.05, 0.05), "label": "LinkedIn"},
    "instagram_feed": {"size": (1080, 1350), "fps": (23, 60), "max_s": 3600, "max_mb": 4000, "lufs": -14, "tp": -1.0, "safe": (0.06, 0.10, 0.05, 0.05), "label": "Instagram feed"},
    "x":        {"size": (1280, 720), "fps": (1, 60), "max_s": 140, "max_mb": 512, "lufs": -14, "tp": -1.0, "safe": (0.05, 0.08, 0.05, 0.05), "label": "X / Twitter"},
    "whatsapp": {"size": (1280, 720), "fps": (1, 60), "max_s": 0, "max_mb": 16, "lufs": -14, "tp": -1.0, "safe": (0.05, 0.05, 0.05, 0.05), "label": "WhatsApp (status/inline ≈16 MB)"},
    "broadcast": {"size": (1920, 1080), "fps": (24, 60), "max_s": 0, "max_mb": 0, "lufs": -23, "tp": -1.0, "safe": (0.05, 0.05, 0.05, 0.05), "label": "Broadcast EBU R128"},
}
ALIASES = {"instagram": "reels", "ig": "reels", "youtube_shorts": "shorts", "yt": "youtube", "twitter": "x", "ebu": "broadcast", "tv": "broadcast"}


def _row(check, status, value="", detail=""):
    return {"check": check, "status": status, "value": value, "detail": detail}


def picture_scan(p: Path, info: dict) -> dict:
    """One low-res decode: per-frame luma (whole + 4 quadrants, linear light), frame differences."""
    W, H = 64, 36
    lum, quad, diff = [], [], []
    prev = None
    for f in P.frames(p, W, H):
        x = f.astype(np.float32) / 255
        lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
        Y = lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        lum.append(float(Y.mean()))
        quad.append([float(Y[:H // 2, :W // 2].mean()), float(Y[:H // 2, W // 2:].mean()),
                     float(Y[H // 2:, :W // 2].mean()), float(Y[H // 2:, W // 2:].mean())])
        g = (x.mean(axis=2) * 255)
        diff.append(float(np.abs(g - prev).mean()) if prev is not None else 99.0)
        prev = g
    n = len(lum)
    if not n:
        raise ToolError(f"could not decode {p.name}")
    fps = n / info["duration"] if info["duration"] else (info.get("fps") or 30)
    return {"lum": np.array(lum), "quad": np.array(quad), "diff": np.array(diff), "fps": fps}


def runs(mask: np.ndarray, fps: float, min_s: float) -> list[tuple[float, float]]:
    out, s = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and s is None:
            s = i
        elif not m and s is not None:
            if (i - s) / fps >= min_s:
                out.append((round(s / fps, 3), round(i / fps, 3)))
            s = None
    return out


def flashes(sig: np.ndarray, fps: float, delta: float = 0.1, dark: float = 0.8) -> tuple[int, float, list[float]]:
    """ITU-R BT.1702 / WCAG 2.3.1 style: a flash = a pair of opposing luminance changes ≥ delta (≈20 cd/m²
    on a 200 cd/m² display) where the darker state is below `dark`. Returns (max flashes in any 1 s
    window, time of that window, flash times)."""
    # turning points with hysteresis of `delta`
    ext = []
    lo_i = hi_i = 0
    direction = 0
    for i in range(1, sig.size):
        if direction >= 0:
            if sig[i] > sig[hi_i]:
                hi_i = i
            if sig[hi_i] - sig[i] >= delta:
                ext.append(hi_i)
                direction, lo_i = -1, i
        if direction <= 0:
            if sig[i] < sig[lo_i]:
                lo_i = i
            if sig[i] - sig[lo_i] >= delta:
                ext.append(lo_i)
                direction, hi_i = 1, i
    ext = sorted(set(ext))
    trans = []   # each transition between consecutive extrema that is big enough
    for a, b in zip(ext, ext[1:]):
        if abs(sig[b] - sig[a]) >= delta and min(sig[a], sig[b]) < dark:
            trans.append(b / fps)
    fl = trans[1::2]  # a flash = two opposing transitions
    best, at = 0, 0.0
    j = 0
    for i, t in enumerate(fl):
        while fl[j] < t - 1.0:
            j += 1
        if i - j + 1 > best:
            best, at = i - j + 1, fl[j]
    return best, at, fl


def audio_scan(p: Path) -> dict:
    r = subprocess.run([need("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(p), "-vn", "-af",
                        "ebur128=peak=true,silencedetect=n=-50dB:d=1.5", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=7200)
    e = r.stderr
    res = {}
    for k, pat in (("lufs", r"I:\s+(-?[\d.]+) LUFS"), ("lra", r"LRA:\s+([\d.]+) LU"), ("true_peak", r"Peak:\s+(-?[\d.inf]+) dBFS")):
        m = re.findall(pat, e)
        try:
            res[k] = float(m[-1]) if m else None
        except ValueError:
            res[k] = None
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", e)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", e)]
    res["silences"] = [(round(max(0.0, s), 2), round(ends[i] if i < len(ends) else -1, 2)) for i, s in enumerate(starts)]
    # per channel at the file's own rate, as integers: a mono downmix or float decode can exceed full
    # scale without anything being clipped in the file
    rr = subprocess.run([need("ffmpeg"), "-v", "error", "-nostdin", "-i", str(p), "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                        capture_output=True, timeout=3600)
    ch = max(1, int(next((s.get("channels") for s in stream_info(p).get("streams", []) if s.get("codec_type") == "audio"), 1) or 1))
    raw = np.frombuffer(rr.stdout, np.int16)
    x = raw[: raw.size // ch * ch].reshape(-1, ch).astype(np.float32) / 32768.0 if raw.size else np.zeros((0, 1), np.float32)
    x = np.abs(x).max(axis=1) if x.size else np.zeros(0, np.float32)
    sr = int(next((s.get("sample_rate") for s in stream_info(p).get("streams", []) if s.get("codec_type") == "audio"), 48000) or 48000)
    if x.size:
        hot = x >= 32766 / 32768
        # a clip = 3+ consecutive full-scale samples
        idx = np.flatnonzero(hot)
        nclip = 0
        if idx.size:
            br = np.flatnonzero(np.diff(idx) > 1)
            starts_i = np.r_[idx[0], idx[br + 1]]
            ends_i = np.r_[idx[br], idx[-1]]
            nclip = int(((ends_i - starts_i + 1) >= 3).sum())
            ct = []
            for s, e2 in zip(starts_i, ends_i):
                t = round(float(s) / sr, 1)
                if e2 - s + 1 >= 3 and (not ct or t - ct[-1] >= 0.5):
                    ct.append(t)
            res["clip_times"] = ct[:20]
        res["clipped_runs"] = nclip
        res["peak_db"] = round(P.db(float(x.max())), 2)
        res["sr_note"] = "per-channel s16 decode"
    return res


def stream_info(p: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(p)],
                       capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout or "{}")


def faststart(p: Path) -> bool | None:
    """True when the MP4 'moov' atom precedes 'mdat' (plays while downloading)."""
    if p.suffix.lower() not in (".mp4", ".m4v", ".mov"):
        return None
    try:
        with open(p, "rb") as f:
            pos = 0
            size_total = p.stat().st_size
            while pos < size_total:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    return None
                sz = int.from_bytes(hdr[:4], "big")
                typ = hdr[4:8].decode("latin1")
                if sz == 1:
                    sz = int.from_bytes(hdr[8:16], "big")
                if typ == "moov":
                    return True
                if typ == "mdat":
                    return False
                if sz < 8:
                    return None
                pos += sz
    except OSError:
        return None
    return None


def letterbox(p: Path, info: dict, n: int = 6) -> dict:
    work = C.scratch("lb-")
    try:
        rows_dark, cols_dark = [], []
        for i in range(n):
            f = work / f"{i}.png"
            C.frame_at(p, info["duration"] * (i + 0.5) / n, f, 320)
            if not f.exists():
                continue
            g = np.asarray(Image.open(f).convert("L"), np.float32) / 255
            r = g.max(axis=1) < 0.09
            c = g.max(axis=0) < 0.09
            if g.mean() < 0.05:  # a black frame says nothing about bars
                continue
            rows_dark.append(r)
            cols_dark.append(c)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    if not rows_dark:
        return {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    R = np.mean(rows_dark, axis=0) >= 0.7
    Cc = np.mean(cols_dark, axis=0) >= 0.7

    def edge(mask):
        k = 0
        while k < mask.size and mask[k]:
            k += 1
        return k / mask.size
    return {"top": round(edge(R), 3), "bottom": round(edge(R[::-1]), 3), "left": round(edge(Cc), 3), "right": round(edge(Cc[::-1]), 3)}


def caption_boxes(ass: Path, W: int, H: int, dur: float, n: int = 8) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Render the .ass alone (over black) at moments when a cue is on screen → text bounding boxes."""
    txt = ass.read_text(encoding="utf-8", errors="ignore")
    cues = []
    for m in re.finditer(r"^Dialogue:\s*[^,]*,(\d+):(\d+):(\d+)\.(\d+),(\d+):(\d+):(\d+)\.(\d+)", txt, re.M):
        g = [int(x) for x in m.groups()]
        a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 100
        b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 100
        cues.append((a, b))
    if not cues:
        return []
    pick = [cues[int(i * (len(cues) - 1) / max(1, n - 1))] for i in range(min(n, len(cues)))]
    out = []
    work = C.scratch("cap-")
    fd = ass.parent / (ass.stem + "-assets") / "fonts"
    fdir = f":fontsdir='{C.ff_path(fd)}'" if fd.exists() else ""
    try:
        for k, (a, b) in enumerate(pick):
            t = (a + b) / 2
            f = work / f"{k}.png"
            subprocess.run([need("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:d={dur + 1:.2f}",
                            "-vf", f"ass=filename='{C.ff_path(ass)}'{fdir}", "-ss", f"{t:.3f}", "-frames:v", "1", str(f)],
                           capture_output=True, timeout=120)
            if not f.exists():
                continue
            g = np.asarray(Image.open(f).convert("L"))
            ys, xs = np.nonzero(g > 40)
            if ys.size:
                out.append((round(t, 2), (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))))
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    return out


def qc_strip(scan: dict, issues: list[tuple[str, float, float, tuple]], D: float, dest: Path, title: str) -> Path:
    W, H = 1400, 230
    im = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(im)
    L, R, T, B = 50, W - 20, 40, H - 40
    lum = scan["lum"]
    n = lum.size
    pts = [(L + (R - L) * i / max(1, n - 1), B - (B - T) * min(1.0, v)) for i, v in enumerate(lum)]
    for lab, a, b, col in sorted(issues, key=lambda x: x[0] != "silence"):   # silence underneath, faults on top
        x0, x1 = L + (R - L) * a / D, L + (R - L) * b / D
        y0 = T if lab != "silence" else T + (B - T) * 0.55
        d.rectangle([x0, y0, max(x0 + 3, x1), B], fill=col)
    if len(pts) > 1:
        d.line(pts, fill=(40, 60, 110), width=1)
    d.text((L, 10), title, font=P.font(16, True), fill=(20, 20, 30))
    d.text((W - 640, 12), "line = picture luminance · red black · purple frozen · orange flashes · blue silence",
           font=P.font(13), fill=(90, 90, 100))
    for k in range(0, int(D) + 1, max(1, int(D / 10) or 1)):
        x = L + (R - L) * k / D
        d.text((x - 10, B + 10), P.tc(k)[:5], font=P.font(12), fill=(90, 90, 100))
    im.save(dest)
    return dest


@tool("video")
def video_qc(path: str, platform: str = "", captions: str = "", project: str = "", out: str = "") -> Result:
    """Delivery QC report before a video goes out — a PASS/WARN/FAIL table image (LOOK at it) + data.checks:
    black frames, frozen/static picture, flash/strobe risk (luminance flashes per second, whole frame and
    quadrants, vs the 3-per-second ITU-R BT.1702/WCAG limit), letterbox/pillarbox bars, audio clipping,
    loudness (integrated LUFS, LRA, true peak vs the platform target), silence gaps, A/V stream alignment
    (start/duration sanity — not lip-sync), resolution / fps / codec / pixel format / faststart vs the
    platform, caption safe zone (renders the .ass beside the file, or captions=<.ass>, and checks text stays
    out of the platform UI zones), duration and file-size limits. platform: reels | tiktok | shorts |
    youtube | linkedin | instagram_feed | x | whatsapp | broadcast | '' (generic). Also a timeline strip
    marking where each problem is."""
    p = C.src_path(path)
    info = C.probe(p)
    pk = ALIASES.get(platform.lower().strip(), platform.lower().strip())
    if pk and pk not in PLATFORMS:
        raise ToolError(f"unknown platform {platform!r}", " | ".join(PLATFORMS))
    spec = PLATFORMS.get(pk) or {}
    rows: list[dict] = []
    issues: list = []
    D = info["duration"]
    js = stream_info(p)
    vs = next((s for s in js.get("streams", []) if s.get("codec_type") == "video"), None)
    as_ = next((s for s in js.get("streams", []) if s.get("codec_type") == "audio"), None)
    # ── format / streams
    if info.get("video"):
        W, H = info["width"], info["height"]
        if spec:
            tw, th = spec["size"]
            if (W, H) == (tw, th):
                rows.append(_row("Resolution", "PASS", f"{W}x{H}", f"{spec['label']} {tw}x{th}"))
            elif abs(W / H - tw / th) < 0.01:
                rows.append(_row("Resolution", "PASS" if W >= tw * 0.66 else "WARN", f"{W}x{H}", f"right shape; {tw}x{th} recommended"))
            else:
                rows.append(_row("Resolution", "FAIL", f"{W}x{H} ({W / H:.3f})", f"{spec['label']} wants {tw}x{th} ({tw / th:.3f}) — reframe/export"))
        else:
            rows.append(_row("Resolution", "INFO", f"{W}x{H}", f"aspect {W / H:.3f}"))
        fps = info.get("fps") or 0
        r_fr = vs.get("r_frame_rate", "0/1") if vs else "0/1"
        num, _, den = r_fr.partition("/")
        rfps = float(num) / float(den or 1) if float(den or 1) else 0
        vfr = rfps and abs(rfps - fps) > 0.5
        lo, hi = spec.get("fps", (1, 120))
        st = "PASS" if lo <= fps <= hi else "FAIL"
        if vfr:
            st = "WARN" if st == "PASS" else st
        rows.append(_row("Frame rate", st, f"{fps:g} fps" + (" (variable)" if vfr else ""), f"allowed {lo}–{hi}" + ("; VFR can drift A/V in editors" if vfr else "")))
        codec, pix = info.get("codec"), info.get("pix_fmt")
        ok_codec = codec in ("h264", "hevc", "av1", "vp9", "prores") if not spec else codec in ("h264", "hevc")
        rows.append(_row("Video codec", "PASS" if ok_codec else ("WARN" if spec else "INFO"), f"{codec} {info.get('profile') or ''}".strip(),
                         "H.264 High / yuv420p is the safe upload format" if spec else ""))
        if pix:
            rows.append(_row("Pixel format", "PASS" if pix in ("yuv420p", "yuvj420p") or not spec else "WARN", pix,
                             "players/phones expect 8-bit 4:2:0" if pix not in ("yuv420p", "yuvj420p") else ""))
    else:
        rows.append(_row("Video stream", "FAIL" if spec else "INFO", "none", "audio-only file"))
    if info.get("audio"):
        ac = info.get("audio_codec")
        rows.append(_row("Audio codec", "PASS" if ac in ("aac", "mp3", "opus") or not spec else "WARN",
                         f"{ac} {info.get('sample_rate')} Hz {info.get('channels')} ch", "AAC 48 kHz stereo recommended" if spec else ""))
    elif spec:
        rows.append(_row("Audio stream", "WARN", "none", "silent upload — platforms may flag or add nothing"))
    fs = faststart(p)
    if fs is not None:
        rows.append(_row("Faststart (moov first)", "PASS" if fs else "WARN", "yes" if fs else "no", "" if fs else "web players must download the whole file first — re-mux with -movflags +faststart"))
    # A/V alignment sanity
    if vs and as_:
        vst, ast = float(vs.get("start_time", 0) or 0), float(as_.get("start_time", 0) or 0)
        vd, ad = float(vs.get("duration", 0) or 0), float(as_.get("duration", 0) or 0)
        off = abs(vst - ast)
        dd = abs(vd - ad) if vd and ad else 0
        st = "PASS" if off <= 0.045 and dd <= 0.25 else ("WARN" if off <= 0.2 and dd <= 1.0 else "FAIL")
        rows.append(_row("A/V stream alignment", st, f"start Δ {off * 1000:.0f} ms, length Δ {dd:.2f}s",
                         "stream start/length sanity only (not lip-sync analysis)"))
    # ── length / size
    if spec.get("max_s"):
        rows.append(_row("Duration", "PASS" if D <= spec["max_s"] else "FAIL", f"{D:.2f}s", f"{spec['label']} limit {spec['max_s']}s"))
    else:
        rows.append(_row("Duration", "INFO", f"{D:.2f}s"))
    mb = p.stat().st_size / 1e6
    if spec.get("max_mb"):
        rows.append(_row("File size", "PASS" if mb <= spec["max_mb"] else "FAIL", f"{mb:.1f} MB",
                         f"limit ≈{spec['max_mb']} MB (published limits change)"))
    else:
        rows.append(_row("File size", "INFO", f"{mb:.1f} MB", f"{8 * mb / max(D, 0.01):.1f} Mbit/s average"))
    scan = None
    if info.get("video"):
        scan = picture_scan(p, info)
        fpsx = scan["fps"]
        # ── black
        black = runs((scan["lum"] < 0.004) & (scan["quad"].max(axis=1) < 0.008), fpsx, 0.2)
        long_mid = [(a, b) for a, b in black if (b - a) >= 0.5 and a > 0.2 and b < D - 0.2]
        st = "PASS" if not black else ("WARN" if long_mid or any(b - a > 1.0 for a, b in black) else "INFO")
        rows.append(_row("Black frames", st, f"{len(black)} segment(s), {sum(b - a for a, b in black):.2f}s",
                         ", ".join(f"{P.tc(a)}–{P.tc(b)}" for a, b in black[:4]) or "none"))
        issues += [("black", a, b, (240, 120, 120)) for a, b in black]
        # ── frozen
        frz = runs(scan["diff"] < 0.12, fpsx, 2.0)
        rows.append(_row("Frozen / static picture", "PASS" if not frz else "WARN", f"{len(frz)} segment(s) ≥ 2 s",
                         (", ".join(f"{P.tc(a)}–{P.tc(b)}" for a, b in frz[:4]) + " (fine for slides/titles; a fault on camera footage)") if frz else "none"))
        issues += [("frozen", a, b, (190, 150, 230)) for a, b in frz]
        # ── flashes
        worst, at, fl = flashes(scan["lum"], fpsx)
        for q in range(4):
            w2, a2, f2 = flashes(scan["quad"][:, q], fpsx)
            if w2 > worst:
                worst, at, fl = w2, a2, f2
        st = "PASS" if worst <= 2 else ("WARN" if worst == 3 else "FAIL")
        rows.append(_row("Flash / strobe risk", st, f"max {worst} flash(es) in 1 s" + (f" at {P.tc(at)}" if worst else ""),
                         "photosensitive-epilepsy guideline: ≤ 3 per second (ITU-R BT.1702 / WCAG 2.3.1); luminance only, approx."))
        issues += [("flash", t, t + 0.05, (250, 170, 60)) for t in fl[:200]]
        # ── letterbox
        lb = letterbox(p, info)
        bars = max(lb["top"], lb["bottom"]) > 0.02 or max(lb["left"], lb["right"]) > 0.02
        kind = "letterbox" if max(lb["top"], lb["bottom"]) > 0.02 else "pillarbox"
        rows.append(_row("Letterbox / pillarbox", "PASS" if not bars else ("WARN" if spec else "INFO"),
                         f"{kind}: " + " ".join(f"{k} {v * 100:.0f}%" for k, v in lb.items() if v > 0.02) if bars else "none",
                         "black bars waste the frame on phones — reframe (video_reframe) or fit='blur'" if bars else ""))
        # ── caption safe zone
        ass = Path(captions).expanduser() if captions else p.with_suffix(".ass")
        if ass.exists() and spec.get("safe"):
            boxes = caption_boxes(ass, info["width"], info["height"], D)
            t_, b_, l_, r_ = spec["safe"]
            Wd, Hd = info["width"], info["height"]
            bad = [(t, bx) for t, bx in boxes if bx[1] < t_ * Hd or bx[3] > (1 - b_) * Hd or bx[0] < l_ * Wd or bx[2] > (1 - r_) * Wd]
            rows.append(_row("Captions in safe zone", "PASS" if not bad else "WARN", f"{len(boxes) - len(bad)}/{len(boxes)} cues inside",
                             (f"outside the {spec['label']} UI-safe area at " + ", ".join(P.tc(t) for t, _ in bad[:4])) if bad else
                             f"safe area: top {t_ * 100:.0f}%, bottom {b_ * 100:.0f}%, sides {l_ * 100:.0f}/{r_ * 100:.0f}% (approx.)"))
        elif ass.exists():
            rows.append(_row("Captions in safe zone", "SKIP", ass.name, "pass platform= to check against its UI zones"))
    # ── audio
    if info.get("audio"):
        a = audio_scan(p)
        tgt = spec.get("lufs")
        lu = a.get("lufs")
        if lu is None:
            rows.append(_row("Loudness", "WARN", "unmeasured"))
        elif tgt is not None:
            dv = abs(lu - tgt)
            rows.append(_row("Integrated loudness", "PASS" if dv <= 1.0 else ("WARN" if dv <= 3.0 else "FAIL"), f"{lu:.1f} LUFS",
                             f"target {tgt} LUFS ±1 — fix with video_loudness"))
        else:
            rows.append(_row("Integrated loudness", "INFO", f"{lu:.1f} LUFS", "streaming -14, broadcast -23 (pass platform=)"))
        tp = a.get("true_peak")
        if tp is not None:
            lim = spec.get("tp", -1.0)
            rows.append(_row("True peak", "PASS" if tp <= lim else ("WARN" if tp <= 0 else "FAIL"), f"{tp:.1f} dBTP", f"≤ {lim} dBTP"))
        if a.get("lra") is not None:
            rows.append(_row("Loudness range (LRA)", "PASS" if a["lra"] <= (20 if pk == "broadcast" else 15) else "WARN", f"{a['lra']:.1f} LU",
                             "very dynamic for phones/social" if a["lra"] > 15 else "fine for the platform"))
        nc = a.get("clipped_runs", 0)
        rows.append(_row("Audio clipping", "PASS" if nc == 0 else ("WARN" if nc < 5 else "FAIL"), f"{nc} clipped run(s)",
                         ("at " + ", ".join(P.tc(t) for t in a.get("clip_times", [])[:5])) if nc else "no full-scale runs (≥3 samples)"))
        sil = [(s, e if e >= 0 else D) for s, e in a["silences"]]
        mid = [(s, e) for s, e in sil if s > 0.5 and e < D - 0.5 and e - s >= 2.0]
        whole = sum(e - s for s, e in sil) > 0.9 * D
        rows.append(_row("Silence gaps", "WARN" if whole else ("PASS" if not mid else "WARN"),
                         "silent throughout" if whole else f"{len(mid)} gap(s) ≥ 2 s mid-programme",
                         ", ".join(f"{P.tc(s)}–{P.tc(e)}" for s, e in mid[:4]) or "below -50 dB for ≥1.5 s counted"))
        issues += [("silence", s, e, (150, 190, 240)) for s, e in sil]
    d = C.out_folder(project, out, p.stem + "-qc")
    thumb = None
    if info.get("video"):
        tp_ = d / "thumb.png"
        C.frame_at(p, D * 0.4, tp_, 480)
        thumb = Image.open(tp_) if tp_.exists() else None
    verdict = "FAIL" if any(r["status"] == "FAIL" for r in rows) else ("WARN" if any(r["status"] == "WARN" for r in rows) else "PASS")
    table = d / "qc-report.png"
    P.table_image(rows, table, f"QC {verdict} — {p.name}", f"{spec.get('label', 'generic checks')} · {D:.2f}s · "
                  + (f"{info.get('width')}x{info.get('height')} {info.get('fps')} fps" if info.get("video") else "audio only"), thumb)
    previews = [str(table)]
    if scan is not None:
        strip = qc_strip(scan, issues, max(D, 0.1), d / "qc-timeline.png", f"{p.name} — where the issues are")
        previews.append(str(strip))
    (d / "qc.json").write_text(json.dumps({"file": str(p), "platform": pk, "verdict": verdict, "checks": rows}, indent=1, ensure_ascii=False),
                               encoding="utf-8")
    bad = [r for r in rows if r["status"] in ("FAIL", "WARN")]
    res = Result(f"QC {verdict} for {p.name}" + (f" ({spec['label']})" if spec else "") + f": {sum(r['status'] == 'PASS' for r in rows)} pass, "
                 f"{sum(r['status'] == 'WARN' for r in rows)} warn, {sum(r['status'] == 'FAIL' for r in rows)} fail.",
                 files=[str(table), str(d / "qc.json")] + previews[1:], previews=previews,
                 warnings=[f"{r['status']}: {r['check']} — {r['value']} {('· ' + r['detail']) if r['detail'] else ''}".strip() for r in bad],
                 data={"verdict": verdict, "platform": pk, "checks": rows})
    if any(r["check"] in ("Integrated loudness", "True peak") and r["status"] != "PASS" for r in rows):
        res.next_steps.append(f"video_loudness(path, target='{pk or 'youtube'}') or video_export(path, preset='{pk or 'youtube'}')")
    if any(r["check"] == "Resolution" and r["status"] == "FAIL" for r in rows):
        res.next_steps.append(f"video_reframe / video_export(preset='{pk}')")
    return res
