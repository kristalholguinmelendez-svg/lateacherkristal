"""ASS (Advanced SubStation) helpers shared by captions and on-screen graphics.

ASS is rendered by FFmpeg/libass and stays editable (Aegisub) as part of the project.
"""
from __future__ import annotations

from functools import lru_cache

from PIL import ImageFont

from .util import ass_color, font_path

W, H = 1080, 1920
KAPPA = 0.5523


def header(brand: dict, styles: list[str]) -> str:
    return "\n".join([
        "[Script Info]",
        f"; {brand['brand']} — generado por kristal",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]) + "\n"


def style(name: str, font: str, size: int, primary: str, outline: str = "#000000", outline_w: float = 0,
          shadow: float = 0, back: str = "#000000", back_alpha: float = 0.0, align: int = 5,
          border_style: int = 1, outline_alpha: float = 1.0) -> str:
    return (f"Style: {name},{font},{size},{ass_color(primary)},{ass_color(primary)},"
            f"{ass_color(outline, outline_alpha)},{ass_color(back, back_alpha)},0,0,0,0,100,100,0,0,"
            f"{border_style},{outline_w},{shadow},{align},0,0,0,1")


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def dialogue(start: float, end: float, text: str, style_name: str, layer: int = 0) -> str:
    return f"Dialogue: {layer},{ts(start)},{ts(end)},{style_name},,0,0,0,,{text}"


def escape(text: str) -> str:
    return text.replace("\\", "⧵").replace("{", "(").replace("}", ")")


@lru_cache(maxsize=8)
def _libass_scale(path: str, family: str) -> float:
    """PIL em-size per ASS \\fs unit, calibrated by rendering a probe string with libass.

    libass derives the em from the font's vertical metrics in a way PIL doesn't expose,
    so measuring once is the only reliable way to make cards and markers fit exactly.
    """
    import os
    import subprocess
    import tempfile

    import numpy as np

    probe_txt, fs = "HAMBURGEFONSTIV", 100
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "p.ass")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1600\nPlayResY: 300\n\n[V4+ Styles]\n"
                     "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                     "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                     "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                     f"Style: P,{family},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
                     "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                     f"Dialogue: 0,0:00:00.00,0:00:01.00,P,,0,0,0,,{{\\pos(50,50)}}{probe_txt}\n")
        fd = os.path.dirname(path)
        res = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=1600x300:d=0.1",
                              "-vf", f"ass={f}:fontsdir={fd}", "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    img = np.frombuffer(res.stdout, np.uint8).reshape(300, 1600)
    cols = np.flatnonzero(img.max(axis=0) > 100)
    ink_w = cols[-1] - cols[0] + 1
    ref = ImageFont.truetype(path, 100)
    l, _, r, _ = ref.getbbox(probe_txt)
    return ink_w / (r - l)  # em pixels per \fs unit


@lru_cache(maxsize=64)
def _pil_font(path: str, family: str, ass_size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(1, int(round(ass_size * _libass_scale(path, family)))))


def text_width(brand: dict, font_key: str, size: int, text: str, spacing: float = 0) -> float:
    f = _pil_font(str(font_path(brand, font_key)), brand["fonts"][font_key], size)
    return f.getlength(text) + spacing * max(0, len(text) - 1)


def fit_size(brand: dict, font_key: str, text: str, max_w: float, max_size: int, min_size: int = 30) -> int:
    size = max_size
    while size > min_size and text_width(brand, font_key, size, text) > max_w:
        size -= 2
    return size


def rounded_rect(w: float, h: float, r: float) -> str:
    """ASS vector drawing of a rounded rectangle with its top-left at (0,0)."""
    r = min(r, w / 2, h / 2)
    k = r * (1 - KAPPA)
    p = lambda v: f"{v:.1f}".rstrip("0").rstrip(".")
    return (f"m {p(r)} 0 l {p(w - r)} 0 b {p(w - k)} 0 {p(w)} {p(k)} {p(w)} {p(r)} "
            f"l {p(w)} {p(h - r)} b {p(w)} {p(h - k)} {p(w - k)} {p(h)} {p(w - r)} {p(h)} "
            f"l {p(r)} {p(h)} b {p(k)} {p(h)} 0 {p(h - k)} 0 {p(h - r)} "
            f"l 0 {p(r)} b 0 {p(k)} {p(k)} 0 {p(r)} 0")


def circle(d: float) -> str:
    return rounded_rect(d, d, d / 2)


def wrap(brand: dict, font_key: str, size: int, words: list[str], max_w: float) -> list[list[str]]:
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if cur and text_width(brand, font_key, size, trial) > max_w:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines
