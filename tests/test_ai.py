"""AI department tests.

Fast tests run everything against in-process FAKE servers (ComfyUI API, Pollinations, HF router, fal) so the whole
client/route/post-processing chain is exercised without GPUs or models. Real generation needs the Mac.
Slow/online tests: live Pollinations call; a real ComfyUI if COMFYUI_TEST_URL is set (structure validation of every
template against its live /object_info)."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from studio.core.registry import call, load_all
from studio.core.result import ToolError

load_all()
from studio.depts.ai import backends as B  # noqa: E402
from studio.depts.ai import catalog, comfy, prompting  # noqa: E402

TEMPLATES = ["t2i", "i2i", "inpaint", "outpaint", "upscale", "video_ltxv_t2v", "video_ltxv_i2v", "video_wan21_t2v",
             "video_wan22_t2v", "video_wan22_i2v"]


# ------------------------------------------------------------------ fake ComfyUI
def _all_model_files() -> list[str]:
    return sorted({f.filename for b in catalog.BUNDLES.values() for f in b.files})


def fake_object_info(uploaded: list[str]) -> dict:
    info = json.loads(json.dumps(comfy.snapshot_object_info()))
    files = _all_model_files()
    for ct, node in info.items():
        for grp in ("required", "optional"):
            for k, spec in (node["input"].get(grp) or {}).items():
                if ct in ("LoadImage", "LoadImageMask") and k == "image":
                    spec[0] = list(uploaded)
                elif "Loader" in ct and "name" in k and isinstance(spec[0], list):
                    spec[0] = files
                elif "Loader" in ct and "name" in k and spec[0] == "COMBO":
                    spec[1]["options"] = files
    return info


class FakeComfy:
    def __init__(self):
        self.uploads: dict[str, bytes] = {}
        self.prompts: dict[str, dict] = {}
        self.history: dict[str, dict] = {}
        self.outputs: dict[str, bytes] = {}
        self.hold = False          # keep jobs "running" (for wait=false / collect tests)
        self.n = 0

    def render(self, pid: str, wf: dict):
        """Pretend to execute: produce images with the right sizes."""
        size, frames = (64, 64), 1
        for n in wf.values():
            i = n["inputs"]
            if n["class_type"] in ("EmptySD3LatentImage", "EmptyLatentImage"):
                size = (i["width"], i["height"])
            if n["class_type"] in ("EmptyLTXVLatentVideo", "EmptyHunyuanLatentVideo", "LTXVImgToVideo", "Wan22ImageToVideoLatent"):
                size, frames = (i["width"], i["height"]), min(i["length"], 25)
        src = None
        for n in wf.values():
            if n["class_type"] == "LoadImage":
                src = Image.open(io.BytesIO(self.uploads[n["inputs"]["image"]])).convert("RGB")
        for n in wf.values():
            if n["class_type"] == "ImagePadForOutpaint" and src is not None:
                i = n["inputs"]
                size = (src.width + i["left"] + i["right"], src.height + i["top"] + i["bottom"])
                src = None
            if n["class_type"] == "ImageScaleBy" and src is not None:
                size = (round(src.width * 4 * n["inputs"]["scale_by"]), round(src.height * 4 * n["inputs"]["scale_by"]))
                src = None
        if src is not None:
            size = src.size
        imgs = []
        for f in range(frames):
            im = Image.new("RGB", size, (40 + f * 5 % 200, 90, 160))
            for x in range(0, size[0], 8):  # some detail so QC doesn't call it flat
                for y in range(0, size[1], 16):
                    im.putpixel((x, y), (255, 255, 255))
            name = f"studio_{pid[:6]}_{f:05d}_.png"
            b = io.BytesIO()
            im.save(b, "PNG")
            self.outputs[name] = b.getvalue()
            imgs.append({"filename": name, "subfolder": "studio", "type": "output"})
        self.history[pid] = {"outputs": {"15": {"images": imgs}}, "status": {"status_str": "success", "completed": True}}


def make_handler(fc: FakeComfy):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/system_stats":
                return self._json({"system": {"comfyui_version": "fake"}, "devices": [{"type": "mps", "name": "fake"}]})
            if u.path == "/object_info":
                return self._json(fake_object_info(list(fc.uploads)))
            if u.path == "/queue":
                run = [[0, p] for p in fc.prompts if p not in fc.history]
                return self._json({"queue_running": run, "queue_pending": []})
            if u.path.startswith("/history/"):
                pid = u.path.split("/")[-1]
                return self._json({pid: fc.history[pid]} if pid in fc.history else {})
            if u.path == "/view":
                q = urllib.parse.parse_qs(u.query)
                data = fc.outputs[q["filename"][0]]
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._json({"error": "nope"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            if self.path == "/upload/image":
                m = re.search(rb'filename="([^"]+)"\r\nContent-Type: [^\r]+\r\n\r\n', body)
                name = m.group(1).decode()
                start = m.end()
                end = body.index(b"\r\n--", start)
                fc.uploads[name] = body[start:end]
                return self._json({"name": name, "subfolder": "", "type": "input"})
            if self.path == "/prompt":
                j = json.loads(body)
                wf = j["prompt"]
                probs = comfy.validate(wf, fake_object_info(list(fc.uploads)))
                if probs:
                    return self._json({"error": {"message": "Prompt outputs failed validation"},
                                       "node_errors": {"x": {"class_type": "?", "errors": [{"message": p} for p in probs]}}}, 400)
                fc.n += 1
                pid = f"pid{fc.n:04d}abcdef"
                fc.prompts[pid] = wf
                if not fc.hold:
                    fc.render(pid, wf)
                return self._json({"prompt_id": pid, "number": fc.n, "node_errors": {}})
            if self.path in ("/free", "/interrupt"):
                return self._json({})
            self._json({"error": "nope"}, 404)
    return H


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def fake_comfy(tmp_path, monkeypatch):
    fc = FakeComfy()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fc))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("COMFYUI_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield fc
    srv.shutdown()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COMFYUI_DIR", str(tmp_path / "home" / "apps" / "ComfyUI"))
    monkeypatch.setenv("COMFYUI_URL", f"http://127.0.0.1:{_free_port()}")   # nothing listening
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "POLLINATIONS_TOKEN", "FAL_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(B, "IS_ARM_MAC", False)
    return tmp_path / "home"


def sample_image(tmp_path, size=(320, 240)) -> Path:
    im = Image.new("RGB", size)
    for x in range(size[0]):
        for y in range(size[1]):
            im.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, (x * y) % 256))
    p = tmp_path / "src.png"
    im.save(p)
    return p


# ------------------------------------------------------------------ catalog / prompting
def test_sizes_and_gen_sizes():
    assert catalog.parse_size("4:5") == (1080, 1350)
    assert catalog.parse_size("story") == (1080, 1920)
    assert catalog.parse_size("800x600") == (800, 600)
    w, h = catalog.parse_size("2:1")
    assert abs(w / h - 2) < 0.02
    with pytest.raises(ValueError):
        catalog.parse_size("banana")
    for target in [(1080, 1350), (1080, 1920), (1920, 1080), (1024, 1024)]:
        for mult in (16, 64):
            w, h = catalog.gen_size(target, 1024 * 1024, mult)
            assert w % mult == 0 and h % mult == 0
            assert abs(w / h - target[0] / target[1]) < 0.08
            assert 0.75e6 < w * h < 1.3e6


def test_colour_names():
    assert catalog.colour_name("#1B2A4A").startswith("navy blue") or "blue" in catalog.colour_name("#1B2A4A")
    assert "gold" in catalog.colour_name("#C9A227")
    assert catalog.colour_name("teal") == "teal"


def test_prompt_families_and_warnings():
    f = prompting.build_image_prompt("a red bicycle", "flux", "product", ["#ff0000"], 1080, 1920, negative="people")
    assert f["negative"] == "" and "Avoid: people" in f["prompt"] and "vertical" in f["prompt"]
    s = prompting.build_image_prompt("a red bicycle", "sdxl", "product", None, 1024, 1024)
    assert "watermark" in s["negative"] and s["prompt"].startswith("a red bicycle,")
    assert prompting.language_warnings("قطة على سطح")  # Arabic flagged
    assert prompting.language_warnings('poster saying "SALE"')
    assert prompting.wants_flow("a man says hello to his daughter")[0]
    assert not prompting.wants_flow("slow drone shot over dunes at sunrise")[0]
    v = prompting.build_video_prompt("a teapot steams", "veo", dialogue="يلا بينا")
    assert "Dialogue" in v["prompt"] and "Audio" in v["prompt"]


def test_prompt_enhance_tool():
    r = call("ai_prompt_enhance", {"idea": "a falafel sandwich", "target": "sdxl", "style": "food"})
    assert r.data["negative"] and "food photography" in r.data["prompt"]
    with pytest.raises(ToolError):
        call("ai_prompt_enhance", {"idea": "x", "style": "nope"})


# ------------------------------------------------------------------ templates & validator
def dummy_params(tpl: dict) -> dict:
    files = _all_model_files()
    p = {}
    for k in comfy.placeholders(tpl["nodes"]):
        p[k] = {"prompt": "x", "negative": "", "prefix": "t", "sampler": "euler", "scheduler": "simple",
                "latent_class": "EmptySD3LatentImage", "image": "in.png", "mask": "m.png",
                "width": 512, "height": 512, "length": 33, "steps": 4, "seed": 1, "batch": 1}.get(k, 8)
        if k in ("unet", "t5", "clip_l", "vae", "ckpt", "umt5", "upscale_model"):
            p[k] = files[0]
        if k in ("cfg", "denoise", "rescale", "fps", "shift"):
            p[k] = 1.0
    return p


@pytest.mark.parametrize("name", TEMPLATES)
def test_templates_valid_against_real_comfy_schema(name):
    tpl = comfy.load_template(name)
    loader = comfy.load_template("loader_flux_gguf") if any(
        isinstance(v, str) and v.startswith("@") for n in tpl["nodes"].values() for v in n["inputs"].values()) else None
    wf = comfy.fill(tpl, dummy_params({"nodes": {**tpl["nodes"], **(loader or {"nodes": {}})["nodes"]}}), loader)
    probs = comfy.validate(wf, comfy.snapshot_object_info(), check_files=False)
    assert probs == [], probs


def test_validator_catches_problems():
    info = comfy.snapshot_object_info()
    wf = {"1": {"class_type": "KSampler", "inputs": {"model": ["9", 0]}},
          "2": {"class_type": "MysteryNode", "inputs": {}},
          "3": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "nothere.gguf"}}}
    info = {k: v for k, v in info.items() if k != "UnetLoaderGGUF"}
    probs = " | ".join(comfy.validate(wf, info))
    assert "missing required input 'seed'" in probs and "links to missing node 9" in probs
    assert "unknown node type MysteryNode" in probs and "ComfyUI-GGUF" in probs


# ------------------------------------------------------------------ routing
def test_router_fallbacks(fake_comfy, monkeypatch):
    assert B.route("t2i")[0] == "comfyui"          # all models visible on the fake server
    assert B.route("inpaint")[0] == "comfyui" and B.route("upscale")[0] == "comfyui"


def test_router_nothing_available():
    be, why = B.route("t2i")
    assert be == "pollinations" and any("not running" in w for w in why)
    with pytest.raises(ToolError):
        B.route("inpaint")
    assert B.route("upscale")[0] == "lanczos"


# ------------------------------------------------------------------ generation through fake ComfyUI
def test_generate_image_comfyui(fake_comfy, home):
    r = call("ai_generate_image", {"prompt": "a lighthouse at dusk", "size": "4:5", "count": 2, "seed": 7,
                                   "backend": "comfyui", "style": "cinematic", "project": "t"})
    pngs = [f for f in r.files if f.endswith(".png")]
    assert len(pngs) == 2
    for p in pngs:
        im = Image.open(p)
        assert im.size == (1080, 1350)
        assert im.text.get("studio:backend") == "comfyui"
        side = json.loads(Path(p).with_suffix(".json").read_text())
        assert side["model"] == "flux-schnell-q4" and side["licence"] == "Apache-2.0" and side["seed"] in (7, 8)
    assert Path(r.previews[0]).exists()
    wf = list(fake_comfy.prompts.values())[0]
    types = {n["class_type"] for n in wf.values()}
    assert {"UnetLoaderGGUF", "DualCLIPLoaderGGUF", "EmptySD3LatentImage", "KSampler", "SaveImage"} <= types
    ks = next(n for n in wf.values() if n["class_type"] == "KSampler")["inputs"]
    assert ks["steps"] == 4 and ks["cfg"] == 1.0 and ks["seed"] in (7, 8)


def test_generate_image_sdxl_uses_negative(fake_comfy):
    call("ai_generate_image", {"prompt": "a cat", "backend": "comfyui", "model": "sdxl-lightning", "negative": "dogs"})
    wf = list(fake_comfy.prompts.values())[0]
    texts = [n["inputs"]["text"] for n in wf.values() if n["class_type"] == "CLIPTextEncode"]
    assert any("dogs" in t for t in texts)
    assert any(n["class_type"] == "CheckpointLoaderSimple" for n in wf.values())


def test_inpaint_keeps_unmasked_pixels(fake_comfy, tmp_path):
    src = sample_image(tmp_path)
    r = call("ai_edit_image", {"image": str(src), "prompt": "a blue box", "mode": "inpaint", "mask_box": [100, 60, 80, 80]})
    out = Image.open(r.files[0]).convert("RGB")
    orig = Image.open(src).convert("RGB")
    assert out.size == orig.size
    for xy in [(5, 5), (310, 230), (20, 200)]:
        assert out.getpixel(xy) == orig.getpixel(xy)
    assert out.getpixel((140, 100)) != orig.getpixel((140, 100))
    assert Path(r.previews[0]).exists()


def test_outpaint_to_story(fake_comfy, tmp_path):
    src = sample_image(tmp_path)
    r = call("ai_edit_image", {"image": str(src), "prompt": "more sky", "mode": "outpaint", "expand_to": "9:16"})
    out = Image.open(r.files[0])
    assert abs(out.width / out.height - 9 / 16) < 0.03
    assert out.width >= 300


def test_edit_needs_mask(fake_comfy, tmp_path):
    with pytest.raises(ToolError):
        call("ai_edit_image", {"image": str(sample_image(tmp_path)), "prompt": "x", "mode": "inpaint"})


def test_upscale_comfy_and_fallback(fake_comfy, tmp_path, monkeypatch):
    src = sample_image(tmp_path)
    r = call("ai_upscale", {"image": str(src), "scale": 2})
    assert Image.open(r.files[0]).size == (640, 480)
    assert json.loads(Path(r.files[1]).read_text())["model"] == "realesrgan-x4"
    wf = list(fake_comfy.prompts.values())[0]
    assert next(n for n in wf.values() if n["class_type"] == "ImageScaleBy")["inputs"]["scale_by"] == 0.5


def test_upscale_lanczos_fallback(tmp_path):
    r = call("ai_upscale", {"image": str(sample_image(tmp_path)), "scale": 3})
    assert Image.open(r.files[0]).size == (960, 720)
    assert any("NO AI upscaler" in w for w in r.warnings)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_video_local_and_collect(fake_comfy):
    r = call("ai_generate_video", {"prompt": "slow push-in on a steaming teapot", "size": "16:9", "duration": 2,
                                   "route": "comfyui", "seed": 3})
    mp4 = r.files[0]
    pr = r.data["probe"]
    assert mp4.endswith(".mp4") and pr["width"] % 32 == 0 and abs(pr["width"] / pr["height"] - 16 / 9) < 0.1
    wf = list(fake_comfy.prompts.values())[0]
    assert any(n["class_type"] == "LTXVScheduler" for n in wf.values())
    fake_comfy.hold = True
    q = call("ai_generate_video", {"prompt": "waves on rocks", "route": "comfyui", "wait": False, "duration": 1})
    pid = q.data["prompt_id"]
    assert "not finished" in call("comfyui_collect", {"prompt_id": pid, "wait": False}).summary
    fake_comfy.render(pid, fake_comfy.prompts[pid])
    done = call("comfyui_collect", {"prompt_id": pid})
    assert done.files[0].endswith(".mp4") and Path(done.previews[0]).exists()


def test_video_handoff_to_flow(fake_comfy):
    r = call("ai_generate_video", {"prompt": "A grandmother tells her grandson a story in the kitchen", "dialogue": "زمان يا حبيبي"})
    assert r.data["handoff"]["handoff_to"] == "flow-studio-director"
    assert "Dialogue" in r.data["handoff"]["prompt"] and Path(r.files[0]).exists()


def test_run_workflow_template_with_upload(fake_comfy, tmp_path):
    src = sample_image(tmp_path)
    r = call("comfyui_run_workflow", {"workflow": "upscale", "params": {"image": str(src), "upscale_model":
                                      "RealESRGAN_x4plus.safetensors", "rescale": 0.25, "prefix": "x"}})
    assert any(f.endswith(".png") for f in r.files) and r.previews
    with pytest.raises(ToolError):
        call("comfyui_run_workflow", {"workflow": "upscale", "params": {}})


def test_rejected_workflow_is_explained(fake_comfy):
    c = comfy.ComfyClient()
    with pytest.raises(ToolError) as e:
        comfy.submit({"1": {"class_type": "KSampler", "inputs": {}}}, {}, c)
    assert "missing required input" in str(e.value)


def test_status_and_backends(fake_comfy):
    s = call("comfyui_status", {})
    assert s.data["running"] is True
    b = call("ai_backends", {"check_network": False})
    assert b.data["routes"]["t2i"]["backend"] == "comfyui"


# ------------------------------------------------------------------ setup / downloads
def test_setup_dry_run_plan(monkeypatch):
    monkeypatch.setattr(comfy, "live_size", lambda f, timeout=10: None)
    r = call("comfyui_setup", {"models": ["flux-schnell-q4", "ltxv-2b"]})
    kinds = [s["kind"] for s in r.data["steps"]]
    assert kinds.count("download") == 5  # T5 shared between FLUX and LTX → downloaded once
    assert any("ComfyUI-GGUF" in s["title"] for s in r.data["steps"])
    assert "Apache-2.0" in r.summary and "LTXV" in r.summary
    with pytest.raises(ToolError):
        call("comfyui_setup", {"models": ["flux-dev"]})


def test_resumable_download(tmp_path, monkeypatch):
    blob = os.urandom(300_000)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            rng = self.headers.get("Range")
            start = int(rng.split("=")[1].split("-")[0]) if rng else 0
            data = blob[start:]
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dest = tmp_path / "m" / "model.bin"
    dest.parent.mkdir()
    (dest.parent / "model.bin.part").write_bytes(blob[:100_000])
    comfy.download(f"http://127.0.0.1:{srv.server_address[1]}/x", dest, len(blob))
    srv.shutdown()
    assert dest.read_bytes() == blob


def test_setup_inline_execution(tmp_path, monkeypatch):
    """confirm=true, background=false with only a yaml step (ComfyUI 'already installed')."""
    cdir = Path(os.environ["COMFYUI_DIR"])
    (cdir / "venv" / "bin").mkdir(parents=True)
    (cdir / "main.py").write_text("")
    (cdir / "venv" / "bin" / "python").write_text("")
    r = call("comfyui_setup", {"models": [], "confirm": True, "background": False})
    assert r.ok and (cdir / "extra_model_paths.yaml").exists()
    assert "diffusion_models" in (cdir / "extra_model_paths.yaml").read_text()


def test_mflux_command():
    cmd = B.mflux_cmd("schnell-q4", "a cat", 1024, 768, [1, 2], "/tmp/o_{seed}.png", image="/tmp/i.png", strength=0.3)
    assert cmd[:5] == ["mflux-generate", "--model", "mflux-community/flux-1-schnell-mflux-q4", "--base-model", "schnell"]
    assert "--quantize" not in cmd and cmd[cmd.index("--seed") + 1:cmd.index("--seed") + 3] == ["1", "2"]
    assert cmd[cmd.index("--image") + 2] == "0.30"
    z = B.mflux_cmd("z-image-turbo", "x", 512, 512, [1], "o.png")
    assert z[0] == "mflux-generate-z-image-turbo" and z[z.index("--quantize") + 1] == "4"
    with pytest.raises(ToolError):
        call("mflux_setup", {"confirm": True, "background": False})  # not an Apple-Silicon Mac here


# ------------------------------------------------------------------ cloud backends (faked)
def _serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _jpeg_with_make(size, make="sana") -> bytes:
    im = Image.new("RGB", size, (200, 120, 60))
    for x in range(0, size[0], 5):
        im.putpixel((x, x % size[1]), (0, 0, 0))
    ex = Image.Exif()
    ex[271] = make
    b = io.BytesIO()
    im.save(b, "JPEG", exif=ex)
    return b.getvalue()


def test_pollinations_fake(monkeypatch):
    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            seen.update(q)
            data = _jpeg_with_make((int(q["width"][0]) // 2, int(q["height"][0]) // 2))
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(data)
    srv, url = _serve(H)
    monkeypatch.setenv("STUDIO_POLLINATIONS_URL", url)
    r = call("ai_generate_image", {"prompt": "a desert fox", "size": "9:16", "seed": 5})
    srv.shutdown()
    assert Image.open(r.files[0]).size == (1080, 1920)
    side = json.loads(Path(r.files[1]).read_text())
    assert side["backend"] == "pollinations" and side["served_model"] == "sana" and side["generated_size"][0] < 1080
    assert seen["seed"] == ["5"] and seen["nologo"] == ["true"]
    assert any("watermark" in w or "logo" in w for w in r.warnings) and any("sana" in w for w in r.warnings)


def test_hf_fake_and_credit_exhaustion(monkeypatch):
    mode = {"code": 200}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Authorization"] == "Bearer hf_test" and body["parameters"]["num_inference_steps"] == 4
            if mode["code"] != 200:
                self.send_response(mode["code"])
                self.end_headers()
                self.wfile.write(b'{"error":"credits"}')
                return
            b = io.BytesIO()
            Image.new("RGB", (body["parameters"]["width"], body["parameters"]["height"]), (9, 99, 199)).save(b, "PNG")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b.getvalue())
    srv, url = _serve(H)
    monkeypatch.setenv("STUDIO_HF_ROUTER", url)
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    r = call("ai_generate_image", {"prompt": "blue sky", "size": "16:9"})
    assert r.data["backend"] == "hf" and Image.open(r.files[0]).size == (1920, 1080)
    assert any("flat" in w for w in r.warnings)          # QC notices a flat image
    mode["code"] = 402
    with pytest.raises(ToolError) as e:
        call("ai_generate_image", {"prompt": "blue sky"})
    assert "credits" in str(e.value)
    srv.shutdown()


def test_byok_requires_confirmation(monkeypatch):
    r = call("ai_generate_image_byok", {"prompt": "x", "provider": "fal"})
    assert "Nothing was spent" in r.summary
    img = io.BytesIO()
    Image.new("RGB", (64, 64), (1, 2, 3)).save(img, "PNG")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            port = self.server.server_address[1]
            b = json.dumps({"images": [{"url": f"http://127.0.0.1:{port}/img.png"}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(img.getvalue())
    srv, url = _serve(H)
    monkeypatch.setenv("STUDIO_FAL_URL", url)
    monkeypatch.setenv("FAL_KEY", "k")
    r = call("ai_generate_image_byok", {"prompt": "x", "provider": "fal", "confirm": True})
    srv.shutdown()
    assert r.warnings[0].startswith("PAID") and Path(r.files[0]).exists()


# ------------------------------------------------------------------ online / real server
@pytest.mark.slow
def test_pollinations_live(monkeypatch):
    try:
        r = call("ai_generate_image", {"prompt": "a lemon on a blue plate, studio photo", "size": "1:1", "seed": 3,
                                       "backend": "pollinations"})
    except ToolError as e:
        pytest.skip(f"pollinations unreachable: {e}")
    assert Image.open(r.files[0]).size == (1024, 1024)


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("COMFYUI_TEST_URL"), reason="set COMFYUI_TEST_URL to a real ComfyUI (+ComfyUI-GGUF)")
@pytest.mark.parametrize("name", TEMPLATES)
def test_templates_against_live_comfy(name):
    info = comfy.ComfyClient(os.environ["COMFYUI_TEST_URL"]).object_info()
    tpl = comfy.load_template(name)
    loader = comfy.load_template("loader_flux_gguf") if any(
        isinstance(v, str) and v.startswith("@") for n in tpl["nodes"].values() for v in n["inputs"].values()) else None
    wf = comfy.fill(tpl, dummy_params({"nodes": {**tpl["nodes"], **(loader or {"nodes": {}})["nodes"]}}), loader)
    assert comfy.validate(wf, info, check_files=False) == []
