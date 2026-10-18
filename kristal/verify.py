"""Edge check for cuts: re-transcribe every segment exactly as cut and compare its first and
last words with what the segment should contain. A clipped syllable shows up as a missing
or different edge word, which energy heuristics alone cannot prove."""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from .analyze import norm
from .util import run


def check_edges(segments: list[dict], paths: dict, model_dir: Path) -> list[dict]:
    import sherpa_onnx

    md = Path(model_dir)
    pick = lambda stem: str(next(md.glob(f"{stem}*.onnx")))
    rec = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=pick("encoder"), decoder=pick("decoder"), joiner=pick("joiner"),
        tokens=str(md / "tokens.txt"), num_threads=4, model_type="nemo_transducer")
    pcm = {}
    for src, p in paths.items():
        res = run(["ffmpeg", "-v", "error", "-i", str(p), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                  stdout=-1)
        pcm[src] = np.frombuffer(res.stdout, dtype=np.float32)
    out = []
    for n, sg in enumerate(segments):
        x = pcm[sg["src"]][int(sg["in"] * 16000): int(sg["out"] * 16000)]
        # 0.3 s of silence around the clip so the model doesn't invent edges
        x = np.concatenate([np.zeros(4800, np.float32), x, np.zeros(4800, np.float32)])
        st = rec.create_stream()
        st.accept_waveform(16000, x)
        rec.decode_stream(st)
        heard = [norm(t) for t in st.result.text.split() if norm(t)]
        want = [norm(t) for t in sg["text"].split() if norm(t)]
        ratio = SequenceMatcher(None, heard, want).ratio()
        first_ok = bool(heard) and bool(want) and _close(heard[0], want[0])
        last_ok = bool(heard) and bool(want) and _close(heard[-1], want[-1])
        out.append({"n": n + 1, "first_ok": first_ok, "last_ok": last_ok, "ratio": round(ratio, 2),
                    "heard": st.result.text.strip(), "want": sg["text"]})
    return out


def _close(a: str, b: str) -> bool:
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.75


class EdgeRefiner:
    """Moves each cut in 30 ms steps until the recogniser hears exactly the intended edge word
    (nothing clipped, nothing of the dropped neighbour), preferring the quietest valid point."""

    STEP, RANGE, WIN = 0.03, 8, 1.6

    def __init__(self, paths: dict, model_dir: Path, envs: dict):
        import sherpa_onnx

        md = Path(model_dir)
        pick = lambda stem: str(next(md.glob(f"{stem}*.onnx")))
        self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=pick("encoder"), decoder=pick("decoder"), joiner=pick("joiner"),
            tokens=str(md / "tokens.txt"), num_threads=4, model_type="nemo_transducer")
        self.envs = envs
        self.pcm = {}
        for src, p in paths.items():
            res = run(["ffmpeg", "-v", "error", "-i", str(p), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                      stdout=-1)
            self.pcm[src] = np.frombuffer(res.stdout, dtype=np.float32)

    def hear(self, src: str, a: float, b: float) -> list[str]:
        x = self.pcm[src][max(0, int(a * 16000)): int(b * 16000)]
        x = np.concatenate([np.zeros(4800, np.float32), x, np.zeros(4800, np.float32)])
        st = self.rec.create_stream()
        st.accept_waveform(16000, x)
        self.rec.decode_stream(st)
        return [norm(t) for t in st.result.text.split() if norm(t)]

    def _best(self, src, t0, valid, lo, hi):
        env = self.envs[src]
        cands = []
        for k in range(-self.RANGE, self.RANGE + 1):
            t = round(t0 + k * self.STEP, 3)
            if lo <= t <= hi and valid(t):
                cands.append((float(env.db[env.idx(t)]) + 0.5 * abs(k), t))
        return min(cands)[1] if cands else None

    def refine(self, segments: list[dict]) -> list[dict]:
        report = []
        for n, sg in enumerate(segments):
            want = [norm(t) for t in sg["text"].split() if norm(t)]
            if not want:
                continue
            src = sg["src"]
            prev = segments[n - 1] if n else None
            nxt = segments[n + 1] if n + 1 < len(segments) else None
            joined_in = prev is not None and prev["src"] == src and abs(prev["out"] - sg["in"]) < 1e-3
            joined_out = nxt is not None and nxt["src"] == src and abs(nxt["in"] - sg["out"]) < 1e-3
            dur = sg["out"] - sg["in"]
            note = []
            if not joined_in and not sg.get("lock_in"):
                def ok_in(t):
                    h = self.hear(src, t, min(t + self.WIN, sg["out"]))
                    return bool(h) and _close(h[0], want[0])
                if not ok_in(sg["in"]):
                    t = self._best(src, sg["in"], ok_in, 0.0, sg["in"] + min(0.25, dur / 3))
                    note.append(f"in {sg['in']:.2f}→{t:.2f}" if t is not None else "in SIN SOLUCIÓN")
                    if t is not None:
                        sg["in"] = t
            if not joined_out and not sg.get("lock_out"):
                def ok_out(t):
                    h = self.hear(src, max(t - self.WIN, sg["in"]), t)
                    return bool(h) and _close(h[-1], want[-1])
                if not ok_out(sg["out"]):
                    t = self._best(src, sg["out"], ok_out, sg["out"] - min(0.25, dur / 3), sg["out"] + 0.3)
                    note.append(f"out {sg['out']:.2f}→{t:.2f}" if t is not None else "out SIN SOLUCIÓN")
                    if t is not None:
                        sg["out"] = t
            if note:
                report.append(f"{n + 1}: " + ", ".join(note) + f"  [{sg['text'][:50]}]")
        return report
