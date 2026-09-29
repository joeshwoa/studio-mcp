"""Brand department tests. Slow tests shape real fonts (downloads Google Fonts once) and render with
Playwright Chromium."""
from __future__ import annotations

import importlib.util
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from studio.depts.brand import kit as K
from studio.depts.brand import logo as L
from studio.depts.design import color as col

HAVE_ALL = all(importlib.util.find_spec(m) for m in ("playwright", "uharfbuzz", "fontTools"))
slow = pytest.mark.skipif(not HAVE_ALL, reason="playwright / uharfbuzz / fonttools not installed")


@pytest.fixture(scope="module", autouse=True)
def studio_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("brand-home")
    old = os.environ.get("STUDIO_HOME")
    os.environ["STUDIO_HOME"] = str(home)
    yield home
    if old is None:
        os.environ.pop("STUDIO_HOME", None)
    else:
        os.environ["STUDIO_HOME"] = old


def test_initials_and_mark_picking():
    assert L.initials_of("Cominde") == "C"
    assert L.initials_of("Beit El Fatar") == "BE"
    marks = L.pick_marks("organic juice bar, fresh", 4)
    assert len(marks) == 4 and len(set(marks)) == 4 and set(marks) <= set(L.MARK_KINDS)


def test_voice_notes():
    v = K.voice_for("Cominde", ["modern", "friendly"], "small businesses", "tech")
    assert v["traits"] and v["do"] and v["dont"] and "social_ar_eg" in v["samples"]


def test_make_kit_palette_and_fonts():
    kit = K.make_kit("Nile Bites", brief="Egyptian street food, warm and playful", personality=["playful", "warm"],
                     name_ar="نايل بايتس")
    c = kit["colors"]
    assert col.contrast(c["ink"], c["paper"]) >= 7
    assert kit["fonts"]["head_ar"] and kit["fonts"]["body"]
    assert kit["language"] == "bilingual" and kit["slug"] == "nile-bites"


@pytest.mark.slow
@slow
def test_text_path_arabic_and_latin():
    tp = L.text_path("كوميندي", "Cairo", 700, 100)
    assert tp.rtl and tp.w > 100 and tp.d.startswith("M")
    tl = L.text_path("Cominde", "Inter", 700, 100, tracking=0.1)
    assert not tl.rtl and tl.cap > 50


@pytest.mark.slow
@slow
def test_every_mark_builds_valid_svg():
    pal = col.build_palette("#1d4ed8", "auto", "tech")["roles"]
    for kind in L.MARK_KINDS:
        c = L.Concept(id="c1", mark=kind, font="Inter", weight=700)
        svgs = L.build_svgs(c, "Acme", "أكمي", "A", pal)
        for k in ("horizontal", "stacked", "icon", "app_icon", "favicon", "horizontal_mono_black", "wordmark_ar"):
            ET.fromstring(svgs[k])  # well-formed


@pytest.mark.slow
@slow
def test_brand_create_apply_guidelines(tmp_path):
    from studio.core.registry import call
    r = call("brand_create", {"name": "Test Bakery", "name_ar": "مخبز تست", "brief": "family bakery in Cairo",
                              "personality": "warm, friendly, heritage", "tagline": "Fresh every morning", "concepts": 3})
    kit_path = Path(r.files[0])
    kit = json.loads(kit_path.read_text(encoding="utf-8"))
    d = kit_path.parent
    for k in ("horizontal", "horizontal_reversed", "stacked", "icon", "favicon", "app_icon", "wordmark_ar"):
        assert (d / kit["logo"]["files"][k]).exists()
    assert (d / "logo" / "favicon.ico").exists() and (d / "logo" / "icon-512.png").exists()
    from PIL import Image
    im = Image.open(d / "logo" / "icon-512.png")
    assert im.getchannel("A").getextrema()[1] == 255  # really rendered, not a broken-image placeholder
    assert len(kit["logo"]["concepts"]) == 3 and all(Path(p).exists() for p in r.previews)
    r2 = call("brand_choose_logo", {"brand": "test-bakery", "concept": "c2", "case": "upper"})
    assert r2.data["files"]["horizontal"]
    r3 = call("brand_apply", {"brand": "test-bakery", "assets": "post,business_card,email_signature", "project": str(tmp_path.name)})
    assert any(f.endswith(".html") for f in r3.files) and any("cropmarks" in f for f in r3.files)
    r4 = call("brand_guidelines", {"brand": "test-bakery", "applications": False})
    pdf = [f for f in r4.files if f.endswith(".pdf")][0]
    pngs = [f for f in r4.files if f.endswith(".png")]
    assert Path(pdf).stat().st_size > 20000 and len(pngs) >= 10
    # design tools accept the brand
    r5 = call("design_create", {"template": "quote", "brand": "test-bakery", "size": "ig_square", "out": str(tmp_path),
                                "content": {"quote": "Best bread in town", "author": "Ali"}})
    assert "brand Test Bakery" in r5.summary
