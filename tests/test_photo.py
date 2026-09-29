"""Photo department tests. Fast ones use synthetic images; @slow ones download small models or run GIMP."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio.core.registry import call, load_all
from studio.core.result import ToolError

pytest.importorskip("cv2")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    load_all()
    return tmp_path / "home"


@pytest.fixture
def photo(tmp_path) -> str:
    """Synthetic 'photo': sky gradient, ground, a subject blob, a bit of noise."""
    W, H = 900, 600
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    a = np.zeros((H, W, 3), np.float32)
    a[..., 0] = 90 + 60 * yy / H
    a[..., 1] = 140 + 40 * yy / H
    a[..., 2] = 210 - 50 * yy / H
    a[yy > H * 0.65] = [70, 110, 60]
    m = ((xx - 620) / 90) ** 2 + ((yy - 330) / 140) ** 2 < 1
    a[m] = [200, 60, 50]
    a += np.random.default_rng(0).normal(0, 4, a.shape)
    p = tmp_path / "photo.jpg"
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(p, quality=92)
    return str(p)


@pytest.fixture
def logo_png(tmp_path) -> str:
    im = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((60, 60, 340, 240), 30, fill=(245, 158, 11, 255))
    p = tmp_path / "logo.png"
    im.save(p)
    return str(p)


def _img(res, i=0):
    return Image.open(res.files[i])


def test_resize_modes(photo):
    r = call("photo_resize", {"image": photo, "width": 400, "height": 400, "mode": "fill"})
    assert _img(r).size == (400, 400) and r.previews
    r = call("photo_resize", {"image": photo, "width": 400, "height": 400, "mode": "fit"})
    assert max(_img(r).size) == 400
    r = call("photo_resize", {"image": photo, "width": 500, "height": 900, "mode": "pad", "pad_color": "blur"})
    assert _img(r).size == (500, 900)
    r = call("photo_resize", {"image": photo, "scale": 3})
    assert any("photo_upscale" in w for w in r.warnings)


def test_smart_crop_keeps_subject(photo):
    r = call("photo_smart_crop", {"image": photo, "ratio": "9:16"})
    l, t, rr, b = r.data["crop_box"]
    assert abs((rr - l) / (b - t) - 9 / 16) < 0.01
    assert l < 620 < rr  # the red subject is inside


def test_crop_trim_rotate_canvas(photo, logo_png):
    r = call("photo_crop", {"image": logo_png, "trim": True})
    assert _img(r).size == (281, 181)
    r = call("photo_rotate", {"image": photo, "angle": 7, "fill": "crop"})
    assert _img(r).size[0] < 900
    r = call("photo_canvas", {"image": photo, "ratio": "1:1", "color": "#ffffff"})
    assert _img(r).size == (900, 900)
    with pytest.raises(ToolError):
        call("photo_crop", {"image": photo, "box": [0, 0, 5000, 10]})


def test_auto_straighten(tmp_path):
    im = Image.new("RGB", (1000, 700), "#ddd")
    d = ImageDraw.Draw(im)
    for y in range(80, 700, 90):
        d.rectangle((0, y, 1000, y + 25), fill="#334")
    p = tmp_path / "tilt.png"
    im.rotate(3, resample=Image.BICUBIC, fillcolor="#ddd").save(p)
    r = call("photo_rotate", {"image": str(p), "auto_straighten": True})
    assert abs(r.data["angle"] + 3) < 0.6


def test_convert_formats(photo, logo_png):
    for fmt in ("webp", "avif", "png", "tif"):
        r = call("photo_convert", {"image": photo, "format": fmt})
        assert Path(r.files[0]).suffix in (f".{fmt}", ".tif")
    r = call("photo_convert", {"image": logo_png, "format": "jpg"})
    assert any("flattened" in w for w in r.warnings)


def test_adjust_and_enhance(photo):
    r = call("photo_adjust", {"image": photo, "exposure_stops": 0.5, "contrast_amount": 0.3, "vibrance_amount": 0.3,
                              "curves": {"rgb": [[0, 10], [128, 140], [255, 250]]}, "vignette_amount": 0.3})
    assert r.data["after"]["mean_luma"] > r.data["before"]["mean_luma"]
    with pytest.raises(ToolError):
        call("photo_adjust", {"image": photo})
    r = call("photo_auto_enhance", {"image": photo})
    assert Path(r.files[0]).exists() and r.previews


def test_look_cube_roundtrip(photo, tmp_path):
    r = call("photo_look", {"image": photo, "look": "teal_orange", "export_cube": True})
    cube = [f for f in r.files if f.endswith(".cube")][0]
    graded = np.asarray(_img(r).convert("RGB"), np.float32)
    r2 = call("photo_apply_lut", {"image": photo, "lut": cube})
    via_lut = np.asarray(_img(r2).convert("RGB"), np.float32)
    assert np.abs(graded - via_lut).mean() < 1.5  # the exported .cube reproduces the look
    r3 = call("photo_look", {"image": photo, "look": "all"})
    assert Path(r3.files[0]).exists()
    with pytest.raises(ToolError):
        call("photo_look", {"image": photo, "look": "nope"})


def test_identity_lut(photo, tmp_path):
    n = 17
    v = np.linspace(0, 1, n)
    lines = ["LUT_3D_SIZE 17"] + [f"{r:.6f} {g:.6f} {b:.6f}" for b in v for g in v for r in v]
    p = tmp_path / "id.cube"
    p.write_text("\n".join(lines))
    r = call("photo_apply_lut", {"image": photo, "lut": str(p), "format": "png"})
    a = np.asarray(Image.open(photo).convert("RGB"), np.float32)
    b = np.asarray(_img(r).convert("RGB"), np.float32)
    assert np.abs(a - b).mean() < 1.0


def test_retouch(photo):
    r = call("photo_retouch", {"image": photo, "regions": [{"type": "circle", "x": 620, "y": 330, "r": 150}]})
    out = np.asarray(_img(r).convert("RGB")).astype(int)
    assert out[330, 620, 0] - out[330, 620, 2] < 60  # the red blob is gone
    with pytest.raises(ToolError):
        call("photo_retouch", {"image": photo})


def test_psd_writer_roundtrip(tmp_path):
    from studio.depts.photo.psd import PsdLayer, write_psd
    a = Image.new("RGBA", (50, 40), (255, 0, 0, 255))
    b = Image.new("RGBA", (20, 20), (0, 0, 255, 128))
    comp = Image.new("RGBA", (100, 80), (255, 255, 255, 255))
    p = write_psd(tmp_path / "t.psd", 100, 80, [PsdLayer("Red", a, 10, 5), PsdLayer("بلو", b, 60, 50, 0.5, "multiply", False)], comp)
    psd_tools = pytest.importorskip("psd_tools")
    psd = psd_tools.PSDImage.open(p)
    layers = list(psd)
    assert [l.name for l in layers] == ["Red", "بلو"]
    assert layers[0].bbox == (10, 5, 60, 45)
    assert layers[1].opacity == 128 and not layers[1].visible
    assert "MULTIPLY" in str(layers[1].blend_mode)


def _spec(photo):
    return {"width": 800, "height": 1000, "background": "linear:#0f172a,#1e293b,90", "layers": [
        {"type": "image", "name": "Photo", "src": photo, "x": 0, "y": 0, "width": 800, "height": 600},
        {"type": "shape", "name": "Card", "shape": "rect", "x": 40, "y": 640, "width": 720, "height": 300, "radius": 24,
         "fill": "#ffffff", "opacity": 0.1},
        {"type": "text", "name": "عنوان", "text": "عرض خاص النهارده", "font": "DejaVu Sans", "size": 64, "color": "#fff",
         "x": 60, "y": 680, "width": 680, "shadow": True},
        {"type": "text", "name": "Sub", "text": "Book now", "font": "DejaVu Sans", "size": 36, "color": "#cbd5e1",
         "x": 60, "y": 860, "width": 680},
        {"type": "fill", "name": "Glow", "fill": "radial:#f59e0b88,#00000000", "blend": "screen", "opacity": 0.5}]}


def test_compose_layers_psd_png(photo):
    r = call("photo_compose_layers", {"spec": _spec(photo), "xcf": False, "name": "poster"})
    psd = [f for f in r.files if f.endswith(".psd")][0]
    png = [f for f in r.files if f.endswith(".png")][0]
    assert Image.open(png).size == (800, 1000)
    names = [l["name"] for l in r.data["layers"]]
    assert names[0] == "Background" and "عنوان shadow" in names and "Glow" in names
    assert Path(psd).read_bytes()[:4] == b"8BPS"
    with pytest.raises(ToolError):
        call("photo_compose_layers", {"spec": {"width": 10}})


def test_blend_modes_math():
    from studio.depts.photo.render import _blend
    cb, cs = np.array([[0.5, 0.2, 0.8]]), np.array([[0.5, 0.5, 0.5]])
    assert np.allclose(_blend(cb, cs, "multiply"), cb * 0.5)
    assert np.allclose(_blend(cb, cs, "screen"), cb + 0.5 - cb * 0.5)
    for m in ("overlay", "soft_light", "hard_light", "color_dodge", "color_burn", "hue", "color", "luminosity"):
        out = _blend(cb, cs, m)
        assert out.shape == cb.shape and np.all(np.isfinite(out))


def test_collage_and_compare(photo, tmp_path):
    imgs = []
    for i in range(5):
        p = tmp_path / f"c{i}.jpg"
        Image.open(photo).rotate(i * 20).save(p)
        imgs.append(str(p))
    for layout in ("grid", "hero", "mosaic"):
        r = call("photo_collage", {"images": imgs, "layout": layout, "width": 1200, "height": 900, "captions": ["واحد", "two"]})
        assert _img(r).size == (1200, 900)
    r = call("photo_compare", {"before": imgs[0], "after": imgs[1], "mode": "split", "labels": ["قبل", "بعد"]})
    assert _img(r).size == (900, 600)
    r = call("photo_compare", {"before": imgs[0], "after": imgs[1]})
    assert _img(r).width > 1800


def test_mockups(photo):
    for scene in ("phone", "poster", "cards"):
        r = call("photo_mockup", {"design": photo, "scene": scene, "width": 800, "height": 600})
        assert _img(r).size == (800, 600)
    r = call("photo_mockup", {"design": photo, "photo": photo, "corners": [[100, 100], [400, 120], [390, 400], [110, 380]]})
    out = np.asarray(_img(r).convert("RGB")).astype(int)
    assert out.shape[:2] == (600, 900)
    with pytest.raises(ToolError):
        call("photo_mockup", {"design": photo, "photo": photo, "corners": [[0, 0]]})


def test_batch(photo, tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    for i in range(3):
        shutil.copy(photo, d / f"p{i}.jpg")
    r = call("photo_batch", {"folder": str(d), "tool_name": "photo_resize", "args": {"width": 200, "height": 200, "mode": "fill"}})
    assert len(r.files) == 3 and all(Image.open(f).size == (200, 200) for f in r.files)
    with pytest.raises(ToolError):
        call("photo_batch", {"folder": str(d), "tool_name": "photo_collage"})


def test_info(photo):
    r = call("photo_info", {"image": photo})
    assert r.data["width"] == 900 and r.previews


# ------------------------------------------------------------------ slow: models / GIMP
@pytest.mark.slow
def test_remove_and_replace_background(photo):
    pytest.importorskip("rembg")
    r = call("photo_remove_background", {"image": photo, "model": "u2netp"})
    cut = _img(r)
    assert cut.mode == "RGBA" and cut.getchannel("A").getextrema() == (0, 255)
    r2 = call("photo_replace_background", {"image": r.files[0], "background": "radial:#ffffff,#d1d5db", "width": 800, "height": 800})
    assert _img(r2).size == (800, 800)


@pytest.mark.slow
def test_upscale(photo, tmp_path):
    pytest.importorskip("onnxruntime")
    small = tmp_path / "s.jpg"
    Image.open(photo).resize((200, 133)).save(small)
    r = call("photo_upscale", {"image": str(small), "scale": 4})
    assert _img(r).size == (800, 532)
    r = call("photo_upscale", {"image": str(small), "scale": 2, "model": "lanczos"})
    assert "not AI" in r.summary


@pytest.mark.slow
def test_compose_layers_xcf(photo):
    from studio.core.result import MissingTool
    from studio.depts.photo.gimp import find_gimp
    try:
        find_gimp()
    except MissingTool:
        pytest.skip("GIMP not installed")
    r = call("photo_compose_layers", {"spec": _spec(photo), "name": "poster"})
    xcf = [f for f in r.files if f.endswith(".xcf")]
    assert xcf and Path(xcf[0]).read_bytes()[:8] == b"gimp xcf"
    assert r.data["xcf_vs_png_mean_diff"] < 12
