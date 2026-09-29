"""Deterministic, template-based prompt building — no LLM, no machine translation.

The agent calling these tools IS the language model: it should write the English idea
itself (translating the user's Egyptian-Arabic request). These templates then add the
model-specific structure each engine responds to best:

  flux / mflux / hf / pollinations   natural-language sentences, no negative prompt
  sdxl                               comma-separated tags + a real negative prompt
  ltxv                               one chronological paragraph: action → camera → look
  wan                                concise shot description + Wan's long negative
  veo (Google Flow)                  subject / action / scene / camera / light / style / AUDIO
"""
from __future__ import annotations

import re

from .catalog import LTXV_NEGATIVE, SDXL_NEGATIVE, STYLES, WAN_NEGATIVE, colour_name

ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
QUOTED = re.compile(r"[\"“”«»]([^\"“”«»]{1,80})[\"“”«»]")

FAMILY_OF_TARGET = {
    "flux": "nl", "mflux": "nl", "hf": "nl", "pollinations": "nl", "z-image": "nl",
    "sdxl": "tags", "sdxl-turbo": "tags", "ltxv": "ltxv", "wan": "wan", "wan21": "wan", "wan22": "wan", "veo": "veo", "flow": "veo",
}

ASPECT_COMPOSITION = {
    "portrait": "vertical portrait composition with the subject in the upper two-thirds",
    "tall": "tall vertical 9:16 composition, subject centred, clear space top and bottom for captions",
    "landscape": "wide horizontal composition with room to breathe",
    "wide": "ultra-wide cinematic composition",
    "square": "balanced centred square composition",
}


def has_arabic(text: str) -> bool:
    return bool(ARABIC.search(text or ""))


def aspect_word(w: int, h: int) -> str:
    r = w / h
    if r < 0.66:
        return "tall"
    if r < 0.95:
        return "portrait"
    if r <= 1.05:
        return "square"
    if r < 2.0:
        return "landscape"
    return "wide"


def language_warnings(text: str) -> list[str]:
    w = []
    if has_arabic(text):
        w.append("The prompt contains Arabic. Image/video models are trained on English captions and ignore or garble "
                 "Arabic — the agent should write the prompt in English (translate it yourself), and add any Arabic "
                 "headline afterwards with the design tools (proper RTL shaping) rather than asking the model to draw it.")
    q = QUOTED.findall(text or "")
    if q:
        if any(has_arabic(x) for x in q):
            w.append("Quoted Arabic text will NOT render correctly in AI images (no model can shape Arabic script "
                     "reliably). Generate a text-free image and set the Arabic type in the design department.")
        else:
            w.append("Quoted text in the image: FLUX can often spell short English words; SDXL usually misspells. "
                     "For anything that must be exact (brand name, price), overlay real type afterwards.")
    return w


def _palette(brand_colors: list[str] | None) -> str:
    cols = [colour_name(c) for c in (brand_colors or []) if c and c.strip()]
    if not cols:
        return ""
    if len(cols) == 1:
        return f"colour palette dominated by {cols[0]}"
    return "colour palette of " + ", ".join(cols[:-1]) + f" and {cols[-1]}"


def _clean(idea: str) -> str:
    s = re.sub(r"\s+", " ", (idea or "").strip()).rstrip(" .,;")
    return s


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s[:2] != s[:2].upper() else s


def build_image_prompt(idea: str, target: str = "flux", style: str = "none", brand_colors: list[str] | None = None,
                       width: int = 1024, height: int = 1024, negative: str = "", no_text: bool = False,
                       enhance: bool = True) -> dict:
    """→ {prompt, negative, notes, family}. With enhance=False the idea passes through (plus palette)."""
    fam = FAMILY_OF_TARGET.get(target, "nl")
    st = STYLES.get(style)
    if st is None:
        raise ValueError(f"unknown style {style!r}; one of {sorted(STYLES)}")
    idea_c = _clean(idea)
    if not idea_c:
        raise ValueError("empty prompt")
    pal = _palette(brand_colors)
    comp = ASPECT_COMPOSITION[aspect_word(width, height)]
    notes: list[str] = []
    neg_parts = [negative.strip()] if negative.strip() else []

    if fam == "tags":
        parts = [idea_c]
        if enhance:
            parts += [st["tags"], comp.replace(" with", ",").replace(" the ", " ")]
        if pal:
            parts.append(pal)
        if enhance:
            parts.append("masterpiece, best quality, highly detailed")
        prompt = ", ".join(p for p in parts if p)
        neg = ", ".join(p for p in [*neg_parts, st["neg"] if enhance else "", SDXL_NEGATIVE if enhance else ""] if p)
        if no_text and "text" not in neg:
            neg = (neg + ", text, letters, typography").strip(", ")
        return {"prompt": prompt, "negative": neg, "notes": notes, "family": fam}

    # natural language (FLUX family)
    if enhance and st["lead"]:
        body = f"{st['lead']} {_lower_first(idea_c)}"
    else:
        body = idea_c[:1].upper() + idea_c[1:]
    sentences = [body + "."]
    if enhance and st["nl"]:
        sentences.append(st["nl"][:1].upper() + st["nl"][1:] + ".")
    if enhance:
        sentences.append(comp[:1].upper() + comp[1:] + ".")
    if pal:
        sentences.append(pal[:1].upper() + pal[1:] + ".")
    if no_text:
        sentences.append("No text, letters or logos anywhere in the image.")
    if enhance:
        sentences.append("Professional, highly detailed, coherent lighting and anatomy.")
    neg = ", ".join(neg_parts)
    if neg:
        # FLUX-schnell / Z-Image-Turbo run without CFG: a negative prompt has no effect. Fold the most important
        # "avoid" terms into the positive as a gentle instruction instead of silently dropping them.
        sentences.append(f"Avoid: {neg}.")
        notes.append("FLUX-schnell has no negative-prompt branch; your negative terms were appended as an 'Avoid:' "
                     "sentence (weak effect). Use backend=comfyui with an SDXL model for true negative prompts.")
    return {"prompt": " ".join(sentences), "negative": "", "notes": notes, "family": fam}


CAMERA_DEFAULTS = {
    "ltxv": "The camera slowly pushes in",
    "wan": "slow dolly-in camera movement",
}


def build_video_prompt(idea: str, target: str = "ltxv", style: str = "cinematic", camera: str = "",
                       brand_colors: list[str] | None = None, negative: str = "", audio: str = "",
                       dialogue: str = "", duration: float = 4.0, aspect: str = "16:9") -> dict:
    fam = FAMILY_OF_TARGET.get(target, "ltxv")
    st = STYLES.get(style) or STYLES["cinematic"]
    idea_c = _clean(idea)
    if not idea_c:
        raise ValueError("empty prompt")
    pal = _palette(brand_colors)
    notes: list[str] = []
    if fam == "ltxv":
        cam = camera.strip() or CAMERA_DEFAULTS["ltxv"]
        cam = cam[:1].upper() + cam[1:]
        look = st["nl"] or "natural lighting, realistic detail"
        realism = " The scene is captured in real-life footage." if style in ("photoreal", "cinematic", "product", "food", "none") else ""
        prompt = (f"{idea_c[:1].upper() + idea_c[1:]}. {cam}, keeping the subject in focus as the motion unfolds smoothly "
                  f"and continuously. {look[:1].upper() + look[1:]}." + (f" {pal[:1].upper() + pal[1:]}." if pal else "") + realism)
        neg = ", ".join(p for p in [negative.strip(), LTXV_NEGATIVE] if p)
        notes.append("LTX-Video likes long, literal, chronological descriptions (what moves, then how the camera moves, "
                     "then the look). Keep one continuous action per clip.")
        return {"prompt": prompt, "negative": neg, "notes": notes, "family": fam}
    if fam == "wan":
        cam = camera.strip() or CAMERA_DEFAULTS["wan"]
        parts = [idea_c, cam, st["tags"] or "cinematic, natural motion", pal]
        prompt = ", ".join(p for p in parts if p)
        neg = ", ".join(p for p in [negative.strip(), WAN_NEGATIVE] if p)
        return {"prompt": prompt, "negative": neg, "notes": notes, "family": fam}
    # veo / Google Flow brief
    cam = camera.strip() or "slow cinematic dolly-in, eye-level, 35mm lens"
    lines = [
        f"Subject & action: {idea_c}.",
        f"Camera: {cam}.",
        f"Look: {st['nl'] or 'cinematic, natural light, rich detail'}." + (f" {pal[:1].upper() + pal[1:]}." if pal else ""),
        f"Format: {aspect}, {duration:g} s.",
    ]
    if dialogue.strip():
        lines.append(f"Dialogue (spoken on camera, lip-synced): \"{dialogue.strip()}\"")
    lines.append(f"Audio: {audio.strip() or 'natural ambience and subtle foley matching the action; no music unless asked'}.")
    if negative.strip():
        lines.append(f"Avoid: {negative.strip()}.")
    notes.append("Veo 3.1 generates sound too — describe dialogue in quotes, SFX and ambience explicitly. "
                 "Egyptian-Arabic dialogue: write it in Arabic script inside the quotes and say 'spoken in Egyptian Arabic'.")
    return {"prompt": "\n".join(lines), "negative": "", "notes": notes, "family": "veo"}


FLOW_HINTS = re.compile(
    r"\b(dialogue|dialog|says|saying|speaks|speaking|talks|talking|conversation|interview|lip ?sync|voice ?over|"
    r"narrat\w*|character|characters|actor|actress|protagonist|scene \d|short film|commercial|ad film|story|"
    r"cinematic|film)\b|[\"“”«»]", re.I)


def wants_flow(prompt: str) -> tuple[bool, str]:
    """Heuristic: does this video need Google Flow (Veo 3.1 — characters, speech, cinematic storytelling)?"""
    m = FLOW_HINTS.search(prompt or "")
    if has_arabic(prompt or ""):
        return True, "Arabic text/dialogue — local video models can't speak; Veo 3.1 in Flow generates speech"
    if m:
        return True, f"mentions '{m.group(0).strip()}' — characters/dialogue/cinematic storytelling is Veo 3.1's strength"
    return False, ""
