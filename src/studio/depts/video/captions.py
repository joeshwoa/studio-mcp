"""Burned captions with libass: word timings → styled .ass (reels word-highlight, karaoke fill, clean,
boxed, bold) + a clean .srt. Arabic is shaped and ordered by libass (HarfBuzz + FriBidi) and gets its
own Arabic face; Latin chunks use the Latin face."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ...core.result import ToolError

AR = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")
STYLES = ("reels", "bold", "karaoke", "clean", "boxed")


def is_rtl(s: str) -> bool:
    return bool(AR.search(s or ""))


def parse_srt(path: str | Path) -> list[dict]:
    txt = Path(path).expanduser().read_text(encoding="utf-8-sig", errors="ignore")
    segs = []
    for block in re.split(r"\n\s*\n", txt.replace("\r", "")):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = re.sub(r"<[^>]+>|\{[^}]+\}", "", block[m.end():].strip()).replace("\n", " ").strip()
        if body:
            segs.append({"start": st, "end": en, "text": body})
    return segs


def words_from_segments(segs: list[dict]) -> list[dict]:
    """Estimate word timing inside each subtitle by word length (approximate)."""
    out = []
    for s in segs:
        ws = str(s["text"]).split()
        if not ws:
            continue
        wt = [len(w) + 2 for w in ws]
        tot = sum(wt)
        t = float(s["start"])
        span = float(s["end"]) - t
        for i, w in enumerate(ws):
            d = span * wt[i] / tot
            out.append({"word": w, "start": t, "end": t + d, "seg_end": i == len(ws) - 1})
            t += d
    return out


def clean_words(words: list[dict]) -> list[dict]:
    out = []
    for w in words or []:
        txt = str(w.get("word", "")).strip()
        if not txt:
            continue
        out.append({"word": txt, "start": float(w["start"]), "end": max(float(w["end"]), float(w["start"]) + 0.05),
                    "seg_end": bool(w.get("seg_end"))})
    out.sort(key=lambda x: x["start"])
    return out


def chunk(words: list[dict], per: int, max_chars: int = 22) -> list[list[dict]]:
    """Group words into caption chunks: ≤ per words and ≤ max_chars, breaking at punctuation,
    sentence ends and pauses > 0.45 s."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nx = words[i + 1] if i + 1 < len(words) else None
        chars = sum(len(x["word"]) + 1 for x in cur) + (len(nx["word"]) if nx else 0)
        brk = (not nx or len(cur) >= per or chars > max_chars or w.get("seg_end")
               or re.search(r"[.!?؟،,;:…]$", w["word"]) or (nx["start"] - w["end"] > 0.45))
        if brk:
            chunks.append(cur)
            cur = []
    return chunks


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _srt_ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def ass_color(hexcol: str, alpha: int = 0) -> str:
    h = hexcol.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def font_files(latin: str, arabic: str, weight: int, dest: Path) -> tuple[str, str, list[str]]:
    """Download (cached) the Google fonts, copy them into dest (libass fontsdir) and return the family
    names libass will match (read from the font files themselves)."""
    warns = []
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    try:
        from ..design import fonts as F
        from fontTools.ttLib import TTFont
    except Exception as e:  # noqa
        return "Arial", "Arial", [f"font helpers unavailable ({e}); libass falls back to a system font"]
    for fam in (latin, arabic):
        try:
            p = F.ensure(fam, [weight])[weight]
            shutil.copy2(p, dest / p.name)
            tt = TTFont(str(p))
            nm = tt["name"]
            # the typographic FAMILY name + ASS Bold flag: matches both through fontsdir (ffmpeg) and through
            # fontconfig (MLT/Kdenlive, which ignores fontsdir and only knows families + styles)
            family = nm.getDebugName(16) or nm.getDebugName(1) or fam
            names.append(family + ("|b" if weight >= 600 else ""))
        except Exception as e:
            warns.append(f"font {fam} unavailable ({e}); using a fallback")
            names.append(fam)
    return names[0], names[1], warns


def caption_geometry(W: int, H: int, style: str = "reels", position: str = "auto", size_scale: float = 1.0) -> dict:
    """Caption layout shared by the burned-in ASS and the native caption layers written for pro NLEs:
    font size and outline in px, position name, vertical margin (px from the edge) and ASS alignment."""
    portrait = H > W * 1.05
    m = min(W, H)
    big = style in ("reels", "bold")
    fs = int(m * (0.088 if big else 0.064 if style == "karaoke" else 0.058) * size_scale * (1.0 if portrait else 0.88))
    outline = max(2, int(fs * (0.11 if big else 0.08)))
    pos = position if position != "auto" else ("lower" if portrait and big else "bottom")
    marginv = int(H * {"bottom": 0.07 if not portrait else 0.16, "lower": 0.27, "middle": 0.45, "top": 0.1}.get(pos, 0.08))
    align = 8 if pos == "top" else (5 if pos == "middle" else 2)
    if pos == "middle":
        marginv = 0
    return {"big": big, "size": fs, "outline": outline, "position": pos, "margin_v": marginv, "align": align,
            "margin_lr": int(W * 0.07), "box": style == "boxed", "uppercase_default": big}


def build_ass(words: list[dict], W: int, H: int, style: str = "reels", *, per: int = 0, accent: str = "#FFD400",
              text_color: str = "#FFFFFF", font: str = "Montserrat ExtraBold", font_ar: str = "Cairo ExtraBold",
              position: str = "auto", uppercase: bool | None = None, size_scale: float = 1.0) -> tuple[str, list[dict]]:
    """→ (ASS document, chunks with timing). Positions: auto | bottom | lower | middle | top."""
    if style not in STYLES:
        raise ToolError(f"unknown caption style {style!r}", f"one of {STYLES}")
    portrait = H > W * 1.05
    m = min(W, H)
    per = per or {"reels": 3, "bold": 3, "karaoke": 5, "clean": 8, "boxed": 7}[style]
    max_chars = {"reels": 18, "bold": 20, "karaoke": 32, "clean": 42, "boxed": 40}[style]
    chunks = chunk(words, per, max_chars)
    for i, c in enumerate(chunks):
        c_start = c[0]["start"]
        nx = chunks[i + 1][0]["start"] if i + 1 < len(chunks) else 1e9
        c_end = min(c[-1]["end"] + 0.35, nx)
        chunks[i] = {"words": c, "start": c_start, "end": max(c_end, c_start + 0.3)}
    g = caption_geometry(W, H, style, position, size_scale)
    big, fs, outline, pos, marginv, align = g["big"], g["size"], g["outline"], g["position"], g["margin_v"], g["align"]
    up = uppercase if uppercase is not None else big
    tc, ac, blk = ass_color(text_color), ass_color(accent), ass_color("#000000")
    dark = ass_color("#111111")
    bs = 1
    back = ass_color("#000000", 0x80)
    if style == "boxed":
        bs, outline = 3, max(4, int(fs * 0.28))
        blk = ass_color("#0B0B0F", 0x10)
    shadow = 0 if style in ("reels", "bold", "boxed") else max(1, int(fs * 0.06))
    mlr = int(W * 0.07)
    hdr = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for suffix, fnt in (("", font), ("_ar", font_ar)):
        bold = "-1" if fnt.endswith("|b") else "0"
        fnt = fnt[:-2] if fnt.endswith("|b") else fnt
        fsz = int(fs * (1.22 if suffix else 1.0))
        prim, sec = (ac, tc) if style == "karaoke" else (tc, tc)
        hdr.append(f"Style: Base{suffix},{fnt},{fsz},{prim},{sec},{blk},{back},{bold},0,0,0,100,100,0,0,{bs},{outline},{shadow},{align},{mlr},{mlr},{marginv},1")
        # highlight layer: box behind the active word (reels) or accent-coloured word (bold)
        pad = max(4, int(fs * 0.16))
        hdr.append(f"Style: Hi{suffix},{fnt},{fsz},{dark},{dark},{ac},{ac},{bold},0,0,0,100,100,0,0,3,{pad},0,{align},{mlr},{mlr},{marginv},1")
        hdr.append(f"Style: Col{suffix},{fnt},{fsz},{ac},{ac},{blk},{back},{bold},0,0,0,100,100,0,0,1,{outline},0,{align},{mlr},{mlr},{marginv},1")
    hdr += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ev = []
    for ch in chunks:
        ws = ch["words"]
        rtl = is_rtl(" ".join(w["word"] for w in ws))
        sfx = "_ar" if rtl else ""
        toks = [_esc(w["word"].upper() if up and not rtl else w["word"]) for w in ws]
        if big or rtl and style == "karaoke":  # social styles drop trailing . , ، ; : (also avoids bidi-misplaced
            toks = [re.sub(r"[.,،؛;:…]+$", "", t) or t for t in toks]   # punctuation in per-word Arabic runs)
        # libass lays out tag-separated runs left→right even in RTL text, so a word-highlight line breaks
        # Arabic word order. Fix: every word is its own run ({} separators) and the runs are given in
        # visual (reversed) order. Timings still follow the spoken order via `order`.
        order = list(range(len(ws)))
        if rtl:
            order = order[::-1]
        pop = r"{\fscx82\fscy82\t(0,90,\fscx106\fscy106)\t(90,160,\fscx100\fscy100)}" if big else ""
        fade = r"{\fad(120,90)}" if style in ("clean", "boxed") else ""
        if style == "karaoke" and not rtl:
            ks = []
            for i, w in enumerate(ws):
                nxt = ws[i + 1]["start"] if i + 1 < len(ws) else w["end"]
                if i == 0 and w["start"] > ch["start"]:
                    ks.append(f"{{\\k{int(round((w['start'] - ch['start']) * 100))}}}")
                ks.append(f"{{\\kf{max(1, int(round((nxt - w['start']) * 100)))}}}{toks[i]}")
            ev.append(f"Dialogue: 0,{_ts(ch['start'])},{_ts(ch['end'])},Base{sfx},,0,0,0,,{{\\fad(80,80)}}" + " ".join(ks))
            continue
        if style not in ("reels", "bold", "karaoke"):
            ev.append(f"Dialogue: 0,{_ts(ch['start'])},{_ts(ch['end'])},Base{sfx},,0,0,0,,{pop}{fade}" + " ".join(toks))
            continue
        # RTL: each word must be its own style run (libass splits bidi runs only where the style changes),
        # so words alternate an invisible 1/255 alpha difference.
        def J(parts, alt=(r"{\1a&H00&}", r"{\1a&H01&}")):
            if not rtl:
                return " ".join(parts)
            return " ".join(alt[k % 2] + p for k, p in enumerate(parts))
        # word highlight: one event per spoken word. reels = accent box BEHIND the word (layer 0) and the
        # word itself in dark text on it (layer 1); bold = the word turns accent-coloured.
        dark_c = ass_color("#111111")
        for i, w in enumerate(ws):
            a = w["start"] if i else ch["start"]
            b = ws[i + 1]["start"] if i + 1 < len(ws) else ch["end"]
            if b - a < 0.02:
                continue
            p2 = pop if i == 0 else ""
            if style == "reels":
                box = r"{\alpha&HFF&}" + J([r"{\alpha&HFF&\1a&HFF&\3a&H00&\4a&H00&}" + toks[j] + r"{\alpha&HFF&}" if j == i else toks[j]
                                             for j in order], alt=(r"{\alpha&HFF&}", r"{\alpha&HFE&}"))
                ev.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},Hi{sfx},,0,0,0,,{p2}{box}")
                txt = J([r"{\1c" + dark_c + r"&\3a&HFF&}" + toks[j] + r"{\1c" + tc + r"&\3a&H00&}" if j == i else toks[j]
                         for j in order])
            else:
                txt = r"{\1c" + tc + r"&}" + J([r"{\1c" + ac + r"&}" + toks[j] + r"{\1c" + tc + r"&}" if j == i else toks[j] for j in order])
            ev.append(f"Dialogue: 1,{_ts(a)},{_ts(b)},Base{sfx},,0,0,0,,{p2}{txt}")
    return "\n".join(hdr + ev) + "\n", chunks


def write_srt(segments: list[dict], dest: Path, max_chars: int = 42) -> Path:
    """Sentence-level subtitles (≤ 2 lines of max_chars) — the file to upload to YouTube/LinkedIn."""
    lines = []
    n = 0
    for s in segments:
        text = " ".join(str(s["text"]).split())
        if not text:
            continue
        # split long cues into ≤ 2×max_chars pieces, time-proportional
        pieces, cur = [], ""
        for w in text.split():
            if len(cur) + len(w) + 1 > 2 * max_chars and cur:
                pieces.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            pieces.append(cur)
        tot = sum(len(p) for p in pieces) or 1
        t = float(s["start"])
        span = float(s["end"]) - t
        for p in pieces:
            d = span * len(p) / tot
            if len(p) > max_chars:  # break into two balanced lines
                ws = p.split()
                best = min(range(1, len(ws)), key=lambda k: abs(len(" ".join(ws[:k])) - len(" ".join(ws[k:]))), default=1)
                p = " ".join(ws[:best]) + "\n" + " ".join(ws[best:])
            n += 1
            lines += [str(n), f"{_srt_ts(t)} --> {_srt_ts(t + d)}", p, ""]
            t += d
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def segments_from_words(words: list[dict], max_words: int = 12) -> list[dict]:
    segs, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nx = words[i + 1] if i + 1 < len(words) else None
        if not nx or len(cur) >= max_words or w.get("seg_end") or re.search(r"[.!?؟]$", w["word"]) or nx["start"] - w["end"] > 0.8:
            segs.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(x["word"] for x in cur)})
            cur = []
    return segs
