"""Deliverables besides the video: corrected transcript, cut log, CMX3600 EDL."""
from __future__ import annotations

import re
from pathlib import Path

from .timeline import Timeline
from .util import fmt_ts


def transcript_md(project: dict, words_out: list[dict], tl: Timeline) -> str:
    L = [f"# Transcripción corregida — {project.get('title', project['name'])}", "",
         f"Duración final: {tl.duration:.2f} s · tiempos referidos al video final", ""]
    cur, start = [], None
    for w in words_out:
        if start is None:
            start = w["os"]
        cur.append(w["w"])
        if re.search(r"[.!?…]$", w["w"]):
            L.append(f"- `{fmt_ts(start)}` {' '.join(cur)}")
            cur, start = [], None
    if cur:
        L.append(f"- `{fmt_ts(start)}` {' '.join(cur)}")
    return "\n".join(L) + "\n"


def cut_log(project: dict, tl: Timeline, analysis_note: str, placed_graphics: list[dict],
            zooms: list[dict], audio_log: dict, look_log: dict) -> str:
    L = [f"# Registro de cortes y tomas — {project.get('title', project['name'])}", "",
         f"FPS de salida {float(tl.fps):.3f} · {len(tl.clips)} cortes · duración {tl.duration:.2f} s", "",
         "| # | Salida | Fuente | Entrada | Salida fuente | Dur | Rol | Encuadre | Texto |",
         "|---|---|---|---|---|---|---|---|---|"]
    for n, c in enumerate(tl.clips):
        a, b = tl.clip_src_range(c)
        o, _ = tl.clip_out_range(c)
        s = c.seg
        L.append(f"| {n + 1} | {fmt_ts(o)} | {c.src} | {fmt_ts(a)} | {fmt_ts(b)} | {b - a:.2f}s | "
                 f"{s.get('role', '')} | {s.get('framing', '')} | {s.get('text', '').replace('|', '/')} |")
    L += ["", "## Zooms / punch-ins", ""]
    for z in zooms:
        o = tl.map(z["src"], z["at"])
        if o is not None:
            L.append(f"- `{fmt_ts(o)}` punch-in — {z.get('why', '')}")
    L += ["", "## Gráficos en pantalla", ""]
    for g in placed_graphics:
        L.append(f"- `{fmt_ts(g['o_start'])}–{fmt_ts(g['o_end'])}` {g['type']}: "
                 + ", ".join(f"{k}={g[k]!r}" for k in ("word", "left", "right", "text") if k in g))
    L += ["", "## Audio aplicado", ""]
    for k, v in audio_log.items():
        if isinstance(v, dict):
            for src, d in v.items():
                L.append(f"- {k} {src}: " + "; ".join(f"{a}: {b}" for a, b in d.items()))
        else:
            L.append(f"- {k}: {v}")
    L += ["", "## Imagen aplicada", ""]
    for src, d in look_log.items():
        L.append(f"- {src}: " + "; ".join(f"{a}: {b}" for a, b in d.items()))
    if analysis_note:
        L += ["", "## Material descartado y motivos", "", analysis_note]
    return "\n".join(L) + "\n"


def _tc(frames: int, fps_int: int) -> str:
    f = frames % fps_int
    s = frames // fps_int
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}:{f:02d}"


def edl(project: dict, tl: Timeline, paths: dict) -> str:
    """CMX3600 EDL (opens in DaVinci Resolve / Premiere / Final Cut via import) to refine by hand."""
    fps_int = int(round(float(tl.fps)))
    L = [f"TITLE: {project['name']}", "FCM: NON-DROP FRAME", ""]
    for n, c in enumerate(tl.clips):
        reel = re.sub(r"[^A-Z0-9]", "", Path(paths[c.src]).stem.upper())[:8] or "AX"
        si, so = _tc(c.f_in, fps_int), _tc(c.f_out, fps_int)
        ri, ro = _tc(c.o_start, fps_int), _tc(c.o_start + c.frames, fps_int)
        L.append(f"{n + 1:03d}  {reel:<8} AA/V  C        {si} {so} {ri} {ro}")
        L.append(f"* FROM CLIP NAME: {Path(paths[c.src]).name}")
        if c.seg.get("text"):
            L.append(f"* COMMENT: {c.seg['text'][:70]}")
        L.append("")
    return "\n".join(L)
