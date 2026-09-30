"""Design department tests. Fast tests need nothing; slow ones render with Playwright Chromium and
download Google Fonts once (cached in a per-session STUDIO_HOME)."""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from studio.depts.design import color as col
from studio.depts.design import presets as P
from studio.depts.design.builder import _inline, has_ar, is_ar

HAVE_PW = importlib.util.find_spec("playwright") is not None


@pytest.fixture(scope="session", autouse=True)
def studio_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("studio-home")
    old = os.environ.get("STUDIO_HOME")
    os.environ["STUDIO_HOME"] = str(home)
    yield home
    if old is None:
        os.environ.pop("STUDIO_HOME", None)
    else:
        os.environ["STUDIO_HOME"] = old


# ------------------------------------------------------------------ fast
def test_contrast_math():
    assert col.contrast("#000", "#fff") == 21.0
    assert col.contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.02)
    assert col.grade(4.6) == "AA" and col.grade(3.2) == "AA-large" and col.grade(3.2, large=True) == "AA"


def test_oklch_roundtrip_and_ensure_contrast():
    for h in ("#123f33", "#ff6b4a", "#3a2bff", "#f4efe6"):
        L, C, H = col.to_oklch(h)
        back = col.from_oklch(L, C, H)
        assert all(abs(a - b) <= 2 for a, b in zip(col.hex_rgb(h), col.hex_rgb(back)))
    fixed = col.ensure_contrast("#ffc857", "#ffffff", 4.5)
    assert col.contrast(fixed, "#ffffff") >= 4.5


def test_palette_has_accessible_core_pairs():
    pal = col.build_palette("#0f766e", "auto", "health friendly")
    r = pal["roles"]
    assert col.contrast(r["ink"], r["paper"]) >= 7
    assert col.contrast(r["muted"], r["paper"]) >= 4.5
    assert col.contrast(r["primary_text"], r["paper"]) >= 4.5
    assert {"primary", "accent", "neutral"} <= set(pal["ramps"])


def test_presets():
    c = P.canvas("ig_story")
    assert (c.w, c.h) == (1080, 1920) and c.safe["bottom"] == 340
    a4 = P.canvas("a4")
    assert a4.print_ and a4.bleed_mm == 3 and a4.mm() == (210, 297)
    assert P.canvas("1200x800").w == 1200
    assert P.canvas("90x50mm").print_
    assert P.canvas("thumbnail").name == "youtube_thumbnail"


def test_inline_markup_and_arabic_detection():
    s = _inline("Grow *faster* with ==AI==\nnow")
    assert '<em class="hl">faster</em>' in s and '<span class="mark">AI</span>' in s and "<br>" in s
    assert has_ar("خصم ٣٠٪ on Flutter") and is_ar("خصم كبير Flutter") and not is_ar("Hello مرحبا world again")


def test_catalog_and_contrast_tools():
    from studio.core.registry import call
    r = call("design_catalog", {"what": "templates"})
    for t in ("headline", "photo", "carousel" if False else "list", "thumbnail", "business_card", "certificate", "menu", "infographic"):
        assert t in r.data["templates"]
    r2 = call("design_contrast", {"foreground": "#999", "background": "#fff"})
    assert r2.data["normal"] == "fail" and col.contrast(r2.data["fix"], "#fff") >= 4.5


# ------------------------------------------------------------------ slow (renders)
slow = pytest.mark.skipif(not HAVE_PW, reason="playwright not installed")


@pytest.mark.slow
@slow
def test_headline_square_png(tmp_path):
    from PIL import Image
    from studio.core.registry import call
    r = call("design_create", {"template": "headline", "size": "ig_square", "out": str(tmp_path),
                               "content": {"eyebrow": "New", "headline": "Build *faster*", "subhead": "Test", "cta": "Go"}})
    png = [f for f in r.files if f.endswith(".png")][0]
    assert Image.open(png).size == (1080, 1080)
    assert r.previews and Path(r.previews[0]).exists()
    assert r.data["fit"][0]["ok"]
    master = Path(r.data["master"])
    assert (master / "index.html").exists() and (master / "spec.json").exists() and list((master / "fonts").glob("*.ttf"))
    assert not any("DOES NOT FIT" in w for w in r.warnings)


@pytest.mark.slow
@slow
def test_arabic_story_is_rtl(tmp_path):
    from studio.core.registry import call
    r = call("design_create", {"template": "headline", "size": "ig_story", "out": str(tmp_path), "style": "nile",
                               "content": {"headline": "ابني تطبيقات *الناس بتحبها*", "cta": "قدّم دلوقتي"}})
    html = (Path(r.data["master"]) / "index.html").read_text(encoding="utf-8")
    assert 'dir="rtl"' in html and "unicode-range" in html


@pytest.mark.slow
@slow
def test_overflow_is_reported(tmp_path):
    from studio.core.registry import call
    r = call("design_create", {"template": "headline", "size": "linkedin_company_cover", "out": str(tmp_path),
                               "content": {"headline": " ".join(["verylongword"] * 40)}})
    assert any("fit" in w.lower() for w in r.warnings)


@pytest.mark.slow
@slow
def test_business_card_print_pdf(tmp_path):
    from studio.core.registry import call
    r = call("design_create", {"template": "business_card", "size": "business_card", "out": str(tmp_path), "formats": "pdf,png",
                               "content": {"name": "Joshua George", "title": "Founder", "phone": "+20 100", "email": "a@b.c"}})
    marks = [f for f in r.files if f.endswith("cropmarks.pdf")]
    pdf = [f for f in r.files if f.endswith(".pdf") and "cropmarks" not in f][0]
    assert marks
    data = Path(pdf).read_bytes()
    assert data.count(b"/Type /Page") - data.count(b"/Type /Pages") == 2 or data.count(b"/Type/Page") >= 2
    pngs = [f for f in r.files if f.endswith(".png")]
    from PIL import Image
    w, h = Image.open(pngs[0]).size
    assert w == pytest.approx((85 + 6) / 25.4 * 300, abs=4)  # 300 dpi incl. 3 mm bleed


@pytest.mark.slow
@slow
def test_carousel_and_resize(tmp_path):
    from studio.core.registry import call
    r = call("design_carousel", {"size": "ig_portrait", "out": str(tmp_path), "formats": "png",
                                 "slides": [{"template": "headline", "headline": "One"}, {"template": "list", "headline": "Two", "items": ["a", "b"]},
                                            {"template": "stat", "stat": "50%", "label": "three", "mode": "dark"}]})
    assert len([f for f in r.files if f.endswith(".png")]) == 3
    r2 = call("design_resize", {"master": r.data["master"], "sizes": "ig_story", "out": str(tmp_path)})
    assert len([f for f in r2.files if f.endswith(".png")]) == 3


@pytest.mark.slow
@slow
def test_render_html_with_fit(tmp_path):
    from PIL import Image
    from studio.core.registry import call
    html = ('<section class="page" style="background:#111;color:#fff"><div data-fit-box style="width:500px;height:200px">'
            '<h1 data-fit data-max="300" data-min="20" style="font-family:Cairo">القاهرة Cairo</h1></div></section>')
    r = call("design_render_html", {"html": html, "size": "600x400", "fonts": ["Cairo:700"], "out": str(tmp_path)})
    png = [f for f in r.files if f.endswith(".png")][0]
    assert Image.open(png).size == (600, 400)
    fit = r.data["reports"][0]["fit"][0]
    assert fit["ok"] and fit["scale"] < 1


@pytest.mark.slow
@slow
def test_design_check_overlay(tmp_path):
    from PIL import Image
    from studio.core.registry import call
    p = tmp_path / "x.png"
    Image.new("RGB", (1280, 720), "#335").save(p)
    r = call("design_check", {"path": str(p)})
    assert "youtube_thumbnail" in r.summary and Path(r.previews[0]).exists()


@pytest.mark.slow
@slow
def test_missing_required_field_warns_and_leave_bottom(tmp_path):
    import numpy as np
    from PIL import Image
    from studio.core.registry import call
    r = call("design_create", {"template": "offer", "size": "ig_square", "out": str(tmp_path), "content": {"headline": "x"}})
    assert any("expects 'offer'" in w for w in r.warnings)
    r = call("design_create", {"template": "headline", "size": "ig_story", "out": str(tmp_path), "style": "midnight",
                               "content": {"headline": "Big *news* today", "subhead": "More below", "cta": "Go",
                                           "leave_bottom": 0.3}})
    im = np.asarray(Image.open([f for f in r.files if f.endswith(".png")][0]).convert("L")).astype(int)
    low = im[int(im.shape[0] * 0.72):]
    assert low.std() < 12, "the reserved lower band should hold no text"
