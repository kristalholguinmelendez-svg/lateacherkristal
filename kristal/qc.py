"""Post-render quality control on the RENDERED files (not on the timeline).

Checks container/codec specs, loudness, awkward silences, black frames, energy at cut
points (possible clipped words), caption/graphic timing and spelling of on-screen text
against the transcript, and extracts review frames + a contact sheet to look at.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from . import audio as A
from .analyze import norm
from .graphics import graphic_text, runs
from .timeline import Timeline
from .util import fmt_ts, probe, run


def check(out_main: Path, out_clean: Path, tl: Timeline, brand: dict, words_out: list[dict],
          placed: list[dict], project: dict, all_words: list[dict], qc_dir: Path) -> tuple[bool, str]:
    qc_dir.mkdir(parents=True, exist_ok=True)
    issues, ok_lines = [], []

    def ok(msg):
        ok_lines.append(f"- ✅ {msg}")

    def bad(msg, severe=True):
        issues.append((severe, f"- {'❌' if severe else '⚠️'} {msg}"))

    # ---- specs
    for path in (out_main, out_clean):
        info = probe(path)
        v, a = info["video"], info["audio"]
        if not v or v["codec"] != "h264" or (v["width"], v["height"]) != (1080, 1920):
            bad(f"{path.name}: vídeo {v and v['codec']} {v and v['width']}x{v and v['height']} (esperado h264 1080x1920)")
        elif abs(v["fps_float"] - float(tl.fps)) > 0.01:
            bad(f"{path.name}: {v['fps_float']:.3f} fps ≠ {float(tl.fps):.3f}")
        else:
            ok(f"{path.name}: H.264 1080×1920 @ {v['fps_float']:.3f} fps, pix_fmt {v['pix_fmt']}")
        if not a or a["codec"] != "aac":
            bad(f"{path.name}: audio {a and a['codec']} (esperado AAC)")
        if abs(info["duration"] - tl.duration) > 0.15:
            bad(f"{path.name}: duración {info['duration']:.2f}s vs línea de tiempo {tl.duration:.2f}s")
        else:
            ok(f"{path.name}: duración {info['duration']:.2f} s (línea de tiempo {tl.duration:.2f} s)")
    head = out_main.read_bytes()[:64 * 1024]
    if b"moov" in head:
        ok("moov al inicio (faststart): reproducción inmediata en móvil")
    else:
        bad("moov no está al inicio del archivo", severe=False)

    # ---- loudness
    lu = A.ebur128(out_main)
    tgt = brand["audio"]["target_lufs"]
    if "integrated_lufs" in lu and abs(lu["integrated_lufs"] - tgt) <= 1.0 and lu.get("true_peak_dbfs", 0) <= -0.9:
        ok(f"Sonoridad {lu['integrated_lufs']} LUFS, pico {lu.get('true_peak_dbfs')} dBFS, LRA {lu.get('lra')} LU")
    else:
        bad(f"Sonoridad fuera de objetivo: {lu}")

    # ---- silences / black frames
    env = A.Envelope(out_main)
    long_sil = [(s, e) for s, e in env.silences(min_len=0.75, margin_db=18) if s > 0.2 and e < tl.duration - 0.3]
    if long_sil:
        bad("Pausas largas: " + ", ".join(f"{fmt_ts(s)} ({e - s:.2f}s)" for s, e in long_sil), severe=False)
    else:
        ok("Sin pausas incómodas (> 0.75 s)")
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(out_main), "-vf", "blackdetect=d=0.1:pix_th=0.06",
               "-an", "-f", "null", "-"], capture=True)
    blacks = re.findall(r"black_start:([\d.]+)", res.stderr)
    if blacks:
        bad("Pantallas negras en " + ", ".join(fmt_ts(float(b)) for b in blacks))
    else:
        ok("Sin pantallas negras")

    # ---- energy at cut points (a loud frame exactly at a cut suggests a clipped word)
    hot = []
    for c in tl.clips[1:]:
        t = tl.clip_out_range(c)[0]
        i = env.idx(t)
        around = env.db[max(0, i - 2): i + 2]
        if len(around) and around.max() > env.speech_db - 6:
            hot.append(fmt_ts(t))
    if hot:
        bad("Cortes con energía alta (revisar palabra cortada): " + ", ".join(hot), severe=False)
    else:
        ok(f"{len(tl.clips) - 1} cortes caen en zonas de baja energía (sin palabras cortadas)")

    # ---- start / end
    if words_out and words_out[0]["os"] > 0.6:
        bad(f"El primer sonido llega a {words_out[0]['os']:.2f}s: hook lento", severe=False)
    else:
        ok("La voz empieza en el primer medio segundo (hook inmediato)")
    if words_out and not re.search(r"[.!?…]$", words_out[-1]["w"]):
        bad(f"La última palabra '{words_out[-1]['w']}' no cierra frase: ¿final cortado?", severe=False)
    elif words_out:
        tail = tl.duration - words_out[-1]["oe"]
        (ok if 0.15 <= tail <= 1.2 else lambda m: bad(m, severe=False))(f"Final con {tail:.2f}s de aire tras la última palabra")

    # ---- on-screen text: every word must be spoken in the recording or explicitly approved
    spoken = {norm(w["w"]) for w in all_words}
    approved = {norm(x) for x in project.get("approved_terms", []) + project.get("vocab", [])}
    for g in placed:
        txt = graphic_text(g)
        toks = [norm(t) for t in re.findall(r"[\w'’-]+", "".join(t for t, _ in runs(txt)))]
        unknown = sorted({t for t in toks if t and t not in spoken and t not in approved and not t.isdigit()
                          and len(t) > 1 and t not in ("vs",)})
        if unknown:
            bad(f"Gráfico {g['type']} @ {fmt_ts(g['o_start'])}: palabras no dichas ni aprobadas → {unknown}. "
                "Añádelas a approved_terms si son correctas.", severe=False)
        if g["o_end"] - g["o_start"] < 1.2:
            bad(f"Gráfico {g['type']} @ {fmt_ts(g['o_start'])} dura {g['o_end'] - g['o_start']:.2f}s: muy rápido para leer", severe=False)
    if placed:
        ok(f"{len(placed)} gráficos verificados contra la transcripción")

    # ---- review frames: hook, every clip middle, every graphic middle
    times = [0.4]
    times += [sum(tl.clip_out_range(c)) / 2 for c in tl.clips]
    times += [(g["o_start"] + g["o_end"]) / 2 + 0.3 for g in placed]
    times += [g["o_reveal"] + 0.5 for g in placed if g.get("o_reveal") is not None]
    times = sorted({round(min(t, tl.duration - 0.05), 2) for t in times})
    frames = []
    for n, t in enumerate(times):
        p = qc_dir / f"frame_{n:02d}_{t:06.2f}s.jpg"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(out_main), "-frames:v", "1",
             "-q:v", "3", str(p)])
        frames.append(p)
    _contact_sheet(frames, times, qc_dir / "contact_sheet.jpg")

    severe = [m for s, m in issues if s]
    lines = [f"# QC — {project.get('title', project['name'])}", "",
             "**Resultado: " + ("APROBADO" if not severe else "REQUIERE CORRECCIÓN") + "**", ""]
    lines += ok_lines + [m for _, m in issues]
    lines += ["", f"Fotogramas de revisión: `{qc_dir.name}/` (hoja de contactos: contact_sheet.jpg)"]
    return not severe, "\n".join(lines) + "\n"


def _contact_sheet(frames: list[Path], times: list[float], out: Path, cols: int = 6) -> None:
    import cv2

    thumbs = []
    for p, t in zip(frames, times):
        img = cv2.imread(str(p))
        if img is None:
            continue
        img = cv2.resize(img, (270, 480), interpolation=cv2.INTER_AREA)
        cv2.rectangle(img, (0, 0), (110, 30), (20, 17, 46), -1)
        cv2.putText(img, f"{t:5.2f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        thumbs.append(img)
    if not thumbs:
        return
    rows = (len(thumbs) + cols - 1) // cols
    sheet = np.full((rows * 490 + 10, cols * 280 + 10, 3), 30, np.uint8)
    for i, th in enumerate(thumbs):
        r, c = divmod(i, cols)
        sheet[10 + r * 490: 10 + r * 490 + 480, 10 + c * 280: 10 + c * 280 + 270] = th
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
