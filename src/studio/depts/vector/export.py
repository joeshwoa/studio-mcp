"""vector_export: SVG → PDF / EPS / PNG at any DPI or size, icon sets, favicons, iOS/Android app icons."""
from __future__ import annotations

import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from ...core.deps import have, run
from ...core.qc import sheet_of_images
from ...core.registry import tool
from ...core.result import Result, ToolError
from ._svg import inkscape_run, out_file, out_folder, read_svg, render_png, rsvg, stem_of, svg_size

ICON_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
IOS_LEGACY = [(20, 2), (20, 3), (29, 2), (29, 3), (38, 2), (38, 3), (40, 2), (40, 3), (60, 2), (60, 3), (64, 2), (64, 3),
              (68, 2), (76, 2), (83.5, 2)]
ANDROID = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def _square_svg(svg: Path, padding: float, background: str, radius: float, dest: Path) -> Path:
    """Wrap an SVG into a square canvas with padding (fraction), optional background and corner radius."""
    root = ET.parse(svg).getroot()
    w, h = svg_size(root)
    side = max(w, h) / (1 - 2 * padding)
    ox, oy = (side - w) / 2, (side - h) / 2
    bg = ""
    if background:
        r = radius * side
        bg = f'<rect width="{side:.3f}" height="{side:.3f}" rx="{r:.3f}" fill="{background}"/>'
    import base64
    data = base64.b64encode(svg.read_bytes()).decode()
    dest.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                    f'width="{side:.3f}" height="{side:.3f}" viewBox="0 0 {side:.3f} {side:.3f}">{bg}'
                    f'<image x="{ox:.3f}" y="{oy:.3f}" width="{w:.3f}" height="{h:.3f}" '
                    f'xlink:href="data:image/svg+xml;base64,{data}"/></svg>', encoding="utf-8")
    return dest


def _png(svg: Path, dest: Path, size: int, opaque_bg: str = "") -> Path:
    render_png(svg, dest, width=size, height=size, background=opaque_bg)
    im = Image.open(dest)
    if im.size != (size, size):  # -a keeps aspect; centre on exact square
        c = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        c.alpha_composite(im.convert("RGBA"), ((size - im.width) // 2, (size - im.height) // 2))
        c.save(dest)
    if opaque_bg:
        Image.open(dest).convert("RGB").save(dest)
    return dest


@tool("vector")
def vector_export(svg: str, formats: list[str] = ["pdf", "png"], dpi: int = 300, width: int = 0, sizes: list[int] = [],
                  preset: str = "", padding: float = 0.0, background: str = "", corner_radius: float = 0.0,
                  text_to_path: bool = False, name: str = "", project: str = "", out: str = "") -> Result:
    """Export an SVG. formats: pdf (vector, print), eps (vector, legacy print — Inkscape), png (at `dpi`
    or `width` px), svg (plain copy). sizes=[…] writes square PNGs at each size. preset:
    'icons' → PNG set 16…1024; 'favicon' → favicon.ico (16/32/48), favicon-16/32.png,
    apple-touch-icon 180 (opaque), android-chrome 192/512, icon.svg, site.webmanifest + HTML snippet;
    'app_icon' → iOS AppIcon.appiconset (1024 + legacy sizes, Contents.json, no alpha) and Android
    mipmap-*/ic_launcher(.png/_round) + 512 Play Store icon. padding (0…0.3 of the side), background
    colour and corner_radius (0…0.5) shape the icon tile. text_to_path outlines fonts in PDF/EPS."""
    src, _tree = read_svg(svg)
    stem = name or stem_of(svg)
    files, warns = [], []
    tmpd = Path(tempfile.mkdtemp(prefix="studio-vec-"))
    try:
        if preset:
            folder = out_folder(project, out, f"{stem}-{preset}")
            tile = _square_svg(src, padding, background, corner_radius, tmpd / "tile.svg")
            if preset == "icons":
                for s in sizes or ICON_SIZES:
                    files.append(str(_png(tile, folder / f"{stem}-{s}.png", s)))
            elif preset == "favicon":
                bg_op = background or "#ffffff"
                pngs = {s: _png(tile, tmpd / f"f{s}.png", s) for s in (16, 32, 48)}
                ico = folder / "favicon.ico"
                Image.open(pngs[48]).convert("RGBA").save(ico, sizes=[(16, 16), (32, 32), (48, 48)],
                                                           append_images=[Image.open(pngs[16]), Image.open(pngs[32])])
                files.append(str(ico))
                for s in (16, 32):
                    shutil.copy(pngs[s], folder / f"favicon-{s}x{s}.png")
                    files.append(str(folder / f"favicon-{s}x{s}.png"))
                tile_op = _square_svg(src, max(padding, 0.1), bg_op, corner_radius, tmpd / "tile_op.svg")
                files.append(str(_png(tile_op, folder / "apple-touch-icon.png", 180, bg_op)))
                for s in (192, 512):
                    files.append(str(_png(tile, folder / f"android-chrome-{s}x{s}.png", s)))
                shutil.copy(src, folder / "icon.svg")
                files.append(str(folder / "icon.svg"))
                man = {"name": stem, "short_name": stem, "icons": [
                    {"src": "/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png"}],
                    "theme_color": bg_op, "background_color": bg_op, "display": "standalone"}
                (folder / "site.webmanifest").write_text(json.dumps(man, indent=1))
                files.append(str(folder / "site.webmanifest"))
                snippet = ('<link rel="icon" href="/favicon.ico" sizes="48x48">\n<link rel="icon" href="/icon.svg" type="image/svg+xml">\n'
                           '<link rel="apple-touch-icon" href="/apple-touch-icon.png">\n<link rel="manifest" href="/site.webmanifest">')
                (folder / "head-snippet.html").write_text(snippet + "\n")
                files.append(str(folder / "head-snippet.html"))
            elif preset == "app_icon":
                if not background:
                    warns.append("no background given — iOS icons must be opaque; used white. Pass background='#…' for a brand colour")
                bg = background or "#ffffff"
                ios = folder / "ios" / "AppIcon.appiconset"
                ios.mkdir(parents=True)
                sq = _square_svg(src, padding if padding else 0.12, bg, 0.0, tmpd / "ios.svg")  # iOS applies its own mask
                imgs = [{"filename": "icon-1024.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}]
                _png(sq, ios / "icon-1024.png", 1024, bg)
                files.append(str(ios / "icon-1024.png"))
                for pt, sc in IOS_LEGACY:
                    px = int(round(pt * sc))
                    fn = f"icon-{pt:g}@{sc}x.png"
                    files.append(str(_png(sq, ios / fn, px, bg)))
                    imgs.append({"filename": fn, "idiom": "iphone" if pt in (20, 29, 40, 60) else "universal",
                                 "platform": "ios", "scale": f"{sc}x", "size": f"{pt:g}x{pt:g}"})
                (ios / "Contents.json").write_text(json.dumps({"images": imgs, "info": {"author": "studio", "version": 1}}, indent=1))
                files.append(str(ios / "Contents.json"))
                andr = folder / "android"
                sq_a = _square_svg(src, padding if padding else 0.14, bg, corner_radius or 0.18, tmpd / "and.svg")
                rnd = _square_svg(src, padding if padding else 0.18, bg, 0.5, tmpd / "andr.svg")
                for dens, px in ANDROID.items():
                    d = andr / f"mipmap-{dens}"
                    d.mkdir(parents=True)
                    files.append(str(_png(sq_a, d / "ic_launcher.png", px)))
                    files.append(str(_png(rnd, d / "ic_launcher_round.png", px)))
                files.append(str(_png(sq_a, andr / "playstore-icon-512.png", 512, bg)))
            else:
                raise ToolError(f"unknown preset {preset!r}", "icons | favicon | app_icon")
            pngs = [f for f in files if f.endswith(".png")]
            pick = sorted(pngs, key=lambda f: Image.open(f).width)
            sheet_src = [pick[0], pick[len(pick) // 3], pick[2 * len(pick) // 3], pick[-1]] if len(pick) > 4 else pick
            (folder / "_previews").mkdir(exist_ok=True)
            prev = sheet_of_images(sheet_src, folder / "_previews" / "icons-sheet.png", tile=256, cols=4)
            res = Result(f"{preset} set for {src.name}: {len(files)} files in {folder}.", files=files, previews=[str(prev)],
                         warnings=warns, data={"folder": str(folder)})
            if preset == "favicon":
                res.data["html"] = snippet
            small = Image.open(pick[0])
            if pick and small.width <= 32:
                res.next_steps.append("check the 16/32 px icons in the sheet: thin lines/text vanish at that size — "
                                      "simplify the mark for favicons if they look muddy")
            return res
        for fmt in formats:
            fmt = fmt.lower()
            if fmt == "pdf":
                p = out_file(project, out, stem, ".pdf")
                if text_to_path or not have("rsvg"):
                    inkscape_run([str(src), "--export-type=pdf", f"--export-filename={p}"] + (["--export-text-to-path"] if text_to_path else []))
                else:
                    run([rsvg(), "-f", "pdf", str(src), "-o", str(p)])
                files.append(str(p))
            elif fmt == "eps":
                p = out_file(project, out, stem, ".eps")
                try:
                    inkscape_run([str(src), "--export-type=eps", f"--export-filename={p}"] + (["--export-text-to-path"] if text_to_path else []))
                except ToolError:
                    run([rsvg(), "-f", "eps", str(src), "-o", str(p)])
                    warns.append("EPS written with rsvg (cairo) — Inkscape not available")
                files.append(str(p))
            elif fmt == "png":
                if sizes:
                    for s in sizes:
                        p = out_file(project, out, f"{stem}-{s}", ".png")
                        files.append(str(_png(_square_svg(src, padding, background, corner_radius, tmpd / "t.svg"), p, s)))
                else:
                    p = out_file(project, out, stem + (f"-{width}w" if width else f"-{dpi}dpi"), ".png")
                    if width:
                        render_png(src, p, width=width, background=background)
                    else:  # CSS px are 96/in; physical units (mm/in/pt) were converted to px by svg_size
                        pw, _ph = svg_size(_tree.getroot())
                        render_png(src, p, width=max(1, round(pw * dpi / 96)), background=background)
                        Image.open(p).save(p, dpi=(dpi, dpi))
                    files.append(str(p))
            elif fmt == "svg":
                p = out_file(project, out, stem, ".svg")
                shutil.copy(src, p)
                files.append(str(p))
            else:
                raise ToolError(f"unknown format {fmt!r}", "pdf | eps | png | svg (or preset=icons|favicon|app_icon)")
        prevs = []
        for f in files:
            if f.endswith(".png"):
                prevs.append(f)
                break
        pdfs = [f for f in files if f.endswith(".pdf")]
        if pdfs and shutil.which("pdftoppm"):
            base = Path(pdfs[0]).parent / "_previews" / (Path(pdfs[0]).stem + "-pdf")
            base.parent.mkdir(exist_ok=True)
            run(["pdftoppm", "-png", "-r", "60", "-singlefile", pdfs[0], str(base)])
            prevs.append(str(base) + ".png")
        return Result(f"Exported {src.name} → {', '.join(Path(f).suffix for f in files)}.", files=files, previews=prevs, warnings=warns)
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
