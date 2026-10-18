"""Face tracking so reframing and zooms always keep Kristal's face well framed."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .util import REPO, log, run

CASCADE = REPO / "assets" / "models" / "haarcascade_frontalface_default.xml"
SAMPLE_FPS = 5
ANALYSIS_W = 480


def track(path: Path, vinfo: dict) -> dict:
    """Return {'t': [...], 'cx': [...], 'cy': [...], 'fh': [...], 'found': ratio} in normalised coords."""
    import cv2

    W, H = vinfo["width"], vinfo["height"]
    w = ANALYSIS_W
    h = int(round(H * w / W / 2) * 2)
    res = run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}",
               "-f", "rawvideo", "-pix_fmt", "gray", "-"], stdout=-1)
    buf = np.frombuffer(res.stdout, dtype=np.uint8)
    n = len(buf) // (w * h)
    frames = buf[: n * w * h].reshape(n, h, w)
    if not hasattr(cv2, "CascadeClassifier"):
        raise SystemExit("Esta versión de OpenCV no incluye CascadeClassifier: "
                         "pip install 'opencv-python-headless>=4.8,<5'")
    det = cv2.CascadeClassifier(str(CASCADE))
    ts, cx, cy, fh = [], [], [], []
    prev = None
    for i in range(n):
        faces = det.detectMultiScale(frames[i], scaleFactor=1.1, minNeighbors=6,
                                     minSize=(int(w * 0.08), int(w * 0.08)))
        t = i / SAMPLE_FPS
        if len(faces):
            # largest face, or the one closest to the previous position (avoids jumping to posters)
            if prev is not None and len(faces) > 1:
                fx = min(faces, key=lambda f: abs(f[0] + f[2] / 2 - prev[0] * w) + abs(f[1] + f[3] / 2 - prev[1] * h))
            else:
                fx = max(faces, key=lambda f: f[2] * f[3])
            x, y, fw, fhh = fx
            c = ((x + fw / 2) / w, (y + fhh / 2) / h, fhh / h)
            prev = c
            ts.append(t)
            cx.append(c[0])
            cy.append(c[1])
            fh.append(c[2])
    found = len(ts) / max(1, n)
    log(f"Rostro detectado en {found * 100:.0f}% de las muestras de {Path(path).name}")
    if len(ts) < 3:
        return {"t": [0.0], "cx": [0.5], "cy": [0.36], "fh": [0.22], "found": found}
    # Median filter to drop false positives, then the renderer smooths in time.
    cxa, cya, fha = (np.array(v) for v in (cx, cy, fh))
    k = 5
    def med(a):
        pad = np.pad(a, k // 2, mode="edge")
        return np.array([np.median(pad[i:i + k]) for i in range(len(a))])
    return {"t": ts, "cx": med(cxa).round(4).tolist(), "cy": med(cya).round(4).tolist(),
            "fh": med(fha).round(4).tolist(), "found": round(found, 3)}


class FaceTrack:
    def __init__(self, data: dict):
        self.t = np.array(data["t"])
        self.cx = np.array(data["cx"])
        self.cy = np.array(data["cy"])
        self.fh = np.array(data["fh"])

    def at(self, t: float) -> tuple[float, float, float]:
        return (float(np.interp(t, self.t, self.cx)), float(np.interp(t, self.t, self.cy)),
                float(np.interp(t, self.t, self.fh)))

    def steady(self, t0: float, t1: float) -> tuple[float, float, float]:
        """Median position over a clip: a locked-off 'virtual camera' per shot, like a real operator."""
        m = (self.t >= t0) & (self.t <= t1)
        if m.sum() == 0:
            return self.at((t0 + t1) / 2)
        return float(np.median(self.cx[m])), float(np.median(self.cy[m])), float(np.median(self.fh[m]))

    def drift(self, t0: float, t1: float) -> float:
        m = (self.t >= t0) & (self.t <= t1)
        if m.sum() < 2:
            return 0.0
        return float(max(np.ptp(self.cx[m]), np.ptp(self.cy[m])))
