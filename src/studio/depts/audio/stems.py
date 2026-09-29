"""Stem separation: Demucs (htdemucs, state of the art, free) or a light spectral fallback."""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import numpy as np

from ...config import models_dir
from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C

_DEMUCS: dict = {}


def _demucs(x: np.ndarray, sr: int, model: str, two_stems: bool) -> tuple[dict, int]:
    os.environ.setdefault("TORCH_HOME", str(models_dir() / "torch"))  # older demucs; 4.1+ uses the HF cache
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    if model not in _DEMUCS:
        _DEMUCS.clear()
        m = get_model(model)
        m.eval()
        _DEMUCS[model] = m
    m = _DEMUCS[model]
    msr = m.samplerate
    y = C.resample(x, sr, msr)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    wav = torch.from_numpy(y.T.copy()).float()
    ref = wav.mean(0)
    mu, sd = ref.mean(), ref.std() + 1e-8
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    with torch.no_grad():
        out = apply_model(m, ((wav - mu) / sd)[None], shifts=1, split=True, overlap=0.25, progress=False,
                          device="cpu")[0]
    out = out * sd + mu
    stems = {name: out[i].numpy().T.astype(np.float32) for i, name in enumerate(m.sources)}
    if two_stems:
        voc = stems["vocals"]
        rest = sum(v for k, v in stems.items() if k != "vocals")
        stems = {"vocals": voc, "no_vocals": rest}
    return stems, msr


def _center(x: np.ndarray, sr: int) -> tuple[dict, int]:
    """Stereo centre extraction: keep time-frequency bins where L≈R (lead vocal is usually panned
    centre). Crude — music panned centre (bass, kick) leaks into 'vocals'."""
    from scipy.signal import istft, stft
    if x.shape[1] < 2:
        raise ToolError("the light 'center' method needs a stereo file (vocals are found by their centre panning)",
                        "install demucs (studio_install stems) for mono files")
    f, t, L = stft(x[:, 0], sr, nperseg=4096)
    _, _, R = stft(x[:, 1], sr, nperseg=4096)
    sim = 2 * np.abs(L * np.conj(R)) / (np.abs(L) ** 2 + np.abs(R) ** 2 + 1e-12)   # 1 when identical
    phase = np.cos(np.angle(L) - np.angle(R))
    mask = np.clip(sim * np.clip(phase, 0, 1), 0, 1) ** 4
    band = ((f > 120) & (f < 8000)).astype(float)[:, None]
    mask *= band
    M = 0.5 * (L + R) * mask
    _, voc = istft(M, sr, nperseg=4096)
    voc = voc[: len(x)]
    voc = np.pad(voc, (0, len(x) - len(voc)))
    vocals = np.stack([voc, voc], 1).astype(np.float32)
    return {"vocals": vocals, "no_vocals": (x - vocals).astype(np.float32)}, sr


@tool("audio")
def audio_separate_stems(path: str, method: str = "auto", stems: str = "two", model: str = "htdemucs",
                         format: str = "wav", project: str = "", out: str = "") -> Result:
    """Split a song or mix into stems: vocals + accompaniment (stems='two', default) or vocals, drums,
    bass, other (stems='four'). Use to remove music under speech, make karaoke/instrumental beds, or
    rescue dialogue. method: auto (Demucs if installed) | demucs (Meta's htdemucs — near
    commercial quality; CPU ≈ 0.5–1.5x realtime, ~80 MB model on first use) | center (no install, stereo
    only, crude: works for centre-panned vocals, leaks bass/kick). Returns one file per stem + a preview."""
    src = C.src_path(path)
    have_demucs = importlib.util.find_spec("demucs") is not None and importlib.util.find_spec("torch") is not None
    meth = method
    if meth == "auto":
        meth = "demucs" if have_demucs else "center"
    if meth == "demucs" and not have_demucs:
        raise ToolError("demucs is not installed (needs PyTorch, ~1 GB)", "studio_install('demucs') after the user agrees, or method='center'")
    x, sr = C.load(src, sr=44100)
    t0 = time.time()
    warnings = []
    if meth == "demucs":
        st, osr = _demucs(x, sr, model, stems != "four")
        quality = "Demucs htdemucs (state-of-the-art open model)"
    elif meth == "center":
        st, osr = _center(x, sr)
        quality = "centre-channel extraction (crude, stereo only)"
        warnings.append("light method: expect instrument bleed in 'vocals' and vocal ghosts in 'no_vocals'; "
                        "install demucs for real separation")
        if stems == "four":
            warnings.append("four stems need demucs — returned two")
    else:
        raise ToolError(f"unknown method {method!r}", "auto | demucs | center")
    files, previews = [], []
    by_name: dict = {}
    d = None
    for name, a in st.items():
        p = C.out_path(project, str(d) if d else out, f"{src.stem}-{name}", "." + format.lstrip("."), kind="audio/stems")
        d = p.parent
        C.save(p, np.clip(a, -1, 1), osr)
        files.append(str(p))
        by_name[name] = str(p)
    el = time.time() - t0
    try:
        from PIL import Image
        ims = [Image.open(C.preview(f, C.sibling(Path(f), ".png"), title=Path(f).name))
               for f in files]
        sheet = Image.new("RGB", (ims[0].width, sum(i.height for i in ims)))
        y = 0
        for i in ims:
            sheet.paste(i, (0, y))
            y += i.height
        sp = C.unique_path(d, f"{src.stem}-stems", ".png")
        sheet.save(sp)
        previews.append(str(sp))
    except Exception as e:
        warnings.append(f"preview failed: {e}")
    lev = {}
    for f in files:
        m, w = C.measure(f)
        lev[Path(f).stem] = m
        if m.get("max_db") is not None and m["max_db"] < -40:
            warnings.append(f"{Path(f).name} is (nearly) silent — nothing of that kind in the source")
    dur = len(x) / sr
    return Result(f"Separated {src.name} ({dur:.1f}s) into {len(files)} stems with {quality} in {el:.0f}s.",
                  files=files, previews=previews, warnings=warnings,
                  data={"method": meth, "stems": by_name,
                        "seconds": round(el, 1), "metrics": lev},
                  next_steps=["Clean a separated voice further with audio_clean(preset='light')."])
