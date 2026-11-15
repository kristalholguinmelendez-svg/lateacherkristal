"""CapCut helper MCP server.

CapCut has no public editing API, so this server cannot open or change
CapCut projects. Instead it prepares the files you bring into CapCut:
subtitle files (.srt), narration scripts and shot lists for English lessons.

Run locally (Claude Code / Claude Desktop):   python -m capcut_mcp.server
Run as a remote connector for claude.ai:      MCP_TRANSPORT=http python -m capcut_mcp.server
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from . import captions as cap

OUTPUT_DIR = Path(os.environ.get("CAPCUT_OUTPUT_DIR", "./capcut_output")).resolve()

mcp = MCPServer(
    name="capcut-helper",
    title="CapCut Helper",
    instructions=(
        "Prepares files to import into CapCut: .srt captions (single or bilingual "
        "English/Spanish), timed narration scripts and lesson shot lists. It cannot "
        "open, edit or export CapCut projects. Every tool returns the file text so the "
        "user can copy it; files are also saved on the server. In CapCut, import captions "
        "with Text > Captions > Import (desktop) or Captions > Import captions (mobile)."
    ),
)


class Caption(BaseModel):
    start: str = Field(description='Start time, e.g. "0:03.5", "00:00:03,500" or "3.5"')
    end: str = Field(description="End time, same formats as start")
    text: str = Field(description="Caption text (use \\n for a second line)")


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower()
    return slug[:60] or "captions"


def _save(name: str, ext: str, content: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"{_slug(name)}-{stamp}.{ext}"
    path.write_text(content, encoding="utf-8")
    return path


def _result(path: Path, content: str, note: str = "") -> str:
    header = f"Saved {path.name}" + (f" — {note}" if note else "")
    return f"{header}\n\n```\n{content}```"


@mcp.tool(description="Build an .srt caption file from captions with exact start/end times.")
def make_srt(captions: list[Caption], file_name: str = "captions") -> str:
    cues = [cap.Cue(cap.parse_timestamp(c.start), cap.parse_timestamp(c.end), c.text) for c in captions]
    srt = cap.cues_to_srt(cues)
    return _result(_save(file_name, "srt", srt), srt, f"{len(cues)} captions")


@mcp.tool(
    description=(
        "Turn a narration script into an auto-timed .srt file. Timings are estimated "
        "from speaking pace, so the user may need to nudge them in CapCut. Optionally pass "
        "one translation per caption to make bilingual subtitles (run once without "
        "translations to see how the script is split)."
    )
)
def script_to_srt(
    script: str,
    words_per_minute: int = 130,
    start_offset_seconds: float = 0.0,
    max_chars_per_line: int = 32,
    translations: list[str] | None = None,
    file_name: str = "lesson-captions",
) -> str:
    cues = cap.auto_time_script(
        script,
        words_per_minute=words_per_minute,
        start_offset=start_offset_seconds,
        max_chars_per_line=max_chars_per_line,
    )
    if translations:
        cues = cap.bilingual_cues(cues, translations)
    srt = cap.cues_to_srt(cues)
    total = cues[-1].end if cues else 0
    return _result(
        _save(file_name, "srt", srt),
        srt,
        f"{len(cues)} captions, about {total:.0f}s at {words_per_minute} wpm",
    )


@mcp.tool(description="Move all captions in an .srt earlier (negative) or later (positive) by some seconds.")
def shift_srt(srt_text: str, offset_seconds: float, file_name: str = "captions-shifted") -> str:
    srt = cap.cues_to_srt(cap.shift_cues(cap.parse_srt(srt_text), offset_seconds))
    return _result(_save(file_name, "srt", srt), srt, f"shifted {offset_seconds:+g}s")


@mcp.tool(
    description=(
        "Make a shot list / storyboard for a short English-lesson video to film and edit "
        "in CapCut. Returns a Markdown table plus the matching narration script."
    )
)
def lesson_shot_list(
    topic: str,
    vocabulary: list[str],
    level: str = "A2",
    target_seconds: int = 60,
    aspect_ratio: str = "9:16",
    file_name: str = "",
) -> str:
    vocabulary = [v.strip() for v in vocabulary if v.strip()]
    if not vocabulary:
        raise ValueError("give at least one vocabulary word")
    intro, outro = 5, 6
    per_word = max(4, (target_seconds - intro - outro) // len(vocabulary))

    rows = [("Hook / title", intro, f'Text overlay: "{topic}" ({level})', f"Today we learn {topic}!")]
    for word in vocabulary:
        rows.append(
            (
                f"Word: {word}",
                per_word,
                f'Close-up or B-roll showing "{word}"; big text "{word}"',
                f"{word.capitalize()}. Repeat after me: {word}.",
            )
        )
    rows.append(("Quick review", outro, "Fast montage of all words with text", "Can you say them all? Follow for more!"))

    t = 0
    lines = [
        f"# {topic} — CapCut shot list",
        f"Level {level} · {aspect_ratio} · ~{sum(r[1] for r in rows)}s",
        "",
        "| # | Time | Shot | Visual / on-screen text | Narration |",
        "|---|------|------|-------------------------|-----------|",
    ]
    for i, (shot, dur, visual, narration) in enumerate(rows, start=1):
        lines.append(f"| {i} | {t}–{t + dur}s | {shot} | {visual} | {narration} |")
        t += dur
    script = "\n".join(r[3] for r in rows)
    lines += [
        "",
        "## Narration script",
        "Pass this to `script_to_srt` to get captions.",
        "",
        script,
        "",
        "## CapCut tips",
        f"- New project → set ratio {aspect_ratio}.",
        "- Import the .srt via Text → Captions → Import, then apply one text style to all.",
        "- Use Audio → Voiceover to record the narration over the clips.",
    ]
    md = "\n".join(lines) + "\n"
    return _result(_save(file_name or topic, "md", md), md)


@mcp.tool(description="List files this server has saved (newest first).")
def list_saved_files() -> str:
    if not OUTPUT_DIR.exists():
        return "No files saved yet."
    files = sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return "\n".join(f"{p.name} ({p.stat().st_size} bytes)" for p in files) or "No files saved yet."


@mcp.tool(description="Read back a file this server saved, by its file name.")
def read_saved_file(name: str) -> str:
    path = (OUTPUT_DIR / name).resolve()
    if path.parent != OUTPUT_DIR or not path.is_file():
        raise ValueError(f"no saved file called {name!r}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport in ("http", "streamable-http"):
        mcp.run(
            "streamable-http",
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
            stateless_http=True,
        )
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
