"""Original background music synthesised in code (no samples, no third-party melodies).

gfunk(): a West-Coast G-funk style loop (boom-bap drums with claps and swung hats,
sliding sub bass, Rhodes-like 9th chords, portamento 'whine' lead). The style is
evocative of 90s West-Coast/GTA-era hip-hop; the progression and motif are original.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000


def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def _adsr(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    a_, d_, r_ = int(a * SR), int(d * SR), int(r * SR)
    env = np.full(n, s, np.float32)
    env[:a_] = np.linspace(0, 1, a_, endpoint=False)[:n] if a_ else env[:a_]
    if d_:
        seg = np.linspace(1, s, d_, endpoint=False)
        env[a_:a_ + d_] = seg[:max(0, min(d_, n - a_))]
    if r_ and n > r_:
        env[-r_:] *= np.linspace(1, 0, r_)
    return env


def _lowpass(x: np.ndarray, fc: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * fc / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):          # one-pole, applied twice below for 12 dB/oct
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def _place(buf: np.ndarray, snd: np.ndarray, t: float, gain: float = 1.0) -> None:
    i = int(t * SR)
    if i >= len(buf):
        return
    seg = buf[i:i + len(snd)]
    seg += snd[:len(seg)] * gain


def kick() -> np.ndarray:
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    f = 45 + 95 * np.exp(-t * 32)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7.5)
    return (np.tanh(y * 2.2) / np.tanh(2.2)).astype(np.float32)


def snare_clap(rng) -> np.ndarray:
    n = int(0.32 * SR)
    t = np.arange(n) / SR
    noise = rng.normal(0, 1, n)
    hp = noise - _lowpass(noise, 1500)
    body = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 28)
    clap = np.zeros(n)
    for k, off in enumerate((0, 0.011, 0.022)):     # three quick bursts = hand clap
        i = int(off * SR)
        m = n - i
        clap[i:] += hp[:m] * np.exp(-np.arange(m) / SR * (60 if k < 2 else 16))
    y = 0.55 * clap + 0.45 * hp * np.exp(-t * 20) + 0.5 * body
    return (y / np.max(np.abs(y))).astype(np.float32)


def hat(rng, open_: bool = False) -> np.ndarray:
    n = int((0.18 if open_ else 0.05) * SR)
    noise = rng.normal(0, 1, n)
    y = noise - _lowpass(_lowpass(noise, 7000), 7000)
    y *= np.exp(-np.arange(n) / SR * (14 if open_ else 70))
    return (y / np.max(np.abs(y))).astype(np.float32)


def bass_note(midi: float, dur: float, slide_from: float | None = None) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f0 = _midi(midi)
    if slide_from is not None:          # G-funk bass slides into the note
        f = f0 + (_midi(slide_from) - f0) * np.exp(-t * 22)
    else:
        f = np.full(n, f0)
    ph = 2 * np.pi * np.cumsum(f) / SR
    y = 0.8 * np.sin(ph) + 0.25 * np.sin(2 * ph) + 0.08 * np.sin(3 * ph)
    return (y * _adsr(n, 0.004, 0.08, 0.75, 0.05)).astype(np.float32)


def rhodes_chord(notes: list[int], dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for m in notes:
        f = _midi(m)
        mod = np.sin(2 * np.pi * f * 2 * t) * 1.2 * np.exp(-t * 3)   # FM bark on the attack
        y += np.sin(2 * np.pi * f * t + mod) * np.exp(-t * 0.9)
    trem = 1 + 0.18 * np.sin(2 * np.pi * 4.2 * t)
    return (y / len(notes) * trem * _adsr(n, 0.006, 0.3, 0.7, 0.25)).astype(np.float32)


def lead(phrase: list[tuple[float, float, float]], total: float, beat: float) -> np.ndarray:
    """phrase: (start_beat, length_beats, midi). Monophonic portamento + vibrato (the 'whine')."""
    n = int(total * SR)
    f_target = np.zeros(n)
    gate = np.zeros(n)
    for sb, lb, m in phrase:
        a, b = int(sb * beat * SR), int((sb + lb) * beat * SR)
        f_target[a:b] = _midi(m)
        gate[a:b] = 1
    # glide: fill gaps with the last pitch, then smooth the frequency (portamento ~70 ms)
    last = _midi(phrase[0][2])
    for i in range(n):
        if f_target[i] == 0:
            f_target[i] = last
        last = f_target[i]
    f = _lowpass(f_target, 2.3)
    t = np.arange(n) / SR
    vib = 1 + 0.012 * np.sin(2 * np.pi * 5.6 * t) * np.clip((t % (beat * 2)) / beat, 0, 1)
    ph = 2 * np.pi * np.cumsum(f * vib) / SR
    tone = np.sin(ph) + 0.35 * np.sin(2 * ph + 0.3) + 0.12 * np.sin(3 * ph)   # soft square-ish
    env = _lowpass(gate, 30)
    return (tone * env * 0.5).astype(np.float32)


def gfunk(out: Path, duration: float, bpm: float = 92, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bar = 4 * beat
    total = duration + 1.0
    drums = np.zeros(int(total * SR), np.float32)
    music = np.zeros_like(drums)
    K, S, H, HO = kick(), snare_clap(rng), hat(rng), hat(rng, True)
    # Original 4-bar progression in A minor (dorian colour): Am9 | Dm9 | Am9 | E7(#9)
    chords = [[57, 60, 64, 67, 71], [50, 57, 60, 64, 65], [57, 60, 64, 67, 71], [52, 56, 59, 62, 67]]
    roots = [45, 38, 45, 40]
    bass_pat = [(0, 1.5, 0), (1.75, 0.5, 0), (2.5, 0.5, 7), (3.0, 0.75, 12)]   # beat, len, semitones
    motif = [  # original lead motif, 4 bars (beats from bar start)
        [(0.5, 1.5, 76), (2.0, 0.5, 79), (2.5, 1.5, 76)],
        [(0.5, 1.0, 74), (1.5, 0.5, 72), (2.0, 2.0, 69)],
        [(0.5, 1.5, 76), (2.0, 0.5, 81), (2.5, 1.5, 79)],
        [(0.5, 1.0, 80), (1.5, 0.5, 77), (2.0, 2.0, 76)],
    ]
    phrase = []
    nbars = int(np.ceil(total / bar))
    for b in range(nbars):
        t0 = b * bar
        k = b % 4
        for kb in (0, 2.5):                                   # boom-bap kick
            _place(drums, K, t0 + kb * beat, 0.95)
        _place(drums, K, t0 + 3.75 * beat, 0.5)
        for sb in (1, 3):                                     # snare + clap on 2 and 4
            _place(drums, S, t0 + sb * beat, 0.62)
        for i in range(8):                                    # swung 8th hats
            swing = 0.08 * beat if i % 2 else 0
            _place(drums, HO if i == 7 else H, t0 + i * beat / 2 + swing, 0.20 if i % 2 else 0.28)
        _place(music, rhodes_chord(chords[k], bar * 0.98), t0, 0.32)
        prev = None
        for sb, lb, st in bass_pat:
            m = roots[k] + st
            _place(music, bass_note(m, lb * beat * 0.95, slide_from=(prev if prev and sb == 3.0 else None)),
                   t0 + sb * beat, 0.62)
            prev = m
        if b >= 1:                                            # lead enters after the first bar
            phrase += [(b * 4 + sb, lb, m) for sb, lb, m in motif[k]]
    if phrase:  # kept low and soft so it never competes with the voice's 1-4 kHz band
        ld = lead(phrase, total, beat)[:len(music)]
        music += _lowpass(_lowpass(ld, 1800), 1800) * 0.30
    mix = drums * 0.9 + music
    # stereo: chords/lead slightly delayed on the right for width, drums centred
    d = int(0.012 * SR)
    left = mix
    right = drums * 0.9 + np.concatenate([np.zeros(d, np.float32), music[:-d]])
    st = np.stack([left, right], axis=1)[:int(duration * SR)]
    st *= np.minimum(1, np.arange(len(st))[:, None] / (0.4 * SR))                    # fade in
    fade = int(1.2 * SR)
    st[-fade:] *= np.linspace(1, 0, fade)[:, None]                                    # fade out
    st = np.tanh(st * 1.4) / np.tanh(1.4)                                             # glue
    st /= np.max(np.abs(st)) / 0.89
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((st * 32767).astype(np.int16).tobytes())
    return out
