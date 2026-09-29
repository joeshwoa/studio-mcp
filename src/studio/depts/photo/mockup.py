"""Mockups: corner-pin a design into any photo (perspective + lighting), or use a built-in scene
(phone, poster, cards) generated procedurally — no stock files needed."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ...core.deps import need
from ...core.registry import tool
from ...core.result import Result, ToolError
from ._common import open_image, out_file, parse_color, preview, save_image, stem_of


def _warp(design: Image.Image, quad: list, size: tuple[int, int], ss: int = 2) -> Image.Image:
    """Perspective-warp an RGBA design into quad [TL, TR, BR, BL] on a canvas of `size` (antialiased)."""
    import cv2
    W, H = size
    d = np.asarray(design.convert("RGBA"))
    h, w = d.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32(quad) * ss
    M = cv2.getPerspectiveTransform(src, dst)
    # premultiply to avoid dark fringes
    pm = d.astype(np.float32)
    pm[..., :3] *= pm[..., 3:4] / 255
    out = cv2.warpPerspective(pm, M, (W * ss, H * ss), flags=cv2.INTER_CUBIC if ss == 1 else cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    out = cv2.resize(out, (W, H), interpolation=cv2.INTER_AREA)
    a = np.clip(out[..., 3:4], 0, 255)
    rgb = np.where(a > 0, out[..., :3] * 255 / np.maximum(a, 1e-3), 0)
    res = np.concatenate([np.clip(rgb, 0, 255), a], -1).astype(np.uint8)
    return Image.fromarray(res, "RGBA")


def _fit_to_quad(design: Image.Image, quad: list, fit: str) -> Image.Image:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    qw = (np.hypot(x1 - x0, y1 - y0) + np.hypot(x2 - x3, y2 - y3)) / 2
    qh = (np.hypot(x3 - x0, y3 - y0) + np.hypot(x2 - x1, y2 - y1)) / 2
    r = qw / max(qh, 1)
    if fit == "stretch":
        return design
    scale = max(1.0, 1600 / max(design.size))
    tw = int(max(design.width, design.height * r) * min(scale, 2))
    size = (tw, int(tw / r))
    if fit == "contain":
        c = Image.new("RGBA", size, (255, 255, 255, 255))
        dd = ImageOps.contain(design.convert("RGBA"), size, Image.LANCZOS)
        c.alpha_composite(dd, ((size[0] - dd.width) // 2, (size[1] - dd.height) // 2))
        return c
    return ImageOps.fit(design.convert("RGBA"), size, Image.LANCZOS)


def _shade(photo: Image.Image, quad: list, strength: float, surface: str) -> np.ndarray:
    """Lighting map from the surface in the photo, 1.0 = average brightness of the surface."""
    g = np.asarray(photo.convert("L"), np.float32) / 255
    m = Image.new("L", photo.size, 0)
    ImageDraw.Draw(m).polygon([tuple(p) for p in quad], fill=255)
    mk = np.asarray(m) > 0
    if not mk.any():
        return np.ones_like(g)
    blur_r = max(2, int(np.sqrt(mk.sum()) / 40)) if surface == "print" else max(4, int(np.sqrt(mk.sum()) / 10))
    gb = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur_r)), np.float32) / 255
    mean = float(gb[mk].mean()) or 0.5
    shade = gb / mean
    shade = 1 + (shade - 1) * strength
    return np.clip(shade, 0.25, 1.6)


# ----------------------------------------------------------------------------- scenes
def _noise_texture(size, amount=6, seed=1, scale=2):
    rng = np.random.default_rng(seed)
    w, h = size
    n = rng.normal(0, 1, (h // scale + 1, w // scale + 1)).astype(np.float32)
    n = np.asarray(Image.fromarray(((n * 0.2 + 0.5).clip(0, 1) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32)
    return (n - 128) / 128 * amount


def _backdrop(size, top, bottom, light=(0.3, 0.2), noise=4):
    from .cutout import _background
    bg = np.asarray(_background(f"linear:{top},{bottom},90", size).convert("RGB"), np.float32)
    W, H = size
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    lx, ly = light
    glow = np.exp(-(((xx / W - lx) / 0.55) ** 2 + ((yy / H - ly) / 0.55) ** 2))
    vig = 1 - 0.35 * np.clip(np.sqrt(((xx / W - 0.5) * 1.4) ** 2 + ((yy / H - 0.5) * 1.4) ** 2) - 0.3, 0, 1)
    bg = bg * (0.88 + 0.2 * glow[..., None]) * vig[..., None]
    bg += _noise_texture(size, noise)[..., None]
    return Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8)).convert("RGBA")


def _soft_shadow(size, poly, blur, opacity, offset=(0, 0)):
    sh = Image.new("L", size, 0)
    ImageDraw.Draw(sh).polygon([(x + offset[0], y + offset[1]) for x, y in poly], fill=int(255 * opacity))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    layer.putalpha(sh)
    return layer


def _rrect_mask(size, radius, ss=3):
    m = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, m.width - 1, m.height - 1), radius=radius * ss, fill=255)
    return m.resize(size, Image.LANCZOS)


def scene_phone(design: Image.Image, bg=("#e9e4dc", "#cfc6b8"), W=1600, H=1200, body="#1c1c1e") -> Image.Image:
    # flat phone, then perspective-tilt the whole device
    pw, ph = 900, 1860
    phone = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    bm = _rrect_mask((pw, ph), 150)
    bodyc = Image.new("RGBA", (pw, ph), parse_color(body))
    # subtle metallic edge gradient
    edge = np.asarray(_rrect_mask((pw, ph), 150), np.float32) / 255
    inner = np.asarray(_rrect_mask((pw - 24, ph - 24), 138), np.float32) / 255
    rim = np.zeros((ph, pw), np.float32)
    rim[12:ph - 12, 12:pw - 12] = inner
    rim = edge - rim
    bodyc.putalpha(bm)
    phone.alpha_composite(bodyc)
    rim_l = Image.new("RGBA", (pw, ph), (120, 120, 126, 0))
    rim_l.putalpha(Image.fromarray((np.clip(rim, 0, 1) * 200).astype(np.uint8)))
    phone.alpha_composite(rim_l)
    sx, sy, sw, sh = 36, 36, pw - 72, ph - 72
    dr = design.width / design.height
    if abs(dr - sw / sh) / (sw / sh) > 0.2:
        # design isn't phone-shaped: fit to width and extend with its own edge colours (like an app screen)
        fitted = ImageOps.contain(design.convert("RGBA"), (sw, sh), Image.LANCZOS)
        arr = np.asarray(design.convert("RGB"), np.float32)
        top_c = tuple(int(v) for v in arr[:3].reshape(-1, 3).mean(0)) + (255,)
        bot_c = tuple(int(v) for v in arr[-3:].reshape(-1, 3).mean(0)) + (255,)
        scr = Image.new("RGBA", (sw, sh), top_c)
        oy = (sh - fitted.height) // 2
        ImageDraw.Draw(scr).rectangle((0, oy + fitted.height // 2, sw, sh), fill=bot_c)
        scr.alpha_composite(fitted, ((sw - fitted.width) // 2, oy))
    else:
        scr = ImageOps.fit(design.convert("RGBA"), (sw, sh), Image.LANCZOS)
    scr.putalpha(Image.fromarray(np.minimum(np.asarray(scr.getchannel("A")), np.asarray(_rrect_mask((sw, sh), 118)))))
    phone.alpha_composite(scr, (sx, sy))
    # dynamic island
    isl = Image.new("L", (pw * 3, ph * 3), 0)
    ImageDraw.Draw(isl).rounded_rectangle(((pw / 2 - 110) * 3, 66 * 3, (pw / 2 + 110) * 3, 128 * 3), radius=31 * 3, fill=255)
    il = Image.new("RGBA", (pw, ph), (8, 8, 10, 0))
    il.putalpha(isl.resize((pw, ph), Image.LANCZOS))
    phone.alpha_composite(il)
    # glass reflection
    yy, xx = np.mgrid[0:ph, 0:pw].astype(np.float32)
    refl = np.clip(((xx / pw) * 0.7 - (yy / ph) + 0.35) * 3, 0, 1) * np.clip((-(xx / pw) * 0.7 + (yy / ph) - 0.05) * 3 + 1, 0, 1)
    gl = Image.new("RGBA", (pw, ph), (255, 255, 255, 0))
    gl.putalpha(Image.fromarray((refl * 26 * (np.asarray(bm) / 255)).astype(np.uint8)))
    phone.alpha_composite(gl)
    # place with perspective: slightly rotated & foreshortened
    scale = H * 0.84 / ph
    cw, chh = pw * scale, ph * scale
    cx, cy = W * 0.5, H * 0.5
    a = np.radians(-9)
    def rot(px, py):
        return (cx + px * np.cos(a) - py * np.sin(a), cy + px * np.sin(a) + py * np.cos(a))
    k = 0.035  # top narrower → leaning back
    quad = [rot(-cw / 2 * (1 - k), -chh / 2), rot(cw / 2 * (1 - k), -chh / 2), rot(cw / 2, chh / 2), rot(-cw / 2, chh / 2)]
    canvas = _backdrop((W, H), *bg, light=(0.25, 0.15))
    canvas.alpha_composite(_soft_shadow((W, H), [(x + 40, y + 50) for x, y in quad], 45, 0.35))
    canvas.alpha_composite(_soft_shadow((W, H), [(x + 10, y + 14) for x, y in quad], 12, 0.35))
    canvas.alpha_composite(_warp(phone, quad, (W, H)))
    return canvas


def scene_poster(design: Image.Image, bg=("#efeae2", "#d9d2c6"), W=1600, H=1200, frame="#1f1f1f") -> Image.Image:
    canvas = _backdrop((W, H), *bg, light=(0.2, 0.1), noise=5)
    r = design.width / design.height
    ph = int(H * 0.72)
    pw = int(ph * r)
    if pw > W * 0.6:
        pw = int(W * 0.6)
        ph = int(pw / r)
    fr, mat = max(14, pw // 40), max(30, pw // 11)
    fw, fh = pw + 2 * (fr + mat), ph + 2 * (fr + mat)
    x0, y0 = (W - fw) // 2, int((H - fh) * 0.42)
    canvas.alpha_composite(_soft_shadow((W, H), [(x0, y0), (x0 + fw, y0), (x0 + fw, y0 + fh), (x0, y0 + fh)], 28, 0.45, (16, 26)))
    canvas.alpha_composite(_soft_shadow((W, H), [(x0, y0), (x0 + fw, y0), (x0 + fw, y0 + fh), (x0, y0 + fh)], 6, 0.35, (3, 5)))
    d = ImageDraw.Draw(canvas)
    d.rectangle((x0, y0, x0 + fw, y0 + fh), fill=parse_color(frame))
    d.rectangle((x0 + fr, y0 + fr, x0 + fw - fr, y0 + fh - fr), fill=(247, 245, 240, 255))
    # inner bevel of the mat
    d.rectangle((x0 + fr + mat - 3, y0 + fr + mat - 3, x0 + fr + mat + pw + 2, y0 + fr + mat + ph + 2), fill=(225, 221, 212, 255))
    canvas.alpha_composite(ImageOps.fit(design.convert("RGBA"), (pw, ph), Image.LANCZOS), (x0 + fr + mat, y0 + fr + mat))
    # soft window light across the glass
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    band = np.exp(-((((xx - x0) / fw) - ((yy - y0) / fh) * 0.6 - 0.15) / 0.12) ** 2) * 0.10
    inside = np.zeros((H, W), np.float32)
    inside[y0 + fr:y0 + fh - fr, x0 + fr:x0 + fw - fr] = 1
    gl = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    gl.putalpha(Image.fromarray((band * inside * 255).astype(np.uint8)))
    canvas.alpha_composite(gl)
    return canvas


def scene_cards(design: Image.Image, back: str = "", bg=("#2b2d31", "#17181a"), W=1600, H=1200) -> Image.Image:
    canvas = _backdrop((W, H), *bg, light=(0.35, 0.2), noise=3)
    r = 3.5 / 2
    cw = W * 0.46
    ch = cw / r
    if not back:
        small = np.asarray(design.convert("RGB").resize((32, 32)), np.float32).reshape(-1, 3)
        sat = small.max(1) - small.min(1)
        back = "#%02x%02x%02x" % tuple(int(v) for v in small[np.argmax(sat)]) if sat.max() > 40 else "#f4f1ea"
    def card_quad(cx, cy, ang, tilt):
        a = np.radians(ang)
        pts = []
        for px, py in ((-cw / 2, -ch / 2), (cw / 2, -ch / 2), (cw / 2, ch / 2), (-cw / 2, ch / 2)):
            py2 = py * (1 - tilt) if py < 0 else py
            px2 = px * (1 - tilt * (0.5 - py / ch))
            pts.append((cx + px2 * np.cos(a) - py2 * np.sin(a), cy + px2 * np.sin(a) + py2 * np.cos(a)))
        return pts
    q_back = card_quad(W * 0.60, H * 0.41, 12, 0.0)   # top-down flat-lay: pure rotation reads as natural
    q_front = card_quad(W * 0.42, H * 0.58, -7, 0.0)
    for q, img in ((q_back, Image.new("RGBA", (700, 400), parse_color(back))),
                   (q_front, ImageOps.fit(design.convert("RGBA"), (1400, 800), Image.LANCZOS))):
        canvas.alpha_composite(_soft_shadow((W, H), [(x + 18, y + 30) for x, y in q], 30, 0.55))
        canvas.alpha_composite(_soft_shadow((W, H), [(x + 3, y + 5) for x, y in q], 5, 0.5))
        card = img.copy()
        # paper sheen
        cwp, chp = card.size
        yy, xx = np.mgrid[0:chp, 0:cwp].astype(np.float32)
        sheen = (1 - (xx / cwp) * 0.12 - (yy / chp) * 0.08)[..., None]
        arr = np.asarray(card, np.float32)
        arr[..., :3] *= sheen
        card = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
        canvas.alpha_composite(_warp(card, q, (W, H)))
    return canvas


SCENES = {"phone": scene_phone, "poster": scene_poster, "cards": scene_cards}


@tool("photo")
def photo_mockup(design: str, scene: str = "phone", photo: str = "", corners: list[list[float]] = [],
                 fit: str = "cover", surface: str = "print", lighting: float = 0.8, width: int = 1600, height: int = 1200,
                 background: str = "", format: str = "jpg", quality: int = 92, project: str = "", out: str = "") -> Result:
    """Place a design on a surface. Built-in generated scenes (no stock photos needed): scene='phone'
    (tilted modern smartphone on a soft backdrop — screens, app shots, stories), 'poster' (framed print on
    a wall), 'cards' (business cards on a dark desk, back colour picked from the design). Or corner-pin
    into YOUR photo: photo=path + corners=[[x,y] TL, TR, BR, BL] of the screen/poster/box face; surface=
    'print' multiplies the photo's lighting and texture into the design, 'screen' keeps it emissive with
    a faint glare; lighting 0…1 sets how much. fit: cover | contain | stretch. background='#top,#bottom'
    recolours scene backdrops. Returns the mockup + preview."""
    need("opencv")
    des = open_image(design).convert("RGBA")
    warns = []
    if photo:
        if len(corners) != 4:
            raise ToolError("corners must be 4 points [[x,y],…] in order TL, TR, BR, BL")
        base = open_image(photo).convert("RGBA")
        quad = [(float(x), float(y)) for x, y in corners]
        area = abs(0.5 * sum(quad[i][0] * quad[(i + 1) % 4][1] - quad[(i + 1) % 4][0] * quad[i][1] for i in range(4)))
        if area < 400:
            raise ToolError("the corner quad is tiny or the points are collinear")
        fitted = _fit_to_quad(des, quad, fit)
        warped = _warp(fitted, quad, base.size)
        arr = np.asarray(warped, np.float32)
        if lighting:
            sh = _shade(base, quad, lighting if surface == "print" else lighting * 0.35, surface)
            arr[..., :3] *= sh[..., None]
            if surface == "print":  # carry photo texture (paper grain, fabric) into the print
                g = np.asarray(base.convert("L"), np.float32)
                hp = g - np.asarray(base.convert("L").filter(ImageFilter.GaussianBlur(2)), np.float32)
                arr[..., :3] += hp[..., None] * 0.6 * lighting
        if surface == "screen":
            H, W = arr.shape[:2]
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            glare = np.clip(1 - np.abs((xx / W - yy / H) - 0.1) * 4, 0, 1) * 18
            arr[..., :3] += glare[..., None]
        warped = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
        # 1px edge soften so it sits in the photo
        a = warped.getchannel("A").filter(ImageFilter.GaussianBlur(0.6))
        warped.putalpha(a)
        result = base.copy()
        result.alpha_composite(warped)
        label = "corner-pin"
    else:
        if scene not in SCENES:
            raise ToolError(f"unknown scene {scene!r}", "phone | poster | cards, or pass photo + corners")
        kw = {"W": width, "H": height}
        if background:
            parts = [p.strip() for p in background.split(",")]
            kw["bg"] = (parts[0], parts[-1])
        result = SCENES[scene](des, **kw)
        label = f"scene '{scene}' (procedurally generated backdrop)"
    ext = "." + format.lower().replace("jpeg", "jpg").lstrip(".")
    p = out_file(project, out, stem_of(design, f"mockup-{scene if not photo else 'pin'}"), ext)
    save_image(result.convert("RGB") if ext != ".png" else result, p, quality)
    if not photo and scene == "cards" and abs(des.width / des.height - 1.75) > 0.25:
        warns.append("scene 'cards' expects a business-card design (3.5:2 landscape) — this design was cropped to fit")
    if des.width < 800:
        warns.append(f"design is only {des.width}px wide — it may look soft in the mockup")
    return Result(f"Mockup via {label}: {result.width}×{result.height}.", files=[str(p)], previews=[preview(p)], warnings=warns,
                  data={"mode": label})
