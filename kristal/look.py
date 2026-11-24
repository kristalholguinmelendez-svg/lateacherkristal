"""Image analysis and conservative correction: exposure, white balance, contrast, noise, sharpness.

Corrections are measured per source and clamped to small ranges, so every take lands on
the same neutral target (colour consistency) without filters, skin smoothing or
invented detail.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .util import is_hdr, log, run

TARGET_Y = 112.0       # mean luma (8-bit, limited range) for a well-exposed talking head
TARGET_U, TARGET_V = 127.0, 131.0   # slightly warm neutral: natural skin


def measure(path: Path, duration: float) -> dict:
    step = max(1.0, duration / 24)
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf",
               f"fps=1/{step:.3f},scale=640:-2,signalstats,metadata=print", "-an", "-f", "null", "-"],
              capture=True)
    keys = ["YAVG", "YLOW", "YHIGH", "UAVG", "VAVG", "SATAVG"]
    vals = {k: [float(x) for x in re.findall(rf"lavfi\.signalstats\.{k}=([\d.]+)", res.stderr)] for k in keys}
    stats = {k: float(np.median(v)) if v else None for k, v in vals.items()}
    stats["noise"] = _noise(path, duration)
    return stats


def _noise(path: Path, duration: float) -> float:
    """Sensor-noise estimate: robust std of the high-pass residual on flat areas (0-255 scale)."""
    import cv2

    sig = []
    for frac in (0.25, 0.5, 0.75):
        res = run(["ffmpeg", "-v", "error", "-ss", f"{duration * frac:.2f}", "-i", str(path), "-frames:v", "1",
                   "-f", "image2pipe", "-vcodec", "png", "-"], stdout=-1, check=False)
        img = cv2.imdecode(np.frombuffer(res.stdout or b"", np.uint8), cv2.IMREAD_GRAYSCALE) if res.stdout else None
        if img is None:
            continue
        img = img.astype(np.float32)
        hp = img - cv2.GaussianBlur(img, (0, 0), 1.5)
        grad = cv2.GaussianBlur(np.abs(cv2.Laplacian(img, cv2.CV_32F)), (0, 0), 3)
        flat = grad < np.percentile(grad, 30)
        sig.append(float(1.4826 * np.median(np.abs(hp[flat]))))
    return round(float(np.median(sig)), 2) if sig else 0.0


def correction(stats: dict, brand: dict, vinfo: dict, overrides: dict | None = None) -> tuple[str, dict]:
    """Return (ffmpeg filter chain applied at source resolution, human description)."""
    L = brand["look"]
    ov = overrides or {}
    chain, desc = [], {}
    if is_hdr(vinfo):
        chain.append("zscale=t=linear:npl=120,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                     "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
        desc["hdr"] = "HDR (HLG/PQ) convertido a SDR BT.709 con tonemap hable"

    y, lo, hi = stats.get("YAVG") or TARGET_Y, stats.get("YLOW") or 16, stats.get("YHIGH") or 235
    # Only fix real problems. Frame averages are dominated by walls and clothes, so a
    # well-lit shot is left alone: no "normalising" exposure, contrast or skin tone.
    gamma = 1.0
    if y < 85:                       # clearly underexposed: lift midtones
        gamma = float(np.clip((100 / max(y, 1)) ** 0.5, 1.0, 1.22))
    elif y > 180 and hi >= 233:      # clearly overexposed with clipping: pull midtones down
        gamma = float(np.clip((160 / y) ** 0.5, 0.88, 1.0))
    bright = 0.0
    spread = hi - lo
    contrast = float(np.clip(1.0 + (110 - spread) / 500, 1.0, L["max_contrast"])) if spread < 110 else 1.0
    sat_avg = stats.get("SATAVG") or 30
    sat = float(np.clip(1.0 + (14 - sat_avg) / 100, 1.0, L["max_saturation"])) if sat_avg < 14 else 1.0
    gamma, bright, contrast, sat = (ov.get(k, v) for k, v in
                                    (("gamma", gamma), ("brightness", bright), ("contrast", contrast), ("saturation", sat)))
    if any(abs(v - d) > 0.005 for v, d in ((gamma, 1), (bright, 0), (contrast, 1), (sat, 1))):
        chain.append(f"eq=gamma={gamma:.3f}:brightness={bright:.3f}:contrast={contrast:.3f}:saturation={sat:.3f}")
    desc["exposicion"] = (f"luma media {y:.0f} → gamma {gamma:.2f}" if gamma != 1 else
                          f"luma media {y:.0f}: bien expuesta, sin cambio")
    desc["contraste"] = (f"rango {lo:.0f}-{hi:.0f} → contraste {contrast:.2f}" if contrast != 1 else
                         f"rango {lo:.0f}-{hi:.0f}: correcto, sin cambio")
    desc["saturacion"] = f"sat media {sat_avg:.0f} → ×{sat:.2f}" if sat != 1 else f"sat media {sat_avg:.0f}: sin cambio"

    # White balance: only strong casts (mixed or wrong lighting), and only half the deviation.
    # A warm room stays warm: that is the real light, not an error.
    u, v = stats.get("UAVG") or TARGET_U, stats.get("VAVG") or TARGET_V
    du, dv = u - TARGET_U, v - TARGET_V
    bm = float(np.clip(-du / 128 * 0.5, -0.06, 0.06)) if abs(du) > 10 else 0.0
    rm = float(np.clip(-dv / 128 * 0.5, -0.06, 0.06)) if abs(dv) > 10 else 0.0
    bm, rm = ov.get("blue_mid", bm), ov.get("red_mid", rm)
    if bm or rm:
        chain.append(f"colorbalance=rm={rm:.3f}:bm={bm:.3f}:pl=1")
        desc["balance_blancos"] = f"U {u:.0f}, V {v:.0f} → rojo medios {rm:+.3f}, azul medios {bm:+.3f}"
    else:
        desc["balance_blancos"] = f"sin dominante fuerte (U {u:.0f}, V {v:.0f}), sin cambio"

    noise = stats.get("noise", 0)
    if noise > 2.2 or ov.get("denoise"):
        s = ov.get("denoise", min(L["denoise"] * noise / 2.5, 2.5))
        chain.append(f"hqdn3d={s:.2f}:{s:.2f}:{s * 2.5:.2f}:{s * 2.5:.2f}")
        desc["ruido_visual"] = f"ruido estimado {noise} → hqdn3d suave {s:.2f}"
    else:
        desc["ruido_visual"] = f"ruido estimado {noise}, sin reducción"
    return ",".join(chain), desc


def stabilize_detect(path: Path, trf: Path) -> None:
    log("Analizando vibración (vidstabdetect) ...")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf",
         f"vidstabdetect=shakiness=5:accuracy=15:result={trf}", "-f", "null", "-"])


def stabilize_filter(trf: Path) -> str:
    return f"vidstabtransform=input={trf}:smoothing=20:zoom=0:optzoom=1:interpol=bicubic"
