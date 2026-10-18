"""Maps source-anchored decisions (segments, words, zooms, graphics) onto the output timeline.

Everything in project.json is anchored to *source* time, so reordering or trimming
segments never desynchronises captions, zooms or on-screen examples.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

COMMON_FPS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
              Fraction(50), Fraction(60000, 1001), Fraction(60)]


def normalize_fps(fps: float) -> Fraction:
    """Phones record VFR; lock output to the nearest standard rate of the recording."""
    return min(COMMON_FPS, key=lambda f: abs(float(f) - fps))


@dataclass
class Clip:
    index: int
    src: str
    f_in: int       # first source frame (inclusive)
    f_out: int      # last source frame (exclusive)
    o_start: int    # first output frame
    seg: dict

    @property
    def frames(self) -> int:
        return self.f_out - self.f_in


class Timeline:
    def __init__(self, segments: list[dict], fps: Fraction, tail_hold: float = 0.0):
        self.fps = fps
        self.clips: list[Clip] = []
        o = 0
        for i, seg in enumerate(segments):
            f_in = int(round(seg["in"] * fps))
            f_out = int(round(seg["out"] * fps))
            if f_out - f_in < 2:
                continue
            self.clips.append(Clip(i, seg["src"], f_in, f_out, o, seg))
            o += f_out - f_in
        # hold the last frame a moment so the ending breathes instead of feeling cut off
        self.hold_frames = int(round(tail_hold * fps))
        self.frames = o + self.hold_frames

    @property
    def duration(self) -> float:
        return float(self.frames / self.fps)

    def t(self, frame: int) -> float:
        return float(frame / self.fps)

    def clip_src_range(self, c: Clip) -> tuple[float, float]:
        return float(c.f_in / self.fps), float(c.f_out / self.fps)

    def clip_out_range(self, c: Clip) -> tuple[float, float]:
        return float(c.o_start / self.fps), float((c.o_start + c.frames) / self.fps)

    def map(self, src: str, t: float, snap_forward: bool = False) -> float | None:
        """Source time -> output time. With snap_forward, a time inside a removed region maps
        to the start of the next kept material from that source."""
        best = None
        for c in self.clips:
            a, b = self.clip_src_range(c)
            if c.src == src and a <= t < b:
                return self.clip_out_range(c)[0] + (t - a)
            if snap_forward and c.src == src and a > t and (best is None or a < best[0]):
                best = (a, c)
        if best:
            return self.clip_out_range(best[1])[0]
        return None

    def map_words(self, src: str, words: list[dict]) -> list[dict]:
        """Words whose centre lies inside kept material, re-timed to output and clipped to the clip."""
        out = []
        for c in self.clips:
            if c.src != src:
                continue
            a, b = self.clip_src_range(c)
            oa = self.clip_out_range(c)[0]
            for w in words:
                mid = (w["s"] + w["e"]) / 2
                if a <= mid < b:
                    out.append({**w, "os": oa + max(w["s"], a) - a, "oe": oa + min(w["e"], b) - a,
                                "clip": c.index})
        out.sort(key=lambda w: w["os"])
        return out
