"""Canvas presets: social sizes (px) with platform safe zones, print sizes (mm) with bleed."""
from __future__ import annotations

from ...core.result import ToolError
from .engine import MM, Canvas

# name: (w, h, unit, description, safe {top,right,bottom,left} in px (screen) — print gets 4 mm, avoid rects)
SOCIAL: dict[str, dict] = {
    "ig_square": {"size": (1080, 1080), "desc": "Instagram/Facebook feed square", "safe": (60, 60, 60, 60)},
    "ig_portrait": {"size": (1080, 1350), "desc": "Instagram feed portrait 4:5 (best reach)", "safe": (60, 60, 60, 60)},
    "ig_story": {"size": (1080, 1920), "desc": "Instagram/Facebook story", "safe": (250, 64, 340, 64),
                 "note": "top 250px (profile bar) and bottom 340px (reply bar) are covered"},
    "ig_reel_cover": {"size": (1080, 1920), "desc": "Reel cover (grid crops to the centre 1080×1440)", "safe": (240, 64, 240, 64)},
    "tiktok": {"size": (1080, 1920), "desc": "TikTok video cover / overlay", "safe": (160, 140, 480, 64),
               "note": "right 140px (buttons), bottom 480px (caption)"},
    "whatsapp_status": {"size": (1080, 1920), "desc": "WhatsApp status", "safe": (200, 64, 260, 64)},
    "fb_post": {"size": (1200, 630), "desc": "Facebook link/landscape post", "safe": (40, 40, 40, 40)},
    "fb_cover": {"size": (1640, 624), "desc": "Facebook page cover (mobile crops the sides)", "safe": (80, 220, 80, 220)},
    "fb_event": {"size": (1920, 1005), "desc": "Facebook event cover", "safe": (60, 60, 60, 60)},
    "linkedin_post": {"size": (1200, 627), "desc": "LinkedIn landscape post", "safe": (40, 40, 40, 40)},
    "linkedin_square": {"size": (1080, 1080), "desc": "LinkedIn square post", "safe": (60, 60, 60, 60)},
    "linkedin_portrait": {"size": (1080, 1350), "desc": "LinkedIn portrait post / carousel page", "safe": (60, 60, 60, 60)},
    "linkedin_cover": {"size": (1584, 396), "desc": "LinkedIn personal banner (photo covers bottom-left)", "safe": (40, 60, 40, 60),
                       "avoid": [(0, 176, 560, 220, "profile photo")]},
    "linkedin_company_cover": {"size": (1128, 191), "desc": "LinkedIn company page cover", "safe": (20, 40, 20, 40)},
    "x_post": {"size": (1600, 900), "desc": "X (Twitter) landscape post", "safe": (40, 40, 40, 40)},
    "x_header": {"size": (1500, 500), "desc": "X (Twitter) header (avatar covers bottom-left)", "safe": (60, 60, 60, 60),
                 "avoid": [(40, 300, 360, 200, "avatar")]},
    "youtube_thumbnail": {"size": (1280, 720), "desc": "YouTube thumbnail (timestamp bottom-right)", "safe": (40, 40, 40, 40),
                          "avoid": [(1080, 630, 200, 90, "timestamp")]},
    "youtube_banner": {"size": (2560, 1440), "desc": "YouTube channel art (all-device safe area 1546×423 centre)",
                       "safe": (508, 507, 509, 507)},
    "pinterest": {"size": (1000, 1500), "desc": "Pinterest pin 2:3", "safe": (50, 50, 90, 50)},
    "twitch_banner": {"size": (1200, 480), "desc": "Twitch profile banner", "safe": (40, 40, 40, 40)},
    "og_image": {"size": (1200, 630), "desc": "Website share image (Open Graph)", "safe": (40, 40, 40, 40)},
    "web_hero": {"size": (1920, 1080), "desc": "Website hero / slide 16:9", "safe": (60, 80, 60, 80)},
    "email_header": {"size": (1200, 400), "desc": "Newsletter header", "safe": (30, 40, 30, 40)},
    "avatar": {"size": (1080, 1080), "desc": "Profile picture (shown as a circle)", "safe": (160, 160, 160, 160)},
}

PRINT: dict[str, dict] = {
    "a3": {"mm": (297, 420), "desc": "A3 poster", "bleed": 3},
    "a4": {"mm": (210, 297), "desc": "A4 portrait (poster, flyer, letterhead, menu)", "bleed": 3},
    "a4_landscape": {"mm": (297, 210), "desc": "A4 landscape (certificate)", "bleed": 3},
    "a5": {"mm": (148, 210), "desc": "A5 flyer", "bleed": 3},
    "a6": {"mm": (105, 148), "desc": "A6 postcard / invitation", "bleed": 3},
    "letter": {"mm": (215.9, 279.4), "desc": "US Letter", "bleed": 3.175},
    "letter_landscape": {"mm": (279.4, 215.9), "desc": "US Letter landscape", "bleed": 3.175},
    "dl": {"mm": (99, 210), "desc": "DL rack card / menu insert", "bleed": 3},
    "poster_50x70": {"mm": (500, 700), "desc": "50×70 cm poster", "bleed": 5},
    "rollup_85x200": {"mm": (850, 2000), "desc": "Roll-up banner 85×200 cm (render at 150 dpi)", "bleed": 5, "dpi": 150},
    "business_card": {"mm": (85, 55), "desc": "Business card EU/ISO 85×55", "bleed": 3},
    "business_card_eg": {"mm": (90, 50), "desc": "Business card 9×5 cm (common in Egypt/Middle East)", "bleed": 3},
    "business_card_us": {"mm": (88.9, 50.8), "desc": "Business card US 3.5×2 in", "bleed": 3.175},
}

ALIASES = {"instagram": "ig_square", "instagram_post": "ig_square", "square": "ig_square", "post": "ig_square",
           "portrait": "ig_portrait", "story": "ig_story", "stories": "ig_story", "reel": "ig_reel_cover",
           "facebook": "fb_post", "linkedin": "linkedin_post", "twitter": "x_post", "x": "x_post",
           "thumbnail": "youtube_thumbnail", "yt_thumbnail": "youtube_thumbnail", "poster": "a3",
           "flyer": "a5", "card": "business_card", "certificate": "a4_landscape", "menu": "a4",
           "letterhead": "a4", "a4_portrait": "a4", "banner": "linkedin_cover", "cover": "fb_cover",
           "carousel": "ig_portrait", "infographic": "ig_story", "og": "og_image", "hero": "web_hero"}


def canvas(name: str = "ig_square", bleed_mm: float | None = None) -> Canvas:
    """Canvas for a preset name, 'WxH' px (e.g. '1200x800') or 'WxHmm' (print)."""
    key = (name or "ig_square").strip().lower()
    key = ALIASES.get(key, key)
    if key in SOCIAL:
        d = SOCIAL[key]
        w, h = d["size"]
        t, r, b, l = d["safe"]
        avoid = [{"x": x, "y": y, "w": aw, "h": ah, "name": n} for x, y, aw, ah, n in d.get("avoid", [])]
        return Canvas(w, h, key, safe={"top": t, "right": r, "bottom": b, "left": l}, avoid=avoid)
    if key in PRINT:
        d = PRINT[key]
        wm, hm = d["mm"]
        bl = d["bleed"] if bleed_mm is None else bleed_mm
        s = 4 * MM  # 4 mm safe inside trim
        return Canvas(wm * MM, hm * MM, key, print_=True, bleed_mm=bl, dpi=d.get("dpi", 300),
                      safe={"top": s, "right": s, "bottom": s, "left": s})
    import re
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|px|in)?", key)
    if m:
        w, h, unit = float(m.group(1)), float(m.group(2)), m.group(3) or "px"
        if unit in ("mm", "in"):
            f = MM if unit == "mm" else 96
            s = 4 * MM
            return Canvas(w * f, h * f, key, print_=True, bleed_mm=3 if bleed_mm is None else bleed_mm,
                          safe={"top": s, "right": s, "bottom": s, "left": s})
        pad = round(min(w, h) * 0.05)
        return Canvas(w, h, key, safe={"top": pad, "right": pad, "bottom": pad, "left": pad})
    raise ToolError(f"unknown size {name!r}", "use design_catalog to list presets, or 'WxH' px / 'WxHmm'")


def catalog() -> list[dict]:
    rows = [{"name": k, "size": f"{v['size'][0]}×{v['size'][1]} px", "use": v["desc"], "note": v.get("note", "")}
            for k, v in SOCIAL.items()]
    rows += [{"name": k, "size": f"{v['mm'][0]:g}×{v['mm'][1]:g} mm (+{v['bleed']:g} mm bleed)", "use": v["desc"]}
             for k, v in PRINT.items()]
    return rows
