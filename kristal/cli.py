"""kristal — editor de video de LA TEACHER KRISTAL.

  python -m kristal init      projects/edible-vs-eatable footage/*.mp4 --vocab edible,eatable
  python -m kristal inspect   projects/edible-vs-eatable
  python -m kristal transcribe projects/edible-vs-eatable [--model small] [--import subs.srt --src A]
  python -m kristal analyze   projects/edible-vs-eatable      # analysis.md + borrador de edición
  python -m kristal render    projects/edible-vs-eatable [--preview]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from . import analyze as AN
from . import captions as CAP
from . import deliver as DL
from . import faces as FA
from . import graphics as GR
from . import look as LK
from . import qc as QC
from . import render as RD
from . import transcribe as TR
from .audio import Envelope
from .timeline import Timeline, normalize_fps
from .util import fmt_ts, load_brand, log, probe, read_json, require, write_json

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts"}


# ---------------------------------------------------------------- project helpers

def load_project(pdir: Path) -> dict:
    p = read_json(pdir / "project.json")
    if p is None:
        raise SystemExit(f"No existe {pdir / 'project.json'}. Crea el proyecto con 'kristal init'.")
    p["_dir"] = str(pdir)
    return p


def save_project(pdir: Path, project: dict) -> None:
    write_json(pdir / "project.json", {k: v for k, v in project.items() if not k.startswith("_")})


def source_paths(pdir: Path, project: dict) -> dict[str, Path]:
    out = {}
    for src, rel in project["sources"].items():
        p = (pdir / rel).resolve()
        if not p.exists():
            raise SystemExit(f"Falta el archivo de la fuente {src}: {p}")
        out[src] = p
    return out


def transcripts(pdir: Path, project: dict) -> dict[str, dict]:
    out = {}
    for src in project["sources"]:
        tr = read_json(pdir / "transcripts" / f"{src}.json")
        if tr is None:
            raise SystemExit(f"Falta la transcripción de {src}. Ejecuta 'kristal transcribe {pdir}'.")
        tr = TR.apply_fixes(tr, project.get("transcript_fixes", {}))
        out[src] = TR.apply_edits(tr, project.get("transcript_edits", []), src)
    return out


# ---------------------------------------------------------------- commands

def cmd_init(a) -> None:
    pdir = Path(a.project)
    if (pdir / "project.json").exists() and not a.force:
        raise SystemExit(f"{pdir}/project.json ya existe (usa --force para reiniciarlo).")
    (pdir / "footage").mkdir(parents=True, exist_ok=True)
    media = []
    for m in a.media:
        mp = Path(m)
        media += sorted(x for x in mp.iterdir() if x.suffix.lower() in MEDIA_EXT) if mp.is_dir() else [mp]
    if not media:
        raise SystemExit("No se encontraron videos.")
    sources = {}
    for i, m in enumerate(media):
        key = chr(ord("A") + i) if i < 26 else f"S{i}"
        dest = pdir / "footage" / m.name
        if not dest.exists():
            if a.copy:
                shutil.copy2(m, dest)
            else:
                dest.symlink_to(m.resolve())
        sources[key] = f"footage/{m.name}"
    project = {
        "name": pdir.name, "title": a.title or pdir.name.replace("-", " ").title(),
        "language": a.lang, "sources": sources,
        "vocab": [v.strip() for v in (a.vocab or "").split(",") if v.strip()],
        "approved_terms": [], "transcript_fixes": {},
        "captions": {"enabled": True, "keywords": []},
        "audio": {"denoise_strength": "auto", "music": None},
        "look": {"auto": True, "stabilize": False, "overrides": {}},
        "segments": [], "zooms": [], "graphics": [],
    }
    save_project(pdir, project)
    log(f"Proyecto creado en {pdir} con {len(sources)} fuente(s): " + ", ".join(f"{k}={v}" for k, v in sources.items()))


def cmd_inspect(a) -> None:
    pdir = Path(a.project)
    project = load_project(pdir)
    for src, p in source_paths(pdir, project).items():
        info = probe(p)
        v, au = info["video"], info["audio"]
        print(f"{src}: {p.name}  {info['duration']:.2f}s")
        if v:
            print(f"   vídeo {v['codec']} {v['width']}x{v['height']} @ {v['fps_float']:.3f} fps "
                  f"(→ {float(normalize_fps(v['fps_float'])):.3f}) {v['pix_fmt']} rot={v['rotation']} "
                  f"trc={v['color_transfer']}")
        if au:
            print(f"   audio {au['codec']} {au['sample_rate']} Hz {au['channels']} canal(es)")


def cmd_transcribe(a) -> None:
    pdir = Path(a.project)
    project = load_project(pdir)
    paths = source_paths(pdir, project)
    targets = [a.src] if a.src else list(paths)
    for src in targets:
        out = pdir / "transcripts" / f"{src}.json"
        if out.exists() and not a.force and not a.imp:
            log(f"{src}: transcripción ya existe ({out}); usa --force para rehacerla.")
            continue
        if a.imp:
            imp = Path(a.imp)
            tr = TR.import_json(imp) if imp.suffix.lower() == ".json" else TR.import_subtitles(imp)
        elif a.engine == "parakeet":
            if not a.model_dir:
                raise SystemExit("--engine parakeet requiere --model-dir (carpeta sherpa-onnx-nemo-parakeet-tdt-*)")
            tr = TR.transcribe_parakeet(paths[src], Path(a.model_dir), Path(a.vad) if a.vad else None)
        else:
            tr = TR.transcribe_whisper(paths[src], project.get("language"), a.model,
                                       project.get("vocab", []) + project.get("approved_terms", []))
        tr["source"] = src
        write_json(out, tr)
        log(f"{src}: {len(tr['words'])} palabras → {out}")


def cmd_analyze(a) -> None:
    pdir = Path(a.project)
    project = load_project(pdir)
    paths = source_paths(pdir, project)
    trs = transcripts(pdir, project)
    envs = AN.load_envelopes(project, paths)
    if project.get("segments") and not (a.force or a.report_only):
        raise SystemExit("project.json ya tiene una edición. Usa --force para sustituirla por un borrador nuevo, "
                         "o --report-only para regenerar solo analysis.md.")
    edit, report = AN.analyze(project, trs, envs)
    (pdir / "analysis.md").write_text(report, encoding="utf-8")
    if not a.report_only:
        project.update({k: v for k, v in edit.items()})
        save_project(pdir, project)
    log(f"Análisis → {pdir / 'analysis.md'} · {len(edit['segments'])} segmentos en el borrador")


def cmd_plan(a) -> None:
    pdir = Path(a.project)
    project = load_project(pdir)
    if not project.get("plan"):
        raise SystemExit("project.json no tiene 'plan'.")
    paths = source_paths(pdir, project)
    trs = transcripts(pdir, project)
    envs = AN.load_envelopes(project, paths)
    segs = AN.plan_to_segments(project["plan"], trs, envs)
    project["segments"] = segs
    if "audio_hints" not in project:
        project["audio_hints"] = {src: {"noise_db": round(e.noise_db, 1), "speech_db": round(e.speech_db, 1),
                                        "hum_hz": None} for src, e in envs.items()}
    save_project(pdir, project)
    tl = Timeline(segs, normalize_fps(probe(next(iter(paths.values())))["video"]["fps_float"]))
    t = 0.0
    for n, sg in enumerate(segs):
        print(f"{n + 1:3d} {fmt_ts(t)} {sg['src']} {sg['in']:7.2f}-{sg['out']:7.2f} [{sg['role']}/{sg['framing']}] {sg['text']}")
        t += sg["out"] - sg["in"]
    log(f"{len(segs)} segmentos · duración {tl.duration:.1f}s")


def _faces(pdir, src, path, vinfo):
    cache = pdir / "work" / f"faces_{src}.json"
    data = read_json(cache)
    if data is None:
        data = FA.track(path, vinfo)
        write_json(cache, data)
    return FA.FaceTrack(data)


def cmd_render(a) -> None:
    t_start = time.time()
    require("ffmpeg")
    pdir = Path(a.project)
    project = load_project(pdir)
    brand = load_brand()
    for key in ("framing", "layout", "captions", "graphics", "audio", "look"):
        brand[key].update(project.get("brand_overrides", {}).get(key, {}))
    paths = source_paths(pdir, project)
    if not project.get("segments"):
        raise SystemExit("No hay segmentos en project.json. Ejecuta 'kristal analyze' o escribe la edición.")
    infos = {src: probe(p) for src, p in paths.items()}
    first = infos[project["segments"][0]["src"]]["video"]
    fps = normalize_fps(project.get("output", {}).get("fps") or first["fps_float"])
    tl = Timeline(project["segments"], fps)
    log(f"Línea de tiempo: {len(tl.clips)} cortes, {tl.duration:.2f}s @ {float(fps):.3f} fps")
    work = pdir / "work"
    exp = pdir / "exports"
    work.mkdir(parents=True, exist_ok=True)
    exp.mkdir(parents=True, exist_ok=True)

    trs = transcripts(pdir, project)
    words_out = sorted((w for src in paths for w in tl.map_words(src, trs[src]["words"])), key=lambda w: w["os"])
    all_words = [w for tr in trs.values() for w in tr["words"]]

    # image: optional stabilisation, face tracks, measured look
    vpaths = dict(paths)
    if project.get("look", {}).get("stabilize"):
        for src, p in paths.items():
            mez = work / f"stab_{src}.mp4"
            if not mez.exists():
                trf = work / f"stab_{src}.trf"
                LK.stabilize_detect(p, trf)
                log(f"Estabilizando {src} ...")
                from .util import run
                run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-vf", LK.stabilize_filter(trf),
                     "-c:v", "libx264", "-crf", "12", "-preset", "fast", "-an", str(mez)])
            vpaths[src] = mez
    faces = {src: _faces(pdir, src, p, infos[src]["video"]) for src, p in vpaths.items()}
    looks, look_log = {}, {}
    for src, p in vpaths.items():
        cache = work / f"look_{src}.json"
        stats = read_json(cache)
        if stats is None:
            stats = LK.measure(p, infos[src]["duration"])
            write_json(cache, stats)
        if project.get("look", {}).get("auto", True):
            looks[src], look_log[src] = LK.correction(stats, brand, infos[src]["video"],
                                                     project.get("look", {}).get("overrides", {}).get(src))
        else:
            looks[src], look_log[src] = "", {"auto": "desactivado"}
        if project.get("look", {}).get("stabilize"):
            look_log[src]["estabilizacion"] = "vidstab 2 pasadas (smoothing 20)"

    # audio
    hints = project.get("audio_hints") or {}
    for src, p in paths.items():
        if src not in hints:
            env = Envelope(p)
            hints[src] = {"noise_db": env.noise_db, "speech_db": env.speech_db, "hum_hz": None}
    log("Restaurando audio ...")
    voice, audio_log = RD.build_audio(tl, paths, brand, project, hints, work)

    # captions + graphics (kept as editable .ass files)
    name = project["name"]
    g_ass, placed = GR.build_ass(project.get("graphics", []), tl, brand)
    (exp / "graphics.ass").write_text(g_ass, encoding="utf-8")
    cap_cfg = project.get("captions", {})
    cap_path = None
    if cap_cfg.get("enabled", True):
        kws = project.get("vocab", []) + cap_cfg.get("keywords", [])
        (exp / "captions.ass").write_text(CAP.build_ass(words_out, brand, cap_cfg, kws, tl.duration), encoding="utf-8")
        cap_path = exp / "captions.ass"
    (exp / f"{name}.srt").write_text(CAP.build_srt(words_out, tl.duration), encoding="utf-8")

    out_main = exp / f"{name}_final.mp4"
    out_clean = exp / f"{name}_sin_subtitulos.mp4"
    log("Renderizando video ...")
    RD.render(tl, vpaths, infos, faces, looks, project.get("zooms", []), brand, voice,
              exp / "graphics.ass", cap_path, out_main, out_clean, preview=a.preview)

    # documents
    (exp / f"{name}_transcripcion.md").write_text(DL.transcript_md(project, words_out, tl), encoding="utf-8")
    analysis = (pdir / "analysis.md").read_text(encoding="utf-8") if (pdir / "analysis.md").exists() else ""
    dropped = "\n".join(l for l in analysis.splitlines() if "✂" in l or "descartada" in l)
    (exp / f"{name}_registro_cortes.md").write_text(
        DL.cut_log(project, tl, dropped, placed, project.get("zooms", []), audio_log, look_log), encoding="utf-8")
    (exp / f"{name}.edl").write_text(DL.edl(project, tl, paths), encoding="utf-8")
    write_json(exp / f"{name}_proyecto.json", {k: v for k, v in project.items() if not k.startswith("_")})

    if a.no_qc:
        return
    log("Control de calidad sobre el render ...")
    passed, report = QC.check(out_main, out_clean, tl, brand, words_out, placed, project, all_words, exp / "qc")
    (exp / "qc" / "report.md").write_text(report, encoding="utf-8")
    print(report)
    log(f"Listo en {time.time() - t_start:.0f}s → {exp}")
    if not passed:
        sys.exit(2)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="kristal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="crear un proyecto a partir de grabaciones")
    p.add_argument("project")
    p.add_argument("media", nargs="+")
    p.add_argument("--title")
    p.add_argument("--vocab", help="términos de la lección separados por comas (p. ej. edible,eatable)")
    p.add_argument("--lang", default="es")
    p.add_argument("--copy", action="store_true", help="copiar los videos en lugar de enlazarlos")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("inspect", help="ver especificaciones técnicas de las fuentes")
    p.add_argument("project")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("transcribe", help="transcripción palabra por palabra")
    p.add_argument("project")
    p.add_argument("--engine", choices=["whisper", "parakeet"], default="whisper")
    p.add_argument("--model", default="small", help="tamaño/carpeta del modelo Whisper")
    p.add_argument("--model-dir", help="carpeta del modelo Parakeet (sherpa-onnx)")
    p.add_argument("--vad", help="silero_vad.onnx para dividir el audio en pausas (recomendado)")
    p.add_argument("--src")
    p.add_argument("--import", dest="imp", help="importar SRT/VTT/JSON en lugar de usar Whisper")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_transcribe)
    p = sub.add_parser("analyze", help="análisis editorial + borrador de edición")
    p.add_argument("project")
    p.add_argument("--force", action="store_true", help="sustituir la edición existente")
    p.add_argument("--report-only", action="store_true", help="solo regenerar analysis.md")
    p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("plan", help="convertir el plan editorial (palabras) en cortes exactos")
    p.add_argument("project")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("render", help="renderizar, exportar y verificar")
    p.add_argument("project")
    p.add_argument("--preview", action="store_true", help="codificación rápida para revisar")
    p.add_argument("--no-qc", action="store_true")
    p.set_defaults(fn=cmd_render)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
