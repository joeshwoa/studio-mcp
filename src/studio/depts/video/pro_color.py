"""Colour correction the way a colourist starts: match a shot to a reference (video_color_match) and
balance a shot automatically (video_auto_color). Both are computed on sampled frames, written as a 33³
.cube 3D LUT (reusable in DaVinci Resolve, Premiere Lumetri, Kdenlive, OBS, the studio's own video_grade
lut=…) and applied with ffmpeg lut3d (tetrahedral)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _pro as P

# ─────────────────────────── colour maths (sRGB D65 ↔ CIELAB) ───────────────────────────

_M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
_MI = np.linalg.inv(_M)
_WP = np.array([0.95047, 1.0, 1.08883])


def srgb_to_lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    xyz = srgb_to_lin(rgb) @ _M.T / _WP
    e = 216 / 24389
    f = np.where(xyz > e, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    e = 216 / 24389

    def inv(f):
        return np.where(f ** 3 > e, f ** 3, (116 * f - 16) / (24389 / 27))
    xyz = np.stack([inv(fx), inv(fy), inv(fz)], axis=-1) * _WP
    return np.clip(lin_to_srgb(xyz @ _MI.T), 0, 1)


# ─────────────────────────── LUTs ───────────────────────────

def lut_grid(n: int = 33) -> np.ndarray:
    """n³×3 RGB grid in .cube order (red fastest)."""
    v = np.linspace(0, 1, n)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    return np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)


def write_cube(rgb_out: np.ndarray, dest: Path, title: str, n: int = 33) -> Path:
    lines = [f'TITLE "{title[:60]}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in np.clip(rgb_out, 0, 1)]
    dest.write_text("\n".join(lines) + "\n", encoding="ascii")
    return dest


def read_cube(path: Path) -> tuple[int, np.ndarray]:
    n, rows = 0, []
    for ln in Path(path).read_text().splitlines():
        ln = ln.strip()
        if ln.startswith("LUT_3D_SIZE"):
            n = int(ln.split()[1])
        elif ln and (ln[0].isdigit() or ln[0] in "-."):
            rows.append([float(x) for x in ln.split()[:3]])
    return n, np.array(rows)


def apply_lut_np(img: np.ndarray, n: int, table: np.ndarray) -> np.ndarray:
    """Trilinear LUT on a float RGB image (for previews; ffmpeg does the video)."""
    t = table.reshape(n, n, n, 3)  # [b][g][r]
    x = np.clip(img, 0, 1) * (n - 1)
    i0 = np.floor(x).astype(int).clip(0, n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                w = (fr if dr else 1 - fr) * (fg if dg else 1 - fg) * (fb if db else 1 - fb)
                out = out + w * t[b0 + db, g0 + dg, r0 + dr]
    return out


# ─────────────────────────── sampling ───────────────────────────

def sample_frames(path: Path, n: int = 8, width: int = 480, at: float = -1.0) -> list[np.ndarray]:
    """Float RGB frames evenly through a video (or the image itself; or one frame at `at` s)."""
    if path.suffix.lower() in C.IMAGE_EXTS:
        im = Image.open(path).convert("RGB")
        im.thumbnail((width, width))
        return [np.asarray(im, np.float32) / 255]
    info = C.need_video(path)
    D = info["duration"]
    ts = [at] if at >= 0 else [D * (i + 0.5) / n for i in range(n)]
    out = []
    work = C.scratch("cm-")
    try:
        for i, t in enumerate(ts):
            f = work / f"{i}.png"
            C.frame_at(path, min(t, max(0.0, D - 0.05)), f, width)
            if f.exists():
                out.append(np.asarray(Image.open(f).convert("RGB"), np.float32) / 255)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    if not out:
        raise ToolError(f"could not read frames from {path.name}")
    return out


def lab_stats(frames: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    px = np.concatenate([f.reshape(-1, 3) for f in frames])
    lum = px.mean(axis=1)
    px = px[(lum > 0.02) & (lum < 0.98)] if ((lum > 0.02) & (lum < 0.98)).mean() > 0.2 else px
    lab = rgb_to_lab(px)
    return lab.mean(axis=0), lab.std(axis=0) + 1e-6


def apply_video_lut(src: Path, cube: Path, dest: Path, info: dict) -> None:
    args = ["-i", str(src), "-vf", f"format=gbrp,lut3d=file='{C.ff_path(cube)}':interp=tetrahedral,format=yuv420p",
            *C.v_encode_args(17)]
    args += ["-map", "0:v:0"]
    if info.get("audio"):
        args += ["-map", "0:a:0", "-c:a", "copy"]
    C.ff(args + [str(dest)], what="apply LUT")


def compare_sheet(rows: list[list[tuple[str, np.ndarray]]], dest: Path, title: str, subtitle: str = "") -> Path:
    tiles = []
    for row in rows:
        for lab, arr in row:
            tiles.append({"img": Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)), "head": lab, "lines": []})
    return P.tile_sheet(tiles, dest, title=title, subtitle=subtitle, cols=len(rows[0]), tile_w=420)


# ─────────────────────────── tools ───────────────────────────

@tool("video")
def video_color_match(path: str, reference: str, strength: float = 1.0, reference_time: float = -1.0, samples: int = 8,
                      preserve_luminance: bool = False, apply: bool = True, project: str = "", out: str = "") -> Result:
    """Match a clip's colour to a reference (another camera, the hero shot, a still/frame grab you like):
    Reinhard colour transfer in CIELAB — mean and spread of lightness and both colour axes — measured on
    `samples` frames of each, baked into a 33³ .cube LUT (reuse it in Resolve/Premiere/Kdenlive or
    video_grade lut=…) and applied with ffmpeg. reference: video or image; reference_time: use one frame of
    a reference video (s). strength 0–1 blends toward the original. preserve_luminance: match colour only
    (keep the clip's exposure/contrast). Returns the graded MP4 (apply=false: LUT only), the .cube and a
    before | after | reference sheet to LOOK at, with measured LAB distances in data."""
    p = C.src_path(path)
    ref = C.src_path(reference)
    info = C.need_video(p)
    strength = float(np.clip(strength, 0, 1))
    src_f = sample_frames(p, samples)
    ref_f = sample_frames(ref, samples, at=reference_time)
    ms, ss = lab_stats(src_f)
    mr, sr = lab_stats(ref_f)
    ratio = np.clip(sr / ss, 0.5, 2.0)
    if preserve_luminance:
        ratio[0], mr = 1.0, np.array([ms[0], mr[1], mr[2]])
    grid = lut_grid(33)
    lab = rgb_to_lab(grid)
    lab2 = (lab - ms) * ratio + mr
    lab2 = lab + (lab2 - lab) * strength
    out_rgb = lab_to_rgb(lab2)
    d = C.out_folder(project, out, f"{p.stem}-match-{ref.stem}")
    cube = write_cube(out_rgb, d / f"match-{p.stem}-to-{ref.stem}.cube", f"match {p.stem} to {ref.stem}")
    n, table = read_cube(cube)
    after = [apply_lut_np(f, n, table) for f in src_f]
    ma, _ = lab_stats(after)
    dist = lambda a, b: float(np.linalg.norm(a - b))  # noqa: E731
    k = [0, len(src_f) // 2, len(src_f) - 1] if len(src_f) >= 3 else list(range(len(src_f)))
    rows = [[(f"before · frame {i + 1}", src_f[i]), (f"after · frame {i + 1}", after[i]), ("reference", ref_f[min(i, len(ref_f) - 1)])]
            for i in sorted(set(k))]
    sheet = compare_sheet(rows, d / "before-after-reference.png", f"Colour match — {p.name} → {ref.name}",
                          f"Reinhard LAB transfer, strength {strength:g}; mean ΔLab to reference {dist(ms, mr):.1f} → {dist(ma, mr):.1f}")
    data = {"cube": str(cube), "source_lab_mean": ms.round(2).tolist(), "reference_lab_mean": mr.round(2).tolist(),
            "after_lab_mean": ma.round(2).tolist(), "delta_before": round(dist(ms, mr), 2), "delta_after": round(dist(ma, mr), 2),
            "std_ratio": ratio.round(3).tolist()}
    res = Result(f"Matched {p.name} to {ref.name}: mean LAB distance {data['delta_before']} → {data['delta_after']} "
                 f"(one global LUT for the whole clip).", files=[str(cube), str(sheet)], previews=[str(sheet)], data=data)
    if apply:
        dest = C.out_path(project, str(d), f"{p.stem}-matched", ".mp4")
        apply_video_lut(p, cube, dest, info)
        res.files.insert(0, str(dest))
        C.finish(res, dest, expect={"width": info["width"], "height": info["height"]}, sheet_rows=2)
    if (np.array(ratio) >= 1.99).any() or (np.array(ratio) <= 0.51).any():
        res.warnings.append("the clips differ a lot (contrast/saturation ratio hit the 0.5–2× safety limit) — check skin tones")
    res.next_steps.append(f"reuse the LUT: video_grade(path, lut='{cube}') or in a timeline clip \"grade\": {{\"lut\": \"{cube}\"}}")
    return res


@tool("video")
def video_auto_color(path: str, strength: float = 1.0, white_balance: bool = True, exposure: bool = True, contrast: bool = True,
                     samples: int = 8, apply: bool = True, project: str = "", out: str = "") -> Result:
    """One-click colour correction (the 'balance' pass before a look): white balance from near-neutral
    pixels (grey-world on low-saturation midtones, guarded by the brightest neutrals), exposure to a
    healthy mid-grey, and contrast by setting black/white points (0.5 / 99.5 percentiles) — measured on
    `samples` frames, written as a .cube LUT and applied with ffmpeg. strength 0–1. Returns the corrected
    MP4 (apply=false: LUT only), the .cube, a before/after sheet and the measured gains in data. For a
    look on top use video_grade (or a timeline grade) after this."""
    p = C.src_path(path)
    info = C.need_video(p)
    strength = float(np.clip(strength, 0, 1))
    fr = sample_frames(p, samples)
    px = np.concatenate([f.reshape(-1, 3) for f in fr])
    lin = srgb_to_lin(px)
    gains = np.ones(3)
    notes = []
    if white_balance:
        mx, mn = px.max(axis=1), px.min(axis=1)
        sat = (mx - mn) / (mx + 1e-6)
        lum = px.mean(axis=1)
        sel = (sat < 0.25) & (lum > 0.15) & (lum < 0.9)
        weak = sel.mean() < 0.02
        if weak:   # a colourful scene with no neutrals: grey-world would invent a cast, so move only a little
            sel = (lum > 0.1) & (lum < 0.95)
            notes.append("few neutral pixels — white balance only nudged (grey world at 30%), check the sheet")
        m = lin[sel].mean(axis=0)
        gains = m.mean() / (m + 1e-6)
        gains = gains / gains[1]
        gains = np.clip(1 + (gains - 1) * 0.3, 0.9, 1.12) if weak else np.clip(gains, 0.75, 1.35)
    g2 = srgb_to_lin(px) * gains
    y = (g2 @ np.array([0.2126, 0.7152, 0.0722]))
    egain = 1.0
    if exposure:
        med = float(np.median(y))
        target = 0.18
        egain = float(np.clip((target / max(med, 1e-4)) ** 0.6, 0.6, 2.2))   # partial move toward mid-grey
    ys = lin_to_srgb(np.clip(y * egain, 0, 1))
    lo, hi = (float(np.percentile(ys, 0.5)), float(np.percentile(ys, 99.5))) if contrast else (0.0, 1.0)
    lo = min(lo, 0.08)
    hi = max(hi, 0.85)
    grid = lut_grid(33)
    g = srgb_to_lin(grid) * gains * egain
    s = lin_to_srgb(np.clip(g, 0, 1.0))
    if contrast:
        s = np.clip((s - lo) / max(1e-3, hi - lo), 0, 1)
    outg = grid + (s - grid) * strength
    d = C.out_folder(project, out, f"{p.stem}-autocolor")
    cube = write_cube(outg, d / f"autocolor-{p.stem}.cube", f"auto colour {p.stem}")
    n, table = read_cube(cube)
    after = [apply_lut_np(f, n, table) for f in fr]
    k = sorted({0, len(fr) // 2, len(fr) - 1})
    rows = [[(f"before · frame {i + 1}", fr[i]), (f"after · frame {i + 1}", after[i])] for i in k]
    sheet = compare_sheet(rows, d / "before-after.png", f"Auto colour — {p.name}",
                          f"WB gains R{gains[0]:.2f} G{gains[1]:.2f} B{gains[2]:.2f} · exposure ×{egain:.2f} · levels {lo:.3f}–{hi:.3f} · strength {strength:g}")
    cast_before = rgb_to_lab(np.concatenate([f.reshape(-1, 3) for f in fr])).mean(axis=0)
    cast_after = rgb_to_lab(np.concatenate([f.reshape(-1, 3) for f in after])).mean(axis=0)
    data = {"cube": str(cube), "wb_gains": gains.round(3).tolist(), "exposure_gain": round(egain, 3),
            "black_point": round(lo, 3), "white_point": round(hi, 3),
            "lab_mean_before": cast_before.round(2).tolist(), "lab_mean_after": cast_after.round(2).tolist()}
    res = Result(f"Auto colour for {p.name}: white-balance gains R{gains[0]:.2f}/G{gains[1]:.2f}/B{gains[2]:.2f}, exposure ×{egain:.2f}, "
                 f"levels {lo:.3f}–{hi:.3f}; average colour cast a*/b* {cast_before[1]:.1f}/{cast_before[2]:.1f} → "
                 f"{cast_after[1]:.1f}/{cast_after[2]:.1f}.", files=[str(cube), str(sheet)], previews=[str(sheet)],
                 warnings=notes, data=data)
    if apply:
        dest = C.out_path(project, str(d), f"{p.stem}-autocolor", ".mp4")
        apply_video_lut(p, cube, dest, info)
        res.files.insert(0, str(dest))
        C.finish(res, dest, expect={"width": info["width"], "height": info["height"]}, sheet_rows=2)
    res.next_steps.append("a look on top: video_grade(path=<corrected mp4>, preset='film_portra', strength=0.6)")
    return res
