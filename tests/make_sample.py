"""Build a synthetic 'raw recording' that exercises the whole pipeline.

It contains what a real take has: a greeting, a stutter, a hesitation, a retake of the
same explanation, a long pause, the best hook at the END, background noise, 60 Hz hum
and a slight blue colour cast. A drawn presenter moves a little so face-safe framing is
exercised. Word timings are written as a word-level transcript (no Whisper model needed).

    python3 tests/make_sample.py OUT_DIR
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
FPS = 30
W, H = 1080, 1920

SCRIPT = [  # (text, voice, pause_after_seconds)
    ("Hola chicos, eh, hoy vamos a ver algo muy útil.", "es", 0.5),
    ("Edible significa que que se puede comer.", "es", 1.6),
    ("Edible significa que se puede comer sin peligro.", "es", 0.4),
    ("Eatable también existe, pero casi nadie lo usa.", "es", 0.5),
    ("mmm", "es", 0.6),
    ("Por ejemplo:", "es", 0.25),
    ("These mushrooms are edible.", "en-us", 0.9),
    ("¿Sabías que casi todos dicen eatable cuando deberían decir edible?", "es", 0.7),
]


def tts(text: str, voice: str, path: Path) -> np.ndarray:
    subprocess.run(["espeak-ng", "-v", voice, "-s", "150", "-w", str(path), text], check=True)
    with wave.open(str(path)) as wf:
        sr = wf.getframerate()
        x = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    # resample to 48 kHz (linear is fine for a test signal) and trim espeak's silent edges
    t_new = np.arange(int(len(x) * SR / sr)) / SR
    x = np.interp(t_new, np.arange(len(x)) / sr, x).astype(np.float32)
    env = np.abs(x) > 0.01
    idx = np.flatnonzero(env)
    return x[max(0, idx[0] - 480): idx[-1] + 960] if len(idx) else x


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_tts.wav"
    rng = np.random.default_rng(7)
    audio = [np.zeros(int(0.8 * SR), np.float32)]  # recording starts with silence
    t = 0.8
    words = []
    for text, voice, pause in SCRIPT:
        x = tts(text, voice, tmp)
        dur = len(x) / SR
        toks = text.split()
        total = sum(len(k) + 1 for k in toks)
        tt = t + 0.01
        for tok in toks:
            d = (dur - 0.03) * (len(tok) + 1) / total
            words.append({"w": tok, "s": round(tt, 3), "e": round(tt + d * 0.9, 3), "p": 0.93})
            tt += d
        audio += [x * 0.55, np.zeros(int(pause * SR), np.float32)]
        t += dur + pause
    tmp.unlink()
    voice = np.concatenate(audio)
    n = len(voice)
    tt = np.arange(n) / SR
    # room tone: low-passed noise + 60 Hz hum with harmonics
    noise = np.convolve(rng.normal(0, 1, n), np.ones(6) / 6, mode="same").astype(np.float32) * 0.006
    hum = (0.004 * np.sin(2 * np.pi * 60 * tt) + 0.002 * np.sin(2 * np.pi * 120 * tt)
           + 0.001 * np.sin(2 * np.pi * 180 * tt)).astype(np.float32)
    mix = np.clip(voice + noise + hum, -1, 1)
    wav = out / "raw_audio.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((mix * 32767).astype(np.int16).tobytes())

    # ---- video: drawn presenter, mouth follows the voice energy
    import cv2

    dur = n / SR
    frames = int(dur * FPS)
    hop = SR // FPS
    energy = np.array([np.sqrt(np.mean(voice[i * hop:(i + 1) * hop] ** 2)) for i in range(frames)])
    energy = energy / (energy.max() + 1e-9)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "fast",
                            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest",
                            str(out / "kristal_raw_take.mp4")], stdin=subprocess.PIPE)
    yy, xx = np.mgrid[0:H, 0:W]
    bg = np.zeros((H, W, 3), np.uint8)
    bg[..., 0] = (150 + 40 * yy / H).astype(np.uint8)      # blue-ish cast on purpose
    bg[..., 1] = (120 + 30 * xx / W).astype(np.uint8)
    bg[..., 2] = (110 + 20 * yy / H).astype(np.uint8)
    for k in range(0, W, 120):                              # bookshelf-like texture to judge zooms
        cv2.rectangle(bg, (k + 10, 180), (k + 70, 520), (90, 70, 160 - k % 60), -1)
    for f in range(frames):
        img = bg.copy()
        cx = int(W / 2 + 60 * np.sin(f / FPS * 0.6))
        cy = int(H * 0.40 + 12 * np.sin(f / FPS * 1.3))
        cv2.ellipse(img, (cx, cy + 520), (330, 300), 0, 180, 360, (70, 60, 140), -1)      # shoulders
        cv2.ellipse(img, (cx, cy - 40), (215, 250), 0, 0, 360, (40, 30, 30), -1)          # hair
        cv2.ellipse(img, (cx, cy), (175, 225), 0, 0, 360, (150, 175, 215), -1)            # face
        for ex in (-65, 65):
            cv2.ellipse(img, (cx + ex, cy - 40), (32, 20), 0, 0, 360, (255, 255, 255), -1)
            cv2.circle(img, (cx + ex, cy - 40), 13, (50, 40, 30), -1)
            cv2.line(img, (cx + ex - 35, cy - 85), (cx + ex + 35, cy - 90), (40, 30, 30), 8)
        cv2.line(img, (cx, cy - 10), (cx - 12, cy + 45), (120, 140, 185), 5)               # nose
        mo = int(6 + 46 * energy[f])
        cv2.ellipse(img, (cx, cy + 110), (55, mo), 0, 0, 360, (60, 50, 150), -1)           # mouth
        img = cv2.add(img, rng.normal(0, 3.5, img.shape).astype(np.int8), dtype=cv2.CV_8U)  # sensor noise
        enc.stdin.write(img.tobytes())
    enc.stdin.close()
    enc.wait()
    wav.unlink()
    (out / "kristal_raw_take.words.json").write_text(json.dumps({"language": "es", "words": words}, ensure_ascii=False,
                                                                 indent=1), encoding="utf-8")
    print(f"sample: {out / 'kristal_raw_take.mp4'} ({dur:.1f}s), words: {len(words)}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "sample"))
