"""Headless Chromium (Playwright) renderer: HTML/CSS → PNG / JPG / WebP / vector PDF (+bleed & crop
marks) / SVG, with in-page text fitting and measured QC (overflow, safe zones, WCAG contrast)."""
from __future__ import annotations

import asyncio
import concurrent.futures
import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from ...core import deps
from ...core.result import MissingTool, ToolError
from . import color as col

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "design"
MM = 96 / 25.4  # CSS px per mm


@dataclass
class Canvas:
    w: float                      # CSS px (trim size, or bleed-inclusive size when bleed_mm > 0 — see total)
    h: float
    name: str = "custom"
    print_: bool = False          # print piece: mm-based PDF, 300 dpi PNG
    bleed_mm: float = 0.0
    safe: dict = field(default_factory=dict)       # {top,right,bottom,left} px from the canvas edge
    avoid: list = field(default_factory=list)      # [{x,y,w,h,name}] platform UI overlays
    dpi: int = 300

    @property
    def bleed_px(self) -> float:
        return self.bleed_mm * MM

    @property
    def tw(self) -> float:  # total width incl. bleed
        return self.w + 2 * self.bleed_px

    @property
    def th(self) -> float:
        return self.h + 2 * self.bleed_px

    @property
    def scale(self) -> float:
        return self.dpi / 96 if self.print_ else 1.0

    def mm(self) -> tuple[float, float]:
        return round(self.w / MM, 2), round(self.h / MM, 2)


# ---- browser -----------------------------------------------------------------------------------

def _candidates() -> list[str]:
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""), str(Path.home() / ".cache/ms-playwright"),
             str(Path.home() / "Library/Caches/ms-playwright")]
    pats = ["chromium_headless_shell-*/chrome-linux/headless_shell",
            "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
            "chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell",
            "chromium_headless_shell-*/chrome-mac/headless_shell",
            "chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux64/chrome",
            "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
            "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"]
    out = []
    for r in roots:
        if not r or not Path(r).is_dir():
            continue
        for p in pats:
            out += sorted(glob.glob(str(Path(r) / p)), reverse=True)
    out += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for b in ("chromium", "chromium-browser", "google-chrome"):
        w = shutil.which(b)
        if w:
            out.append(w)
    return [p for p in out if Path(p).exists()]


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception as first:
        for exe in _candidates():
            try:
                return p.chromium.launch(executable_path=exe)
            except Exception:
                continue
        raise MissingTool(f"Chromium for Playwright is not installed ({str(first).splitlines()[0][:160]})",
                          "python -m playwright install chromium   (or: studio install design)")


def with_browser(job):
    """Run job(browser) in a Playwright session. Safe inside the MCP server's asyncio loop
    (the sync API can't run there, so it moves to a worker thread)."""
    deps.need("chromium")

    def _run():
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = _launch(p)
            try:
                return job(b)
            finally:
                b.close()
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if in_loop:
        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            return ex.submit(_run).result()
    return _run()


def studio_js() -> str:
    return (ASSETS / "studio.js").read_text(encoding="utf-8")


# ---- rendering ------------------------------------------------------------------------------------

MARKS_JS = r"""
(cfg) => {
  const {bleed, slug, trimW, trimH} = cfg;           // px
  const st = document.createElement('style');
  st.textContent = `html,body{margin:0;background:#fff!important}
  .sheet{position:relative;width:${trimW + 2*bleed + 2*slug}px;height:${trimH + 2*bleed + 2*slug}px;overflow:hidden;break-after:page;background:#fff}
  .sheet:last-child{break-after:auto}
  .sheet>.page{position:absolute;left:${slug}px;top:${slug}px}
  .cm{position:absolute;background:#000;z-index:9}
  .sheet .lbl{position:absolute;left:${slug + bleed}px;bottom:${Math.max(2, slug*0.25)}px;font:7px/1 Helvetica,Arial,sans-serif;color:#000}`;
  document.head.appendChild(st);
  const L = slug * 0.62, g = slug * 0.18, t = 0.5;   // mark length, gap from bleed, 0.5px ≈ 0.13mm hairline
  const x0 = slug + bleed, y0 = slug + bleed, x1 = x0 + trimW, y1 = y0 + trimH;
  const pages = [...document.querySelectorAll('.page')];
  pages.forEach((pg, i) => {
    const sh = document.createElement('div'); sh.className = 'sheet';
    pg.parentNode.insertBefore(sh, pg); sh.appendChild(pg);
    const m = (x, y, w, h) => { const d = document.createElement('i'); d.className = 'cm';
      Object.assign(d.style, {left: x+'px', top: y+'px', width: w+'px', height: h+'px'}); sh.appendChild(d); };
    for (const x of [x0, x1]) { m(x - t/2, 0, t, slug - bleed - g + 0); m(x - t/2, y1 + bleed + g, t, L); }
    for (const y of [y0, y1]) { m(0, y - t/2, slug - bleed - g, t); m(x1 + bleed + g, y - t/2, L, t); }
    const lb = document.createElement('div'); lb.className = 'lbl';
    lb.textContent = (cfg.label || '') + '  ·  page ' + (i+1) + '/' + pages.length + '  ·  trim ' + cfg.trimMM + ' mm  ·  bleed ' + cfg.bleedMM + ' mm';
    sh.appendChild(lb);
  });
  return pages.length;
}
"""


def _contrast_check(shot_text: Path, shot_bg: Path, texts: list[dict], scale: float, page_h: float,
                    canvas_min: float) -> list[dict]:
    """Measure WCAG contrast of each text box against the pixels actually behind it
    (text hidden render), using the 10th-percentile worst background pixel."""
    try:
        bg = np.asarray(Image.open(shot_bg).convert("RGB")).astype(np.float32)
    except Exception:
        return []
    H, W = bg.shape[:2]
    lin = np.where(bg / 255 <= 0.04045, bg / 255 / 12.92, ((bg / 255 + 0.055) / 1.055) ** 2.4)
    lum = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    out = []
    for t in texts:
        if t.get("decorative"):
            continue
        x0 = int(max(0, t["x"] * scale)); y0 = int(max(0, (t["y"] + t.get("page", 0) * page_h) * scale))
        x1 = int(min(W, (t["x"] + t["w"]) * scale)); y1 = int(min(H, (t["y"] + t.get("page", 0) * page_h + t["h"]) * scale))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        inset = int((y1 - y0) * 0.18)  # line boxes include leading above/below the glyphs
        region = lum[y0 + inset:max(y0 + inset + 1, y1 - inset), x0:x1].ravel()
        try:
            rgb = [float(v) for v in t["color"].split("(")[1].split(")")[0].split(",")[:3]]
            alpha = float(t["color"].split(",")[3].strip(" )")) if t["color"].startswith("rgba") else 1.0
        except Exception:
            continue
        if alpha < 0.05:
            continue
        fl = col.luminance(tuple(rgb))
        ratios = (np.maximum(fl, region) + 0.05) / (np.minimum(fl, region) + 0.05)
        worst = float(np.percentile(ratios, 20))
        med = float(np.median(ratios))
        size = t["size"]
        large = size >= canvas_min * 0.045 or (size >= canvas_min * 0.035 and t["weight"] >= 700)
        need = 3.0 if large else 4.5
        out.append({"text": t["text"], "ratio": round(worst, 2), "median": round(med, 2), "need": need,
                    "ok": worst >= need or (t.get("shadow") and med >= need), "size": round(size, 1)})
    return out


def render(html: Path, canvas: Canvas, outputs: dict[str, Path | list[Path]], pages: int = 1,
           qc: bool = True, fonts_check: list[str] | None = None, min_text_px: float = 0,
           crop_marks: bool = False, label: str = "") -> dict:
    """Render a local HTML file whose design lives in `.page` elements (one per page).

    outputs: {"png": [paths per page] | path, "jpg": ..., "webp": ..., "pdf": path, "pdf_marks": path}
    Returns the QC report dict (fit, overflow, unsafe, contrast …)."""
    html = Path(html).resolve()
    W, H = int(round(canvas.tw)), int(round(canvas.th))

    def job(browser):
        rep: dict = {}
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=canvas.scale)
        page = ctx.new_page()
        page.goto(html.as_uri(), wait_until="load", timeout=60000)
        cfg = {"safe": {k: v + canvas.bleed_px for k, v in canvas.safe.items()} if canvas.safe else None,
               "avoid": [{**a, "x": a["x"] + canvas.bleed_px, "y": a["y"] + canvas.bleed_px} for a in canvas.avoid],
               "fonts": fonts_check or [], "minText": min_text_px}
        has_js = page.evaluate("() => !!(window.__studio && window.__studio.run)")
        if not has_js:
            page.add_script_tag(content=studio_js())
        rep = page.evaluate("(cfg) => window.__studio.run(cfg)", cfg)
        n = page.locator(".page").count() or 1
        rep["pages"] = n
        pngs = outputs.get("png")
        want_png = pngs is not None or "jpg" in outputs or "webp" in outputs
        shots: list[Path] = []
        if want_png or qc:
            base = Path(pngs[0] if isinstance(pngs, list) else pngs) if pngs else Path(str(html) + ".tmp.png")
            for i in range(n):
                p = (pngs[i] if isinstance(pngs, list) and i < len(pngs) else
                     base.with_name(f"{base.stem}-p{i + 1}.png") if n > 1 and not isinstance(pngs, list) else base)
                loc = page.locator(".page").nth(i) if page.locator(".page").count() else None
                if loc:
                    loc.screenshot(path=str(p), animations="disabled")
                else:
                    page.screenshot(path=str(p))
                shots.append(Path(p))
            if qc:
                # full-page shot with and without text for the contrast measurement
                full_t = Path(str(html) + ".qc-t.png"); full_b = Path(str(html) + ".qc-b.png")
                qscale = 1.0
                ctx2 = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=qscale)
                p2 = ctx2.new_page()
                p2.goto(html.as_uri(), wait_until="load", timeout=60000)
                if not p2.evaluate("() => !!(window.__studio && window.__studio.run)"):
                    p2.add_script_tag(content=studio_js())
                r2 = p2.evaluate("(cfg) => window.__studio.run(cfg)", cfg)
                p2.evaluate("() => window.__studio.hideText(true)")
                p2.screenshot(path=str(full_b), full_page=True, animations="disabled")
                ctx2.close()
                ph = H
                # pages stack vertically; compute each page's top from the DOM
                rep["contrast"] = _contrast_check(full_t, full_b, r2.get("texts", []), qscale, ph, min(canvas.w, canvas.h))
                full_b.unlink(missing_ok=True)
        rep["png"] = [str(s) for s in shots]
        if outputs.get("pdf") or outputs.get("pdf_marks"):
            css = f"@page{{size:{W}px {H}px;margin:0}} .page{{break-after:page}} .page:last-of-type{{break-after:auto}} html,body{{margin:0}}"
            if outputs.get("pdf"):
                page.add_style_tag(content=css)
                page.emulate_media(media="print")
                page.pdf(path=str(outputs["pdf"]), width=f"{W}px", height=f"{H}px", print_background=True,
                         prefer_css_page_size=True)
                page.emulate_media(media="screen")
            if outputs.get("pdf_marks"):
                slug = 12 * MM
                page.evaluate(MARKS_JS, {"bleed": canvas.bleed_px, "slug": slug, "trimW": canvas.w, "trimH": canvas.h,
                                         "trimMM": "%g×%g" % canvas.mm(), "bleedMM": "%g" % canvas.bleed_mm, "label": label})
                SW, SH = W + 2 * slug, H + 2 * slug
                page.add_style_tag(content=f"@page{{size:{SW}px {SH}px;margin:0}}")
                page.emulate_media(media="print")
                page.pdf(path=str(outputs["pdf_marks"]), width=f"{SW}px", height=f"{SH}px", print_background=True,
                         prefer_css_page_size=True)
        ctx.close()
        return rep

    rep = with_browser(job)
    shots = [Path(p) for p in rep.get("png", [])]
    for fmt in ("jpg", "webp"):
        if fmt in outputs:
            dests = outputs[fmt] if isinstance(outputs[fmt], list) else [outputs[fmt]] if len(shots) == 1 else \
                [Path(outputs[fmt]).with_name(f"{Path(outputs[fmt]).stem}-p{i + 1}.{fmt}") for i in range(len(shots))]
            for s, d in zip(shots, dests):
                im = Image.open(s).convert("RGB")
                if fmt == "jpg":
                    im.save(d, "JPEG", quality=92, optimize=True, progressive=True, subsampling=0,
                            dpi=(canvas.dpi, canvas.dpi) if canvas.print_ else (72, 72))
                else:
                    im.save(d, "WEBP", quality=90, method=6)
            rep[fmt] = [str(d) for d in dests]
    if outputs.get("png") is None:
        for s in shots:
            s.unlink(missing_ok=True)
        rep["png"] = []
    elif canvas.print_:
        for s in shots:  # tag print PNGs with their dpi
            Image.open(s).save(s, dpi=(canvas.dpi, canvas.dpi))
    return rep


def pdf_to_svg(pdf: Path, svg: Path, page: int = 1) -> Path | None:
    """Vector SVG of one PDF page via poppler's pdftocairo (text becomes outlined paths)."""
    exe = shutil.which("pdftocairo") or ("/opt/homebrew/bin/pdftocairo" if Path("/opt/homebrew/bin/pdftocairo").exists() else None)
    if not exe:
        return None
    r = subprocess.run([exe, "-svg", "-f", str(page), "-l", str(page), str(pdf), str(svg)], capture_output=True, timeout=120)
    return svg if r.returncode == 0 and svg.exists() else None


def pdf_to_cmyk(pdf: Path, dest: Path) -> Path | None:
    """CMYK conversion with Ghostscript (optional) — for printers that refuse RGB PDFs."""
    gs = shutil.which("gs")
    if not gs:
        return None
    r = subprocess.run([gs, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
                        "-sColorConversionStrategy=CMYK", "-sProcessColorModel=DeviceCMYK",
                        "-dPDFSETTINGS=/prepress", f"-sOutputFile={dest}", str(pdf)], capture_output=True, timeout=300)
    return dest if r.returncode == 0 and dest.exists() else None


def qc_warnings(rep: dict) -> list[str]:
    w = []
    if rep.get("overflow"):
        w.append("TEXT DOES NOT FIT even at the minimum size — shorten: " + "; ".join(rep["overflow"][:4]))
    shrunk = [f for f in rep.get("fit", []) if f.get("scale", 1) < 0.62 and f.get("ok")]
    if shrunk:
        w.append("text was shrunk a lot to fit (" + ", ".join(f"{f['box']} ×{f['scale']}" for f in shrunk[:4]) +
                 ") — a shorter headline/body will read better")
    if rep.get("outside"):
        w.append("text runs outside the canvas: " + "; ".join(rep["outside"][:4]))
    if rep.get("unsafe"):
        u = list(dict.fromkeys(rep["unsafe"]))
        w.append("text outside the platform/print safe zone: " + "; ".join(u[:4]) + (f" (+{len(u) - 4} more)" if len(u) > 4 else ""))
    if rep.get("avoid"):
        w.append("text under platform UI (will be covered): " + "; ".join(rep["avoid"][:3]))
    if rep.get("images"):
        w.append("images failed to load: " + ", ".join(rep["images"][:4]))
    if rep.get("fonts"):
        w.append("fonts not loaded (fallback used): " + ", ".join(rep["fonts"]))
    if rep.get("tiny"):
        w.append("text may be too small to read: " + "; ".join(rep["tiny"][:4]))
    bad = [c for c in rep.get("contrast", []) if not c["ok"]]
    if bad:
        w.append("low contrast (WCAG): " + "; ".join(f"'{c['text'][:28]}' {c['ratio']}:1 (needs {c['need']})" for c in bad[:5]))
    return w


def contrast_summary(rep: dict) -> dict:
    cs = rep.get("contrast", [])
    if not cs:
        return {}
    worst = min(cs, key=lambda c: c["ratio"] / c["need"])
    return {"checked": len(cs), "failing": sum(1 for c in cs if not c["ok"]),
            "worst": {"text": worst["text"], "ratio": worst["ratio"], "need": worst["need"]}}
