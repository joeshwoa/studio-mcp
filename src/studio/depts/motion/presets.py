"""Ready, agency-grade composition templates for motion_compose. Each builds a full spec from a few
fields, laid out for the canvas (16:9, 9:16, 1:1 …) inside the title-safe area, so the router and the
AI can start from a polished piece and edit the spec JSON afterwards."""
from __future__ import annotations

import math
import re

from ...core.result import ToolError
from . import parse_size

AR = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")


class Canvas:
    def __init__(self, size: str):
        self.w, self.h = parse_size(size or "1920x1080")
        self.u = min(self.w, self.h) / 1080
        self.portrait = self.h > self.w * 1.05
        self.square = not self.portrait and self.w < self.h * 1.3
        self.size = f"{self.w}x{self.h}"

    def s(self, v: float) -> float:  # 1080p-relative units → px
        return round(v * self.u, 2)


def _beats(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n|\|", str(text or "")) if b.strip()]


def _words(s: str) -> int:
    return len([w for w in re.split(r"\s+", s.replace("*", "")) if w])


def _post(grain=4, vignette=0.35, leaks=False, **kw):
    p = {"grain": grain, "vignette": vignette}
    if leaks:
        p["light_leaks"] = leaks if isinstance(leaks, dict) else True
    p.update(kw)
    return p


# ------------------------------------------------------------------------------------------ templates
def kinetic_quote(f: dict, c: Canvas) -> dict:
    """quote (beats split by | or new lines, *emphasis*), author, emph_style highlight|color|underline."""
    beats = _beats(f.get("quote") or f.get("text") or "Make it *simple*.|Make it *move*.")
    rtl = any(AR.search(b) for b in beats)
    layers, t = [], 0.35
    base = c.s(150 if not c.portrait else 120)
    punch = []
    for i, b in enumerate(beats):
        n = _words(b)
        hold = 0.55 + n * 0.26 + 0.6
        size = base * (1.25 if n <= 2 else 1.0 if n <= 5 else 0.8)
        last = i == len(beats) - 1
        layer = {"type": "text", "text": b, "size": round(size), "weight": 900, "align": "center", "w": round(c.w * (0.84 if c.portrait else 0.8)),
                 "y": "48%", "in": round(t, 3), "out": round(t + hold + (1.6 if last else 0), 3), "line_height": 1.4 if rtl else 1.02,
                 "emph_style": f.get("emph_style", "highlight"), "emph_color": "accent", "emph_text": "background",
                 "enter": {"preset": "split-words", "style": "punch", "stagger": 0.17, "duration": 0.55},
                 "animate": [{"preset": "highlight", "at": 0.2 + n * 0.17, "duration": 0.5}] if "*" in b and f.get("emph_style", "highlight") == "highlight" else [],
                 "exit": {"preset": "split-words-out", "style": "rise", "stagger": 0.04, "duration": 0.35} if not last else {"preset": "fade-out", "duration": 0.6}}
        if "*" in b and f.get("emph_style", "highlight") == "highlight":
            layer["emph_style"] = "plain"
        layers.append(layer)
        punch.append(round(t, 2))
        t += hold
    if f.get("author"):
        layers.append({"type": "text", "text": ("— " + f["author"]) if not AR.search(f["author"]) else (f["author"] + " —"), "font": "body", "weight": 600,
                       "size": round(c.s(50)), "color": "muted", "y": "82%" if not c.portrait else "72%", "align": "center",
                       "in": round(t - 1.9, 3), "enter": {"preset": "rise", "duration": 0.8}, "animate": [{"preset": "underline", "at": 0.5, "whole": True, "color": "accent"}]})
    dur = round(t + 0.2, 2)
    return {"name": "kinetic-quote", "size": c.size, "background": {"type": "animated", "blob_colors": ["primary", "accent", "primary"], "intensity": 0.22},
            "post": _post(grain=5, vignette=0.4),
            "scenes": [{"duration": dur, "camera": {"preset": "drift", "punch": punch[1:], "punch_amount": 0.035}, "layers": layers}]}


def stat_reveal(f: dict, c: Canvas) -> dict:
    """value, prefix, suffix, decimals, label, kicker, sublabel, digits latin|arabic; items=[{…}] for several stats (one scene each)."""
    items = f.get("items") or [f]
    scenes = []
    for i, it in enumerate(items):
        val = float(it.get("value", 100))
        label = it.get("label", "Happy customers")
        rtl = bool(AR.search(label))
        dia = round(min(c.h * (0.56 if not c.portrait else 0.36), c.w * 0.8))
        cy = 0.43 if not c.portrait else 0.42
        L = [
            {"type": "shape", "shape": "circle", "w": dia, "h": dia, "y": f"{cy * 100}%", "fill": {"type": "radial", "colors": ["panel", "background"]},
             "opacity": 0.9, "enter": {"preset": "pop", "duration": 1.0, "at": 0.05}},
            {"type": "shape", "shape": "ring", "w": dia, "h": dia, "y": f"{cy * 100}%", "rotation": -90, "stroke": {"colors": ["primary", "accent"], "angle": 120},
             "stroke_width": round(c.s(12)), "enter": {"preset": "draw", "duration": 2.0, "ease": "power3.inOut"}},
            {"type": "counter", "text": "{n}", "prefix": it.get("prefix", ""), "suffix": it.get("suffix", ""), "value": val, "decimals": it.get("decimals", 0),
             "digits": it.get("digits", "arabic" if rtl and it.get("digits") != "latin" else "latin"),
             "size": round(dia * 0.3), "w": round(dia * 0.8), "weight": 900, "y": f"{cy * 100}%", "align": "center", "nowrap": True,
             "animate": [{"preset": "counter", "at": 0.25, "duration": 2.0}], "enter": {"preset": "zoom", "duration": 0.9}},
            {"type": "text", "text": label, "size": round(c.s(64)), "weight": 800, "y": f"{(cy + dia / c.h / 2 + 0.1) * 100:.1f}%", "align": "center",
             "w": round(c.w * 0.86), "enter": {"preset": "split-words", "style": "rise", "at": 0.9, "stagger": 0.08}},
        ]
        if it.get("kicker"):
            L.append({"type": "text", "text": it["kicker"], "font": "body", "size": round(c.s(36)), "weight": 800, "letter_spacing": 0.2, "uppercase": True,
                      "color": "accent", "y": f"{(cy - dia / c.h / 2 - 0.075) * 100:.1f}%", "align": "center", "enter": {"preset": "block", "duration": 0.8}})
        if it.get("sublabel"):
            L.append({"type": "text", "text": it["sublabel"], "font": "body", "size": round(c.s(38)), "color": "muted", "y": f"{(cy + dia / c.h / 2 + 0.18) * 100:.1f}%",
                      "align": "center", "w": round(c.w * 0.75), "enter": {"preset": "fade", "at": 1.3}})
        sc = {"duration": 4.2, "layers": L, "camera": "push-in", "background": {"type": "grid", "spacing": 72, "pattern_opacity": 0.06}}
        if i:
            sc["transition"] = {"type": "push", "duration": 0.7, "direction": "left"}
        scenes.append(sc)
    return {"name": "stat-reveal", "size": c.size, "post": _post(grain=4, vignette=0.35), "scenes": scenes}


def social_promo(f: dict, c: Canvas) -> dict:
    """headline, subline, product (image path), features [≤4 strings], price, old_price, cta, logo (svg/png or 'brand:logo'), handle."""
    head = f.get("headline", "Your new *favourite*")
    feats = (f.get("features") or ["Fast", "Beautiful", "Yours"])[:4]
    P = c.portrait
    rtl = any(AR.search(str(x)) for x in [head, f.get("subline", ""), f.get("cta", "")] + list(feats))
    sc = []
    # 1 hook — kicker above and subline below the headline are stacked on its MEASURED height (any language)
    hs = c.s(130 if not P else 118)
    hw = c.w * 0.86
    hy = 0.47 if f.get("subline") else 0.5
    kicker = f.get("kicker", "جديد" if rtl else "NEW")
    sc.append({"duration": 2.6, "camera": {"preset": "zoom-in", "amount": 0.06}, "background": {"type": "animated", "intensity": 0.3}, "layers": [
        {"type": "text", "id": "hook_head", "text": head, "size": round(hs), "weight": 900, "align": "center", "w": round(hw), "y": f"{hy * 100:.1f}%",
         "emph_color": "primary", "enter": {"preset": "split-words", "style": "punch", "stagger": 0.14, "duration": 0.6}},
        {"type": "text", "text": kicker, "font": "body", "weight": 800, "size": round(c.s(44)), "letter_spacing": 0 if rtl else 0.25, "uppercase": not rtl,
         "bg": {"color": "accent"}, "color": "background", "above": "hook_head", "gap": round(c.s(46)), "enter": {"preset": "pop", "duration": 0.6}} if kicker else None,
        {"type": "text", "text": f.get("subline", ""), "font": "body", "size": round(c.s(52)), "color": "muted", "align": "center", "w": round(c.w * 0.8),
         "below": "hook_head", "gap": round(c.s(54)), "enter": {"preset": "rise", "at": 0.9}} if f.get("subline") else None]})
    # 2 product hero
    prod = []
    if f.get("product"):
        pw = c.w * (0.72 if P else 0.42)
        prod.append({"type": "image", "src": f["product"], "fit": "contain", "w": round(pw), "h": round(c.h * (0.48 if P else 0.7)), "x": "50%" if P else "33%",
                     "y": "42%" if P else "52%", "shadow": True, "enter": {"preset": "pop", "duration": 1.0},
                     "animate": [{"preset": "shine", "at": 1.0}], "loop_anim": {"preset": "float", "amount": 14}})
    else:
        prod.append({"type": "icon", "name": f.get("icon", "star"), "size": round(c.s(320)), "x": "50%" if P else "33%", "y": "42%" if P else "50%",
                     "color": "primary", "enter": {"preset": "elastic"}, "loop_anim": "float"})
    prod += [{"type": "shape", "shape": "ring", "w": round(c.s(140)), "h": round(c.s(140)), "x": "16%" if P else "12%", "y": "22%", "stroke": "accent", "stroke_width": round(c.s(8)),
              "enter": {"preset": "draw", "at": 0.3}, "loop_anim": {"preset": "float", "period": 2.6}},
             {"type": "shape", "shape": "star", "w": round(c.s(70)), "h": round(c.s(70)), "x": "84%" if P else "52%", "y": "16%" if P else "20%", "fill": "accent",
              "enter": {"preset": "spin", "at": 0.5}, "loop_anim": {"preset": "spin-loop", "speed": 40}},
             {"type": "text", "text": f.get("product_line") or f.get("name") or "", "size": round(c.s(92 if not P else 84)), "weight": 900,
              "w": round(c.w * (0.84 if P else 0.42)), "x": "50%" if P else "73%", "y": "78%" if P else "45%", "align": "center" if P else "start",
              "anchor": "center" if P else "left", "enter": {"preset": "split-lines", "style": "rise", "at": 0.4}}]
    if not prod[-1]["text"]:  # no copy invented: without product_line/name the product stands alone
        prod.pop()
        if f.get("product"):
            prod[0]["y"] = "50%"
            prod[0]["h"] = round(c.h * (0.6 if P else 0.75))
    elif not P:
        prod[-1]["x"] = "55%"
    sc.append({"duration": 3.2, "transition": {"type": "whip", "duration": 0.55}, "background": {"type": "animated", "blob_colors": ["accent", "primary", "accent"], "intensity": 0.22},
               "camera": "drift", "layers": prod})
    # 3 features
    fl = []
    n = len(feats)
    for i, ft in enumerate(feats):
        yy = (0.3 + i * (0.5 / max(1, n - 1))) if n > 1 else 0.5
        xi = 0.22 if not P else 0.16
        tx = xi + (0.07 if not P else 0.12)
        fl.append({"type": "icon", "name": ["check", "bolt", "heart", "star"][i % 4], "size": round(c.s(84)), "bg": "primary", "color": "on_primary", "pad": 0.26,
                   "x": f"{(1 - xi if rtl else xi) * 100:.1f}%", "y": f"{yy * 100}%", "in": 0.2 + i * 0.35, "enter": "pop"})
        fl.append({"type": "text", "text": ft, "size": round(c.s(84 if not P else 66)), "weight": 800, "x": f"{(1 - tx if rtl else tx) * 100:.1f}%",
                   "y": f"{yy * 100}%", "anchor": "right" if rtl else "left", "align": "start", "w": round(c.w * (0.66 if not P else 0.68)),
                   "in": 0.25 + i * 0.35, "enter": {"preset": "block", "duration": 0.7, "color": "accent"}})
    sc.append({"duration": 1.2 + n * 0.55 + 1.2, "transition": {"type": "shape", "duration": 0.8}, "background": {"type": "grid", "pattern_opacity": 0.05}, "layers": fl, "camera": "push-in"})
    # 4 offer
    if f.get("price"):
        off = [{"type": "text", "text": f.get("offer_label", "دلوقتي بـ" if rtl else "Now only"), "font": "body", "size": round(c.s(44)), "color": "muted", "y": "32%", "align": "center",
                "enter": "rise"},
               {"type": "text", "text": f["price"], "size": round(c.s(220)), "weight": 900, "y": "50%", "align": "center", "color": "accent",
                "enter": {"preset": "zoom", "duration": 0.9}, "animate": [{"preset": "shine", "at": 1.0}], "glow": "accent"}]
        if f.get("old_price"):
            off.append({"type": "text", "text": f["old_price"], "font": "body", "size": round(c.s(56)), "color": "muted", "y": "67%", "align": "center",
                        "style": {"textDecoration": "line-through"}, "enter": {"preset": "fade", "at": 0.6}})
        sc.append({"duration": 2.6, "transition": {"type": "zoom", "duration": 0.6}, "background": {"type": "animated"}, "camera": {"preset": "handheld", "amount": 0.6}, "layers": off})
    # 5 CTA
    cta = [{"type": "text", "text": f.get("cta", "اطلب دلوقتي" if rtl else "Shop now"), "size": round(c.s(84)), "weight": 800, "color": "on_primary", "bg": {"color": "primary", "pad": [0.8, 0.32], "radius": c.s(80)},
            "y": "56%" if f.get("logo") else "50%", "enter": {"preset": "pop", "duration": 0.7}, "loop_anim": {"preset": "pulse", "amount": 0.04, "period": 1.1}}]
    if f.get("logo"):
        is_svg = str(f["logo"]).endswith(".svg") or str(f["logo"]).startswith("brand:")
        cta.insert(0, {"type": "svg" if is_svg else "image", "src": f["logo"], "fit": "contain", "w": round(c.w * (0.5 if P else 0.28)), "h": round(c.h * 0.14),
                       "y": "33%", "enter": {"preset": "draw" if is_svg else "pop", "duration": 1.2}})
    if f.get("handle"):
        cta.append({"type": "text", "text": f["handle"], "font": "body", "size": round(c.s(44)), "color": "muted", "y": "72%", "enter": {"preset": "rise", "at": 0.5}})
    sc.append({"duration": 3.0, "transition": {"type": "iris", "duration": 0.7}, "background": {"type": "animated", "intensity": 0.3}, "camera": "push-in", "layers": cta})
    for s in sc:
        s["layers"] = [l for l in s["layers"] if l]
    return {"name": "social-promo", "size": c.size, "post": _post(grain=4, vignette=0.3, leaks={"intensity": 0.2}), "scenes": sc}


def _phone(c: Canvas, screens: list[str], x: str, y: str, h: float, t0: float, step: float) -> dict:
    ph = h
    pw = ph * 0.49
    r = pw * 0.16
    kids = []
    for i, s in enumerate(screens):
        lay = {"type": "image", "src": s, "fit": "cover", "w": round(pw * 0.92), "h": round(ph * 0.955), "x": round(pw / 2), "y": round(ph / 2), "radius": round(r * 0.8)}
        if i:
            lay["in"] = round(t0 + i * step - 0.6, 3)
            lay["enter"] = {"preset": "slide", "from": "right", "distance": round(pw), "duration": 0.7, "fade": False, "ease": "expo.inOut"}
        kids.append(lay)
    if not screens:  # tasteful generated UI placeholder
        kids += [{"type": "shape", "shape": "rect", "w": round(pw * 0.92), "h": round(ph * 0.955), "x": round(pw / 2), "y": round(ph / 2), "radius": round(r * 0.8),
                  "fill": {"colors": ["primary", "accent"], "angle": 160}},
                 {"type": "shape", "shape": "rect", "w": round(pw * 0.6), "h": round(ph * 0.03), "x": round(pw * 0.38), "y": round(ph * 0.14), "radius": 8, "fill": "white", "opacity": 0.9},
                 *[{"type": "shape", "shape": "rect", "w": round(pw * 0.78), "h": round(ph * 0.14), "x": round(pw / 2), "y": round(ph * (0.3 + k * 0.17)), "radius": round(r * 0.5),
                    "fill": "white", "opacity": 0.18, "in": 0.6 + k * 0.15, "enter": "rise"} for k in range(4)]]
    kids += [{"type": "shape", "shape": "rect", "w": round(pw * 0.28), "h": round(ph * 0.03), "x": round(pw / 2), "y": round(ph * 0.04), "radius": 40, "fill": "#000000"}]
    return {"type": "group", "w": round(pw), "h": round(ph), "x": x, "y": y,
            "style": {"background": "#0B0B0F", "borderRadius": f"{r}px", "boxShadow": f"0 0 0 {c.s(5)}px #2A2C33, 0 {c.s(40)}px {c.s(90)}px rgba(0,0,0,.55)", "overflow": "hidden"},
            "children": kids, "enter": {"preset": "rise", "distance": round(c.s(260)), "duration": 1.2}, "loop_anim": {"preset": "float", "amount": 10, "period": 4}}


def app_promo(f: dict, c: Canvas) -> dict:
    """app_name, tagline, screens [image paths], captions [one per screen], cta, logo."""
    screens = f.get("screens") or []
    caps = f.get("captions") or ["Plan your day", "Track progress", "Share with friends"][: max(1, len(screens) or 3)]
    step = 2.4
    n = max(len(screens), len(caps), 1)
    dur = 1.4 + n * step + 0.6
    P = c.portrait
    ph = c.h * (0.62 if P else 0.8)
    phone = _phone(c, screens, "50%" if P else "68%", "60%" if P else "52%", ph, 1.4, step)
    glow = {"type": "shape", "shape": "circle", "w": round(ph * 1.1), "h": round(ph * 1.1), "x": "50%" if P else "68%", "y": "60%" if P else "52%",
            "fill": {"type": "radial", "colors": ["primary", "transparent"]}, "opacity": 0.45, "enter": {"preset": "scale", "duration": 1.6}, "loop_anim": {"preset": "pulse", "amount": 0.05, "period": 3}}
    L = [glow, phone]
    L.append({"type": "text", "text": f.get("app_name", "Your App"), "size": round(c.s(140 if not P else 110)), "weight": 900,
              "x": "50%" if P else "8%", "y": "12%" if P else "28%", "anchor": "center" if P else "left", "align": "center" if P else "start",
              "w": round(c.w * (0.84 if P else 0.45)), "enter": {"preset": "split-chars", "style": "rise", "stagger": 0.035}})
    for i, cap in enumerate(caps[:n]):
        a, b = 1.4 + i * step, 1.4 + (i + 1) * step
        last = i == n - 1
        L.append({"type": "text", "text": cap, "size": round(c.s(80 if not P else 64)), "weight": 700, "color": "text",
                  "x": "50%" if P else "8%", "y": "21%" if P else "48%", "anchor": "center" if P else "left", "align": "center" if P else "start",
                  "w": round(c.w * (0.84 if P else 0.42)), "in": round(a - 0.3, 3), "out": round(b - 0.25, 3) if not last else None,
                  "enter": {"preset": "split-words", "style": "rise", "stagger": 0.06}, "exit": None if last else {"preset": "fade-out", "duration": 0.3}})
        L.append({"type": "shape", "shape": "rect", "w": round(c.s(70)), "h": round(c.s(8)), "radius": 4, "fill": "accent",
                  "x": "50%" if P else "8%", "y": "26%" if P else "57%", "anchor": "center" if P else "left", "in": round(a - 0.2, 3),
                  "out": round(b - 0.25, 3) if not last else None, "enter": {"preset": "wipe", "duration": 0.5}})
    for l in L:
        for k in [k for k, v in l.items() if v is None]:
            l.pop(k)
    if f.get("tagline") or f.get("cta"):
        L.append({"type": "text", "text": f.get("cta") or f.get("tagline"), "size": round(c.s(44)), "weight": 800, "color": "on_primary",
                  "bg": {"color": "primary", "radius": c.s(60), "pad": [0.8, 0.3]}, "x": "50%" if P else "8%", "y": "93%" if P else "72%",
                  "anchor": "center" if P else "left", "in": round(dur - 1.8, 3), "enter": "pop"})
    return {"name": "app-promo", "size": c.size, "post": _post(grain=3, vignette=0.3),
            "background": {"type": "animated", "intensity": 0.26},
            "scenes": [{"duration": round(dur, 2), "camera": "drift", "layers": L}]}


def logo_sting_pro(f: dict, c: Canvas) -> dict:
    """logo (svg/png path or 'brand:logo'), tagline, accent shapes on/off."""
    logo = f.get("logo") or "brand:logo"
    is_svg = str(logo).endswith(".svg") or str(logo).startswith("brand:")
    lw, lh = c.w * (0.56 if not c.portrait else 0.78), c.h * (0.4 if not c.portrait else 0.2)
    L = []
    for k, (sz, col, at) in enumerate([(760, "primary", 0.0), (560, "accent", 0.12), (360, "primary", 0.24)]):
        L.append({"type": "shape", "shape": "ring", "w": round(c.s(sz)), "h": round(c.s(sz)), "y": "46%", "stroke": col, "stroke_width": round(c.s(4 + k * 2)),
                  "in": at, "out": at + 1.6, "enter": {"preset": "draw", "duration": 0.9, "ease": "power3.out"},
                  "exit": {"preset": "scale-out", "from_scale": 1.5, "duration": 0.6, "ease": "power2.in"}})
    for i in range(8):
        a = i * math.pi / 4
        L.append({"type": "shape", "shape": "rect", "w": round(c.s(90)), "h": round(c.s(10)), "radius": 5, "fill": "accent" if i % 2 else "primary",
                  "x": round(c.w / 2 + math.cos(a) * c.s(300)), "y": round(c.h * 0.46 + math.sin(a) * c.s(300)), "rotation": round(math.degrees(a), 1),
                  "in": 1.0, "out": 1.9, "enter": {"preset": "scale", "from_scale": 0, "duration": 0.35},
                  "animate": [{"preset": "motion-path", "path": [[0, 0], [round(math.cos(a) * c.s(160)), round(math.sin(a) * c.s(160))]], "at": 0, "duration": 0.9, "ease": "expo.out"}],
                  "exit": {"preset": "fade-out", "duration": 0.4}})
    L.append({"type": "svg" if is_svg else "image", "src": logo, "fit": "contain", "w": round(lw), "h": round(lh), "y": "46%", "in": 0.7,
              "enter": {"preset": "draw", "duration": 1.3} if is_svg else {"preset": "zoom", "duration": 1.0},
              "animate": [{"preset": "shine", "at": 2.1, "duration": 1.0}, {"preset": "pulse", "at": 1.95, "amount": 0.03, "period": 0.5, "until": 2.45}]})
    if f.get("tagline"):
        L.append({"type": "text", "text": f["tagline"], "font": "body", "size": round(c.s(58)), "weight": 700, "letter_spacing": 0 if AR.search(f["tagline"]) else 0.14, "color": "muted",
                  "y": "70%" if not c.portrait else "60%", "align": "center", "in": 2.2, "enter": {"preset": "split-words", "style": "blur", "stagger": 0.08}})
    return {"name": "logo-sting", "size": c.size, "post": _post(grain=4, vignette=0.45, leaks={"intensity": 0.25}, glow=0.35),
            "background": {"type": "animated", "intensity": 0.2},
            "scenes": [{"duration": float(f.get("duration", 4.5)), "camera": {"preset": "push-in", "amount": 0.06}, "layers": L}]}


def lower_third_pro(f: dict, c: Canvas) -> dict:
    """name, role, style bar|box|minimal|news, position left|right|center (auto by language), duration. Alpha output."""
    name, role = f.get("name", "Joshua George"), f.get("role", "Founder & Lead Developer")
    rtl = bool(AR.search(name + role))
    pos = f.get("position") or ("right" if rtl else "left")
    style = f.get("style", "bar")
    dur = float(f.get("duration", 6))
    mx = c.w * 0.07
    x = mx if pos == "left" else c.w - mx if pos == "right" else c.w / 2
    anc = "bottom-left" if pos == "left" else "bottom-right" if pos == "right" else "bottom"
    al = "start" if pos != "center" else "center"
    y = c.h * (0.82 if not c.portrait else 0.74)
    ns, rs = c.s(72), c.s(40)
    L = []
    if style in ("bar", "news"):
        L.append({"type": "shape", "shape": "rect", "w": round(c.s(10)), "h": round(ns * 2.25), "fill": "accent", "radius": 3,
                  "x": round(x - (c.s(30) if pos == "left" else -c.s(30))), "y": round(y), "anchor": "bottom-right" if pos == "left" else "bottom-left",
                  "enter": {"preset": "wipe", "from": "bottom", "duration": 0.6}, "exit": {"preset": "wipe-out", "from": "bottom", "duration": 0.4}})
    if style == "news":
        L.append({"type": "text", "text": f.get("tag", "LIVE" if not rtl else "مباشر"), "font": "body", "size": round(rs * 0.8), "weight": 800, "color": "white",
                  "bg": {"color": "#E11D48", "radius": 4, "pad": [0.5, 0.18]}, "x": round(x), "y": round(y - ns * 2.45), "anchor": anc, "align": al,
                  "in": 0.3, "enter": "pop", "exit": {"preset": "fade-out", "duration": 0.3}})
    box = style in ("box", "news")
    L.append({"type": "text", "text": name, "size": round(ns), "weight": 800, "color": "background" if box else "text",
              "bg": {"color": "text", "radius": 6, "pad": [0.45, 0.16]} if box else None, "x": round(x), "y": round(y - rs * 1.9), "anchor": anc, "align": al,
              "nowrap": True, "shadow": not box, "in": 0.15, "enter": {"preset": "block", "duration": 0.8, "color": "primary"},
              "exit": {"preset": "wipe-out", "duration": 0.45, "from": "left" if pos != "right" else "right"}})
    L.append({"type": "text", "text": role, "font": "body", "size": round(rs), "weight": 600, "color": "white" if box else "accent",
              "bg": {"color": "primary", "radius": 6, "pad": [0.6, 0.22]} if box else None, "x": round(x), "y": round(y), "anchor": anc, "align": al,
              "nowrap": True, "shadow": not box, "in": 0.55, "enter": {"preset": "slide", "from": "bottom" if style == "minimal" else ("right" if pos == "right" else "left"), "distance": round(c.s(40)), "duration": 0.7},
              "exit": {"preset": "fade-out", "duration": 0.3}})
    for l in L:
        if l.get("bg") is None:
            l.pop("bg", None)
    return {"name": "lower-third", "size": c.size, "transparent": True, "post": {"grain": 0},
            "scenes": [{"duration": dur, "background": "none", "layers": L}]}


def title_sequence(f: dict, c: Canvas) -> dict:
    """title, subtitle, credits [lines shown before the title], duration per credit. Cinematic: letterbox, grain, light leaks, slow push."""
    credits = f.get("credits") or ["A STUDIO PRODUCTION"]
    sc = []
    for i, cr in enumerate(credits):
        sc.append({"duration": 2.8, "transition": {"type": "crossfade", "duration": 0.9} if i else None, "background": "background",
                   "camera": {"preset": "push-in", "amount": 0.04}, "layers": [
                       {"type": "text", "text": cr, "font": "body", "size": round(c.s(44)), "weight": 500, "letter_spacing": 0.32, "uppercase": True, "color": "muted",
                        "align": "center", "w": round(c.w * 0.8), "y": "50%", "enter": {"preset": "blur", "duration": 1.4, "ease": "power2.out"},
                        "tweens": [{"target": "C", "from": {"letterSpacing": "0.18em"}, "to": {"letterSpacing": "0.4em"}, "at": 0, "duration": 2.8, "ease": "none"}],
                        "exit": {"preset": "blur-out", "duration": 0.8}}]})
    title = f.get("title", "THE FUTURE IS NOW")
    rtl = bool(AR.search(title))
    sc.append({"duration": float(f.get("title_duration", 4.5)), "transition": {"type": "dip", "duration": 1.0, "color": "#000000"}, "background": {"type": "animated", "intensity": 0.18},
               "camera": {"preset": "push-in", "amount": 0.07}, "layers": [
                   {"type": "text", "text": title, "size": round(c.s(150 if not c.portrait else 110)), "weight": 800, "align": "center", "w": round(c.w * 0.86), "y": "48%",
                    "letter_spacing": 0 if rtl else 0.04, "uppercase": not rtl, "glow": "primary",
                    "enter": {"preset": "split-chars", "style": "blur", "stagger": 0.05, "duration": 1.2} if not rtl else {"preset": "split-words", "style": "blur", "stagger": 0.15, "duration": 1.2},
                    "animate": [{"preset": "shine", "at": 2.0, "duration": 1.4}]},
                   {"type": "text", "text": f.get("subtitle", ""), "font": "body", "size": round(c.s(36)), "weight": 500, "letter_spacing": 0.3, "uppercase": True,
                    "color": "muted", "y": "64%", "align": "center", "in": 1.6, "enter": {"preset": "fade", "duration": 1.2}} if f.get("subtitle") else None]})
    for s in sc:
        s["layers"] = [l for l in s["layers"] if l]
        if s.get("transition") is None:
            s.pop("transition", None)
    return {"name": "title-sequence", "size": c.size, "fade_out": 0.8,
            "post": _post(grain=8, vignette=0.55, leaks={"intensity": 0.3}, letterbox=2.39, glow=0.25), "scenes": sc}


def chart_story(f: dict, c: Canvas) -> dict:
    """title, data [{label,value}], kind bar|hbar|line|area|donut, prefix/suffix, highlight (index), annotation {index,text}, takeaway, source."""
    data = f.get("data") or [{"label": "2021", "value": 12}, {"label": "2022", "value": 19}, {"label": "2023", "value": 31}, {"label": "2024", "value": 54}]
    kind = f.get("kind", "bar")
    P = c.portrait
    title = f.get("title", "Revenue keeps *climbing*")
    hl = f.get("highlight", len(data) - 1 if kind in ("bar", "hbar", "column") else None)
    ch = {"type": "chart", "chart": kind, "data": data, "w": round(c.w * (0.84 if P else 0.72)), "h": round(c.h * (0.48 if P else 0.58)),
          "y": "56%" if not P else "52%", "prefix": f.get("prefix", ""), "suffix": f.get("suffix", ""), "decimals": f.get("decimals", 0),
          "at": 0.6, "font_size": round(c.s(38)), "digits": f.get("digits", "latin"), "enter": {"preset": "fade", "duration": 0.4}}
    if hl is not None:
        ch["highlight"] = hl
    if f.get("annotation"):
        an = dict(f["annotation"])
        i = an.get("index", len(data) - 1)
        ch["annotations"] = [an]
    L = [{"type": "text", "text": title, "size": round(c.s(78)), "weight": 800, "y": "13%" if not P else "14%", "align": "center", "w": round(c.w * 0.86),
          "enter": {"preset": "split-words", "style": "rise", "stagger": 0.06}}, ch]
    if f.get("takeaway"):
        L.append({"type": "text", "text": f["takeaway"], "font": "body", "size": round(c.s(38)), "weight": 600, "color": "accent", "y": "90%" if not P else "84%",
                  "align": "center", "w": round(c.w * 0.8), "in": 2.6, "enter": {"preset": "typewriter"}})
    if f.get("source"):
        L.append({"type": "text", "text": f["source"], "font": "body", "size": round(c.s(22)), "color": "muted", "x": round(c.w * 0.05), "y": round(c.h * 0.96),
                  "anchor": "bottom-left", "enter": "fade"})
    return {"name": "chart-story", "size": c.size, "post": _post(grain=3, vignette=0.3),
            "scenes": [{"duration": float(f.get("duration", 6.5)), "background": {"type": "grid", "pattern_opacity": 0.05}, "camera": "push-in", "layers": L}]}


def _smooth(P: list[tuple[float, float]]) -> str:
    """Catmull-Rom spline through the points → cubic Bézier SVG path (no kinks at waypoints)."""
    d = f"M{P[0][0]:.1f},{P[0][1]:.1f}"
    for i in range(1, len(P)):
        p0, p1, p2 = P[i - 2] if i > 1 else P[i - 1], P[i - 1], P[i]
        p3 = P[i + 1] if i + 1 < len(P) else P[i]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return d


def map_route(f: dict, c: Canvas) -> dict:
    """points [[x,y], …] in 0–1 canvas fractions (route waypoints), labels {from, to}, map_svg (optional own SVG map, e.g. drawn
    or public-domain), title. Without map_svg a stylised dotted grid is used (no copyrighted map data)."""
    pts = f.get("points") or [[0.18, 0.7], [0.35, 0.45], [0.55, 0.55], [0.8, 0.28]]
    P = [(p[0] * c.w, p[1] * c.h) for p in pts]
    d = _smooth(P)
    route = {"type": "shape", "shape": "path", "d": d, "w": c.w, "h": c.h, "viewBox": f"0 0 {c.w} {c.h}", "fill": "none", "stroke": "accent",
             "stroke_width": round(c.s(7)), "enter": {"preset": "draw", "at": 0.8, "duration": 2.6, "ease": "power2.inOut"}}
    ghost = {"type": "shape", "shape": "path", "d": d, "w": c.w, "h": c.h, "viewBox": f"0 0 {c.w} {c.h}", "fill": "none", "stroke": "text",
             "stroke_width": round(c.s(3)), "dash": f"{c.s(4)} {c.s(14)}", "opacity": 0.35, "enter": {"preset": "fade", "at": 0.2, "duration": 0.8}}
    L = []
    if f.get("map_svg"):
        L.append({"type": "svg", "src": f["map_svg"], "w": c.w, "h": c.h, "fit": "cover", "opacity": 0.35, "enter": "fade"})
    L += [ghost, route]
    labels = f.get("labels") or {}
    for k, (px_, py_) in ((0, P[0]), (1, P[-1])):
        L.append({"type": "shape", "shape": "circle", "w": round(c.s(30)), "h": round(c.s(30)), "x": round(px_), "y": round(py_), "fill": "primary" if k == 0 else "accent",
                  "in": 0.6 if k == 0 else 3.3, "enter": {"preset": "pop", "duration": 0.5}, "loop_anim": {"preset": "pulse", "amount": 0.25, "period": 1.2}})
        L.append({"type": "shape", "shape": "ring", "w": round(c.s(70)), "h": round(c.s(70)), "x": round(px_), "y": round(py_), "stroke": "primary" if k == 0 else "accent",
                  "stroke_width": round(c.s(3)), "in": 0.6 if k == 0 else 3.3, "enter": {"preset": "scale", "from_scale": 0, "duration": 0.9}, "opacity": 0.6})
        lab = labels.get("from" if k == 0 else "to")
        if lab:
            L.append({"type": "text", "text": lab, "font": "body", "size": round(c.s(44)), "weight": 700, "x": round(px_), "y": round(py_ + c.s(52)), "anchor": "top",
                      "align": "center", "nowrap": True, "bg": {"color": "panel", "radius": 10, "pad": [0.5, 0.2]}, "in": 0.8 if k == 0 else 3.5, "enter": "pop"})
    rel = _smooth([(x - P[0][0], y - P[0][1]) for x, y in P])
    L.append({"type": "icon", "name": "pin", "size": round(c.s(88)), "color": "accent", "x": round(P[0][0]), "y": round(P[0][1]), "anchor": "bottom",
              "in": 0.7, "enter": {"preset": "drop", "duration": 0.6},
              "animate": [{"preset": "motion-path", "path": rel, "at": 0.1, "duration": 2.6, "ease": "power2.inOut"}]})
    if f.get("title"):
        L.append({"type": "text", "text": f["title"], "size": round(c.s(70)), "weight": 800, "x": round(c.w * 0.06), "y": round(c.h * 0.1), "anchor": "top-left",
                  "align": "start", "enter": {"preset": "split-words", "style": "rise"}})
    return {"name": "map-route", "size": c.size, "post": _post(grain=3, vignette=0.4),
            "scenes": [{"duration": float(f.get("duration", 5.5)), "camera": {"preset": "push-in", "amount": 0.05},
                        "background": {"type": "dots", "spacing": 30, "pattern_opacity": 0.22}, "layers": L}]}


def countdown_pro(f: dict, c: Canvas) -> dict:
    """start (≤10), end_text, digits latin|arabic."""
    n = max(1, min(int(f.get("start", 5)), 10))
    dig = f.get("digits", "latin")
    L = []
    for i in range(n):
        v = n - i
        txt = "".join("٠١٢٣٤٥٦٧٨٩"[int(ch)] for ch in str(v)) if dig == "arabic" else str(v)
        L.append({"type": "shape", "shape": "ring", "w": round(c.s(560)), "h": round(c.s(560)), "stroke": "primary", "stroke_width": round(c.s(14)), "in": i, "out": i + 1,
                  "rotation": -90, "enter": {"preset": "draw", "duration": 1.0, "ease": "none"}})
        L.append({"type": "text", "text": txt, "size": round(c.s(330)), "weight": 900, "align": "center", "in": i, "out": i + 1,
                  "enter": {"preset": "zoom", "duration": 0.45, "from_scale": 1.8}, "exit": {"preset": "scale-out", "duration": 0.2}})
    L.insert(0, {"type": "shape", "shape": "ring", "w": round(c.s(560)), "h": round(c.s(560)), "stroke": "muted", "stroke_width": round(c.s(14)), "opacity": 0.18, "out": n})
    end = f.get("end_text", "")
    if end:
        L.append({"type": "text", "text": end, "size": round(c.s(260)), "weight": 900, "color": "accent", "align": "center", "in": n,
                  "enter": {"preset": "pop", "duration": 0.6}, "glow": "accent"})
    return {"name": "countdown", "size": c.size, "post": _post(grain=3, vignette=0.4),
            "scenes": [{"duration": n + (1.6 if end else 0.2), "background": {"type": "animated"}, "camera": {"preset": "drift", "punch": list(range(1, n + 1)), "punch_amount": 0.03},
                        "layers": L}]}


def transition_pro(f: dict, c: Canvas) -> dict:
    """style stripes|diagonal|iris|blocks|slide (alpha overlay; cut at the midpoint), duration, colors [≤3 tokens]."""
    st = f.get("style", "stripes")
    d = float(f.get("duration", 1.2))
    cols = f.get("colors") or ["primary", "accent", "text"]
    L = []
    h2 = d / 2
    if st in ("stripes", "blocks"):
        n = 6 if st == "stripes" else 4
        for i in range(n):
            w = c.w / n + 2
            if st == "stripes":
                L.append({"type": "shape", "shape": "rect", "w": round(w), "h": c.h, "x": round(i * c.w / n), "y": 0, "anchor": "top-left", "fill": cols[i % len(cols)],
                          "keyframes": [{"t": 0 + i * 0.04, "y": -c.h}, {"t": h2 * 0.8 + i * 0.04, "y": 0, "ease": "expo.inOut"},
                                        {"t": h2 + i * 0.04, "y": 0}, {"t": d - 0.04 * (n - i), "y": c.h, "ease": "expo.inOut"}]})
            else:
                for j in range(n):
                    L.append({"type": "shape", "shape": "rect", "w": round(c.w / n + 2), "h": round(c.h / n + 2), "x": round((i + 0.5) * c.w / n), "y": round((j + 0.5) * c.h / n),
                              "fill": cols[(i + j) % len(cols)], "keyframes": [{"t": (i + j) * 0.03, "scale": 0}, {"t": h2 * 0.75 + (i + j) * 0.03, "scale": 1, "ease": "back.out(1.4)"},
                                                                            {"t": h2 + 0.1 + (i + j) * 0.03, "scale": 1}, {"t": d - 0.05, "scale": 0, "ease": "expo.in"}]})
    elif st == "diagonal":
        for i, col in enumerate(cols):
            L.append({"type": "shape", "shape": "rect", "w": round(c.w * 1.6), "h": round(c.h * 1.6), "skew": -18, "fill": col,
                      "keyframes": [{"t": i * 0.06, "x": round(c.w * 1.9)}, {"t": h2 + i * 0.03, "x": round(c.w * 0.5), "ease": "expo.inOut"},
                                    {"t": h2 + 0.05 + (len(cols) - i) * 0.03, "x": round(c.w * 0.5)}, {"t": d, "x": round(-c.w * 0.9), "ease": "expo.inOut"}]})
    elif st == "iris":
        r = math.hypot(c.w, c.h) * 1.05
        for i, col in enumerate(cols[:2]):
            L.append({"type": "shape", "shape": "circle", "w": round(r), "h": round(r), "fill": col,
                      "keyframes": [{"t": i * 0.08, "scale": 0}, {"t": h2 + i * 0.04, "scale": 1, "ease": "expo.inOut"}, {"t": d - 0.02, "scale": 1}]})
        for l in L:  # second half: shrink away
            l["keyframes"].append({"t": d, "scale": 0, "ease": "expo.inOut"})
    else:  # slide
        for i, col in enumerate(cols):
            L.append({"type": "shape", "shape": "rect", "w": c.w, "h": c.h, "fill": col,
                      "keyframes": [{"t": i * 0.07, "x": round(c.w * 1.5)}, {"t": h2 + i * 0.03, "x": round(c.w * 0.5), "ease": "expo.inOut"},
                                    {"t": h2 + 0.05 + (len(cols) - i) * 0.04, "x": round(c.w * 0.5)}, {"t": d, "x": round(-c.w * 0.5), "ease": "expo.inOut"}]})
    return {"name": f"transition-{st}", "size": c.size, "transparent": True, "post": {"grain": 0}, "markers": {"cut": h2},
            "scenes": [{"duration": d, "background": "none", "layers": L}]}


TEMPLATES = {
    "kinetic_quote": (kinetic_quote, "word-by-word punch typography for a quote or hook (Arabic-safe)"),
    "stat_reveal": (stat_reveal, "big number counts up inside a drawn ring, label + kicker; several stats → push transitions"),
    "social_promo": (social_promo, "3–5 scene promo: hook, product hero, features, price, CTA + logo (9:16 or 16:9)"),
    "app_promo": (app_promo, "phone mockup with app screens sliding + captions + CTA"),
    "logo_sting_pro": (logo_sting_pro, "rings + bursts build, logo draws on, shine + glow, tagline"),
    "lower_third_pro": (lower_third_pro, "bar | box | minimal | news lower thirds (alpha)"),
    "title_sequence": (title_sequence, "cinematic credits → title: letterbox, grain, light leaks, tracking text"),
    "chart_story": (chart_story, "animated bar/line/area/donut chart with title, callout and takeaway"),
    "map_route": (map_route, "route draws across a stylised map, pin travels along it, labels pop"),
    "countdown_pro": (countdown_pro, "ring-timer countdown with punchy numbers and end text"),
    "transition_pro": (transition_pro, "alpha transition overlays: stripes | diagonal | iris | blocks | slide (cut at midpoint)"),
}


def build_template(name: str, fields: dict, size: str = "", brand: str = "", base: dict | None = None) -> dict:
    key = name.strip().lower().replace("-", "_")
    if key not in TEMPLATES:
        raise ToolError(f"unknown template {name!r}", "one of " + ", ".join(TEMPLATES))
    fn = TEMPLATES[key][0]
    if key == "logo_sting_pro" and not (fields or {}).get("logo") and not brand:
        raise ToolError("logo_sting_pro needs a logo", "pass fields={'logo': '<path.svg|.png>'} or brand=<saved brand slug>")
    c = Canvas(size or (base or {}).get("size") or ("1080x1920" if key in () else "1920x1080"))
    spec = fn(dict(fields or {}), c)
    if base:  # caller overrides on top (post, colors, fps, …)
        for k, v in base.items():
            if k not in ("scenes", "layers"):
                spec[k] = v
    if brand:
        spec["brand"] = brand
    return spec
