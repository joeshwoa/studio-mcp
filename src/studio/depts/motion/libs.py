"""Pinned JavaScript libraries for motion pages — downloaded once, then used offline.

Nothing big is vendored in the repo: on first use each file is fetched from cdn.jsdelivr.net (the npm
package, exact version) into STUDIO_HOME/cache/jslibs/<pkg>@<version>/<path>, and its sha256 is
recorded in cache/jslibs/lock.json. Later runs verify the file against that hash (trust on first use) —
a changed file is re-downloaded and, if it still differs, refused. Pages reference the cached files
through file:// URLs (classic scripts) or an import map (three.js ES modules).

Licences: GSAP 3.13+ incl. every plugin (SplitText, MorphSVG, DrawSVG, …) is free for commercial use
under the GSAP "no charge" standard licence (not for building no-code visual animation builders that
compete with Webflow); lottie-web MIT; three.js MIT; d3 ISC."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from ...config import cache_dir
from ...core.result import ToolError

CDN = "https://cdn.jsdelivr.net/npm/"

GSAP_V = "3.15.0"
LOTTIE_V = "5.13.0"
THREE_V = "0.186.1"
D3_V = "7.9.0"

GSAP_PLUGINS = ["CustomEase", "SplitText", "MorphSVGPlugin", "DrawSVGPlugin", "MotionPathPlugin", "ScrambleTextPlugin",
                "Flip", "Physics2DPlugin", "TextPlugin", "EasePack", "CustomWiggle", "CustomBounce"]

THREE_FILES = [
    "build/three.core.js", "build/three.module.js",
    "examples/jsm/loaders/SVGLoader.js", "examples/jsm/loaders/GLTFLoader.js",
    "examples/jsm/utils/BufferGeometryUtils.js", "examples/jsm/utils/SkeletonUtils.js",
    "examples/jsm/environments/RoomEnvironment.js", "examples/jsm/geometries/RoundedBoxGeometry.js",
    "examples/jsm/postprocessing/EffectComposer.js", "examples/jsm/postprocessing/RenderPass.js",
    "examples/jsm/postprocessing/UnrealBloomPass.js", "examples/jsm/postprocessing/OutputPass.js",
    "examples/jsm/postprocessing/ShaderPass.js", "examples/jsm/postprocessing/Pass.js",
    "examples/jsm/postprocessing/MaskPass.js", "examples/jsm/shaders/CopyShader.js",
    "examples/jsm/shaders/LuminosityHighPassShader.js", "examples/jsm/shaders/OutputShader.js",
]

# name → (npm package, version, [files inside the package]) ; classic scripts load in this order
LIBS: dict[str, tuple[str, str, list[str]]] = {
    "gsap": ("gsap", GSAP_V, ["dist/gsap.min.js"] + [f"dist/{p}.min.js" for p in GSAP_PLUGINS]),
    "lottie": ("lottie-web", LOTTIE_V, ["build/player/lottie.min.js"]),
    "d3": ("d3", D3_V, ["dist/d3.min.js"]),
    "three": ("three", THREE_V, THREE_FILES),
}

LICENCES = {
    "gsap": "GSAP standard 'no charge' licence (free incl. commercial use, all plugins; not for no-code animation builders competing with Webflow) — gsap.com/standard-license",
    "lottie": "lottie-web — MIT",
    "three": "three.js — MIT",
    "d3": "d3 — ISC",
}


def root() -> Path:
    p = cache_dir() / "jslibs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _lock_path() -> Path:
    return root() / "lock.json"


def _lock() -> dict:
    try:
        return json.loads(_lock_path().read_text())
    except Exception:
        return {}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fetch(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "studio-mcp"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception as e:
        raise ToolError(f"could not download {url} ({type(e).__name__}: {e})",
                        "connect to the internet once — motion libraries are cached in STUDIO_HOME/cache/jslibs "
                        "and work offline after the first download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def ensure(name: str) -> dict[str, Path]:
    """Download (once) and verify every file of library `name`. Returns {package path: local file}."""
    if name not in LIBS:
        raise ToolError(f"unknown JS library {name!r}", f"one of {sorted(LIBS)}")
    pkg, ver, files = LIBS[name]
    base = root() / f"{pkg}@{ver}"
    lock = _lock()
    out: dict[str, Path] = {}
    changed = False
    for f in files:
        dest = base / f
        key = f"{pkg}@{ver}/{f}"
        if dest.exists() and key in lock and _sha(dest) != lock[key]:
            dest.unlink()  # corrupted or tampered → fetch again, must match the recorded hash
        if not dest.exists():
            _fetch(f"{CDN}{pkg}@{ver}/{f}", dest)
            if key in lock and _sha(dest) != lock[key]:
                dest.unlink()
                raise ToolError(f"{key} does not match its recorded sha256 — refusing to use it",
                                f"if the CDN legitimately changed, delete the entry from {_lock_path()}")
        if key not in lock:
            lock[key] = _sha(dest)
            changed = True
        out[f] = dest
    if changed:
        _lock_path().write_text(json.dumps(lock, indent=1, sort_keys=True))
    return out


def script_tags(names: list[str], inline: bool = False) -> str:
    """<script> tags (classic scripts) for gsap/lottie/d3, plus gsap.registerPlugin for every plugin."""
    tags = []
    for n in names:
        if n == "three":
            continue
        for _f, p in ensure(n).items():
            if inline:
                tags.append("<script>" + p.read_text(encoding="utf-8").replace("</script", "<\\/script") + "</script>")
            else:
                tags.append(f'<script src="{p.resolve().as_uri()}"></script>')
        if n == "gsap":
            plugs = [p for p in GSAP_PLUGINS if p != "EasePack"] + ["SlowMo", "ExpoScaleEase", "RoughEase"]
            tags.append("<script>[" + ",".join(f"'{p}'" for p in plugs) + "].forEach(n => { try { if (window[n]) "
                        "gsap.registerPlugin(window[n]); } catch (e) { console.warn('gsap plugin ' + n + ': ' + e); } });"
                        "</script>")
    return "\n".join(tags)


def three_importmap() -> str:
    """<script type=importmap> mapping 'three' and 'three/addons/' to the cached files."""
    files = ensure("three")
    base = files["build/three.module.js"].parent.parent
    imap = {"imports": {"three": files["build/three.module.js"].resolve().as_uri(),
                        "three/addons/": (base / "examples" / "jsm").resolve().as_uri() + "/"}}
    return f'<script type="importmap">{json.dumps(imap)}</script>'


def status() -> dict:
    """Which libraries are cached (for docs / doctor)."""
    lock = _lock()
    res = {}
    for n, (pkg, ver, files) in LIBS.items():
        have = sum((root() / f"{pkg}@{ver}" / f).exists() for f in files)
        res[n] = {"version": ver, "files": len(files), "cached": have,
                  "hashed": sum(f"{pkg}@{ver}/{f}" in lock for f in files), "licence": LICENCES[n]}
    return res
