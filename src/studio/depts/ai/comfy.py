"""ComfyUI integration: HTTP client, workflow templates, offline validation, install/launch.

Why polling instead of the WebSocket: /history/<prompt_id> gives the same final state, needs no
extra dependency and survives dropped connections. Progress is read from /queue.
Everything uses urllib from the standard library.
"""
from __future__ import annotations

import copy
import json
import mimetypes
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from ...config import models_dir, studio_home, sub
from ...core.result import ToolError
from .catalog import BUNDLES, COMFY_REPO, CUSTOM_NODES, NODE_PACKS, Bundle, ModelFile

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "comfy"
IS_MAC = platform.system() == "Darwin"
IS_ARM_MAC = IS_MAC and platform.machine() == "arm64"


# ---------------------------------------------------------------- locations
def comfy_dir() -> Path:
    env = os.environ.get("COMFYUI_DIR", "").strip()
    return Path(env).expanduser() if env else studio_home() / "apps" / "ComfyUI"


def comfy_models_root() -> Path:
    """Model files live on the studio drive, shared by any ComfyUI install via extra_model_paths.yaml."""
    p = models_dir() / "comfyui"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_file() -> Path:
    return sub("apps") / "comfyui-state.json"


def _state() -> dict:
    try:
        return json.loads(state_file().read_text())
    except Exception:
        return {}


def _save_state(**kw) -> None:
    st = _state()
    st.update(kw)
    state_file().write_text(json.dumps(st, indent=1))


def base_url() -> str:
    env = os.environ.get("COMFYUI_URL", "").strip()
    if env:
        return env.rstrip("/")
    return _state().get("url", "http://127.0.0.1:8188").rstrip("/")


def model_path(f: ModelFile) -> Path:
    return comfy_models_root() / f.folder / f.filename


def bundle_installed(b: Bundle) -> bool:
    return all(model_path(f).exists() for f in b.files)


# ---------------------------------------------------------------- client
class ComfyClient:
    def __init__(self, url: str | None = None, timeout: float = 30):
        self.url = (url or base_url()).rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    # low level
    def _req(self, path: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None,
             timeout: float | None = None) -> bytes:
        req = urllib.request.Request(self.url + path, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise ComfyHTTPError(e.code, body) from None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            raise ToolError(f"ComfyUI is not reachable at {self.url} ({getattr(e, 'reason', e)})",
                            "start it with comfyui_start (or comfyui_setup first), or set COMFYUI_URL") from None

    def get_json(self, path: str):
        return json.loads(self._req(path) or b"null")

    def post_json(self, path: str, obj) -> dict:
        raw = self._req(path, json.dumps(obj).encode(), {"Content-Type": "application/json"}, "POST")
        return json.loads(raw) if raw.strip() else {}

    # API
    def alive(self) -> bool:
        try:
            self.system_stats()
            return True
        except Exception:
            return False

    def system_stats(self) -> dict:
        req = urllib.request.Request(self.url + "/system_stats")
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read())

    def object_info(self) -> dict:
        return self.get_json("/object_info")

    def queue_state(self) -> dict:
        return self.get_json("/queue")

    def upload_image(self, path: str | Path, name: str | None = None) -> str:
        """Upload into ComfyUI's input folder; returns the name to put in LoadImage."""
        p = Path(path)
        name = name or f"studio_{uuid.uuid4().hex[:10]}{p.suffix.lower() or '.png'}"
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n".encode(), p.read_bytes(), b"\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
            f"--{boundary}--\r\n".encode(),
        ])
        raw = self._req("/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}, "POST",
                        timeout=120)
        j = json.loads(raw)
        return (j.get("subfolder") + "/" if j.get("subfolder") else "") + j["name"]

    def queue(self, workflow: dict) -> str:
        try:
            j = self.post_json("/prompt", {"prompt": workflow, "client_id": self.client_id})
        except ComfyHTTPError as e:
            raise ToolError("ComfyUI rejected the workflow: " + explain_prompt_error(e.body),
                            "check comfyui_status for installed models / custom nodes") from None
        if j.get("node_errors"):
            raise ToolError("ComfyUI rejected the workflow: " + explain_prompt_error(json.dumps(j)))
        return j["prompt_id"]

    def wait(self, prompt_id: str, timeout: float = 900, poll: float = 1.0) -> dict:
        """Block until the prompt finished; return its history entry. Raises on error/timeout."""
        t0 = time.time()
        while True:
            h = self.get_json(f"/history/{prompt_id}") or {}
            entry = h.get(prompt_id)
            if entry:
                st = entry.get("status", {})
                if st.get("status_str") == "error":
                    msg = "unknown error"
                    for m in st.get("messages", []):
                        if m and m[0] == "execution_error":
                            d = m[1]
                            msg = f"{d.get('node_type')}: {d.get('exception_message', '').strip()}"
                    raise ToolError(f"ComfyUI failed while running the workflow — {msg}",
                                    "on a 16 GB Mac an out-of-memory error means: smaller size/length, close apps, "
                                    "or comfyui_start with lowvram=true")
                if st.get("completed", True) or entry.get("outputs"):
                    return entry
            if time.time() - t0 > timeout:
                raise JobTimeout(prompt_id)
            time.sleep(poll)

    def download_outputs(self, entry: dict, dest: Path) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        for node_id in sorted(entry.get("outputs", {}), key=str):
            out = entry["outputs"][node_id]
            for key in ("images", "gifs", "videos", "animated"):
                for item in out.get(key, []) or []:
                    if not isinstance(item, dict) or "filename" not in item or item.get("type") == "temp":
                        continue
                    q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                                "type": item.get("type", "output")})
                    data = self._req(f"/view?{q}", timeout=300)
                    p = dest / item["filename"]
                    p.write_bytes(data)
                    files.append(p)
        return files

    def free(self) -> None:
        try:
            self.post_json("/free", {"unload_models": True, "free_memory": True})
        except Exception:
            pass

    def interrupt(self) -> None:
        self.post_json("/interrupt", {})


class ComfyHTTPError(Exception):
    def __init__(self, code: int, body: str):
        super().__init__(f"HTTP {code}: {body[:300]}")
        self.code, self.body = code, body


class JobTimeout(Exception):
    def __init__(self, prompt_id: str):
        super().__init__(prompt_id)
        self.prompt_id = prompt_id


def explain_prompt_error(body: str) -> str:
    try:
        j = json.loads(body)
    except Exception:
        return body[:500]
    parts = []
    err = j.get("error") or {}
    if isinstance(err, dict) and err.get("message"):
        parts.append(err["message"] + (f" ({err.get('details')})" if err.get("details") else ""))
    for nid, ne in (j.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            parts.append(f"node {nid} [{ne.get('class_type')}]: {e.get('message')} {e.get('details', '')}".strip())
    return "; ".join(parts) or body[:500]


# ---------------------------------------------------------------- templates
def load_template(name: str) -> dict:
    p = ASSETS / f"{name}.json"
    if not p.exists():
        raise ToolError(f"no ComfyUI template {name!r}")
    return json.loads(p.read_text())


def _subst(value, params: dict, exports: dict):
    if isinstance(value, str):
        if value.startswith("$") and value[1:] in params:
            return params[value[1:]]
        if value.startswith("$"):
            raise ToolError(f"workflow placeholder {value} has no value")
        if value.startswith("@") and value[1:] in exports:
            return list(exports[value[1:]])
        return value
    if isinstance(value, list):
        return [_subst(v, params, exports) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, params, exports) for k, v in value.items()}
    return value


def fill(template: dict, params: dict, loader: dict | None = None) -> dict:
    """Template (+ optional loader template) + params → ComfyUI API-format workflow."""
    nodes: dict = {}
    exports: dict = {}
    if loader:
        for nid, n in loader["nodes"].items():
            nodes[nid] = _subst(copy.deepcopy(n), params, {})
        exports = loader.get("exports", {})
    for nid, n in template["nodes"].items():
        nodes[nid] = _subst(copy.deepcopy(n), params, exports)
    return nodes


def placeholders(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, str) and obj.startswith("$"):
        out.add(obj[1:])
    elif isinstance(obj, list):
        for v in obj:
            out |= placeholders(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= placeholders(v)
    return out


def snapshot_object_info() -> dict:
    return json.loads((ASSETS / "object_info_subset.json").read_text())["nodes"]


def _spec_options(spec) -> list | None:
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None


def validate(workflow: dict, info: dict, check_files: bool = True) -> list[str]:
    """Structural check against ComfyUI's /object_info: node types, required inputs, link targets and
    types, enum values (incl. model file names). Returns a list of human-readable problems."""
    problems: list[str] = []
    for nid, node in workflow.items():
        ct = node.get("class_type")
        if ct not in info:
            pack = NODE_PACKS.get(ct)
            problems.append(f"node {nid}: unknown node type {ct}" + (f" — install custom node pack {pack}" if pack else ""))
            continue
        spec = info[ct]["input"]
        req, opt = spec.get("required") or {}, spec.get("optional") or {}
        inputs = node.get("inputs", {})
        for k in req:
            if k not in inputs:
                problems.append(f"node {nid} [{ct}]: missing required input '{k}'")
        for k, v in inputs.items():
            s = req.get(k) or opt.get(k)
            if s is None:
                problems.append(f"node {nid} [{ct}]: unknown input '{k}'")
                continue
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int):
                src = workflow.get(v[0])
                if src is None:
                    problems.append(f"node {nid} [{ct}].{k}: links to missing node {v[0]}")
                    continue
                sct = src.get("class_type")
                if sct in info:
                    outs = info[sct]["output"]
                    if v[1] >= len(outs):
                        problems.append(f"node {nid}.{k}: {sct} has no output #{v[1]}")
                    elif isinstance(s[0], str) and s[0] not in ("*", "COMBO") and outs[v[1]] != s[0]:
                        problems.append(f"node {nid} [{ct}].{k}: expects {s[0]} but gets {outs[v[1]]} from {sct}")
                continue
            opts = _spec_options(s)
            if opts is not None:
                is_file = ("Loader" in ct and "name" in k) or (ct in ("LoadImage", "LoadImageMask") and k == "image")
                if is_file and not check_files:
                    continue
                if v not in opts:
                    if is_file:
                        problems.append(f"node {nid} [{ct}].{k}: file '{v}' is not installed in ComfyUI")
                    else:
                        problems.append(f"node {nid} [{ct}].{k}: '{v}' not one of {opts[:8]}…")
            elif s[0] == "INT" and not isinstance(v, int):
                problems.append(f"node {nid} [{ct}].{k}: expects INT, got {v!r}")
            elif s[0] == "FLOAT" and not isinstance(v, (int, float)):
                problems.append(f"node {nid} [{ct}].{k}: expects FLOAT, got {v!r}")
            elif s[0] == "STRING" and not isinstance(v, str):
                problems.append(f"node {nid} [{ct}].{k}: expects STRING, got {v!r}")
            elif s[0] == "BOOLEAN" and not isinstance(v, bool):
                problems.append(f"node {nid} [{ct}].{k}: expects BOOLEAN, got {v!r}")
            if len(s) > 1 and isinstance(s[1], dict) and isinstance(v, (int, float)) and not isinstance(v, bool):
                lo, hi = s[1].get("min"), s[1].get("max")
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    problems.append(f"node {nid} [{ct}].{k}: {v} outside [{lo}, {hi}]")
    return problems


# ---------------------------------------------------------------- running a job
JOBS_DIR_NAME = "comfy-jobs"


def jobs_dir() -> Path:
    p = sub("cache") / JOBS_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def submit(workflow: dict, meta: dict, client: ComfyClient | None = None, validate_live: bool = True) -> tuple[ComfyClient, str]:
    c = client or ComfyClient()
    if validate_live:
        try:
            info = c.object_info()
        except ComfyHTTPError:
            info = None
        if info:
            probs = validate(workflow, info)
            if probs:
                raise ToolError("Workflow does not match this ComfyUI install: " + "; ".join(probs[:6]),
                                "run comfyui_status to see installed models; comfyui_setup models=[…] to add them")
    pid = c.queue(workflow)
    (jobs_dir() / f"{pid}.json").write_text(json.dumps({"prompt_id": pid, "url": c.url, "meta": meta,
                                                        "workflow": workflow, "submitted": time.time()}, default=str))
    return c, pid


def load_job(prompt_id: str) -> dict:
    p = jobs_dir() / f"{prompt_id}.json"
    if not p.exists():
        raise ToolError(f"no studio job {prompt_id}", "the prompt_id comes from ai_generate_video/comfyui_run_workflow")
    return json.loads(p.read_text())


# ---------------------------------------------------------------- setup plan
def hf_endpoint() -> str:
    return (os.environ.get("STUDIO_HF_ENDPOINT") or os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")


def hf_url(f: ModelFile) -> str:
    return f"{hf_endpoint()}/{f.repo}/resolve/main/{urllib.parse.quote(f.path)}"


def live_size(f: ModelFile, timeout: float = 10) -> int | None:
    """Size from the Hub (x-linked-size header on the un-followed redirect)."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(hf_url(f), method="HEAD", headers=_hf_headers())
    try:
        r = opener.open(req, timeout=timeout)
        hdr = r.headers
    except urllib.error.HTTPError as e:
        hdr = e.headers
    except Exception:
        return None
    for k in ("x-linked-size", "content-length"):
        v = hdr.get(k) if hdr else None
        if v and v.isdigit() and int(v) > 1000:
            return int(v)
    return None


def _hf_headers() -> dict:
    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def torch_install_cmd(py: str) -> list[str]:
    cmd = [py, "-m", "pip", "install", "torch", "torchvision", "torchaudio"]
    if not IS_MAC and not shutil.which("nvidia-smi"):
        cmd += ["--index-url", "https://download.pytorch.org/whl/cpu"]
    return cmd


def plan_setup(models: list[str], custom_nodes: list[str] | None = None, update: bool = False,
               check_sizes: bool = True) -> dict:
    unknown = [m for m in models if m not in BUNDLES]
    if unknown:
        raise ToolError(f"unknown model id(s) {unknown}", f"choose from {sorted(BUNDLES)}")
    cdir = comfy_dir()
    py = str(cdir / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    steps: list[dict] = []
    installed = (cdir / "main.py").exists()
    if not installed:
        steps.append({"kind": "cmd", "title": "download ComfyUI", "cmd": ["git", "clone", "--depth", "1", COMFY_REPO, str(cdir)]})
    elif update:
        steps.append({"kind": "cmd", "title": "update ComfyUI", "cmd": ["git", "-C", str(cdir), "pull", "--ff-only"]})
    if not Path(py).exists():
        steps.append({"kind": "cmd", "title": "create ComfyUI's own Python environment",
                      "cmd": [sys.executable, "-m", "venv", str(cdir / "venv")]})
        steps.append({"kind": "cmd", "title": "install PyTorch (" + ("Metal/MPS build" if IS_MAC else "CPU/CUDA build") + ")",
                      "cmd": torch_install_cmd(py), "size": "≈0.7–2.5 GB"})
    if not installed or update or not Path(py).exists():
        steps.append({"kind": "cmd", "title": "install ComfyUI requirements",
                      "cmd": [py, "-m", "pip", "install", "-r", str(cdir / "requirements.txt")]})
    packs = list(dict.fromkeys((custom_nodes or []) + [p for m in models for p in BUNDLES[m].nodes]))
    for pack in packs:
        if pack not in CUSTOM_NODES:
            raise ToolError(f"unknown custom node pack {pack!r}", f"known: {sorted(CUSTOM_NODES)}")
        pdir = cdir / "custom_nodes" / pack
        if not pdir.exists():
            steps.append({"kind": "cmd", "title": f"custom nodes: {pack}", "cmd": ["git", "clone", "--depth", "1", CUSTOM_NODES[pack], str(pdir)]})
            steps.append({"kind": "cmd", "title": f"requirements for {pack}",
                          "cmd": [py, "-m", "pip", "install", "-r", str(pdir / "requirements.txt")]})
    steps.append({"kind": "yaml", "title": "point ComfyUI at the studio model folder", "path": str(cdir / "extra_model_paths.yaml")})
    total = 0
    seen: set[str] = set()
    for m in models:
        for f in BUNDLES[m].files:
            key = f"{f.folder}/{f.filename}"
            if key in seen:
                continue
            seen.add(key)
            dest = comfy_models_root() / f.folder / f.filename
            size = (live_size(f) if check_sizes else None) or f.size
            if dest.exists() and dest.stat().st_size == size:
                continue
            steps.append({"kind": "download", "title": f"{f.filename} ({size / 1e9:.2f} GB) for {m}", "url": hf_url(f),
                          "dest": str(dest), "size": size, "repo": f.repo})
            total += size
    try:
        free = shutil.disk_usage(comfy_models_root()).free
    except OSError:
        free = 0
    return {"comfy_dir": str(cdir), "models_root": str(comfy_models_root()), "models": models, "custom_nodes": packs,
            "steps": steps, "download_bytes": total, "free_bytes": free,
            "licences": {m: {"licence": BUNDLES[m].licence, "commercial": BUNDLES[m].commercial} for m in models}}


EXTRA_YAML = """# written by studio-mcp: models live on the studio drive, not inside ComfyUI
studio:
    base_path: {root}
    checkpoints: checkpoints
    diffusion_models: |
        diffusion_models
        unet
    text_encoders: |
        text_encoders
        clip
    clip: clip
    unet: unet
    vae: vae
    loras: loras
    upscale_models: upscale_models
    controlnet: controlnet
"""


def write_extra_yaml(path: str | Path) -> None:
    root = comfy_models_root()
    for d in ("checkpoints", "diffusion_models", "text_encoders", "vae", "loras", "upscale_models", "controlnet"):
        (root / d).mkdir(parents=True, exist_ok=True)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(EXTRA_YAML.format(root=str(root)))


def download(url: str, dest: str | Path, size: int | None = None, progress=None, chunk: int = 1 << 20) -> Path:
    """Resumable download (.part + HTTP Range). Verifies the final size when known."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (not size or dest.stat().st_size == size):
        return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = _hf_headers()
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and size and have == size:
            part.rename(dest)
            return dest
        if e.code in (401, 403):
            raise ToolError(f"download refused ({e.code}) for {url}", "the repo may be gated: accept its licence on huggingface.co and set HF_TOKEN")
        raise ToolError(f"download failed ({e.code}) for {url}")
    mode = "ab" if have and r.status == 206 else "wb"
    if mode == "wb":
        have = 0
    last = time.time()
    with r, open(part, mode) as fh:
        while True:
            buf = r.read(chunk)
            if not buf:
                break
            fh.write(buf)
            have += len(buf)
            if progress and time.time() - last > 2:
                progress(have)
                last = time.time()
    if size and have != size:
        raise ToolError(f"incomplete download of {dest.name}: {have} of {size} bytes", "run the setup again — it resumes")
    part.rename(dest)
    return dest


def setup_log() -> Path:
    return sub("logs") / "comfyui-setup.log"


def setup_status_file() -> Path:
    return sub("logs") / "comfyui-setup.json"


def execute_plan(plan: dict) -> dict:
    """Run every step, recording progress to logs/comfyui-setup.json (read by comfyui_status)."""
    status = {"started": time.time(), "state": "running", "steps": [], "pid": os.getpid()}
    sfile = setup_status_file()

    def save():
        sfile.write_text(json.dumps(status, indent=1))

    with open(setup_log(), "a") as log:
        for i, st in enumerate(plan["steps"]):
            rec = {"i": i, "title": st["title"], "state": "running"}
            status["steps"].append(rec)
            save()
            log.write(f"\n=== {st['title']}\n")
            log.flush()
            try:
                if st["kind"] == "cmd":
                    r = subprocess.run(st["cmd"], stdout=log, stderr=subprocess.STDOUT, timeout=7200)
                    if r.returncode != 0:
                        raise RuntimeError(f"exit {r.returncode}: {' '.join(st['cmd'][:4])}… (see {setup_log()})")
                elif st["kind"] == "yaml":
                    write_extra_yaml(st["path"])
                elif st["kind"] == "download":
                    def prog(n, rec=rec, total=st.get("size")):
                        rec["bytes"] = n
                        rec["pct"] = round(100 * n / total, 1) if total else None
                        save()
                    download(st["url"], st["dest"], st.get("size"), prog)
                rec["state"] = "done"
            except Exception as e:
                rec["state"] = "failed"
                rec["error"] = str(e)
                status["state"] = "failed"
                status["finished"] = time.time()
                save()
                log.write(f"FAILED: {e}\n")
                return status
            save()
    status["state"] = "done"
    status["finished"] = time.time()
    save()
    return status


# ---------------------------------------------------------------- server
def server_log() -> Path:
    return sub("logs") / "comfyui.log"


def start_server(port: int = 8188, lowvram: bool = False, wait: float = 90, extra_args: list[str] | None = None) -> dict:
    cdir = comfy_dir()
    py = cdir / "venv" / "bin" / "python"
    if not (cdir / "main.py").exists() or not py.exists():
        raise ToolError(f"ComfyUI is not installed at {cdir}", "run comfyui_setup (dry run first), or set COMFYUI_DIR/COMFYUI_URL")
    url = f"http://127.0.0.1:{port}"
    if ComfyClient(url).alive():
        _save_state(url=url)
        return {"url": url, "already_running": True}
    if not (cdir / "extra_model_paths.yaml").exists():
        write_extra_yaml(cdir / "extra_model_paths.yaml")
    cmd = [str(py), "main.py", "--listen", "127.0.0.1", "--port", str(port), "--preview-method", "none"]
    if lowvram:
        cmd.append("--lowvram")
    if not IS_MAC and not shutil.which("nvidia-smi"):
        cmd.append("--cpu")
    cmd += extra_args or []
    env = {**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
    log = open(server_log(), "a")
    proc = subprocess.Popen(cmd, cwd=str(cdir), stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    _save_state(url=url, pid=proc.pid, started=time.time(), cmd=cmd)
    t0 = time.time()
    while time.time() - t0 < wait:
        if proc.poll() is not None:
            raise ToolError(f"ComfyUI exited during start-up (code {proc.returncode})", f"see {server_log()}")
        if ComfyClient(url).alive():
            return {"url": url, "pid": proc.pid, "seconds": round(time.time() - t0, 1)}
        time.sleep(1)
    return {"url": url, "pid": proc.pid, "still_starting": True}


def stop_server() -> dict:
    st = _state()
    pid = st.get("pid")
    if not pid:
        return {"stopped": False, "reason": "no ComfyUI started by the studio (pid unknown)"}
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        _save_state(pid=None)
        return {"stopped": False, "reason": "process already gone"}
    except PermissionError:
        os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.3)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            pass
    _save_state(pid=None)
    return {"stopped": True, "pid": pid}
