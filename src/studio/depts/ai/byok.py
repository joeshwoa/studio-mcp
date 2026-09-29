"""OPTIONAL paid providers with the user's own key. Never used by the automatic router.

Prices are indicative (2026) and only shown so the user can consent knowingly.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request

from PIL import Image

from ...core.result import ToolError
from .backends import Gen

PROVIDERS = {
    "fal": {"env": "FAL_KEY", "model": "fal-ai/flux/schnell", "price": "≈ US$0.003 per megapixel (FLUX schnell)",
            "url": "https://fal.run"},
    "replicate": {"env": "REPLICATE_API_TOKEN", "model": "black-forest-labs/flux-schnell", "price": "≈ US$0.003 per image",
                  "url": "https://api.replicate.com"},
    "openai": {"env": "OPENAI_API_KEY", "model": "gpt-image-1", "price": "≈ US$0.01–0.17 per image depending on quality",
               "url": "https://api.openai.com"},
}


def _http(url: str, body: dict | None, headers: dict, timeout: float = 180) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **headers},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise ToolError(f"{url.split('/')[2]} error {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except (urllib.error.URLError, OSError) as e:
        raise ToolError(f"{url.split('/')[2]} not reachable ({getattr(e, 'reason', e)})")


def _fetch_image(url: str) -> Image.Image:
    return Image.open(io.BytesIO(_http(url, None, {}))).convert("RGB")


def generate(provider: str, prompt: str, w: int, h: int, seed: int, model: str = "") -> Gen:
    p = PROVIDERS.get(provider)
    if not p:
        raise ToolError(f"unknown provider {provider!r}", f"one of {sorted(PROVIDERS)}")
    key = os.environ.get(p["env"], "")
    if not key:
        raise ToolError(f"{provider} needs {p['env']} in the environment (the user's own paid key)")
    base = os.environ.get(f"STUDIO_{provider.upper()}_URL", p["url"]).rstrip("/")
    model = model or p["model"]
    t0 = time.time()
    if provider == "fal":
        j = json.loads(_http(f"{base}/{model}", {"prompt": prompt, "image_size": {"width": w, "height": h}, "seed": seed,
                                                 "num_images": 1, "enable_safety_checker": True},
                             {"Authorization": f"Key {key}"}))
        im = _fetch_image(j["images"][0]["url"])
    elif provider == "replicate":
        ar = _nearest_ar(w, h, ["1:1", "16:9", "21:9", "3:2", "2:3", "4:5", "5:4", "3:4", "4:3", "9:16", "9:21"])
        j = json.loads(_http(f"{base}/v1/models/{model}/predictions",
                             {"input": {"prompt": prompt, "aspect_ratio": ar, "seed": seed, "output_format": "png"}},
                             {"Authorization": f"Bearer {key}", "Prefer": "wait"}))
        outp = j.get("output")
        if not outp:
            raise ToolError(f"replicate returned no output (status {j.get('status')})")
        im = _fetch_image(outp[0] if isinstance(outp, list) else outp)
    else:
        size = {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024"}[
            "square" if 0.9 < w / h < 1.1 else "portrait" if w < h else "landscape"]
        j = json.loads(_http(f"{base}/v1/images/generations", {"model": model, "prompt": prompt, "size": size, "n": 1},
                             {"Authorization": f"Bearer {key}"}))
        im = Image.open(io.BytesIO(base64.b64decode(j["data"][0]["b64_json"]))).convert("RGB")
    return Gen(im, seed, provider, model, f"{provider} terms of service", "per provider terms (paid)", im.size, None,
               time.time() - t0, {"paid": True, "price": p["price"]})


def _nearest_ar(w: int, h: int, options: list[str]) -> str:
    r = w / h
    return min(options, key=lambda o: abs(int(o.split(":")[0]) / int(o.split(":")[1]) - r))
