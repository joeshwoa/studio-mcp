"""Brief → brand direction. Reads the brief, sector, audience and personality with WHOLE-WORD
matching (so "Cairo" never reads as "ai", "crafted" never as "art"), picks the sector, and returns a
direction: a curated palette (chosen with a per-name seed so two coffee shops don't look identical),
type pairings that suit the sector, and logo marks that relate to the subject (cup, bean, book, roof,
shield, drop…) before generic geometry and monograms.

Curated palettes beat computed harmonies for brands: they are what a designer would actually pick
for the category, and the rest of the system (neutrals, ramps, WCAG pairs) is still derived."""
from __future__ import annotations

import hashlib
import re

# Each palette: primary (brand colour, readable-ish on paper), secondary (deep, for dark fields),
# accent (the pop), paper (tinted background). All chosen by hand for the category.
SECTORS: list[dict] = [
    {"id": "coffee", "keys": ["coffee", "cafe", "café", "espresso", "roast*", "roaster*", "barista", "latte", "brew*",
                              "coffeehouse", "tea", "teahouse", "قهوة", "كافيه", "بن"],
     "palettes": [
         {"name": "espresso & caramel", "primary": "#6F4E37", "secondary": "#2B1A12", "accent": "#D08C60", "paper": "#F6EFE6"},
         {"name": "terracotta roast", "primary": "#B5523B", "secondary": "#3A2419", "accent": "#E8B04B", "paper": "#F7F0E6"},
         {"name": "sage & clay café", "primary": "#5E7D63", "secondary": "#2F2A24", "accent": "#C98B5A", "paper": "#F4F1EA"},
         {"name": "night roast & brass", "primary": "#1F4A44", "secondary": "#14201E", "accent": "#C8913E", "paper": "#F3EEE4"},
     ],
     "pairs": ["cafe", "bistro", "heritage", "friendly"], "marks": ["cup", "bean", "arch", "star8", "monogram_circle"],
     "harmony": "analogous"},
    {"id": "food", "keys": ["food", "restaurant", "kitchen", "bakery", "bake*", "bread", "pastry", "sweet*", "dessert*",
                            "burger*", "pizza*", "grill*", "koshary", "falafel", "juice*", "catering", "chef", "bistro",
                            "diner", "eatery", "snack*", "مطعم", "أكل", "حلويات", "مخبز"],
     "palettes": [
         {"name": "tomato & mustard", "primary": "#D9482B", "secondary": "#2E1E1A", "accent": "#F2B544", "paper": "#FBF4EA"},
         {"name": "olive & paprika", "primary": "#6B7F3A", "secondary": "#23261A", "accent": "#E07A3F", "paper": "#F7F3E8"},
         {"name": "saffron & aubergine", "primary": "#E3A11B", "secondary": "#3B1F3A", "accent": "#D9482B", "paper": "#FCF6EA"},
         {"name": "mint & cherry", "primary": "#2E8B6E", "secondary": "#1C2A26", "accent": "#E8505B", "paper": "#F6F5EE"},
     ],
     "pairs": ["bistro", "playful", "cafe", "bold"], "marks": ["bowl", "sun", "petals", "monogram_circle", "arch"],
     "harmony": "complementary"},
    {"id": "tech", "keys": ["tech", "technology", "ai", "a.i.", "software", "saas", "app", "apps", "digital", "data",
                            "cloud", "cyber*", "startup", "platform", "api", "devtools", "developer*", "automation",
                            "fintech", "edtech", "robot*", "ml", "تقنية", "برمجيات", "تطبيق"],
     "palettes": [
         {"name": "electric blue & mint", "primary": "#3959DA", "secondary": "#0E1B3D", "accent": "#2EE6A8", "paper": "#F6F8FC"},
         {"name": "ultraviolet & amber", "primary": "#6C47FF", "secondary": "#15103A", "accent": "#FFB547", "paper": "#F7F6FD"},
         {"name": "deep teal & coral", "primary": "#0F8A8A", "secondary": "#062B30", "accent": "#FF6B4A", "paper": "#F4F8F8"},
         {"name": "graphite & volt", "primary": "#2B3A55", "secondary": "#0D121C", "accent": "#B6F23A", "paper": "#F5F6F8"},
     ],
     "pairs": ["modern", "tech-grotesk", "geometric", "grotesk"], "marks": ["spark", "bubble", "stack", "letter_split", "quarters", "hexagon"],
     "harmony": "complementary"},
    {"id": "finance", "keys": ["finance", "financial", "bank*", "invest*", "insurance", "accounting", "accountant*",
                               "audit*", "tax", "wealth", "capital", "consult*", "legal", "law", "lawyer*", "firm",
                               "advisory", "payments", "brokerage", "تمويل", "بنك", "استثمار", "محاسبة", "محاماة"],
     "palettes": [
         {"name": "navy & gold", "primary": "#1E3A5F", "secondary": "#0B1A2E", "accent": "#C9A227", "paper": "#F6F5F1"},
         {"name": "forest & brass", "primary": "#1F5E4A", "secondary": "#0D2620", "accent": "#D4B26A", "paper": "#F5F4EF"},
         {"name": "ink teal & copper", "primary": "#134E5E", "secondary": "#0A2530", "accent": "#E3A857", "paper": "#F4F6F6"},
         {"name": "oxblood & stone", "primary": "#6B2737", "secondary": "#221015", "accent": "#C7A57A", "paper": "#F6F3EF"},
     ],
     "pairs": ["corporate", "editorial", "luxury"], "marks": ["shield", "bars", "chevrons", "monogram_square", "hexagon"],
     "harmony": "complementary"},
    {"id": "health", "keys": ["health", "healthcare", "clinic*", "medical", "medicine", "hospital*", "doctor*", "dental",
                              "dentist*", "pharma*", "pharmacy", "physio*", "therapy", "lab", "labs", "diagnos*", "nursing",
                              "صحة", "عيادة", "مستشفى", "صيدلية", "طبي"],
     "palettes": [
         {"name": "clinical teal & coral", "primary": "#0E8A8A", "secondary": "#0B3B3E", "accent": "#FF8A65", "paper": "#F4F9F9"},
         {"name": "trust blue & green", "primary": "#2D6FD6", "secondary": "#0D2A55", "accent": "#34C08A", "paper": "#F5F8FD"},
         {"name": "mint & sun", "primary": "#2BA88A", "secondary": "#12372F", "accent": "#F2C14E", "paper": "#F4FAF7"},
     ],
     "pairs": ["clinical", "friendly", "corporate"], "marks": ["plus", "heart", "drop", "orbit", "petals"],
     "harmony": "complementary"},
    {"id": "wellness", "keys": ["wellness", "spa", "yoga", "pilates", "beauty", "skincare", "skin", "cosmetic*",
                                "salon", "makeup", "nail*", "massage", "self-care", "مساج", "تجميل", "بشرة"],
     "palettes": [
         {"name": "blush & honey", "primary": "#C47A83", "secondary": "#3D2328", "accent": "#E4B861", "paper": "#FBF4F2"},
         {"name": "clay & sage", "primary": "#B9785A", "secondary": "#3A2A22", "accent": "#8FAF91", "paper": "#F8F2EC"},
         {"name": "lilac & peach", "primary": "#8E7CC3", "secondary": "#2A2340", "accent": "#F4A98E", "paper": "#F8F6FB"},
     ],
     "pairs": ["organic", "luxury", "friendly"], "marks": ["petals", "leaf", "drop", "sun", "monogram_letter"],
     "harmony": "analogous"},
    {"id": "eco", "keys": ["eco", "ecological", "organic", "farm*", "agri*", "plant*", "garden*", "sustainab*", "green",
                           "nature", "natural", "recycl*", "solar", "vegan", "herbal", "زراعة", "عضوي", "بيئة"],
     "palettes": [
         {"name": "leaf & sunflower", "primary": "#3F7D4E", "secondary": "#1C3324", "accent": "#E3B23C", "paper": "#F5F5EC"},
         {"name": "olive & terracotta", "primary": "#6E7F36", "secondary": "#2B3018", "accent": "#D9794A", "paper": "#F7F4EA"},
         {"name": "moss & sky", "primary": "#4B6B3C", "secondary": "#1F2B1A", "accent": "#7DB7D8", "paper": "#F3F5EE"},
     ],
     "pairs": ["organic", "friendly", "editorial"], "marks": ["sprout", "leaf", "sun", "drop", "wave"],
     "harmony": "analogous"},
    {"id": "education", "keys": ["education", "educational", "academy", "school*", "course*", "learn*", "training",
                                 "tutor*", "university", "college", "institute", "teach*", "student*", "exam*",
                                 "تعليم", "أكاديمية", "مدرسة", "كورس"],
     "palettes": [
         {"name": "royal & sunflower", "primary": "#3B4CCA", "secondary": "#141A4A", "accent": "#FFB400", "paper": "#F6F7FC"},
         {"name": "teal & tangerine", "primary": "#0F7C90", "secondary": "#0A2E36", "accent": "#F97B4F", "paper": "#F3F8F9"},
         {"name": "plum & lime", "primary": "#6A3D9A", "secondary": "#221434", "accent": "#A6D94A", "paper": "#F8F6FB"},
     ],
     "pairs": ["friendly", "editorial", "modern"], "marks": ["book", "sprout", "orbit", "stack", "star8"],
     "harmony": "complementary"},
    {"id": "kids", "keys": ["kids", "kid", "children", "child", "toy*", "baby", "babies", "nursery", "kindergarten",
                            "أطفال", "حضانة"],
     "palettes": [
         {"name": "sunny primaries", "primary": "#2F80ED", "secondary": "#1B2A4A", "accent": "#FFC93C", "paper": "#FFF9EE"},
         {"name": "coral & sky", "primary": "#FF6F59", "secondary": "#2B2D42", "accent": "#43BCCD", "paper": "#FFF7F2"},
     ],
     "pairs": ["playful", "friendly"], "marks": ["sun", "heart", "bubble", "quarters", "star8"], "harmony": "triadic"},
    {"id": "property", "keys": ["real estate", "property", "properties", "realty", "construction", "architect*",
                                "interior*", "developer", "developments", "housing", "home", "homes", "furniture",
                                "عقارات", "مقاولات", "تشطيب", "ديكور"],
     "palettes": [
         {"name": "charcoal & brass", "primary": "#2F3B45", "secondary": "#161C22", "accent": "#C2A26B", "paper": "#F5F3EF"},
         {"name": "stone & sand", "primary": "#7A6A58", "secondary": "#2B2621", "accent": "#D8B384", "paper": "#F6F2EC"},
         {"name": "deep green & travertine", "primary": "#2E5E4E", "secondary": "#14281F", "accent": "#D6C3A1", "paper": "#F4F2EC"},
     ],
     "pairs": ["geometric", "corporate", "luxury"], "marks": ["roof", "arch", "stack", "hexagon", "chevrons"],
     "harmony": "analogous"},
    {"id": "travel", "keys": ["travel", "tour*", "tourism", "hotel*", "resort*", "beach", "nile", "cruise*", "trip*",
                              "holiday*", "safari", "hostel", "airline", "سياحة", "فندق", "رحلات"],
     "palettes": [
         {"name": "nile blue & sun", "primary": "#1F6FA8", "secondary": "#0D2A40", "accent": "#F2A541", "paper": "#F5F8FA"},
         {"name": "desert & turquoise", "primary": "#C8773E", "secondary": "#3B2616", "accent": "#2A9D8F", "paper": "#FAF4EC"},
         {"name": "red sea coral", "primary": "#0B8FA4", "secondary": "#083139", "accent": "#FF7F6E", "paper": "#F3F9FA"},
     ],
     "pairs": ["modern", "heritage", "friendly"], "marks": ["pin", "sun", "pyramid", "wave", "arch"], "harmony": "complementary"},
    {"id": "luxury", "keys": ["luxury", "luxurious", "fashion", "jewel*", "jewellery", "jewelry", "perfume*", "fragrance*",
                              "boutique", "bridal", "couture", "haute", "watch*", "atelier", "مجوهرات", "عطور", "أزياء"],
     "palettes": [
         {"name": "onyx & champagne", "primary": "#2A2622", "secondary": "#141210", "accent": "#C6A15B", "paper": "#F7F4EE"},
         {"name": "emerald & gold", "primary": "#0F4C3A", "secondary": "#08261D", "accent": "#C9A96E", "paper": "#F5F3EC"},
         {"name": "burgundy & rose gold", "primary": "#6D1F2F", "secondary": "#2A0C12", "accent": "#D4A58A", "paper": "#F8F3F0"},
     ],
     "pairs": ["luxury", "heritage", "fashion"], "marks": ["monogram_letter", "star8", "arch", "hexagon", "petals"],
     "harmony": "monochrome"},
    {"id": "heritage", "keys": ["heritage", "tradition*", "craft", "crafts", "handmade", "artisan*", "oriental",
                                "egypt*", "pharao*", "khan", "museum*", "culture", "cultural", "antique*", "souvenir*",
                                "تراث", "مصري", "حرف"],
     "palettes": [
         {"name": "lapis & gold", "primary": "#1D4E89", "secondary": "#0C213D", "accent": "#D4A017", "paper": "#F7F3EA"},
         {"name": "terracotta & turquoise", "primary": "#B4553A", "secondary": "#3A1D14", "accent": "#2A9D8F", "paper": "#F8F1E7"},
         {"name": "turquoise & copper", "primary": "#1B8A8F", "secondary": "#0B3436", "accent": "#C9812F", "paper": "#F5F2EA"},
     ],
     "pairs": ["heritage", "editorial", "luxury"], "marks": ["star8", "arch", "pyramid", "sun", "hexagon"], "harmony": "complementary"},
    {"id": "sport", "keys": ["sport*", "fitness", "gym*", "athlet*", "running", "runner*", "football", "soccer",
                             "padel", "tennis", "crossfit", "training club", "martial", "boxing", "رياضة", "جيم"],
     "palettes": [
         {"name": "signal red & yellow", "primary": "#E4312B", "secondary": "#141414", "accent": "#F5D000", "paper": "#F7F7F5"},
         {"name": "cobalt & volt", "primary": "#0057FF", "secondary": "#0A0F1F", "accent": "#C6FF00", "paper": "#F5F7FB"},
         {"name": "blaze & ice", "primary": "#FF5A1F", "secondary": "#1B1B1F", "accent": "#29C5F6", "paper": "#F8F6F4"},
     ],
     "pairs": ["bold", "grotesk"], "marks": ["bolt", "chevrons", "letter_split", "spark", "wave"], "harmony": "split"},
    {"id": "creative", "keys": ["creative", "agency", "design", "studio", "media", "production*", "art", "arts", "artist*",
                                "music", "film*", "photograph*", "video", "podcast*", "content", "marketing", "branding",
                                "إنتاج", "تصميم", "دعاية", "تسويق"],
     "palettes": [
         {"name": "magenta & lemon", "primary": "#D6336C", "secondary": "#1E1030", "accent": "#FFD43B", "paper": "#FBF6F8"},
         {"name": "coral & cobalt", "primary": "#FF6B57", "secondary": "#1B1A2E", "accent": "#5B8CFF", "paper": "#FBF7F5"},
         {"name": "ink & acid", "primary": "#1D1D1F", "secondary": "#0B0B0C", "accent": "#D7FF3A", "paper": "#F4F4F1"},
     ],
     "pairs": ["creative", "grotesk", "geometric"], "marks": ["quarters", "letter_split", "spark", "orbit", "bubble"], "harmony": "split"},
    {"id": "logistics", "keys": ["logistic*", "delivery", "deliveries", "shipping", "courier*", "transport*", "cargo",
                                 "freight", "fleet", "moving", "شحن", "توصيل", "نقل"],
     "palettes": [
         {"name": "safety orange & navy", "primary": "#F26B1D", "secondary": "#1E2A38", "accent": "#2BB3C0", "paper": "#F7F6F3"},
         {"name": "route blue & lime", "primary": "#1F5FBF", "secondary": "#0C1D38", "accent": "#9BD13B", "paper": "#F4F7FB"},
     ],
     "pairs": ["bold", "modern", "corporate"], "marks": ["chevrons", "pin", "bolt", "stack"], "harmony": "complementary"},
    {"id": "community", "keys": ["charity", "ngo", "nonprofit", "non-profit", "foundation", "church", "community",
                                 "volunteer*", "mosque", "youth", "ministry", "خيري", "كنيسة", "مجتمع"],
     "palettes": [
         {"name": "hope blue & warm gold", "primary": "#2F5DA8", "secondary": "#14264A", "accent": "#F0B429", "paper": "#F7F6F2"},
         {"name": "olive branch & clay", "primary": "#5C7A3A", "secondary": "#24301A", "accent": "#D98C5F", "paper": "#F6F4EC"},
     ],
     "pairs": ["friendly", "editorial", "heritage"], "marks": ["heart", "sun", "bubble", "sprout", "orbit"], "harmony": "complementary"},
]

GENERIC = {
    "id": "general",
    "palettes": [
        {"name": "indigo & amber", "primary": "#3D4BA8", "secondary": "#161B3F", "accent": "#F2A93B", "paper": "#F7F7FA"},
        {"name": "teal & coral", "primary": "#127C7C", "secondary": "#0A2E2E", "accent": "#FF7A5C", "paper": "#F4F8F8"},
        {"name": "plum & peach", "primary": "#7A3E7A", "secondary": "#2A1430", "accent": "#F5A97F", "paper": "#F9F5F8"},
        {"name": "forest & ochre", "primary": "#2F6B4F", "secondary": "#12281E", "accent": "#D9A441", "paper": "#F5F5EF"},
        {"name": "brick & slate", "primary": "#B2472F", "secondary": "#262B33", "accent": "#7FA7C9", "paper": "#F8F4F0"},
    ],
    "pairs": ["modern", "friendly", "geometric", "egypt-classic"],
    "marks": ["monogram_square", "orbit", "quarters", "spark", "arch", "monogram_circle"],
    "harmony": "complementary",
}

# Personality words move the pick inside a sector (warm → warmer palette, etc.)
WARM = {"warm", "cozy", "cosy", "crafted", "handmade", "rustic", "earthy", "homey", "friendly", "artisan"}
COOL = {"calm", "clean", "clinical", "fresh", "cool", "crisp", "trust", "trustworthy", "reliable"}
DARK = {"luxury", "premium", "elegant", "exclusive", "sophisticated", "night", "moody", "bold"}
PLAYFUL = {"playful", "fun", "young", "youthful", "cheerful", "vibrant", "colourful", "colorful"}
LUX_WORDS = {"luxury", "luxurious", "elegant", "premium", "fashion", "jewelry", "jewellery", "heritage", "classic",
             "exclusive", "couture", "boutique", "sophisticated"}
TECH_WORDS = {"tech", "technology", "ai", "software", "startup", "digital", "app", "saas", "data", "cloud", "platform"}

_WORD = re.compile(r"[\w؀-ۿ'.-]+", re.UNICODE)


def words(text: str) -> list[str]:
    return [w.strip("'.-").lower() for w in _WORD.findall(text or "") if w.strip("'.-")]


def has(text_words: list[str], text: str, key: str) -> bool:
    """Whole-word match; 'roast*' matches roast/roastery/roasters; multi-word keys match as a phrase."""
    if " " in key:
        return re.search(r"(?<![\w])" + re.escape(key) + r"(?![\w])", text) is not None
    if key.endswith("*"):
        k = key[:-1]
        return any(w.startswith(k) for w in text_words)
    return key in text_words


def hits(text: str, keys: list[str]) -> int:
    t = (text or "").lower()
    ws = words(t)
    return sum(1 for k in keys if has(ws, t, k))


def seed_of(*parts: str) -> int:
    return int(hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:8], 16)


def detect_sector(brief: str = "", sector: str = "", personality: list[str] | None = None) -> tuple[dict, list[dict]]:
    """Best sector (the explicit `sector` field counts 3×) and the runners-up with any hits."""
    scored = []
    for s in SECTORS:
        sc = hits(sector, s["keys"]) * 3 + hits(brief, s["keys"]) + hits(" ".join(personality or []), s["keys"])
        if s["id"] == sector.strip().lower():
            sc += 5
        if sc:
            scored.append((sc, s))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return GENERIC, []
    return scored[0][1], [s for _, s in scored[1:3]]


def _warmth(hexc: str) -> float:
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return (r - b) / 255


def _lum(hexc: str) -> float:
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def direction(name: str, brief: str = "", sector: str = "", audience: str = "", personality: list[str] | None = None,
              variation: str = "") -> dict:
    """{"sector", "also", "palette", "palettes", "pairs", "marks", "harmony", "lux", "tech", "moods"}."""
    personality = personality or []
    main, also = detect_sector(brief, sector, personality)
    moods = set(words(" ".join([brief, audience, " ".join(personality)])))
    pals = list(main["palettes"])
    # order palettes by how well they fit the mood words, then rotate by the name seed inside ties
    def fit(p):
        s = 0.0
        if moods & WARM:
            s += _warmth(p["primary"]) + 0.5 * _warmth(p["accent"])
        if moods & COOL:
            s -= _warmth(p["primary"])
        if moods & DARK:
            s += 1 - _lum(p["primary"])
        if moods & PLAYFUL:
            s += _lum(p["accent"])
        return round(s, 1)
    seed = seed_of(name.lower(), variation)
    rot = seed % len(pals)
    pals = pals[rot:] + pals[:rot]
    pals.sort(key=fit, reverse=True)
    marks = list(main["marks"])
    for s in also:  # a second sector adds its first subject mark (e.g. coffee + heritage → star8)
        for m in s["marks"][:2]:
            if m not in marks:
                marks.insert(min(2, len(marks)), m)
                break
    lux = bool(moods & LUX_WORDS) or main["id"] == "luxury"
    tech = bool(moods & TECH_WORDS) or main["id"] == "tech"
    pairs = list(main["pairs"])
    if lux and "luxury" not in pairs[:2]:
        pairs.insert(1, "luxury")
    return {"sector": main["id"], "also": [s["id"] for s in also], "palette": pals[0], "palettes": pals,
            "pairs": pairs, "marks": marks, "harmony": main.get("harmony", "complementary"), "lux": lux, "tech": tech,
            "moods": sorted(moods & (WARM | COOL | DARK | PLAYFUL | LUX_WORDS)), "seed": seed}
