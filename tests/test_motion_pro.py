"""Pro motion engines: GSAP compositions, Lottie, three.js 3D, Blender, Remotion, router, templates.
Fast tests need no browser; render tests need Playwright + Chromium and the JS libraries (downloaded once
from jsDelivr into STUDIO_HOME/cache/jslibs — skipped when offline and not cached)."""
from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio.core.registry import call
from studio.core.result import MissingTool, ToolError

ROOT = Path(__file__).resolve().parents[1]
BPY_SCRIPT = ROOT / "src" / "studio" / "assets" / "blender" / "studio_bpy.py"
S = "320x180"


@pytest.fixture(scope="session", autouse=True)
def home(tmp_path_factory):
    h = tmp_path_factory.mktemp("mprohome")
    mp = pytest.MonkeyPatch()
    mp.setenv("STUDIO_HOME", str(h))
    # reuse an already-downloaded JS library cache when the developer has one (saves the download)
    for cand in (os.environ.get("STUDIO_JSLIBS_CACHE", ""), "/tmp/mhome/cache/jslibs"):
        if cand and Path(cand).is_dir():
            shutil.copytree(cand, h / "cache" / "jslibs", dirs_exist_ok=True)
            break
    yield h
    mp.undo()


@pytest.fixture(scope="session")
def browser():
    pytest.importorskip("playwright")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg missing")
    try:
        call("motion_render_html", {"html": "<html><body style='background:#000'></body></html>", "duration": 0.1, "size": "64x36", "fps": 10})
    except ToolError:
        pytest.skip("Chromium for Playwright not installed")
    from studio.depts.motion import libs
    try:
        for n in ("gsap", "lottie"):
            libs.ensure(n)
    except ToolError:
        pytest.skip("JS libraries not cached and no internet")


def _frame(video: str, n: int, dest: Path) -> np.ndarray:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", str(dest)], check=True)
    return np.asarray(Image.open(dest).convert("RGB"), np.float32)


# ------------------------------------------------------------------------------------------ no browser
def test_registered():
    from studio.core.registry import load_all
    t = load_all()
    for name in ("motion_compose", "motion_lottie", "motion_3d", "motion_blender", "motion_remotion", "motion_plan"):
        assert name in t, name
    # every existing tool still there
    for name in ("motion_title_card", "motion_lower_third", "motion_render_html", "motion_templates"):
        assert name in t


def test_templates_build_valid_specs(tmp_path):
    from studio.depts.motion import compose, presets
    logo = tmp_path / "l.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>')
    for name in presets.TEMPLATES:
        for size in ("1920x1080", "1080x1920"):
            spec = presets.build_template(name, {"logo": str(logo)} if name == "logo_sting_pro" else {}, size=size)
            assert spec["scenes"], name
            spec, _f, cx = compose.normalise(spec, warnings=[])
            assert compose.spec_total(spec) > 0.5
            assert not [w for w in cx.warnings if "unknown" in w], (name, cx.warnings)
    cat = call("motion_templates", {})
    assert "kinetic_quote" in cat.data["pro_templates"]


def test_spec_validation_and_timing():
    from studio.depts.motion import compose
    with pytest.raises(ToolError):
        compose.normalise({"layers": [{"type": "hologram"}]})
    w: list[str] = []
    spec, _f, _c = compose.normalise({"layers": [{"type": "text", "text": "x", "enter": "teleport"}]}, warnings=w)
    assert any("teleport" in x for x in w)
    spec = {"scenes": [{"duration": 3, "layers": []}, {"duration": 3, "transition": {"type": "wipe", "duration": 1}, "layers": []},
                       {"duration": 2, "transition": "cut", "layers": []}]}
    spec, _f, _c = compose.normalise(spec)
    assert compose.spec_total(spec) == pytest.approx(7.0)
    with pytest.raises(ToolError):
        compose.load_spec("{not json")


def test_router_decisions():
    cases = {
        "kinetic typography reel of an Egyptian Arabic quote": ("motion_compose", "kinetic_quote"),
        "animated bar chart of our revenue growth": ("motion_compose", "chart_story"),
        "3D chrome title for a youtube intro": ("motion_3d", None),
        "photoreal glass logo with caustics and depth of field": ("motion_blender", None),
        "recolour this lottie to the brand": ("motion_lottie", None),
        "lower third with name and role": ("motion_compose", "lower_third_pro"),
    }
    for brief, (tool, tpl) in cases.items():
        r = call("motion_plan", {"brief": brief})
        assert r.data["tool"] == tool, (brief, r.data)
        if tpl:
            assert r.data["template"] == tpl
        assert r.data["decision_matrix"] and r.next_steps
    r = call("motion_plan", {"brief": "product spin", "assets": ["/x/model.glb"]})
    assert r.data["tool"] == "motion_3d" and r.data["args"]["preset"] == "product"
    assert call("motion_plan", {"brief": "خلي الجملة دي تتحرك كلمة كلمة"}).data["arabic"] is True


def test_lottie_recolor_and_dotlottie(tmp_path):
    from studio.depts.motion import lottie as L
    data = _lottie()
    pal = L.palette(data)
    assert pal[0]["color"] == "#FF0000"
    new, applied = L.recolor(data, {"#FF0000": "#00AA88"})
    assert L.palette(new)[0]["color"] == "#00AA88" and applied
    mono, _ = L.recolor(data, "mono:#3366FF")
    assert L.palette(mono)[0]["color"] != "#FF0000"
    br, ap = L.recolor(data, "brand", {"primary": "#123456", "accent": "#FFC53D"})
    assert "#123456" in [p["color"] for p in L.palette(br)]
    zp = tmp_path / "a.lottie"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("manifest.json", json.dumps({"animations": [{"id": "main"}]}))
        z.writestr("animations/main.json", json.dumps(data))
    assert L.load_lottie(zp)["fr"] == 30
    assert L.info(data)["seconds"] == 1.0


def test_engine_post_and_sheet(tmp_path):
    from studio.depts.motion import engine
    a = np.zeros((90, 160, 4), np.float32)
    a[..., 3] = 255
    a[40:50, 70:90, :3] = 255
    g = engine.post_frame(a.copy(), {"glow": 1.0, "vignette": 0.5})
    assert g[45, 60, 0] > 0  # bloom bled light next to the bright box
    assert g[0, 0, 0] == 0
    fr = []
    for i in range(4):
        p = tmp_path / f"f_{i + 1:06d}.png"
        Image.new("RGB", (160, 90), (i * 60, 0, 0)).save(p)
        fr.append(p)
    out = engine.frames_sheet(fr, tmp_path / "s.png", False, cols=2, rows=2, tile_w=160, fps=30)
    assert out.exists()
    assert engine.timecode(1.5, 30) == "00:01:15"


def test_blender_script_compiles_and_parses(tmp_path):
    py_compile.compile(str(BPY_SCRIPT), doraise=True)
    sys.path.insert(0, str(BPY_SCRIPT.parent))
    try:
        import importlib
        mod = importlib.import_module("studio_bpy")  # bpy is absent here → module still imports
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"preset": "logo", "svg": "x.svg", "frames_dir": str(tmp_path / "f")}))
        j = mod.parse_args(["blender", "-b", "--", str(job)])
        assert j["engine"] == "EEVEE" and j["fps"] == 30 and j["preset"] == "logo"
        assert mod.hex_rgba("#FFFFFF") == (1.0, 1.0, 1.0, 1.0)
        assert abs(mod.hex_rgba("#808080")[0] - 0.2158) < 1e-3  # sRGB → linear
    finally:
        sys.path.remove(str(BPY_SCRIPT.parent))


def test_blender_bad_args():
    with pytest.raises(ToolError):
        call("motion_blender", {"preset": "explosion"})
    with pytest.raises(ToolError):
        call("motion_blender", {"preset": "title", "quality": "ultra"})


def test_remotion_gates(tmp_path):
    with pytest.raises(ToolError) as e:
        call("motion_remotion", {"project_dir": str(tmp_path), "composition": "Main"})
    assert "licence" in str(e.value).lower()
    with pytest.raises(ToolError):
        call("motion_remotion", {"project_dir": str(tmp_path), "composition": "Main", "licence_ok": True})
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"remotion": "4.0.0"}}))
    if shutil.which("npx"):
        with pytest.raises(ToolError) as e:  # node_modules missing and install not allowed
            call("motion_remotion", {"project_dir": str(tmp_path), "composition": "Main", "licence_ok": True})
        assert "install" in (str(e.value) + e.value.hint).lower()
    else:
        with pytest.raises(MissingTool):
            call("motion_remotion", {"project_dir": str(tmp_path), "composition": "Main", "licence_ok": True})


# ------------------------------------------------------------------------------------------ renders
def _lottie() -> dict:
    """1 s, 30 fps, 200×100: a red 40×40 square moving x 20 → 180."""
    return {"v": "5.7.0", "fr": 30, "ip": 0, "op": 30, "w": 200, "h": 100, "nm": "t", "ddd": 0, "assets": [], "layers": [{
        "ddd": 0, "ind": 1, "ty": 4, "nm": "box", "sr": 1, "ip": 0, "op": 30, "st": 0, "bm": 0,
        "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]},
               "p": {"a": 1, "k": [{"t": 0, "s": [20, 50, 0], "e": [180, 50, 0], "i": {"x": [1], "y": [1]}, "o": {"x": [0], "y": [0]}},
                                   {"t": 30, "s": [180, 50, 0]}]}},
        "shapes": [{"ty": "gr", "it": [{"ty": "rc", "d": 1, "s": {"a": 0, "k": [40, 40]}, "p": {"a": 0, "k": [0, 0]}, "r": {"a": 0, "k": 0}},
                                       {"ty": "fl", "c": {"a": 0, "k": [1, 0, 0, 1]}, "o": {"a": 0, "k": 100}, "r": 1},
                                       {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]},
                                        "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}}]}]}]}


def test_lottie_render_frame_exact(browser, tmp_path):
    src = tmp_path / "box.json"
    src.write_text(json.dumps(_lottie()))
    r = call("motion_lottie", {"src": str(src), "background": "#000000", "out": str(tmp_path / "box.mp4")})
    assert r.ok and r.data["frames"] == 30 and r.data["lottie"]["fps"] == 30
    a = _frame(r.files[0], 15, tmp_path / "f15.png")  # t=0.5 s → centre x = 100
    xs = np.where(a[50, :, 0] > 128)[0]
    assert abs((xs.min() + xs.max()) / 2 - 100) <= 3
    r2 = call("motion_lottie", {"src": str(src), "recolor_map": {"#FF0000": "#00FF00"}, "speed": 2, "loops": 2, "background": "#000000",
                                "out": str(tmp_path / "g.mp4")})
    assert r2.data["frames"] == 30 and r2.data["recolored"]
    g = _frame(r2.files[0], 3, tmp_path / "g3.png")
    assert g[..., 1].max() > 150 and g[..., 0].max() < 80


def test_compose_preview_arabic_and_transitions(browser, tmp_path):
    spec = {"size": S, "post": {"vignette": 0.3, "light_leaks": True}, "scenes": [
        {"duration": 1.5, "background": {"type": "animated"}, "camera": "push-in", "layers": [
            {"type": "text", "text": "خلّي الفكرة *تتحرك*", "size": 40, "enter": {"preset": "split-chars", "style": "rise"},
             "animate": [{"preset": "highlight", "at": 0.6}]}]},
        {"duration": 1.5, "transition": {"type": "whip", "duration": 0.5}, "layers": [
            {"type": "chart", "chart": "bar", "w": 260, "h": 120, "data": [{"label": "A", "value": 3}, {"label": "B", "value": 5}], "highlight": 1},
            {"type": "icon", "name": "rocket", "size": 40, "x": 40, "y": 30, "enter": "pop", "loop_anim": "float"}]},
    ]}
    r = call("motion_compose", {"spec": spec, "preview": True, "out": str(tmp_path)})
    assert r.ok and Path(r.previews[0]).exists() and len(r.data["times"]) == 12
    assert not [w for w in r.warnings if "pageerror" in w or "compose:" in w], r.warnings


def test_compose_video_keyframes_exact(browser, tmp_path):
    spec = {"size": "240x60", "fps": 10, "background": "#000000", "post": {"grain": 0, "vignette": 0}, "scenes": [{"duration": 1.0, "layers": [
        {"type": "shape", "shape": "rect", "w": 20, "h": 20, "fill": "#FF0000", "x": 0, "y": 30, "anchor": "left",
         "keyframes": [{"t": 0, "x": 0}, {"t": 1, "x": 200, "ease": "linear"}]}]}]}
    r = call("motion_compose", {"spec": spec, "formats": ["mp4"], "out": str(tmp_path / "k.mp4")})
    assert r.ok and r.data["frames"] == 10
    a = _frame(r.files[0], 5, tmp_path / "k5.png")  # t=0.5 → left edge at x=100
    xs = np.where(a[30, :, 0] > 128)[0]
    assert abs(xs.min() - 100) <= 2
    assert any(f.endswith("-spec.json") for f in r.files) and any(f.endswith("-source.html") for f in r.files)


def test_compose_motion_blur(browser, tmp_path):
    spec = {"size": "240x60", "fps": 10, "background": "#000000", "post": {"grain": 0}, "scenes": [{"duration": 1.0, "layers": [
        {"type": "shape", "shape": "rect", "w": 10, "h": 20, "fill": "#FFFFFF", "x": 0, "y": 30, "anchor": "left",
         "keyframes": [{"t": 0, "x": 0}, {"t": 1, "x": 220, "ease": "linear"}]}]}]}
    sharp = call("motion_compose", {"spec": spec, "formats": ["png"], "out": str(tmp_path / "a")})
    blur = call("motion_compose", {"spec": spec, "formats": ["png"], "motion_blur": 6, "out": str(tmp_path / "b")})
    fa = sorted(Path(next(f for f in sharp.files if f.endswith("-frames"))).glob("*.png"))[5]
    fb = sorted(Path(next(f for f in blur.files if f.endswith("-frames"))).glob("*.png"))[5]
    ra = np.asarray(Image.open(fa).convert("L"))[30]
    rb = np.asarray(Image.open(fb).convert("L"))[30]
    mid = lambda r: int(((r > 20) & (r < 235)).sum())  # noqa: E731  partially covered pixels = smear
    assert mid(rb) > mid(ra) + 4
    assert (rb > 20).sum() > (ra > 20).sum()  # the streak is longer than the box


@pytest.mark.slow
@pytest.mark.parametrize("name", ["kinetic_quote", "stat_reveal", "logo_sting_pro", "lower_third_pro", "chart_story", "transition_pro"])
def test_templates_render(browser, tmp_path, name):
    fields = {"kinetic_quote": {"quote": "مش لازم تكون *كبير*|عشان تبدأ"}, "logo_sting_pro": {"logo": str(_logo(tmp_path))}}.get(name, {})
    r = call("motion_compose", {"template": name, "fields": fields, "size": S, "fps": 15, "out": str(tmp_path)})
    assert r.ok, r.warnings
    assert r.data["qc"]["motion"] > 0.05 and r.data["qc"]["blank_frames"] < r.data["qc"]["sampled"]
    assert len(r.previews) >= 2


def _logo(d: Path) -> Path:
    p = d / "logo.svg"
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 100"><circle cx="50" cy="50" r="40" fill="#FF5A36"/>'
                 '<rect x="110" y="30" width="170" height="16" rx="8" fill="#ffffff"/></svg>')
    return p


@pytest.mark.slow
def test_three_title_arabic(browser, tmp_path):
    from studio.depts.motion import libs, three_d
    try:
        libs.ensure("three")
        svg = three_d.text_svg("استوديو", fonts={})
    except ToolError:
        pytest.skip("three.js or fonts not available offline")
    assert "<path" in svg
    r = call("motion_3d", {"preset": "title", "text": "استوديو", "size": "256x144", "duration": 1, "fps": 8, "out": str(tmp_path)})
    assert r.ok and r.data["qc"]["motion"] > 0.05 and not [w for w in r.warnings if "motion3d" in w]


@pytest.mark.slow
def test_three_logo_alpha(browser, tmp_path):
    from studio.depts.motion import libs
    try:
        libs.ensure("three")
    except ToolError:
        pytest.skip("three.js not cached and offline")
    r = call("motion_3d", {"preset": "logo", "logo": str(_logo(tmp_path)), "transparent": True, "size": "256x144", "duration": 1, "fps": 8,
                           "formats": ["webm"], "out": str(tmp_path)})
    assert r.ok and r.data["qc"]["alpha_coverage_max"] > 0.02


@pytest.mark.slow
def test_blender_render_or_fallback(browser, tmp_path):
    from studio.depts.motion.blender import blender_cmd
    r = call("motion_blender", {"preset": "logo", "logo": str(_logo(tmp_path)), "size": "160x90", "duration": 0.5, "fps": 6,
                                "quality": "draft", "engine_name": "CYCLES", "out": str(tmp_path)})
    assert r.ok
    if blender_cmd():
        assert any(f.endswith(".blend") for f in r.files) and not r.data.get("fallback")
    else:
        assert r.data["fallback"] and r.summary.startswith("FALLBACK")
