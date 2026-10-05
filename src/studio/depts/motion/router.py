"""motion_plan — pick the right motion engine for a brief and hand back the exact next call."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ...core.registry import tool
from ...core.result import Result

AR = re.compile(r"[؀-ۿ]")

TEMPLATE_WORDS = {
    "kinetic_quote": ["quote", "kinetic", "typograph", "hook", "words", "manifesto", "lyric", "اقتباس", "حكمة", "جملة", "كلام"],
    "stat_reveal": ["stat", "number", "counter", "kpi", "percent", "%", "milestone", "followers", "رقم", "أرقام", "نسبة", "إحصائ"],
    "social_promo": ["promo", " ad", "advert", "product", "sale", "offer", "discount", "launch", "reel", "tiktok", "instagram", "story", "إعلان", "عرض", "خصم", "منتج"],
    "app_promo": ["app", "screens", "screenshot", "ios", "android", "mobile", "تطبيق", "ابلكيشن"],
    "logo_sting_pro": ["logo reveal", "logo sting", "logo intro", "logo animation", "sting", "ident", "شعار", "لوجو"],
    "lower_third_pro": ["lower third", "lower-third", "name tag", "name and title", "super", "اسم ووظيفة"],
    "title_sequence": ["title sequence", "opening", "credits", "cinematic title", "trailer", "film title", "intro title", "تتر", "مقدمة"],
    "chart_story": ["chart", "graph", "data", "growth", "revenue", "sales figures", "bar", "line chart", "infographic", "رسم بياني", "بيانات"],
    "map_route": ["map", "route", "journey", "travel", "delivery", "path", "خريطة", "رحلة", "مسار"],
    "countdown_pro": ["countdown", "timer", "count down", "عد تنازلي"],
    "transition_pro": ["transition", "wipe", "انتقال"],
}
W3D = ["3d", "3-d", "three-d", "three dimensional", "extrud", "metallic", "chrome", "gold text", "glass", "turntable", ".glb", "gltf",
       "particles", "particle", "device mockup", "phone mockup", "laptop mockup", "ثلاثي", "مجسم", "٣د"]
WREAL = ["photoreal", "realistic", "cycles", "caustic", "depth of field", "dof", "bokeh", "blender", "path trac", "physically",
         "high-end render", "hyperreal", "ray trac", "refraction", "واقعي"]
WREMOTION = ["remotion", "react video", "react component", "jsx", "tsx"]
WLOTTIE = ["lottie", "bodymovin", "lottiefiles", "dotlottie", ".lottie"]

MATRIX = [
    ("Typography, titles, lower thirds, infographics, charts, UI/app promos, logo animation (2D), transitions",
     "motion_compose (HTML + GSAP)", "fastest, crisp vector text, every preset/easing, Arabic shaping by Chromium, editable spec + HTML"),
    ("A designer-made animation file (.json / .lottie from After Effects/LottieFiles)", "motion_lottie (lottie-web)",
     "plays the file exactly as designed; recolour to brand, retime, alpha; or a lottie layer in motion_compose"),
    ("3D text/logo, product spin, device mockup, particles — needed fast", "motion_3d (three.js)",
     "real-time WebGL: seconds per second of video, env reflections, shadows, alpha"),
    ("Photoreal 3D: glass, caustics, optical depth of field, physically based light, long render OK", "motion_blender (Blender)",
     "Cycles/EEVEE quality and an editable .blend; minutes per second; falls back to three.js if Blender is missing"),
    ("An existing React/Remotion project or data-driven batch templates (licence OK)", "motion_remotion (Remotion)",
     "reuses the team's React code; needs Node + licence confirmation (≤3 people or company licence)"),
]


def _has(text: str, words: list[str]) -> list[str]:
    return [w.strip() for w in words if w in text]


@tool("motion")
def motion_plan(brief: str, assets: list[str] = [], size: str = "", duration: float = 0, needs_alpha: bool = False,
                needs_3d: bool = False, photoreal: bool = False, arabic: bool = False, brand: str = "") -> Result:
    """Router for motion graphics: describe the job in plain words (+ optional assets, size, duration,
    needs_alpha, needs_3d, photoreal, arabic, brand) and get the best engine with the reasons, the
    exact next tool call (args filled in), a ready spec skeleton/template, and the alternatives.
    Engines: motion_compose (HTML+GSAP: 2D typography/infographics/UI/logo/transitions — default),
    motion_lottie (designer .json/.lottie files), motion_3d (three.js real-time 3D), motion_blender
    (photoreal Blender, slow), motion_remotion (existing React projects, licence-gated). Call this
    first when unsure; it costs nothing and renders nothing."""
    b = " " + (brief or "").lower() + " "
    reasons, alts = [], []
    files = [str(a) for a in (assets or [])]
    exts = {Path(f).suffix.lower() for f in files}
    is_ar = arabic or bool(AR.search(brief or ""))
    alpha = needs_alpha or bool(_has(b, ["transparent", "alpha", "overlay", "lower third", "lower-third", "شفاف"]))
    size = size or ("1080x1920" if _has(b, ["reel", "tiktok", "story", "shorts", "vertical", "9:16", "ريلز"]) else
                    "1080x1080" if _has(b, ["square", "1:1", "post"]) else "1920x1080")
    remotion_proj = next((f for f in files if Path(f).expanduser().is_dir() and (Path(f).expanduser() / "package.json").exists()
                          and "remotion" in (Path(f).expanduser() / "package.json").read_text(errors="ignore")), None)
    lottie_file = next((f for f in files if Path(f).suffix.lower() in (".json", ".lottie")), None)
    glb = next((f for f in files if Path(f).suffix.lower() in (".glb", ".gltf")), None)
    svg = next((f for f in files if Path(f).suffix.lower() == ".svg"), None)
    images = [f for f in files if Path(f).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    w3d, wreal = _has(b, W3D), _has(b, WREAL)

    tscore = {k: len(_has(b, v)) for k, v in TEMPLATE_WORDS.items()}
    template = max(tscore, key=tscore.get) if max(tscore.values()) > 0 else ""

    if remotion_proj or _has(b, WREMOTION):
        eng, tool_ = "remotion", "motion_remotion"
        reasons.append("an existing Remotion/React project was mentioned — reuse it instead of rebuilding")
        reasons.append("needs Node.js and licence_ok=true (≤3-person organisation or a Remotion company licence) — ask the user")
        args = {"project_dir": remotion_proj or "<path to the Remotion project>", "composition": "<CompositionId>", "licence_ok": False}
        alts.append("motion_compose — rebuild it as a GSAP composition (no Node, no licence question)")
    elif lottie_file or _has(b, WLOTTIE):
        if _has(b, ["text", "title", "logo", "caption", "add", "with", "plus", "compose", "نص"]) and len(b) > 60:
            eng, tool_ = "compose+lottie", "motion_compose"
            reasons.append("a Lottie file plus other elements → use it as a lottie LAYER inside a GSAP composition")
            args = {"spec": {"size": size, "scenes": [{"duration": duration or 5, "layers": [
                {"type": "lottie", "src": lottie_file or "<file.json>", "w": "60%", "loop": True},
                {"type": "text", "text": "<headline>", "y": "85%", "enter": {"preset": "split-words", "style": "rise"}}]}]}}
        else:
            eng, tool_ = "lottie", "motion_lottie"
            reasons.append("a designer-made Lottie plays exactly as designed; lottie-web is frame-exact under the virtual clock")
            reasons.append("recolour to brand colours with recolor_mode='brand' + brand=<slug>, retime with speed/loops")
            args = {"src": lottie_file or "<file.json|.lottie>", "transparent": alpha}
            if brand:
                args.update(brand=brand, recolor_mode="brand")
        alts.append("motion_compose — build the animation natively if no Lottie exists yet")
    elif needs_3d or w3d or glb or photoreal or wreal:
        real = photoreal or bool(wreal)
        preset = ("product" if glb or _has(b, ["product", "turntable", "منتج"]) else
                  "device" if _has(b, ["phone", "laptop", "device", "mockup", "screen", "موبايل"]) else
                  "particles" if _has(b, ["particle", "stars", "galaxy", "dust"]) else
                  "logo" if (svg or _has(b, ["logo", "شعار"])) else
                  "abstract" if _has(b, ["abstract", "background", "loop", "shapes"]) else "title")
        if real:
            from .blender import blender_cmd
            have_b = bool(blender_cmd())
            eng, tool_ = "blender", "motion_blender"
            reasons.append("photoreal request (" + ", ".join(wreal or ["photoreal"]) + ") → Blender path tracing/EEVEE, real DOF + motion blur, .blend master")
            if not have_b:
                reasons.append("Blender is NOT installed here → motion_blender will fall back to three.js and say so; install Blender for the real thing")
            bp = {"product": "turntable", "device": "title", "particles": "abstract"}.get(preset, preset)
            args = {"preset": bp, "engine_name": "CYCLES" if _has(b, ["glass", "caustic", "cycles", "refraction"]) else "EEVEE",
                    "quality": "preview", "transparent": alpha}
            alts.append("motion_3d — same idea in real time (seconds instead of minutes), less physically accurate")
        else:
            eng, tool_ = "three", "motion_3d"
            reasons.append("3D (" + ", ".join(w3d or ["3d"]) + ") that must render fast → three.js WebGL (env reflections, soft shadows, ACES), frame-exact")
            if is_ar and preset == "title":
                reasons.append("Arabic 3D text is shaped with HarfBuzz into outlines, then extruded — letters join correctly")
            args = {"preset": preset, "transparent": alpha, "duration": duration or 5}
            alts.append("motion_blender — photoreal glass/caustics/optical DOF when render time is acceptable")
        if preset == "title":
            args["text"] = "<title text>"
        if preset == "logo":
            args["logo"] = svg or ("" if brand else "<logo.svg>")
        if preset == "product":
            args["model"] = glb or "<model.glb>"
        if preset == "device" and images:
            args["screen"] = images[0]
        alts.append("motion_compose layer {type:'3d', scene:{…}} — mix the 3D object with 2D titles/charts in one comp")
    else:
        eng, tool_ = "compose", "motion_compose"
        reasons.append("2D motion design (typography/infographic/UI/logo/transition) → HTML+GSAP composition: crisp text, 40 presets, "
                       "transitions, camera, charts, editable spec + HTML")
        if is_ar:
            reasons.append("Arabic: Chromium shapes it; presets animate Arabic by word/line, never by letter (joining stays intact)")
        args = {"template": template, "fields": {}} if template else {"spec": {"size": size, "scenes": [{"duration": duration or 5, "layers": [
            {"type": "text", "text": "<headline with *emphasis*>", "size": 120, "y": "45%", "align": "center",
             "enter": {"preset": "split-words", "style": "rise"}, "exit": "fade-out"}]}]}}
        alts.append("motion_lottie — if a designer already made the animation as Lottie")
        alts.append("motion_3d — if the brief wants depth/3D objects")
    if tool_ == "motion_compose":
        args.setdefault("size", size)
        if brand:
            args["brand"] = brand
        if alpha:
            args["transparent"] = True
        args["preview"] = True
        reasons.append("start with preview=true (12 stills, seconds), check the sheet, then render the video with preview=false")
        if template:
            reasons.append(f"template '{template}' matches the brief — fill its fields, then edit the saved spec JSON for anything custom")
    elif tool_ in ("motion_3d", "motion_blender"):
        args.setdefault("size", size)
        if brand:
            args["brand"] = brand
    skeleton = None
    if template and tool_ == "motion_compose":
        from .presets import TEMPLATES, build_template
        fdoc = (TEMPLATES[template][0].__doc__ or "").strip()
        try:
            skeleton = build_template(template, {}, size=size)
        except Exception:
            skeleton = None
        args["fields_help"] = fdoc
    summary = f"Use {tool_} ({eng}). " + " ".join(reasons[:2])
    res = Result(summary, data={"engine": eng, "tool": tool_, "args": args, "template": template or None, "reasons": reasons,
                                "alternatives": alts, "size": size, "alpha": alpha, "arabic": is_ar,
                                "decision_matrix": [{"when": a, "use": u, "why": w} for a, u, w in MATRIX]})
    if skeleton:
        res.data["spec_skeleton"] = skeleton
    res.next_steps.append(f"call {tool_}({json.dumps({k: v for k, v in args.items() if k != 'fields_help'}, ensure_ascii=False)[:600]})")
    return res
