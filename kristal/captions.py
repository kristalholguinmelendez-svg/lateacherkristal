"""Dynamic burned-in captions (ASS) and platform SRT.

Short chunks (≤3 words), active word highlighted as it is spoken, lesson terms always
in the accent colour, placed in a band that never covers the eyes, the mouth or the
on-screen examples.
"""
from __future__ import annotations

import re

from . import ass
from .analyze import norm
from .util import ass_rgb

_STRIP = re.compile(r"^[\"'“”«(]+|[\"'“”»),.;:]+$")


def clean_token(tok: str, upper: bool) -> str:
    t = _STRIP.sub("", tok)
    return t.upper() if upper else t


def chunk_words(words: list[dict], max_words: int, max_chars: int, gap: float = 0.35) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            text_len = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if (len(cur) >= max_words or text_len > max_chars or w["os"] - prev["oe"] > gap
                    or re.search(r"[.,!?;:…]$", prev["w"]) or w["clip"] != prev["clip"]):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], brand: dict, cfg: dict, keywords: list[str], duration: float) -> str:
    c = dict(brand["captions"])
    c.update(cfg or {})
    col = brand["colors"]
    font = brand["fonts"][c["font"]]
    box = c.get("box", False)
    if box:  # semi-transparent plate behind the line (BorderStyle 3 draws it in OutlineColour)
        st = ass.style("Cap", font, c["size"], col[c["text_color"]], outline=col["ink"], outline_w=14,
                       outline_alpha=0.55, border_style=3)
    else:
        st = ass.style("Cap", font, c["size"], col[c["text_color"]], outline=col[c["outline_color"]],
                       outline_w=c["outline"], shadow=c["shadow"], back=col["ink"], back_alpha=0.6)
    out = [ass.header(brand, [st])]
    kw = {norm(k) for k in keywords}
    y = int(brand["layout"]["captions_center_y"] * ass.H)
    # centred, narrower than the safe area on the right (platform buttons) mirrored on the left
    margin = max(brand["layout"]["safe_left"], 1 - brand["layout"]["safe_right"]) * ass.W
    max_w = ass.W - 2 * margin
    cx = ass.W // 2
    chunks = chunk_words(words, c["max_words"], c["max_chars"])
    for n, ch in enumerate(chunks):
        start = max(0.0, ch[0]["os"] - 0.04)
        nxt = chunks[n + 1][0]["os"] - 0.04 if n + 1 < len(chunks) else duration
        end = min(nxt, ch[-1]["oe"] + 0.35, duration)
        tokens = [clean_token(w["w"], c["uppercase"]) for w in ch]
        size = ass.fit_size(brand, c["font"], " ".join(tokens), max_w, c["size"], 48)
        for i, w in enumerate(ch):
            a = start if i == 0 else max(start, w["os"])
            b = end if i == len(ch) - 1 else max(a + 0.01, ch[i + 1]["os"])
            parts = []
            for j, (tok, wj) in enumerate(zip(tokens, ch)):
                is_kw = norm(wj["w"]) in kw
                if j == i:
                    color = col[c["active_color"]]
                elif is_kw:
                    color = col[c["keyword_color"]]
                else:
                    color = col[c["text_color"]]
                parts.append(f"{{\\1c{ass_rgb(color)}}}{ass.escape(tok)}")
            pop = f"\\fscx90\\fscy90\\t(0,{c['pop_ms']},\\fscx100\\fscy100)" if i == 0 else ""
            fs = f"\\fs{size}" if size != c["size"] else ""
            out.append(ass.dialogue(a, b, f"{{\\an5\\pos({cx},{y}){fs}{pop}}}" + " ".join(parts), "Cap"))
    return "\n".join(out) + "\n"


def build_srt(words: list[dict], duration: float, max_chars: int = 42) -> str:
    """Upload-ready SRT: natural casing, up to 2 lines of ~42 chars, sentence-aware."""
    cues, cur = [], []
    for w in words:
        if cur:
            text = " ".join(x["w"] for x in cur)
            if (len(text) + len(w["w"]) > max_chars * 2 or w["os"] - cur[-1]["oe"] > 0.6
                    or (re.search(r"[.!?…]$", cur[-1]["w"]) and len(text) > 12)):
                cues.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    lines = []
    for n, cue in enumerate(cues):
        s = cue[0]["os"]
        e = min(cue[-1]["oe"] + 0.25, cues[n + 1][0]["os"] if n + 1 < len(cues) else duration)
        text = " ".join(x["w"] for x in cue)
        if len(text) > max_chars:
            words_ = text.split()
            best = min(range(1, len(words_)), key=lambda k: abs(len(" ".join(words_[:k])) - len(text) / 2))
            text = " ".join(words_[:best]) + "\n" + " ".join(words_[best:])
        lines.append(f"{n + 1}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{text}\n")
    return "\n".join(lines)


def _srt_ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
