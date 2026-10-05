"""Labelled contact sheets for stock results — the picture the calling AI LOOKS at to choose.

Each tile: the thumbnail letterboxed on dark grey (so orientation is visible), a big number to
pick by, provider chip, duration badge, then three caption lines: provider · size · fps,
licence (green = free & no credit, amber = credit required, red = NOT for commercial use) and
the title."""
from __future__ import annotations

import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ._http import cached_file, stock_cache

TILE_W, IMG_H, CAP_H, GAP = 360, 216, 78, 14
BG, CARD, IMGBG = "#e7e9ee", "#ffffff", "#1f2430"
GREEN, AMBER, RED, INK, MUTED = "#15803d", "#b45309", "#b91c1c", "#111827", "#4b5563"

_fonts: dict = {}


def _fc_file(cp: int) -> str:
    """A system font file that covers code point `cp` (CJK, Cyrillic, Devanagari…) via fontconfig."""
    try:
        r = subprocess.run(["fc-match", "-f", "%{file}", f"sans:charset={cp:x}"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


def font(size: int, bold: bool = False, text: str = ""):
    """Inter for Latin, Cairo for Arabic (via photo.fonts), any covering system font for other scripts."""
    from ..photo.fonts import has_arabic
    other = next((ord(c) for c in text if ord(c) > 0x2FF and not has_arabic(c) and not c.isspace()
                  and not 0x2000 <= ord(c) <= 0x2BFF), 0)
    key = (size, bold, "ar" if has_arabic(text) else (other >> 8 if other else 0))
    if key in _fonts:
        return _fonts[key]
    f = None
    if other:
        fp = _fc_file(other)
        if fp:
            try:
                f = ImageFont.truetype(fp, size)
            except Exception:
                f = None
    if f is None:
        try:
            from ..photo.fonts import pil_font
            f = pil_font("", size, 700 if bold else 400, text="" if other else text)[0]
        except Exception:
            f = None
    if f is None:
        for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                     "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
                     "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf"):
            try:
                f = ImageFont.truetype(cand, size)
                break
            except Exception:
                continue
    f = f or ImageFont.load_default()
    _fonts[key] = f
    return f


def fit_text(d: ImageDraw.ImageDraw, text: str, f, maxw: int) -> str:
    text = text or ""
    if d.textlength(text, font=f) <= maxw:
        return text
    while text and d.textlength(text + "…", font=f) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"


def licence_colour(it: dict) -> str:
    if not it.get("commercial_ok"):
        return RED
    return AMBER if it.get("attribution_required") else GREEN


def licence_label(it: dict) -> str:
    lic = it.get("license", "?")
    if not it.get("commercial_ok"):
        return f"{lic} · NOT commercial"
    bits = [lic, "credit req." if it.get("attribution_required") else "no credit needed"]
    if it.get("share_alike"):
        bits.append("share-alike")
    if it.get("modify_ok") is False:
        bits.append("no edits")
    return " · ".join(bits)


def _svg_to_png(svg: Path, side: int = 256) -> Path | None:
    out = svg.with_suffix(f".{side}.png")
    if out.exists():
        return out
    try:
        from ...core.deps import DEPS, find_bin
        rs = find_bin(DEPS["rsvg"])
        if rs:
            subprocess.run([rs, "-w", str(side), "-h", str(side), "-a", "-b", "white", str(svg), "-o", str(out)],
                           capture_output=True, timeout=60, check=True)
            return out
    except Exception:
        return None
    return None


def _audio_wave(it: dict) -> Image.Image | None:
    """Real waveform of a small audio preview (SFX/HQ previews are ~50–500 kB)."""
    files = [f for f in it.get("files") or [] if f.get("url")]
    if not files:
        return None
    f = min(files, key=lambda f: f.get("size") or 0)
    if it.get("kind") != "sfx" and not (0 < (f.get("size") or 0) <= 5_000_000) and (it.get("duration") or 999) > 60:
        return None
    try:
        fam = "wikimedia" if "wikimedia.org" in f["url"] else ("archive" if "archive.org" in f["url"] else "")
        src = cached_file(f["url"], ".audio", max_bytes=8 << 20, family=fam)
        png = src.with_suffix(".wave.png")
        if not png.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex",
                            "aformat=channel_layouts=mono,showwavespic=s=720x400:colors=#60a5fa:scale=sqrt",
                            "-frames:v", "1", str(png)], capture_output=True, timeout=60)
        if not png.exists():
            return None
        wav = Image.open(png).convert("RGBA")
        bg = Image.new("RGBA", wav.size, IMGBG)
        bg.alpha_composite(wav)
        return bg.convert("RGB")
    except Exception:
        return None


def fetch_thumb(it: dict) -> Image.Image | None:
    url = it.get("thumb") or ""
    if not url and it.get("kind") in ("music", "sfx"):
        return _audio_wave(it)
    if not url:
        return None
    try:
        if it.get("kind") == "icon" or url.lower().split("?")[0].endswith(".svg"):
            svg = cached_file(url, ".svg")
            if (it.get("extra") or {}).get("animated"):
                from .providers import static_svg
                st = svg.with_suffix(".static.svg")
                if not st.exists():
                    st.write_text(static_svg(svg.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
                svg = st
            png = _svg_to_png(svg, 200)
            return Image.open(png).convert("RGB") if png else None
        fam = "openverse" if "api.openverse.org" in url else ("wikimedia" if "wikimedia.org" in url else "")
        p = cached_file(url, ".img", family=fam)
        im = Image.open(p)
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, "white")
            bg.alpha_composite(im)
            im = bg
        return im.convert("RGB")
    except Exception:
        return None


def fetch_thumbs(items: list[dict]) -> list[Image.Image | None]:
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(fetch_thumb, items))


def audio_placeholder(w: int, h: int) -> Image.Image:
    """Neutral card for audio with no preview image — clearly NOT a waveform."""
    im = Image.new("RGB", (w, h), IMGBG)
    d = ImageDraw.Draw(im)
    cx, cy = w // 2, h // 2 - 10
    d.rectangle([cx - 30, cy - 12, cx - 14, cy + 12], fill="#60a5fa")           # speaker body
    d.polygon([(cx - 14, cy - 12), (cx + 6, cy - 30), (cx + 6, cy + 30), (cx - 14, cy + 12)], fill="#60a5fa")
    for r in (16, 30):
        d.arc([cx + 6 - r + 10, cy - r, cx + 6 + r + 10, cy + r], -45, 45, fill="#60a5fa", width=4)
    d.text((w / 2, cy + 52), "audio · waveform after download", font=font(13), fill="#9ca3af", anchor="mm")
    return im


def _badge(d: ImageDraw.ImageDraw, xy, text: str, fill: str, f, fg: str = "white", pad=(7, 3), anchor="lt"):
    x, y = xy
    tw = d.textlength(text, font=f)
    asc, desc = f.getmetrics()
    w, h = tw + 2 * pad[0], asc + desc + 2 * pad[1]
    if anchor == "rt":
        x -= w
    elif anchor == "rb":
        x, y = x - w, y - h
    elif anchor == "lb":
        y -= h
    d.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=fill)
    d.text((x + pad[0], y + pad[1]), text, font=f, fill=fg)
    return w, h


def render(tiles: list[dict], dest: Path, header: str, subheader: str = "", cols: int = 4) -> Path:
    """tiles: {n, img (PIL|None), kind, provider, line1, line2, colour, title, badge, play}"""
    cols = max(1, min(cols, len(tiles) or 1))
    rows = (len(tiles) + cols - 1) // cols
    head_h = 64 if subheader else 46
    W = max(GAP + cols * (TILE_W + GAP), 800)
    H = head_h + GAP + rows * (IMG_H + CAP_H + GAP) + 26
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    d.text((GAP, 12), fit_text(d, header, font(20, True, header), W - 2 * GAP), font=font(20, True, header), fill=INK)
    if subheader:
        d.text((GAP, 40), fit_text(d, subheader, font(14, False, subheader), W - 2 * GAP), font=font(14, False, subheader), fill=MUTED)
    f_num, f_chip, f_l1, f_l2, f_t = font(22, True), font(13, True), font(14, True), font(13, False), font(13, False)
    for i, t in enumerate(tiles):
        cx = GAP + (i % cols) * (TILE_W + GAP)
        cy = head_h + GAP + (i // cols) * (IMG_H + CAP_H + GAP)
        d.rounded_rectangle([cx, cy, cx + TILE_W, cy + IMG_H + CAP_H], radius=10, fill=CARD)
        box = Image.new("RGB", (TILE_W, IMG_H), IMGBG)
        im = t.get("img")
        if im is None and t.get("kind") in ("music", "sfx"):
            im = audio_placeholder(TILE_W, IMG_H)
        if im is not None:
            im = im.copy()
            if t.get("kind") == "icon":
                bgw = Image.new("RGB", (TILE_W, IMG_H), "white")
                im.thumbnail((IMG_H - 40, IMG_H - 40))
                bgw.paste(im, ((TILE_W - im.width) // 2, (IMG_H - im.height) // 2))
                box = bgw
            else:
                im.thumbnail((TILE_W, IMG_H), Image.LANCZOS)
                box.paste(im, ((TILE_W - im.width) // 2, (IMG_H - im.height) // 2))
        else:
            bd = ImageDraw.Draw(box)
            bd.text((TILE_W / 2, IMG_H / 2), "no preview", font=font(16), fill="#9ca3af", anchor="mm")
        mask = Image.new("L", (TILE_W, IMG_H), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, TILE_W, IMG_H + 12], radius=10, fill=255)
        sheet.paste(box, (cx, cy), mask)
        if t.get("play"):   # small play glyph, bottom-left, so the picture stays readable
            px, py = cx + 22, cy + IMG_H - 22
            d.ellipse([px - 14, py - 14, px + 14, py + 14], fill="#000000")
            d.polygon([(px - 5, py - 8), (px - 5, py + 8), (px + 9, py)], fill="white")
        _badge(d, (cx + 8, cy + 8), f"{t['n']}", "#111827", f_num, pad=(10, 3))
        _badge(d, (cx + TILE_W - 8, cy + 8), t.get("provider", "").upper(), "#2563eb", f_chip, anchor="rt")
        if t.get("badge"):
            _badge(d, (cx + TILE_W - 8, cy + IMG_H - 8), t["badge"], "#000000", f_chip, anchor="rb")
        y = cy + IMG_H + 8
        d.text((cx + 10, y), fit_text(d, t.get("line1", ""), f_l1, TILE_W - 20), font=f_l1, fill=INK)
        col = t.get("colour", MUTED)
        d.ellipse([cx + 10, y + 26, cx + 20, y + 36], fill=col)
        d.text((cx + 26, y + 22), fit_text(d, t.get("line2", ""), f_l2, TILE_W - 36), font=f_l2, fill=col)
        title = t.get("title", "")
        ft = font(13, False, title)
        d.text((cx + 10, y + 44), fit_text(d, title, ft, TILE_W - 20), font=ft, fill=MUTED)
    legend = "● green: free, no credit   ● amber: credit required (see attribution)   ● red: NOT for commercial use"
    d.text((GAP, H - 22), legend, font=font(12), fill=MUTED)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest


def media_frame(path: Path, kind: str, duration: float = 0) -> Image.Image | None:
    """A representative picture of a DOWNLOADED file: middle frame / waveform / the image itself."""
    try:
        tmp = stock_cache() / "thumbs" / f"dl-{hashlib.sha1(str(path).encode()).hexdigest()}.png"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        if kind == "video":
            t = max(0.0, (duration or 2) * 0.4)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=720:-2", str(tmp)], capture_output=True, timeout=120)
            if not tmp.exists():
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-frames:v", "1", "-vf", "scale=720:-2", str(tmp)],
                               capture_output=True, timeout=120)
            return Image.open(tmp).convert("RGB") if tmp.exists() else None
        if kind in ("music", "sfx"):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-filter_complex",
                            "aformat=channel_layouts=mono,showwavespic=s=720x432:colors=#60a5fa:scale=sqrt",
                            "-frames:v", "1", str(tmp)], capture_output=True, timeout=180)
            if tmp.exists():
                wav = Image.open(tmp).convert("RGBA")
                bg = Image.new("RGBA", wav.size, IMGBG)
                bg.alpha_composite(wav)
                return bg.convert("RGB")
            return None
        if str(path).lower().endswith(".svg"):
            png = _svg_to_png(path, 400)
            return Image.open(png).convert("RGB") if png else None
        im = Image.open(path)
        im.draft("RGB", (720, 720))
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, "white")
            bg.alpha_composite(im)
            im = bg
        return im.convert("RGB")
    except Exception:
        return None
