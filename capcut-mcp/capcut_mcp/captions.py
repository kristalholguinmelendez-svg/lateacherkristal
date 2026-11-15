"""Caption and script helpers that produce files CapCut can import.

CapCut (desktop and mobile) imports subtitles as .srt files
(Text > Captions > Import / "Local captions"). Everything here is plain
Python with no network access, so it is easy to test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Cue:
    start: float  # seconds
    end: float  # seconds
    text: str


def format_timestamp(seconds: float) -> str:
    """Format seconds as an SRT timestamp: HH:MM:SS,mmm."""
    if seconds < 0:
        raise ValueError("timestamps cannot be negative")
    total_ms = round(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def parse_timestamp(value: str | float | int) -> float:
    """Accept 12.5, "12.5", "0:12", "1:02.5", "00:01:02,500" and return seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip().replace(",", ".")
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"cannot read timestamp {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def cues_to_srt(cues: list[Cue]) -> str:
    """Render cues as SRT text, validating ordering and durations."""
    blocks = []
    for i, cue in enumerate(cues, start=1):
        if cue.end <= cue.start:
            raise ValueError(f"cue {i} ends before it starts ({cue.start}s -> {cue.end}s)")
        text = cue.text.strip()
        if not text:
            raise ValueError(f"cue {i} has no text")
        blocks.append(
            f"{i}\n{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n{text}\n"
        )
    return "\n".join(blocks)


def wrap_line(text: str, max_chars: int) -> str:
    """Wrap text onto at most two lines, splitting near the middle."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    words = text.split(" ")
    best, best_diff = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(a) - len(b))
        if best_diff is None or diff < best_diff:
            best, best_diff = (a, b), diff
    return "\n".join(best) if best else text


def split_script(script: str, max_chars: int) -> list[str]:
    """Split a script into caption-sized chunks (at most two lines each)."""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", script.strip())
    chunks: list[str] = []
    limit = max_chars * 2
    for sentence in sentences:
        sentence = " ".join(sentence.split())
        if not sentence:
            continue
        current = ""
        for word in sentence.split(" "):
            candidate = f"{current} {word}".strip()
            if len(candidate) > limit and current:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def auto_time_script(
    script: str,
    words_per_minute: int = 130,
    start_offset: float = 0.0,
    gap: float = 0.15,
    max_chars_per_line: int = 32,
    min_duration: float = 1.2,
) -> list[Cue]:
    """Estimate timings for a script read at a steady pace.

    130 wpm is a comfortable pace for English learners; use ~160 for
    native-speed narration.
    """
    if words_per_minute <= 0:
        raise ValueError("words_per_minute must be positive")
    seconds_per_word = 60.0 / words_per_minute
    t = start_offset
    cues = []
    for chunk in split_script(script, max_chars_per_line):
        duration = max(min_duration, len(chunk.split()) * seconds_per_word)
        cues.append(Cue(t, t + duration, wrap_line(chunk, max_chars_per_line)))
        t += duration + gap
    return cues


def bilingual_cues(cues: list[Cue], translations: list[str]) -> list[Cue]:
    """Put a translation line under each caption (e.g. English over Spanish)."""
    if len(translations) != len(cues):
        raise ValueError(
            f"got {len(translations)} translations for {len(cues)} captions; they must match"
        )
    return [
        Cue(c.start, c.end, f"{c.text}\n{' '.join(t.split())}")
        for c, t in zip(cues, translations)
    ]


def parse_srt(srt: str) -> list[Cue]:
    """Read SRT text back into cues (useful for editing an exported file)."""
    cues = []
    for block in re.split(r"\n\s*\n", srt.strip().replace("\r\n", "\n")):
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        timing_idx = 1 if "-->" not in lines[0] else 0
        start_s, end_s = (p.strip() for p in lines[timing_idx].split("-->"))
        cues.append(
            Cue(parse_timestamp(start_s), parse_timestamp(end_s), "\n".join(lines[timing_idx + 1 :]))
        )
    return cues


def shift_cues(cues: list[Cue], offset: float) -> list[Cue]:
    """Move every caption earlier or later by `offset` seconds."""
    shifted = [Cue(c.start + offset, c.end + offset, c.text) for c in cues]
    if shifted and shifted[0].start < 0:
        raise ValueError("shift would move captions before 0:00")
    return shifted
