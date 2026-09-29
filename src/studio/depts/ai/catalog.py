"""Static knowledge for the AI department: model files, licences, size and style presets.

Every model listed here is FREE to download. Licences differ — the `licence` and
`commercial` fields are shown to the user before any download and written into every
sidecar JSON, so nobody ships a non-commercial model in a client job by accident.
Sizes were read from the Hugging Face API (2026-09); the installer re-checks them live.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GB = 1_000_000_000


@dataclass(frozen=True)
class ModelFile:
    repo: str            # Hugging Face repo
    path: str            # path inside the repo
    folder: str          # ComfyUI model folder (diffusion_models, text_encoders, vae, checkpoints, upscale_models)
    size: int            # bytes (approximate; re-checked live)

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Bundle:
    """A ready-to-use ComfyUI model set (everything one template needs)."""
    id: str
    title: str
    kind: str                      # image | video | upscale
    family: str                    # flux | sdxl | sdxl-turbo | ltxv | wan21 | wan22 | esrgan
    files: tuple[ModelFile, ...]
    licence: str
    commercial: str                # plain-language commercial-use verdict
    nodes: tuple[str, ...] = ()    # custom node packs needed
    speed_mac16: str = ""          # honest, UNMEASURED estimate for a 16 GB M-series Mac
    notes: str = ""
    defaults: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


# ---- shared files -------------------------------------------------------------------------
T5_Q5 = ModelFile("city96/t5-v1_1-xxl-encoder-gguf", "t5-v1_1-xxl-encoder-Q5_K_M.gguf", "text_encoders", 3_387_000_000)
CLIP_L = ModelFile("comfyanonymous/flux_text_encoders", "clip_l.safetensors", "text_encoders", 246_000_000)
# The FLUX VAE (ae.safetensors) — BFL's own repo is gated, this Apache-2.0 repackage is not.
FLUX_VAE = ModelFile("Comfy-Org/Lumina_Image_2.0_Repackaged", "split_files/vae/ae.safetensors", "vae", 335_000_000)
UMT5_Q5 = ModelFile("city96/umt5-xxl-encoder-gguf", "umt5-xxl-encoder-Q5_K_M.gguf", "text_encoders", 4_146_000_000)

GGUF_NODES = ("ComfyUI-GGUF",)

BUNDLES: dict[str, Bundle] = {b.id: b for b in [
    Bundle("flux-schnell-q4", "FLUX.1-schnell GGUF Q4_K_S (best free all-rounder, fits 16 GB)", "image", "flux",
           (ModelFile("city96/FLUX.1-schnell-gguf", "flux1-schnell-Q4_K_S.gguf", "diffusion_models", 6_784_000_000),
            T5_Q5, CLIP_L, FLUX_VAE),
           "Apache-2.0", "yes — commercial use allowed", GGUF_NODES,
           "≈60–150 s per 1024² image (4 steps) on MPS",
           "GGUF instead of fp8 on purpose: Apple's MPS backend cannot run float8 weights.",
           {"steps": 4, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "area": 1024 * 1024, "multiple": 16}),
    Bundle("flux-schnell-q5", "FLUX.1-schnell GGUF Q5_K_S (a little sharper, +1.5 GB)", "image", "flux",
           (ModelFile("city96/FLUX.1-schnell-gguf", "flux1-schnell-Q5_K_S.gguf", "diffusion_models", 8_263_000_000),
            T5_Q5, CLIP_L, FLUX_VAE),
           "Apache-2.0", "yes — commercial use allowed", GGUF_NODES,
           "≈70–170 s per 1024² image on MPS; close the browser/other apps on 16 GB",
           "", {"steps": 4, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "area": 1024 * 1024, "multiple": 16}),
    Bundle("sdxl-lightning", "SDXL-Lightning 4-step (fast, huge LoRA/ecosystem, supports negative prompts)", "image", "sdxl",
           (ModelFile("ByteDance/SDXL-Lightning", "sdxl_lightning_4step.safetensors", "checkpoints", 6_938_000_000),),
           "CreativeML OpenRAIL++-M", "yes — commercial use allowed (use-based restrictions of OpenRAIL apply)", (),
           "≈15–40 s per 1024² image on MPS",
           "Best for img2img / inpaint / outpaint on a 16 GB Mac (lighter than FLUX).",
           {"steps": 4, "cfg": 1.0, "sampler": "euler", "scheduler": "sgm_uniform", "area": 1024 * 1024, "multiple": 64}),
    Bundle("sdxl-turbo", "SDXL-Turbo (1-step 512² drafts, very fast)", "image", "sdxl-turbo",
           (ModelFile("stabilityai/sdxl-turbo", "sd_xl_turbo_1.0_fp16.safetensors", "checkpoints", 6_938_000_000),),
           "Stability AI Community License", "only if your organisation earns < US$1M/year (free registration with Stability AI)", (),
           "≈3–8 s per 512² image on MPS",
           "Native 512×512; quality is draft-level. Prefer sdxl-lightning for finals.",
           {"steps": 1, "cfg": 1.0, "sampler": "euler_ancestral", "scheduler": "simple", "area": 512 * 512, "multiple": 64}),
    Bundle("realesrgan-x4", "Real-ESRGAN x4plus upscaler (photos, general)", "upscale", "esrgan",
           (ModelFile("Comfy-Org/Real-ESRGAN_repackaged", "RealESRGAN_x4plus.safetensors", "upscale_models", 66_858_000),),
           "BSD-3-Clause", "yes — commercial use allowed", (), "≈5–20 s for 1024²→4096² on MPS", ""),
    Bundle("ultrasharp-4x", "4x-UltraSharp upscaler (crisper detail, popular with AI images)", "upscale", "esrgan",
           (ModelFile("Kim2091/UltraSharp", "4x-UltraSharp.safetensors", "upscale_models", 67_040_000),),
           "CC BY-NC-SA 4.0", "NO — non-commercial only; use realesrgan-x4 for client work", (),
           "≈5–20 s for 1024²→4096² on MPS", ""),
    Bundle("ltxv-2b", "LTX-Video 2B 0.9.8 distilled (fastest local text/image→video)", "video", "ltxv",
           (ModelFile("Lightricks/LTX-Video", "ltxv-2b-0.9.8-distilled.safetensors", "checkpoints", 6_341_000_000), T5_Q5),
           "LTXV Open Weights License", "yes for individuals/companies under US$10M annual revenue", GGUF_NODES,
           "≈3–10 min for a 4 s 768×512 clip (97 frames, 8 steps) on MPS — unmeasured",
           "Checkpoint includes the VAE. T5 text encoder shared with FLUX (GGUF). fp8 variant skipped: MPS has no float8.",
           {"steps": 8, "cfg": 1.0, "fps": 24, "width": 768, "height": 512, "frame_multiple": 8}),
    Bundle("wan21-1.3b", "Wan 2.1 T2V 1.3B (good motion, text→video only)", "video", "wan21",
           (ModelFile("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors",
                      "diffusion_models", 2_838_000_000),
            UMT5_Q5,
            ModelFile("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/vae/wan_2.1_vae.safetensors", "vae", 254_000_000)),
           "Apache-2.0", "yes — commercial use allowed", GGUF_NODES,
           "≈10–25 min for a 2 s 832×480 clip (33 frames, 30 steps) on MPS — unmeasured",
           "", {"steps": 30, "cfg": 6.0, "shift": 8.0, "fps": 16, "width": 832, "height": 480, "frame_multiple": 4}),
    Bundle("wan22-5b-q4", "Wan 2.2 TI2V 5B GGUF Q4_K_M (best local quality, text OR image→video)", "video", "wan22",
           (ModelFile("QuantStack/Wan2.2-TI2V-5B-GGUF", "Wan2.2-TI2V-5B-Q4_K_M.gguf", "diffusion_models", 3_433_000_000),
            UMT5_Q5,
            ModelFile("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/vae/wan2.2_vae.safetensors", "vae", 1_409_000_000)),
           "Apache-2.0", "yes — commercial use allowed", GGUF_NODES,
           "≈20–45 min for a 2 s 832×480 clip (49 frames, 20 steps) on MPS — unmeasured; overnight-batch material",
           "Trained for 1280×704 @24 fps; smaller sizes run faster but soften detail.",
           {"steps": 20, "cfg": 5.0, "shift": 8.0, "fps": 24, "width": 832, "height": 480, "frame_multiple": 4}),
]}

DEFAULT_SETUP = ["flux-schnell-q4", "realesrgan-x4"]

CUSTOM_NODES = {
    "ComfyUI-GGUF": "https://github.com/city96/ComfyUI-GGUF",
}
# node class → custom node pack that provides it (for friendly "missing node" errors)
NODE_PACKS = {"UnetLoaderGGUF": "ComfyUI-GGUF", "DualCLIPLoaderGGUF": "ComfyUI-GGUF", "CLIPLoaderGGUF": "ComfyUI-GGUF"}

COMFY_REPO = "https://github.com/comfyanonymous/ComfyUI"


# ---- mflux (MLX, Apple Silicon) --------------------------------------------------------------
@dataclass(frozen=True)
class MfluxModel:
    id: str
    cli: str                 # executable
    model_arg: str           # --model value
    base_model: str          # --base-model value ("" = none)
    quantize: int            # -q value (0 = pre-quantised weights, no flag)
    steps: int
    size: int
    licence: str
    commercial: str
    speed: str
    repo: str                # HF repo that will be downloaded


MFLUX_MODELS: dict[str, MfluxModel] = {m.id: m for m in [
    MfluxModel("schnell-q4", "mflux-generate", "mflux-community/flux-1-schnell-mflux-q4", "schnell", 0, 4, 9_613_000_000,
               "Apache-2.0", "yes — commercial use allowed", "≈20–60 s per 1024² image (M1 slowest, M4 fastest) — unmeasured",
               "mflux-community/flux-1-schnell-mflux-q4"),
    MfluxModel("schnell-q8", "mflux-generate", "mflux-community/flux-1-schnell-mflux-q8", "schnell", 0, 4, 13_000_000_000,
               "Apache-2.0", "yes — commercial use allowed", "slower than q4, tight on 16 GB", "mflux-community/flux-1-schnell-mflux-q8"),
    MfluxModel("z-image-turbo", "mflux-generate-z-image-turbo", "z-image-turbo", "", 4, 8, 20_000_000_000,
               "Apache-2.0", "yes — commercial use allowed",
               "≈30–90 s per 1024² image, quantised to 4-bit at load time — unmeasured; excellent photorealism",
               "Tongyi-MAI/Z-Image-Turbo"),
]}
MFLUX_UPSCALE = {"cli": "mflux-upscale-seedvr2", "repo": "numz/SeedVR2_comfyUI", "size": 6_783_000_000, "licence": "Apache-2.0"}


# ---- sizes --------------------------------------------------------------------------------------
SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024), "square": (1024, 1024), "instagram-square": (1080, 1080),
    "4:5": (1080, 1350), "portrait": (1080, 1350), "instagram": (1080, 1350), "1080x1350": (1080, 1350),
    "9:16": (1080, 1920), "story": (1080, 1920), "reel": (1080, 1920), "tiktok": (1080, 1920),
    "16:9": (1920, 1080), "landscape": (1920, 1080), "youtube": (1920, 1080),
    "youtube-thumbnail": (1280, 720), "og": (1200, 630), "linkedin": (1200, 627),
    "3:2": (1536, 1024), "2:3": (1024, 1536), "4:3": (1440, 1080), "3:4": (1080, 1440),
    "21:9": (2520, 1080), "a4": (2480, 3508), "a4-landscape": (3508, 2480),
}


def parse_size(size: str) -> tuple[int, int]:
    s = (size or "1:1").strip().lower().replace(" ", "")
    if s in SIZE_PRESETS:
        return SIZE_PRESETS[s]
    for sep in ("x", "×", "*"):
        if sep in s:
            a, _, b = s.partition(sep)
            if a.isdigit() and b.isdigit():
                w, h = int(a), int(b)
                if not (64 <= w <= 8192 and 64 <= h <= 8192):
                    raise ValueError("size must be between 64 and 8192 px per side")
                return w, h
    if ":" in s:
        a, _, b = s.partition(":")
        try:
            ra, rb = float(a), float(b)
        except ValueError:
            ra = rb = 0
        if ra > 0 and rb > 0:
            # 1 MP at that aspect, long side rounded to 8
            h = (1024 * 1024 / (ra / rb)) ** 0.5
            return int(round(h * ra / rb / 8) * 8), int(round(h / 8) * 8)
    raise ValueError(f"unknown size {size!r}: use a preset ({', '.join(sorted(SIZE_PRESETS))}), 'WxH' or 'W:H'")


def gen_size(target: tuple[int, int], area: int, multiple: int, max_side: int = 2048) -> tuple[int, int]:
    """Native generation size for a model: same aspect as target, ~`area` pixels, sides multiple of `multiple`."""
    tw, th = target
    ar = tw / th
    h = (area / ar) ** 0.5
    w = h * ar
    if tw * th < area:          # small targets: don't generate bigger than needed (but ≥ 60% of native area)
        scale = max((tw * th / area) ** 0.5, 0.78)
        w, h = w * scale, h * scale
    w = max(multiple, min(max_side, int(round(w / multiple)) * multiple))
    h = max(multiple, min(max_side, int(round(h / multiple)) * multiple))
    return w, h


# ---- styles ------------------------------------------------------------------------------------
# natural-language fragments (FLUX/Z-Image/Veo), tag fragments (SDXL), extra negatives (SDXL/Wan)
STYLES: dict[str, dict[str, str]] = {
    "none": {"lead": "", "nl": "", "tags": "", "neg": ""},
    "photoreal": {"lead": "A high-end photograph of",
                  "nl": "shot on a full-frame camera with a 50mm prime lens, natural skin and material textures, true-to-life colour, subtle film grain",
                  "tags": "photo, realistic, 50mm, natural light, detailed textures, sharp focus",
                  "neg": "illustration, painting, cartoon, cgi, plastic skin, oversaturated"},
    "cinematic": {"lead": "A cinematic film still of",
                  "nl": "anamorphic lens, shallow depth of field, motivated practical lighting, rich contrast, teal-and-amber colour grade, 35mm film grain",
                  "tags": "cinematic still, anamorphic, shallow depth of field, dramatic lighting, film grain, color graded",
                  "neg": "flat lighting, amateur, snapshot, cartoon"},
    "product": {"lead": "A premium commercial product photograph of",
                "nl": "studio lighting with a large softbox and rim light, seamless backdrop, crisp reflections, precise focus on the product, clean negative space for copy",
                "tags": "product photography, studio lighting, softbox, seamless background, commercial, sharp, clean",
                "neg": "clutter, busy background, hands, text, watermark, distorted product"},
    "editorial": {"lead": "An editorial magazine photograph of",
                  "nl": "confident composition, soft directional window light, refined styling, muted sophisticated palette",
                  "tags": "editorial photography, vogue style, soft window light, refined, muted palette",
                  "neg": "cheesy stock photo, harsh flash, oversaturated"},
    "food": {"lead": "An appetising food photograph of",
             "nl": "overhead or 45-degree angle, soft natural side light, fresh garnish, shallow depth of field, styled props",
             "tags": "food photography, appetizing, soft side light, shallow depth of field, styled",
             "neg": "unappetizing, messy, plastic food, harsh light"},
    "architecture": {"lead": "An architectural photograph of",
                     "nl": "corrected verticals, golden-hour light, clean lines, wide-angle lens, calm sky",
                     "tags": "architecture photography, wide angle, golden hour, clean lines",
                     "neg": "distorted perspective, tilted, clutter"},
    "illustration": {"lead": "A polished digital illustration of",
                     "nl": "clean confident linework, harmonious limited palette, soft shading, storybook quality",
                     "tags": "digital illustration, clean lines, limited palette, soft shading",
                     "neg": "photo, realistic, messy, blurry"},
    "flat-vector": {"lead": "A flat vector illustration of",
                    "nl": "bold geometric shapes, flat colours with no gradients, minimal detail, generous negative space, modern brand-illustration style",
                    "tags": "flat vector illustration, geometric, flat colors, minimal, clean, corporate illustration",
                    "neg": "photo, 3d, gradients, texture, noise, realistic"},
    "3d-render": {"lead": "A 3D render of",
                  "nl": "soft global illumination, smooth clay-like materials, subtle ambient occlusion, pastel studio backdrop, octane-style render",
                  "tags": "3d render, octane render, soft lighting, clay material, pastel background",
                  "neg": "photo, flat, sketch, noisy"},
    "isometric": {"lead": "An isometric 3D illustration of",
                  "nl": "true isometric angle, tidy miniature diorama, soft shadows, clean pastel palette",
                  "tags": "isometric, 3d, diorama, miniature, soft shadows, pastel",
                  "neg": "perspective distortion, photo, messy"},
    "anime": {"lead": "An anime-style illustration of",
              "nl": "expressive characters, clean cel shading, vibrant yet balanced colours, detailed background art",
              "tags": "anime style, cel shading, vibrant, detailed background",
              "neg": "photo, 3d, realistic, deformed"},
    "watercolor": {"lead": "A delicate watercolour painting of",
                   "nl": "soft washes, visible paper texture, gentle colour bleeds, loose expressive brushwork",
                   "tags": "watercolor painting, paper texture, soft washes, loose brushwork",
                   "neg": "photo, digital, sharp edges, 3d"},
    "poster": {"lead": "A bold graphic poster artwork of",
               "nl": "strong central focal point, dramatic scale, limited high-contrast palette, generous clear space at the top for a headline",
               "tags": "graphic poster, bold composition, high contrast, limited palette, negative space",
               "neg": "clutter, busy, low contrast, text, letters"},
    "minimal": {"lead": "A minimalist image of",
                "nl": "a single subject, vast negative space, soft even light, restrained two-tone palette, calm and elegant",
                "tags": "minimalist, negative space, simple, calm, elegant",
                "neg": "clutter, busy, many objects"},
    "luxury": {"lead": "A luxurious, high-end image of",
               "nl": "rich deep tones, polished metal and marble accents, dramatic low-key lighting, refined elegance",
               "tags": "luxury, premium, low key lighting, marble, gold accents, elegant",
               "neg": "cheap, cluttered, plastic, bright flat light"},
    "social-ad": {"lead": "A scroll-stopping social media advertisement visual of",
                  "nl": "bold clear subject, bright punchy colours, clean background with space for a headline and logo, high contrast, mobile-first framing",
                  "tags": "advertising, bold, vibrant, clean background, high contrast, eye-catching",
                  "neg": "clutter, dull colors, text, watermark"},
    "islamic-geometric": {"lead": "An elegant artwork of",
                          "nl": "intricate Islamic geometric patterns and arabesque ornament, mashrabiya lattice shadows, warm Cairo light, refined gold and deep blue accents",
                          "tags": "islamic geometric pattern, arabesque, mashrabiya, ornate, gold and blue",
                          "neg": "text, letters, calligraphy, misspelled writing"},
    "egyptian-heritage": {"lead": "A striking image of",
                          "nl": "inspired by ancient Egyptian art and modern Cairo, warm sandstone and lapis tones, sunlit desert atmosphere, respectful authentic detail",
                          "tags": "ancient egyptian motifs, sandstone, lapis lazuli, desert light",
                          "neg": "text, hieroglyph gibberish, cartoon"},
}

SDXL_NEGATIVE = ("lowres, blurry, jpeg artifacts, watermark, signature, text, logo, deformed, disfigured, "
                 "bad anatomy, extra fingers, mutated hands, poorly drawn face, cropped, worst quality")
# English rendering of Wan's recommended default negative prompt
WAN_NEGATIVE = ("bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, "
                "still, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, "
                "extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, "
                "fused fingers, still picture, messy background, three legs, many people in the background, walking backwards")
LTXV_NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted, watermark, text"

# named colours for brand hints (models understand names better than hex)
COLOUR_NAMES: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0), "charcoal": (54, 69, 79), "slate grey": (112, 128, 144), "silver grey": (192, 192, 192),
    "white": (255, 255, 255), "ivory": (255, 255, 240), "cream": (255, 253, 208), "beige": (225, 198, 153),
    "sand": (194, 178, 128), "tan": (210, 180, 140), "warm brown": (150, 90, 50), "chocolate brown": (90, 50, 30),
    "burgundy": (128, 0, 32), "crimson": (220, 20, 60), "red": (220, 40, 40), "coral": (255, 127, 80),
    "terracotta": (204, 78, 54), "orange": (255, 140, 0), "amber": (255, 191, 0), "gold": (212, 175, 55),
    "mustard yellow": (225, 173, 1), "yellow": (250, 218, 50), "lime green": (160, 210, 50), "olive green": (107, 142, 35),
    "sage green": (156, 175, 136), "emerald green": (0, 155, 119), "forest green": (34, 90, 50), "mint": (152, 255, 200),
    "teal": (0, 128, 128), "turquoise": (64, 224, 208), "cyan": (0, 200, 230), "sky blue": (120, 190, 240),
    "royal blue": (40, 80, 220), "cobalt blue": (0, 71, 171), "navy blue": (20, 30, 80), "midnight blue": (25, 25, 60),
    "indigo": (75, 0, 130), "violet": (140, 80, 200), "lavender": (190, 170, 230), "plum": (110, 40, 90),
    "magenta": (220, 0, 150), "hot pink": (255, 80, 160), "blush pink": (240, 190, 190), "rose": (200, 90, 110),
}


def colour_name(hex_or_name: str) -> str:
    """'#1B2A4A' → 'midnight blue (#1B2A4A)'; names pass through."""
    s = hex_or_name.strip()
    h = s.lstrip("#")
    if len(h) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in h):
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

        def dist(c):  # weighted RGB distance ("redmean")
            rm = (rgb[0] + c[0]) / 2
            dr, dg, db = rgb[0] - c[0], rgb[1] - c[1], rgb[2] - c[2]
            return (2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db
        name = min(COLOUR_NAMES, key=lambda n: dist(COLOUR_NAMES[n]))
        return f"{name} (#{h.upper()})"
    return s
