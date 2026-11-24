"""Sound effects synthesised in code (no third-party samples, no licensing questions).

whoosh: swept band of noise, for zooms and cuts
pop:    short pitched blip, for text/cards appearing
ding:   soft bell, for the answer / key reveal
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000


def whoosh(dur: float = 0.42, f0: float = 300, f1: float = 5000, seed: int = 1) -> np.ndarray:
    n = int(dur * SR)
    noise = np.random.default_rng(seed).normal(0, 1, n + 2048)
    out = np.zeros(n + 2048)
    hop, win = 256, np.hanning(1024)
    freqs = np.fft.rfftfreq(1024, 1 / SR)
    for start in range(0, n, hop):
        x = noise[start:start + 1024] * win
        prog = start / n
        fc = f0 * (f1 / f0) ** (prog ** 0.8)          # exponential sweep upwards
        mask = np.exp(-0.5 * (np.log(np.maximum(freqs, 1) / fc) / 0.45) ** 2)
        out[start:start + 1024] += np.fft.irfft(np.fft.rfft(x) * mask) * win
    out = out[:n]
    t = np.linspace(0, 1, n)
    env = np.sin(np.pi * np.clip(t * 1.15, 0, 1)) ** 1.6   # swell, quick release
    return _norm(out * env)


def pop(dur: float = 0.09) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 950 * np.exp(-t * 28) + 260                         # pitch drops fast: "bloop"
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = np.exp(-t * 45) * np.clip(t * SR / 48, 0, 1)      # 1 ms attack, no click
    return _norm(np.sin(ph) * env)


def ding(dur: float = 0.9, f: float = 1318.5) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    partials = [(1.0, 1.0, 5.5), (2.0, 0.45, 8.0), (2.76, 0.25, 11.0), (5.4, 0.08, 16.0)]
    y = sum(a * np.sin(2 * np.pi * f * k * t) * np.exp(-t * d) for k, a, d in partials)
    y *= np.clip(t * SR / 96, 0, 1)
    return _norm(y)


def _norm(y: np.ndarray) -> np.ndarray:
    return (y / (np.max(np.abs(y)) + 1e-9)).astype(np.float32)


SOUNDS = {"whoosh": whoosh, "pop": pop, "ding": ding}


def track(events: list[tuple[float, str]], duration: float, levels_db: dict) -> np.ndarray:
    """Mono SFX track: events are (output time, kind)."""
    out = np.zeros(int(duration * SR) + SR, np.float32)
    cache = {}
    for t, kind in events:
        if kind not in cache:
            cache[kind] = SOUNDS[kind]() * 10 ** (levels_db.get(kind, -16) / 20)
        snd = cache[kind]
        i = int(max(0.0, t) * SR)
        seg = out[i:i + len(snd)]
        seg += snd[:len(seg)]
    return out[:int(duration * SR)]


def mix_into(voice_wav: Path, sfx: np.ndarray, out_wav: Path, ceiling_db: float = -1.5) -> None:
    """Add the SFX track to a stereo 16-bit voice WAV without exceeding the ceiling:
    where a sum would clip, only the effect is turned down (the voice is never touched)."""
    with wave.open(str(voice_wav)) as wf:
        ch, sr, n = wf.getnchannels(), wf.getframerate(), wf.getnframes()
        v = np.frombuffer(wf.readframes(n), dtype=np.int16).astype(np.float32).reshape(-1, ch) / 32768
    assert sr == SR, sr
    s = np.zeros(len(v), np.float32)
    s[:min(len(s), len(sfx))] = sfx[:len(s)]
    ceiling = 10 ** (ceiling_db / 20)
    room = np.clip(ceiling - np.max(np.abs(v), axis=1), 0, None)
    s = np.sign(s) * np.minimum(np.abs(s), room)
    mixed = v + s[:, None]
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((np.clip(mixed, -1, 1) * 32767).astype(np.int16).tobytes())


def auto_events(tl, zooms: list[dict], placed: list[dict], min_gap: float = 0.35) -> list[tuple[float, str]]:
    """Whoosh on punch-ins and on reframing cuts, pop when a card appears, ding on reveals/pulses."""
    ev: list[tuple[float, str]] = []
    for z in zooms:
        t = tl.map(z["src"], z["at"])
        if t is not None:
            ev.append((t - 0.12, "whoosh"))
    for p, c in zip(tl.clips, tl.clips[1:]):
        if p.seg.get("framing") != c.seg.get("framing"):
            ev.append((tl.clip_out_range(c)[0] - 0.14, "whoosh"))
    for g in placed:
        ev.append((g["o_start"], "pop"))
        if g.get("o_reveal") is not None:
            ev.append((g["o_reveal"], "ding"))
        for t in g.get("o_pulses", []):
            ev.append((t, "ding"))
    ev.sort()
    kept: list[tuple[float, str]] = []
    for t, k in ev:  # thin out: never two of the same kind on top of each other
        if all(not (k == k2 and abs(t - t2) < min_gap) for t2, k2 in kept):
            kept.append((max(0.0, t), k))
    return kept
