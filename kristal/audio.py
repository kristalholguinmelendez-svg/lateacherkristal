"""Audio analysis (energy envelope, cut snapping, noise floor) and the restoration chain."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from .util import log, run

SR = 16000
HOP = 0.010  # envelope resolution: 10 ms


class Envelope:
    """RMS envelope of a source, used to place cuts in natural gaps and to score takes."""

    def __init__(self, path: Path):
        res = run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                   "-f", "f32le", "-"], capture=False, check=True, stdout=-1)
        pcm = np.frombuffer(res.stdout, dtype=np.float32)
        self.pcm = pcm
        hop = int(SR * HOP)
        n = max(1, len(pcm) // hop)
        frames = pcm[: n * hop].reshape(n, hop)
        self.rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
        self.db = 20 * np.log10(self.rms + 1e-9)
        self.peak = np.max(np.abs(frames), axis=1)
        self.duration = len(pcm) / SR
        speech = self.db[self.db > np.percentile(self.db, 60)]
        self.noise_db = float(np.percentile(self.db, 10))
        self.speech_db = float(np.median(speech)) if len(speech) else -20.0

    def idx(self, t: float) -> int:
        return int(np.clip(round(t / HOP), 0, len(self.db) - 1))

    def snap(self, t: float, window: float = 0.09, side: str = "both") -> float:
        """Move a cut point to the quietest 10 ms frame near t (never into a louder syllable).

        side='left' only searches earlier (for segment starts we may begin a bit early),
        side='right' only later (for segment ends we may end a bit later).
        """
        lo = t - window if side in ("both", "left") else t
        hi = t + window if side in ("both", "right") else t
        a, b = self.idx(lo), self.idx(hi)
        if b <= a:
            return t
        seg = self.db[a:b + 1]
        # bias towards the requested point so equal-quiet frames don't drift far
        dist = np.abs(np.arange(a, b + 1) * HOP - t) / max(window, 1e-3)
        best = a + int(np.argmin(seg + dist * 3.0))
        return best * HOP

    def level(self, t0: float, t1: float) -> dict:
        a, b = self.idx(t0), max(self.idx(t1), self.idx(t0) + 1)
        db = self.db[a:b]
        pk = self.peak[a:b]
        voiced = db[db > self.noise_db + 12]
        return {"speech_db": float(np.median(voiced)) if len(voiced) else float(np.median(db)),
                "clip_ratio": float(np.mean(pk > 0.985)) if len(pk) else 0.0,
                "snr_db": float((np.median(voiced) if len(voiced) else np.median(db)) - self.noise_db)}

    def silences(self, min_len: float = 0.35, margin_db: float = 14.0) -> list[tuple[float, float]]:
        thr = self.noise_db + margin_db
        quiet = self.db < thr
        out, start = [], None
        for i, q in enumerate(quiet):
            if q and start is None:
                start = i
            elif not q and start is not None:
                if (i - start) * HOP >= min_len:
                    out.append((start * HOP, i * HOP))
                start = None
        if start is not None and (len(quiet) - start) * HOP >= min_len:
            out.append((start * HOP, len(quiet) * HOP))
        return out


# ---------------------------------------------------------------- restoration chain

def restoration_chain(brand: dict, noise_db: float, snr_db: float, cfg: dict | None = None,
                      hum_hz: int | None = None) -> tuple[str, dict]:
    """Per-source cleanup (runs on the whole recording, before cutting): rumble, noise, hum.

    Denoise strength comes from the measured SNR and is capped at 12 dB of reduction so
    the voice never turns metallic. Returns (filter string, description of what was applied).
    """
    a = dict(brand["audio"])
    a.update(cfg or {})
    applied: dict = {}
    chain = [f"highpass=f={a['highpass_hz']}:poles=2"]
    applied["highpass"] = f"{a['highpass_hz']} Hz (rumble, golpes de mesa)"

    strength = a.get("denoise_strength", "auto")
    if strength == "rnnoise":
        from .util import REPO
        model = REPO / a.get("rnnoise_model", "assets/models/sh.rnnn")
        if not model.exists():
            raise SystemExit(f"Falta el modelo RNNoise {model}: ejecuta scripts/fetch_models.sh")
        mix = a.get("rnnoise_mix", 0.85)
        chain.append(f"arnndn=m='{model}':mix={mix}")
        applied["denoise"] = (f"RNNoise (red neuronal para voz) mezcla {mix:.0%} con la señal original para "
                              f"evitar voz metálica; piso de ruido medido {noise_db:.1f} dBFS (SNR {snr_db:.1f} dB)")
        return ",".join(chain), applied
    if strength == "auto":
        nr = 0 if snr_db >= 45 else 6 if snr_db >= 35 else 9 if snr_db >= 25 else 12
    elif strength == "off":
        nr = 0
    else:
        nr = min(float(strength), 20)
    if nr > 0:
        nf = int(np.clip(noise_db, -80, -20))
        chain.append(f"afftdn=nr={nr}:nf={nf}:tn=1")
        applied["denoise"] = f"afftdn nr={nr} dB, piso de ruido medido {noise_db:.1f} dBFS (SNR {snr_db:.1f} dB)"
    else:
        applied["denoise"] = f"no necesario (SNR {snr_db:.1f} dB)"
    if hum_hz:
        for k in (1, 2, 3):
            chain.append(f"bandreject=f={hum_hz * k}:width_type=q:w=12")
        applied["hum"] = f"notch {hum_hz} Hz + 2 armónicos"
    return ",".join(chain), applied


def voice_chain(brand: dict, cfg: dict | None = None) -> tuple[str, dict]:
    """Global voice polish after cutting: natural EQ, de-essing, moderate compression."""
    a = dict(brand["audio"])
    a.update(cfg or {})
    p = a["presence_db"]
    chain = [
        "equalizer=f=220:t=q:w=1.1:g=-1.5",         # less boxiness / mud
        f"equalizer=f=3200:t=q:w=1.0:g={p}",         # intelligibility / presence
        "equalizer=f=9500:t=q:w=0.8:g=1.0",          # gentle air
        "deesser=i=0.35:m=0.5:f=0.5",
        "acompressor=threshold=-21dB:ratio=2.6:attack=12:release=180:makeup=2:knee=4",
    ]
    return ",".join(chain), {
        "eq": f"-1.5 dB @220 Hz, +{p} dB @3.2 kHz, +1 dB @9.5 kHz",
        "deesser": "suave", "compresor": "2.6:1, umbral -21 dB (moderado)"}


def measure_loudness(path: Path, prefilter: str = "anull") -> dict:
    """Integrated loudness / true peak via loudnorm's analysis pass."""
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
               f"{prefilter},loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
              capture=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr, re.S)
    if not m:
        raise RuntimeError("No pude medir la sonoridad (loudnorm).")
    return json.loads(m.group(0))


def master_chain(gain_db: float, true_peak: float) -> str:
    """Static gain to target + peak limiter at 4x oversampling (catches inter-sample peaks).

    Unlike loudnorm's dynamic fallback this never pumps and always lands on target.
    """
    ceiling = 10 ** ((true_peak - 0.3) / 20)
    return (f"volume={gain_db:.2f}dB,aresample=192000,"
            f"alimiter=limit={ceiling:.4f}:attack=1:release=80:level=disabled,aresample=48000")


def ebur128(path: Path) -> dict:
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
               "ebur128=peak=true", "-f", "null", "-"], capture=True)
    tail = res.stderr[res.stderr.rfind("Summary:"):]
    out = {}
    for key, pat in (("integrated_lufs", r"I:\s+(-?[\d.]+) LUFS"), ("lra", r"LRA:\s+(-?[\d.]+) LU"),
                     ("true_peak_dbfs", r"Peak:\s+(-?[\d.]+) dBFS")):
        m = re.search(pat, tail)
        if m:
            out[key] = float(m.group(1))
    return out


def detect_hum(env: Envelope) -> int | None:
    """Return 50 or 60 if a steady mains hum dominates the quiet parts, else None."""
    sil = env.silences(min_len=0.4)
    if not sil:
        return None
    chunks = [env.pcm[int(a * SR):int(b * SR)] for a, b in sil[:20]]
    x = np.concatenate(chunks) if chunks else np.zeros(1)
    if len(x) < SR // 2:
        return None
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    band = (freqs > 30) & (freqs < 400)
    base = np.median(spec[band]) + 1e-9
    for hz in (50, 60):
        k = (freqs > hz - 1.5) & (freqs < hz + 1.5)
        k2 = (freqs > 2 * hz - 1.5) & (freqs < 2 * hz + 1.5)
        if spec[k].max(initial=0) / base > 25 and spec[k2].max(initial=0) / base > 8:
            log(f"Zumbido eléctrico detectado a {hz} Hz")
            return hz
    return None
