"""On-screen teaching graphics, rendered as ASS vector drawings + text.

Types (all text comes from project.json, i.e. approved by Kristal — nothing is invented):

  word     big vocabulary card        {word, label?, phonetic?}
  vs       comparison of two terms    {left, right, left_tag?, right_tag?, left_state?, right_state?, reveal?}
  example  example sentence           {text: "These mushrooms are [edible].", translation?}
  title    hook headline (top band)   {text: "¿[EDIBLE] o [EATABLE]?"}
  tag      small pill label           {text, color?, y?}

Timing: `start` (and optional `end`/`reveal`) are SOURCE times of `src`; `dur` is in seconds.
"""
from __future__ import annotations

import re

from . import ass
from .util import ass_alpha, ass_rgb

STATE_COLOR = {"correct": "correct", "wrong": "wrong", "neutral": "muted"}
STATE_ICON = {"correct": "✓", "wrong": "✗", "neutral": ""}


def runs(markup: str) -> list[tuple[str, bool]]:
    """'These are [edible].' -> [('These are ', False), ('edible', True), ('.', False)]"""
    out = []
    for part in re.split(r"(\[[^\]]+\])", markup):
        if part:
            out.append((part[1:-1], True) if part.startswith("[") else (part, False))
    return out


class G:
    def __init__(self, brand: dict):
        self.b = brand
        self.c = brand["colors"]
        self.g = brand["graphics"]
        self.lines: list[str] = []

    # -------- primitives
    def anim(self, x: float, y: float, delay: float = 0, an: int = 7, alpha: float = 1.0,
             appear: bool = True, rise: float = 36) -> str:
        """Slide-up + fade-in to `alpha` (then fade-out); appear=False just places the element."""
        i, o = self.g["in_ms"], self.g["out_ms"]
        d = int(delay * 1000)
        target = ass_alpha(alpha)
        if not appear:
            return f"\\an{an}\\pos({x:.1f},{y:.1f})\\alpha{target}\\fad(0,{o})"
        return (f"\\an{an}\\move({x:.1f},{y + rise:.1f},{x:.1f},{y:.1f},{d},{d + i})"
                f"\\alpha&HFF&\\t({d},{d + i},\\alpha{target})\\fad(0,{o})")

    def card(self, t0, t1, x, y, w, h, fill=None, alpha=None, delay=0, layer=1, border=None, appear=True):
        fill = fill or self.g.get("card_fill", "cream")
        alpha = self.g["card_alpha"] if alpha is None else alpha
        r = self.g["card_radius"]
        kw = dict(delay=delay, appear=appear)
        self.lines.append(ass.dialogue(t0, t1, f"{{{self.anim(x + 4, y + 12, alpha=0.30 * alpha, **kw)}\\p1"
                                       f"\\bord0\\shad0\\1c{ass_rgb(self.c['ink'])}\\blur16}}"
                                       f"{ass.rounded_rect(w, h, r)}", "G", layer))
        if border:
            bw = 8
            self.lines.append(ass.dialogue(t0, t1, f"{{{self.anim(x - bw, y - bw, **kw)}\\p1\\bord0\\shad0"
                                           f"\\1c{ass_rgb(self.c[border])}}}"
                                           f"{ass.rounded_rect(w + 2 * bw, h + 2 * bw, r + bw)}", "G", layer))
        self.lines.append(ass.dialogue(t0, t1, f"{{{self.anim(x, y, alpha=alpha, **kw)}\\p1\\bord0\\shad0"
                                       f"\\1c{ass_rgb(self.c[fill])}}}{ass.rounded_rect(w, h, r)}", "G", layer + 1))

    def text(self, t0, t1, cx, cy, txt, size, color="ink", font="display", delay=0, layer=3, extra="",
             appear=True):
        fname = self.b["fonts"][font]
        self.lines.append(ass.dialogue(t0, t1, f"{{{self.anim(cx, cy, delay=delay, an=5, appear=appear)}"
                                       f"\\fn{fname}\\fs{size}\\bord0\\shad0\\1c{ass_rgb(self.c[color])}{extra}}}"
                                       f"{ass.escape(txt)}", "G", layer))

    # -------- types
    def word(self, t0, t1, gr):
        L = self.b["layout"]
        ls = self.g["label_size"]
        word, label, phon = gr["word"], gr.get("label", ""), gr.get("phonetic", "")
        size = ass.fit_size(self.b, "display", word, 820, gr.get("size", self.g["word_size"]))
        w = min(960, max(ass.text_width(self.b, "display", size, word),
                         ass.text_width(self.b, "strong", ls, label),
                         ass.text_width(self.b, "body", ls, phon)) + 160)
        rows = [("word", size * 1.15)] + ([("phon", ls * 1.35)] if phon else []) + ([("label", ls * 1.45)] if label else [])
        h = sum(r[1] for r in rows) + 70
        cy = L["graphics_center_y"] * ass.H
        x, y = (ass.W - w) / 2, cy - h / 2
        self.card(t0, t1, x, y, w, h)
        ty = y + 35
        for kind, rh in rows:
            mid = ty + rh / 2
            if kind == "word":
                self.text(t0, t1, ass.W / 2, mid, word, size, self.g.get("card_text", "ink"))
            elif kind == "phon":
                self.text(t0, t1, ass.W / 2, mid, phon, ls, "muted", "body", delay=0.12)
            else:
                self.text(t0, t1, ass.W / 2, mid, label, ls, self.g.get("label_color", "accent"), "strong", delay=0.18)
            ty += rh

    def vs(self, t0, t1, gr, t_reveal):
        L = self.b["layout"]
        ls = self.g["label_size"]
        words = (gr["left"], gr["right"])
        states = (gr.get("left_state", "neutral"), gr.get("right_state", "neutral"))
        tags = tuple((STATE_ICON[st] + " " + tg).strip() for st, tg in
                     zip(states, (gr.get("left_tag", ""), gr.get("right_tag", ""))))
        has_tag = any(tags)
        cy = L["graphics_center_y"] * ass.H
        stagger = gr.get("stagger", 0.45)
        max_size = gr.get("size", self.g["word_size"])
        pad = 110
        # side by side only while both words stay big; otherwise stack full-width cards
        cw_side = 450
        side_size = min(ass.fit_size(self.b, "display", w_, cw_side - pad, max_size) for w_ in words)
        side = side_size >= 84 and not gr.get("stacked")
        if side:
            cw, ch, gap = cw_side, (300 if has_tag else 240), 60
            boxes = [((ass.W - 2 * cw - gap) / 2, cy - ch / 2), ((ass.W + gap) / 2, cy - ch / 2)]
        else:
            cw, ch, gap = 900, (180 if has_tag else 140), 30
            boxes = [((ass.W - cw) / 2, cy - ch - gap / 2), ((ass.W - cw) / 2, cy + gap / 2)]
        size = min(ass.fit_size(self.b, "display", w_, cw - pad, max_size) for w_ in words)  # same size: fair contrast
        for k, (x, y) in enumerate(boxes):
            delay = stagger * k
            # word position never moves: room for the tag is reserved from the first frame
            wy = y + ch / 2 - (ls * 0.75 if has_tag else 0)
            ty = wy + size * 0.5 + ls * 0.85
            geo = (x, y, cw, ch, wy, ty, size)
            if t_reveal is None or t_reveal <= t0 + delay + 0.3:
                self._vs_card(t0, t1, geo, words[k], states[k], tags[k], delay, verdict=True)
            else:
                # neutral first; the verdict (✓/✗, colours) lands exactly when Kristal says it
                self._vs_card(t0, t_reveal, geo, words[k], "neutral", "", delay, verdict=False)
                self._vs_card(t_reveal, t1, geo, words[k], states[k], tags[k], 0, verdict=True, swap=True)
        d = 100
        self.lines.append(ass.dialogue(t0, t1, f"{{{self.anim(ass.W / 2 - d / 2, cy - d / 2, delay=stagger * 0.5)}"
                                       f"\\p1\\bord0\\shad0\\1c{ass_rgb(self.c['accent'])}}}{ass.circle(d)}", "G", 6))
        self.text(t0, t1, ass.W / 2, cy - 3, "vs", 50, "white", "display", delay=stagger * 0.5, layer=7)

    def _vs_card(self, t0, t1, geo, word, st, tag, delay, verdict, swap=False):
        x, y, cw, ch, wy, ty, size = geo
        wrong = verdict and st == "wrong"
        border = "correct" if verdict and st == "correct" else None
        self.card(t0, t1, x, y, cw, ch, delay=delay, alpha=0.86 if wrong else None, border=border,
                  appear=not swap)
        self.text(t0, t1, x + cw / 2, wy, word, size, "wrong" if wrong else "ink", delay=delay,
                  extra="\\s1" if wrong else "", appear=not swap)
        if verdict and tag:
            tsize = ass.fit_size(self.b, "strong", tag.upper(), cw - 50, self.g["label_size"], 26)
            self.text(t0, t1, x + cw / 2, ty, tag.upper(), tsize, STATE_COLOR[st], "strong",
                      delay=0 if swap else delay + 0.15,
                      extra="\\fscx70\\fscy70\\t(0,160,\\fscx100\\fscy100)" if swap else "")

    def example(self, t0, t1, gr):
        L = self.b["layout"]
        size = gr.get("size", self.g["example_size"])
        max_w = 840
        # words made of styled parts, so 'edible' + '.' stay glued: "[edible]."
        words: list[list[tuple[str, bool]]] = [[]]
        for txt, hl in runs(gr["text"]):
            for k, piece in enumerate(txt.split(" ")):
                if k > 0:
                    words.append([])
                if piece:
                    words[-1].append((piece, hl))
        words = [w for w in words if w]
        plain = lambda w: "".join(t for t, _ in w)
        def wrap(limit):
            out, cur = [], []
            for w in words:
                trial = " ".join(plain(x) for x in cur + [w])
                if cur and ass.text_width(self.b, "strong", size, trial) > limit:
                    out.append(cur)
                    cur = [w]
                else:
                    cur.append(w)
            if cur:
                out.append(cur)
            return out
        lines = wrap(max_w)
        if len(lines) > 1:  # balance the lines: no orphan word on the last one
            total = ass.text_width(self.b, "strong", size, " ".join(plain(w) for w in words))
            limit = total / len(lines)
            while limit < max_w:
                cand = wrap(limit)
                if len(cand) == len(lines):
                    lines = cand
                    break
                limit += 10
        tr = gr.get("translation", "")
        tr_size = int(size * self.g.get("translation_scale", 0.62))
        tr_lines = [" ".join(l) for l in ass.wrap_balanced(self.b, "body", tr_size, tr.split(), max_w)] if tr else []
        tr_lh = tr_size * 1.3
        lh = size * 1.3
        widths = [ass.text_width(self.b, "strong", size, " ".join(plain(w) for w in ln)) for ln in lines]
        w = min(980, max(widths + [ass.text_width(self.b, "body", tr_size, t) for t in tr_lines]) + 130)
        h = lh * len(lines) + 76 + (tr_lh * len(tr_lines) + tr_size * 0.5 if tr_lines else 0)
        cy = L["graphics_center_y"] * ass.H
        x, y = (ass.W - w) / 2, cy - h / 2
        self.card(t0, t1, x, y, w, h)
        d0 = int(gr.get("highlight_delay", 0.35) * 1000)
        i_ms = self.g["in_ms"]
        space = ass.text_width(self.b, "strong", size, "a a") - 2 * ass.text_width(self.b, "strong", size, "a")
        for li, ln in enumerate(lines):
            ly = y + 38 + lh * li + lh / 2
            pos = (ass.W - widths[li]) / 2
            spans, cur_span = [], None   # merge highlighted pieces (and the spaces between them)
            for wi, wd in enumerate(ln):
                for piece, hl in wd:
                    pw = ass.text_width(self.b, "strong", size, piece)
                    if hl:
                        cur_span = [pos, pos + pw] if cur_span is None else [cur_span[0], pos + pw]
                    elif cur_span is not None:
                        spans.append(cur_span)
                        cur_span = None
                    pos += pw
                nxt_hl = wi + 1 < len(ln) and ln[wi + 1][0][1]
                if cur_span is not None and not nxt_hl:
                    spans.append(cur_span)
                    cur_span = None
                pos += space
            if cur_span is not None:
                spans.append(cur_span)
            marker_alpha = self.g.get("marker_alpha", 0.85)
            for x0, x1 in (spans if marker_alpha > 0 else []):  # marker sweeping left -> right under the key part
                mh = size * 0.52
                self.lines.append(ass.dialogue(
                    t0, t1, f"{{\\an7\\pos({x0 - 8:.1f},{ly - mh / 2 + size * 0.14:.1f})\\p1\\bord0"
                            f"\\shad0\\1c{ass_rgb(self.c[self.g.get('marker', 'gold')])}\\alpha&HFF&\\fscx0"
                            f"\\t({i_ms},{i_ms + 1},\\alpha{ass_alpha(marker_alpha)})\\t({d0},{d0 + 280},\\fscx100)"
                            f"\\fad(0,{self.g['out_ms']})}}{ass.rounded_rect(x1 - x0 + 16, mh, 10)}", "G", 3))
            # highlighted part: by default dark ink over the gold marker (pink on gold lacks contrast)
            base_c = self.c[self.g.get("example_text", self.g.get("card_text", "ink"))]
            hl_c = self.c[self.g.get("highlight_text", self.g.get("card_text", "ink"))]
            parts = " ".join("".join(f"{{\\1c{ass_rgb(hl_c if hl else base_c)}}}{ass.escape(t)}"
                                     for t, hl in wd) for wd in ln)
            self.lines.append(ass.dialogue(t0, t1, f"{{{self.anim(ass.W / 2, ly, an=5)}\\fn{self.b['fonts']['strong']}"
                                           f"\\fs{size}\\bord0\\shad0}}" + parts, "G", 4))
        for k, line in enumerate(tr_lines):
            ty = y + 38 + lh * len(lines) + tr_size * 0.5 + tr_lh * k + tr_lh / 2
            self.text(t0, t1, ass.W / 2, ty, line, tr_size, self.g.get("translation_color", "muted"), "body",
                      delay=0.2, layer=4)

    def title(self, t0, t1, gr):
        L = self.b["layout"]
        size = gr.get("size", 92)
        txt = gr["text"]
        plain = "".join(t for t, _ in runs(txt))
        size = ass.fit_size(self.b, "display", plain, 940, size)
        y = gr.get("y", L["safe_top"] + 0.05) * ass.H
        parts = []
        for t, hl in runs(txt):
            color = self.c["gold"] if hl else self.c["white"]
            parts.append(f"{{\\1c{ass_rgb(color)}}}{ass.escape(t)}")
        self.lines.append(ass.dialogue(t0, t1, f"{{\\an5\\pos({ass.W / 2:.0f},{y:.0f})\\fn{self.b['fonts']['display']}"
                                       f"\\fs{size}\\bord6\\shad3\\3c{ass_rgb(self.c['ink'])}\\4c{ass_rgb(self.c['ink'])}"
                                       f"\\fscx80\\fscy80\\alpha&HFF&\\t(0,140,\\fscx104\\fscy104\\alpha&H00&)"
                                       f"\\t(140,240,\\fscx100\\fscy100)\\fad(0,{self.g['out_ms']})}}" + "".join(parts),
                                       "G", 7))

    def header(self, t0, t1, gr, pulses):
        """Persistent phrase plate (e.g. the expression of the lesson, from frame 0).

        main line (English) + optional sub line (translation); it 'pulses' at each time in
        `pulses` (e.g. when Kristal says the phrase)."""
        L = self.b["layout"]
        main, sub = gr["main"], gr.get("sub", "")
        msize = ass.fit_size(self.b, "display", main, 880, gr.get("size", 88))
        ssize = int(gr.get("sub_size", 46))
        w = min(1000, max(ass.text_width(self.b, "display", msize, main),
                          ass.text_width(self.b, "strong", ssize, sub)) + 90)
        h = msize * 1.2 + (ssize * 1.35 if sub else 0) + 40
        cy = gr.get("y", 0.125) * ass.H
        x, y = (ass.W - w) / 2, cy - h / 2
        my = y + 20 + msize * 0.6
        sy = my + msize * 0.6 + ssize * 0.7
        cuts = [t0] + sorted(t for t in pulses if t0 < t < t1) + [t1]
        for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
            first = k == 0
            # whole plate pops in on frame 0; later segments bounce instead of re-appearing
            if first:
                fx = "\\fscx70\\fscy70\\t(0,160,\\fscx106\\fscy106)\\t(160,260,\\fscx100\\fscy100)"
            else:
                fx = "\\t(0,110,\\fscx114\\fscy114)\\t(110,300,\\fscx100\\fscy100)"
            fo = f"\\fad(0,{self.g['out_ms']})" if b == t1 else ""
            # everything anchored at the plate centre (\\an5) so the pop/pulse scales from the middle
            self.lines.append(ass.dialogue(a, b, f"{{\\an5\\pos({ass.W / 2:.1f},{cy:.1f}){fx}{fo}\\p1\\bord0\\shad0"
                                           f"\\1c{ass_rgb(self.c[gr.get('fill', self.g.get('card_fill', 'cream'))])}"
                                           f"\\1a{ass_alpha(gr.get('alpha', 0.92))}}}{ass.rounded_rect(w, h, 28)}", "G", 8))
            for txt, size, ty, col, font in ((main, msize, my, gr.get("main_color", "green"), "display"),
                                             (sub, ssize, sy, gr.get("sub_color", "red"), "strong")):
                if not txt:
                    continue
                self.lines.append(ass.dialogue(a, b, f"{{\\an5\\pos({ass.W / 2:.1f},{ty:.1f}){fx}{fo}"
                                               f"\\fn{self.b['fonts'][font]}\\fs{size}\\bord0\\shad0"
                                               f"\\1c{ass_rgb(self.c[col])}}}{ass.escape(txt)}", "G", 9))

    def tag(self, t0, t1, gr):
        size = gr.get("size", self.g["label_size"])
        txt = gr["text"]
        color = gr.get("color", "accent")
        w = ass.text_width(self.b, "strong", size, txt) + 64
        h = size * 1.6
        cy = gr.get("y", self.b["layout"]["graphics_center_y"]) * ass.H
        x, y = (ass.W - w) / 2, cy - h / 2
        self.card(t0, t1, x, y, w, h, fill=color, alpha=1.0)
        self.text(t0, t1, ass.W / 2, cy, txt, size, gr.get("text_color", "white"), "strong")


def build_ass(graphics: list[dict], timeline, brand: dict) -> tuple[str, list[dict]]:
    """Return (ASS text, placed graphics with output times for logs/QC)."""
    g = G(brand)
    placed = []
    for gr in graphics:
        t0 = timeline.map(gr["src"], gr["start"], snap_forward=True)
        if t0 is None:
            continue
        if "end" in gr:
            t1 = timeline.map(gr.get("end_src", gr["src"]), gr["end"], snap_forward=True) or (t0 + gr.get("dur", 3.0))
        else:
            t1 = t0 + gr.get("dur", 3.0)
        t1 = min(t1, timeline.duration)
        t_rev = timeline.map(gr.get("reveal_src", gr["src"]), gr["reveal"], snap_forward=True) if gr.get("reveal") is not None else None
        kind = gr["type"]
        pulses = []
        if kind == "word":
            g.word(t0, t1, gr)
        elif kind == "vs":
            g.vs(t0, t1, gr, t_rev)
        elif kind == "example":
            g.example(t0, t1, gr)
        elif kind == "title":
            g.title(t0, t1, gr)
        elif kind == "tag":
            g.tag(t0, t1, gr)
        elif kind == "header":
            pulses = [t for t in (timeline.map(gr["src"], t) for t in gr.get("pulse", [])) if t is not None]
            g.header(t0, t1, gr, pulses)
        else:
            raise ValueError(f"Tipo de gráfico desconocido: {kind}")
        placed.append({**gr, "o_start": round(t0, 3), "o_end": round(t1, 3),
                       "o_reveal": round(t_rev, 3) if t_rev is not None else None,
                       "o_pulses": [round(t, 3) for t in pulses] if kind == "header" else []})
    style = ass.style("G", brand["fonts"]["display"], 60, brand["colors"]["ink"])
    return ass.header(brand, [style]) + "\n".join(g.lines) + "\n", placed


def graphic_text(gr: dict) -> str:
    keys = ("word", "label", "phonetic", "left", "right", "left_tag", "right_tag", "text", "translation",
            "main", "sub")
    return " ".join(str(gr.get(k, "")) for k in keys)
