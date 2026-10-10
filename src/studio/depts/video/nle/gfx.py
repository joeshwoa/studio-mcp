"""Native, editable graphics: read the motion design back out of its HTML page (the same page the
graphic was rendered from) and describe it as layers every pro app can rebuild — text layers (real
text, font, weight, size, colour, alignment, direction), shapes (boxes, bars, pills with colour,
gradient and corner radius) and images (logos, badges, backdrops as transparent PNGs).

The layout is taken at the moment the graphic is fully on screen ("hold"); the entrance/exit become a
fade + rise animation in the app (the exact original motion stays one click away as the rendered
alpha clip, kept on the track below and disabled)."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ...motion import engine as E

SCAN = r"""
() => {
  const W = innerWidth, H = innerHeight;
  const layers = []; const imgs = [];
  const AR = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
  function eff(el) { let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') return 0; o *= parseFloat(cs.opacity || '1'); }
    return o; }
  function clipBox(el, r) {   // intersect with ancestors' overflow/clip-path:inset() boxes
    let x0 = r.left, y0 = r.top, x1 = r.right, y1 = r.bottom;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const cs = getComputedStyle(e);
      const b = e.getBoundingClientRect();
      const m = /inset\(([^)]*)\)/.exec(cs.clipPath || '');
      if (m) { const v = m[1].split(/\s+/).map(s => s.endsWith('%') ? parseFloat(s) / 100 : null);
        if (v.length >= 4 && v.every(q => q !== null)) { x0 = Math.max(x0, b.left + b.width * v[3]); x1 = Math.min(x1, b.right - b.width * v[1]);
          y0 = Math.max(y0, b.top + b.height * v[0]); y1 = Math.min(y1, b.bottom - b.height * v[2]); } }
      if (e !== el && (cs.overflow === 'hidden' || cs.overflow === 'clip')) { x0 = Math.max(x0, b.left); x1 = Math.min(x1, b.right);
        y0 = Math.max(y0, b.top); y1 = Math.min(y1, b.bottom); } }
    return [x0, y0, Math.max(0, x1 - x0), Math.max(0, y1 - y0)]; }
  function rgba(s) { const m = /rgba?\(([^)]+)\)/.exec(s || ''); if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
  function colors(s) { return (s.match(/rgba?\([^)]+\)/g) || []).map(rgba); }
  const all = document.body.querySelectorAll('*');
  let k = 0;
  for (const el of all) {
    const tag = el.tagName.toLowerCase();
    if (['script', 'style', 'link', 'meta', 'br'].includes(tag)) continue;
    if (el.closest('svg') && tag !== 'svg') continue;
    const op = eff(el); if (op < 0.03) continue;
    const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) continue;
    const box = clipBox(el, r); if (box[2] < 1 || box[3] < 1) continue;
    if (box[0] > W || box[1] > H || box[0] + box[2] < 0 || box[1] + box[3] < 0) continue;
    const cs = getComputedStyle(el); const z = k++;
    if (['img', 'svg', 'canvas', 'video'].includes(tag)) { el.setAttribute('data-gfx-img', String(z));
      imgs.push(z); layers.push({type: 'image', z, box, opacity: op, radius: parseFloat(cs.borderTopLeftRadius) || 0, tag}); continue; }
    const bg = rgba(cs.backgroundColor); const grad = /gradient/.test(cs.backgroundImage) ? colors(cs.backgroundImage) : [];
    if ((bg && bg[3] > 0.03) || grad.length) {
      const ang = /(-?\d+(\.\d+)?)deg/.exec(cs.backgroundImage);
      layers.push({type: 'rect', z, box, opacity: op, color: grad.length ? grad[0] : bg, color2: grad.length > 1 ? grad[grad.length - 1] : null,
                   angle: ang ? parseFloat(ang[1]) : 180, radius: parseFloat(cs.borderTopLeftRadius) || 0,
                   border: (parseFloat(cs.borderTopWidth) || 0) > 0 ? {w: parseFloat(cs.borderTopWidth), color: rgba(cs.borderTopColor)} : null,
                   shadow: cs.boxShadow && cs.boxShadow !== 'none'}); }
    for (const node of el.childNodes) {
      if (node.nodeType !== 3 || !node.textContent.trim()) continue;
      const rg = document.createRange(); rg.selectNodeContents(node);
      const rr = rg.getBoundingClientRect(); if (rr.width < 1) continue;
      let text = node.textContent.replace(/\s+/g, ' ').trim();
      if (cs.textTransform === 'uppercase') text = text.toUpperCase();
      const lines = Array.from(rg.getClientRects()).filter(q => q.width > 0.5);
      const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.2;
      layers.push({type: 'text', z: k++, text, box: [rr.left, rr.top, rr.width, rr.height], opacity: op,
                   family: cs.fontFamily.split(',')[0].replace(/["']/g, '').trim(), weight: parseInt(cs.fontWeight) || 400,
                   size: parseFloat(cs.fontSize), color: rgba(cs.color), letter_spacing: parseFloat(cs.letterSpacing) || 0,
                   line_height: lh, lines: Math.max(1, Math.round(rr.height / lh)), align: cs.textAlign,
                   rtl: AR.test(text) || cs.direction === 'rtl', italic: cs.fontStyle === 'italic',
                   shadow: cs.textShadow && cs.textShadow !== 'none', stroke: parseFloat(cs.webkitTextStrokeWidth) || 0,
                   clip_text: /text/.test(cs.webkitBackgroundClip || cs.backgroundClip || '') ? colors(cs.backgroundImage) : null});
    }
  }
  return {W, H, layers, imgs};
}
"""

METRIC = r"""
() => { let s = 0; const W = innerWidth, H = innerHeight;
  for (const el of document.body.querySelectorAll('*')) {
    if (el.children.length) continue;
    let o = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') { o = 0; break; } o *= parseFloat(cs.opacity || '1'); }
    if (o < 0.02) continue; const r = el.getBoundingClientRect();
    const w = Math.max(0, Math.min(W, r.right) - Math.max(0, r.left)), h = Math.max(0, Math.min(H, r.bottom) - Math.max(0, r.top));
    s += o * w * h; }
  return s; }
"""


def extract(html: Path, W: int, H: int, duration: float, dest: Path, fonts: dict | None = None, name: str = "") -> dict:
    """→ spec {name, W, H, dur, hold, anim:{in, out, rise}, layers:[…], fonts:[…]}; PNGs for image layers in dest."""
    from playwright.async_api import async_playwright
    dest.mkdir(parents=True, exist_ok=True)

    async def job():
        async with async_playwright() as p:
            browser = await E._launch(p)
            try:
                ctx = await browser.new_context(viewport={"width": int(W), "height": int(H)})
                await ctx.add_init_script(E.VCLOCK_JS)
                page = await ctx.new_page()
                await page.goto(Path(html).resolve().as_uri(), wait_until="load", timeout=60000)
                await page.evaluate("async () => { await document.fonts.ready; if (window.__ready) await window.__ready; }")
                cdp = await ctx.new_cdp_session(page)
                await cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
                dur = duration or float(await page.evaluate("() => window.__duration || 3"))
                n = 36
                ts = [dur * (i + 0.5) / n for i in range(n)]
                vals = []
                for t in ts:
                    await page.evaluate("(ms) => window.__vt.seek(ms)", t * 1000)
                    vals.append(float(await page.evaluate(METRIC)))
                mx = max(vals) or 1.0
                full = [i for i, v in enumerate(vals) if v >= 0.97 * mx]
                i_in, i_out = full[0], full[-1]
                hold = ts[(i_in + i_out) // 2]
                await page.evaluate("(ms) => window.__vt.seek(ms)", hold * 1000)
                scan = await page.evaluate(SCAN)
                for lay in scan["layers"]:
                    if lay["type"] != "image":
                        continue
                    z = lay["z"]
                    png = dest / f"{_slug(name)}-img{z}.png"
                    await page.evaluate("""(z) => { for (const el of document.body.querySelectorAll('*')) {
                        const keep = el.matches(`[data-gfx-img="${z}"]`) || el.querySelector(`[data-gfx-img="${z}"]`) || el.closest(`[data-gfx-img="${z}"]`);
                        el.style.setProperty('visibility', keep ? 'visible' : 'hidden', 'important');
                        if (!keep && !el.querySelector(`[data-gfx-img="${z}"]`)) el.style.setProperty('background', 'transparent', 'important'); } }""", z)
                    x, y, w, h = lay["box"]
                    await page.screenshot(path=str(png), omit_background=True,
                                          clip={"x": max(0, x), "y": max(0, y), "width": max(1, min(W - max(0, x), w)), "height": max(1, min(H - max(0, y), h))})
                    lay["png"] = str(png)
                    await page.evaluate("() => { for (const el of document.body.querySelectorAll('*')) { el.style.removeProperty('visibility'); el.style.removeProperty('background'); } }")
                # the "plate": everything except the text (shapes, gradients, images) as one transparent PNG —
                # apps without vector shapes (Final Cut titles, Premiere, CapCut) put it under the live text
                plate = dest / f"{_slug(name)}-plate.png"
                await page.evaluate("""() => { for (const el of document.body.querySelectorAll('*')) {
                    if (Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim())) el.style.setProperty('color', 'transparent', 'important');
                    el.style.setProperty('text-shadow', 'none', 'important'); el.style.setProperty('-webkit-text-stroke', '0', 'important'); } }""")
                await page.screenshot(path=str(plate), omit_background=True)
                scan["plate"] = str(plate)
                return dur, hold, ts[i_in], ts[i_out], scan
            finally:
                await browser.close()

    dur, hold, t_in, t_out, scan = E._run_async(job)
    layers = _clean(scan["layers"])
    fams = _fonts(layers, fonts or {})
    spec = {"name": name, "W": W, "H": H, "dur": round(dur, 3), "hold": round(hold, 3),
            "anim": {"in": round(max(0.2, min(t_in, dur / 3)), 3), "out": round(max(0.2, min(dur - t_out, dur / 3)), 3),
                     "rise": round(H * 0.018, 1)},
            "layers": layers, "fonts": fams, "plate": _trim_plate(scan.get("plate"))}
    (dest / f"{_slug(name)}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return spec


def _trim_plate(p: str | None) -> dict | None:
    """Plate PNG → {png, box} cropped to its visible pixels (None when it is empty)."""
    if not p:
        return None
    from PIL import Image
    with Image.open(p) as im:
        im = im.convert("RGBA")
        bb = im.getchannel("A").point(lambda v: 255 if v > 3 else 0).getbbox()
        if not bb:
            Path(p).unlink(missing_ok=True)
            return None
        im.crop(bb).save(p)
    return {"png": p, "box": [float(bb[0]), float(bb[1]), float(bb[2] - bb[0]), float(bb[3] - bb[1])]}


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s or "graphic").strip("-")[:40] or "graphic"


def _hex(c) -> str:
    if not c:
        return "#FFFFFF"
    return "#%02X%02X%02X" % tuple(int(round(max(0, min(255, v)))) for v in c[:3])


def _clean(layers: list[dict]) -> list[dict]:
    out = []
    for l in sorted(layers, key=lambda x: x["z"]):
        l = dict(l)
        l["box"] = [round(v, 1) for v in l["box"]]
        if l["type"] == "rect":
            a = (l["color"] or [0, 0, 0, 1])[3]
            l["opacity"] = round(l["opacity"] * a, 3)
            l["color"] = _hex(l["color"])
            l["color2"] = _hex(l["color2"]) if l.get("color2") else None
            if l.get("border"):
                l["border"] = {"w": l["border"]["w"], "color": _hex(l["border"]["color"])}
        elif l["type"] == "text":
            col = l["clip_text"][0] if l.get("clip_text") else l["color"]
            l["opacity"] = round(l["opacity"] * ((col or [0, 0, 0, 1])[3] if col else 1), 3)
            l["color"] = _hex(col)
            l.pop("clip_text", None)
            l["size"] = round(l["size"], 1)
        if l["opacity"] < 0.03:
            continue
        out.append(l)
    return out


def _fonts(layers: list[dict], fonts: dict) -> list[dict]:
    """CSS role faces (sf-head / sf-body) → real families, weights, PostScript names and font files."""
    try:
        from ...motion import _resolve_fonts
        rf = _resolve_fonts(fonts or None)
    except Exception:
        rf = {"head": "Montserrat", "body": "Inter", "head_ar": "Cairo", "body_ar": "Tajawal"}
    seen, out = {}, []
    for l in layers:
        if l["type"] != "text":
            continue
        role = "head" if "head" in l["family"] else "body" if "body" in l["family"] else None
        fam = l["family"]
        if role:
            fam = rf.get(role + "_ar" if l["rtl"] else role) or rf.get(role) or fam
        l["family"] = fam
        key = (fam, l["weight"])
        if key not in seen:
            seen[key] = font_info(fam, l["weight"])
            out.append(seen[key])
        l["font"] = seen[key]["ps"]
        l["font_file"] = seen[key]["file"]
        l["style"] = seen[key]["style"]
    return out


def font_info(family: str, weight: int) -> dict:
    info = {"family": family, "weight": weight, "ps": family.replace(" ", ""), "style": _style_name(weight), "file": ""}
    try:
        from ...design import fonts as F
        from fontTools.ttLib import TTFont
        got = F.ensure(family, [weight])
        p = got.get(weight) or next(iter(got.values()))
        nm = TTFont(str(p))["name"]
        info.update({"ps": nm.getDebugName(6) or info["ps"], "file": str(p),
                     "style": nm.getDebugName(17) or nm.getDebugName(2) or info["style"],
                     "full": nm.getDebugName(4) or family})
    except Exception:
        pass
    return info


def _style_name(w: int) -> str:
    return {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold",
            700: "Bold", 800: "ExtraBold", 900: "Black"}.get(int(round(w / 100.0)) * 100, "Regular")


def caption_spec(cap: dict, W: int, H: int) -> dict:
    """Caption look as text-layer properties (for the native caption track)."""
    g = cap["geometry"]
    fam = cap.get("family") or "Montserrat"
    fam_ar = cap.get("family_ar") or "Tajawal"
    weight = 800 if g["big"] else 700
    lat, ar = font_info(fam, weight), font_info(fam_ar, weight)
    y = {"top": g["margin_v"], "middle": H / 2}.get(g["position"], H - g["margin_v"])
    return {"size": g["size"], "size_ar": round(g["size"] * 1.22), "outline": g["outline"], "box": g["box"],
            "align": g["align"], "anchor_y": round(y, 1), "valign": "top" if g["position"] == "top" else "middle" if g["position"] == "middle" else "bottom",
            "width": W - 2 * g["margin_lr"], "color": "#FFFFFF", "accent": cap.get("accent") or "#FFD400",
            "uppercase": cap.get("uppercase") if cap.get("uppercase") is not None else g["uppercase_default"],
            "font": lat, "font_ar": ar, "style": cap.get("style", "clean")}


def collect_fonts(specs: list[dict], cap: dict | None, dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    files = set()
    for s in specs:
        for f in s.get("fonts", []):
            if f.get("file"):
                files.add(f["file"])
    if cap:
        for k in ("font", "font_ar"):
            if cap.get(k, {}).get("file"):
                files.add(cap[k]["file"])
    out = []
    for f in sorted(files):
        p = Path(f)
        if p.exists():
            shutil.copy2(p, dest / p.name)
            out.append(p.name)
    return out


def unsafe(spec: dict) -> str:
    """Why a snapshot cannot stand for the graphic ('' = fine): overlapping texts mean several states are
    on screen at once (e.g. a button label swapping), no text at all means it is pure motion."""
    texts = [l for l in spec["layers"] if l["type"] == "text"]
    if not texts:
        return "no text to edit"
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            ax, ay, aw, ah = a["box"]
            bx, by, bw, bh = b["box"]
            ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
            iy = max(0, min(ay + ah, by + bh) - max(ay, by))
            if ix * iy > 0.3 * min(aw * ah, bw * bh):
                return "several states overlap"
    return ""


def preview(spec: dict, rendered: Path | None, dest: Path, fonts_dir: Path | None = None) -> dict:
    """Native rebuild (plate + live text drawn with the real fonts) next to the rendered frame at the
    hold moment → side-by-side PNG + a difference score (0 = identical)."""
    import subprocess
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    W, H = spec["W"], spec["H"]
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if spec.get("plate"):
        with Image.open(spec["plate"]["png"]) as pl:
            canvas.alpha_composite(pl.convert("RGBA"), (int(spec["plate"]["box"][0]), int(spec["plate"]["box"][1])))
    dr = ImageDraw.Draw(canvas)
    for l in spec["layers"]:
        if l["type"] != "text":
            continue
        try:
            f = ImageFont.truetype(l.get("font_file") or "", int(round(l["size"])))
        except Exception:
            f = ImageFont.load_default()
        x, y, w, h = l["box"]
        tb = dr.textbbox((0, 0), l["text"], font=f)
        tx = x + (w - (tb[2] - tb[0])) / 2 - tb[0]
        ty = y + (h - (tb[3] - tb[1])) / 2 - tb[1]
        dr.text((tx, ty), l["text"], font=f, fill=l["color"])
    native = canvas
    ref = None
    if rendered and Path(rendered).exists():
        tmp = dest.with_suffix(".ref.png")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{spec['hold']:.3f}", "-i", str(rendered), "-frames:v", "1",
                        "-pix_fmt", "rgba", str(tmp)], check=False)
        if tmp.exists():
            ref = Image.open(tmp).convert("RGBA").resize((W, H))
            tmp.unlink()
    bg = Image.new("RGBA", (W, H), (60, 64, 72, 255))
    a = Image.alpha_composite(bg, native)
    score = None
    if ref is not None:
        b = Image.alpha_composite(bg, ref)
        score = float(np.abs(np.asarray(a, np.float32)[..., :3] - np.asarray(b, np.float32)[..., :3]).mean())
        sheet = Image.new("RGBA", (W * 2 + 20, H), (20, 20, 24, 255))
        sheet.paste(b, (0, 0))
        sheet.paste(a, (W + 20, 0))
    else:
        sheet = a
    sheet.convert("RGB").resize((sheet.width // 3, sheet.height // 3)).save(dest)
    return {"preview": str(dest), "diff": None if score is None else round(score, 2)}
