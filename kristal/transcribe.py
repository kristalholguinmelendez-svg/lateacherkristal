"""Word-level transcription.

Primary engine: faster-whisper (word_timestamps=True). If no model can be loaded
(offline machine, blocked model hub) a transcript can be imported from SRT/VTT or
from a word-level JSON exported by another tool (WhisperX, Descript, CapCut...).
"""
from __future__ import annotations

import re
from pathlib import Path

from .util import log, read_json


def transcribe_whisper(path: Path, language: str | None, model_size: str, vocab: list[str]) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover
        raise SystemExit("faster-whisper no está instalado: pip install faster-whisper") from e
    log(f"Cargando modelo Whisper '{model_size}' ...")
    try:
        model = WhisperModel(model_size, device="auto", compute_type="auto")
    except Exception as e:  # network-blocked hub, missing weights ...
        raise SystemExit(
            f"No pude cargar el modelo '{model_size}' ({type(e).__name__}: {str(e)[:200]}).\n"
            "Opciones: (1) ejecutar en una máquina con acceso a huggingface.co, "
            "(2) pasar --model con una carpeta local del modelo CTranslate2, o "
            "(3) importar una transcripción: kristal transcribe <proyecto> --import archivo.srt|json")
    # Bilingual lessons: the prompt lists the English terms so Whisper spells them right.
    prompt = None
    if vocab:
        prompt = "Clase de inglés con LA TEACHER KRISTAL. Vocabulario: " + ", ".join(vocab) + "."
    segments, info = model.transcribe(
        str(path), language=language, word_timestamps=True, initial_prompt=prompt,
        vad_filter=False,            # keep false starts: we need them to pick the best take
        condition_on_previous_text=False,  # reduces hallucinated repetitions across takes
        beam_size=5)
    words, segs = [], []
    for seg in segments:
        segs.append({"s": round(seg.start, 3), "e": round(seg.end, 3), "text": seg.text.strip()})
        for w in seg.words or []:
            token = w.word.strip()
            if token:
                words.append({"w": token, "s": round(w.start, 3), "e": round(w.end, 3),
                              "p": round(w.probability, 3)})
    return {"engine": f"faster-whisper/{model_size}", "language": info.language,
            "words": words, "segments": segs}


def transcribe_parakeet(path: Path, model_dir: Path, vad_model: Path | None = None) -> dict:
    """NVIDIA Parakeet TDT v3 (25 European languages, punctuation + casing) through sherpa-onnx.

    Weights come from the sherpa-onnx GitHub releases, which stay reachable where the
    Hugging Face hub is blocked. Audio is split at pauses with Silero VAD and each chunk
    is decoded with token timestamps, merged into words ('▁' marks a word start).
    """
    import numpy as np
    import sherpa_onnx

    from .util import run

    md = Path(model_dir)
    pick = lambda stem: str(next(md.glob(f"{stem}*.onnx")))
    rec = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=pick("encoder"), decoder=pick("decoder"), joiner=pick("joiner"),
        tokens=str(md / "tokens.txt"), num_threads=4, model_type="nemo_transducer",
        decoding_method="greedy_search")
    sr = 16000
    res = run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
              stdout=-1)
    pcm = np.frombuffer(res.stdout, dtype=np.float32)
    chunks = _vad_chunks(pcm, sr, vad_model) if vad_model else [(0, pcm)]
    words, segs = [], []
    for start_sample, x in chunks:
        off = start_sample / sr
        st = rec.create_stream()
        st.accept_waveform(sr, x)
        rec.decode_stream(st)
        r = st.result
        toks, times = list(r.tokens), list(r.timestamps)
        durs = list(getattr(r, "durations", []) or [])
        if not toks:
            continue
        segs.append({"s": round(off + times[0], 3), "e": round(off + len(x) / sr, 3), "text": r.text.strip()})
        lps = list(getattr(r, "ys_log_probs", []) or [])
        cur = None
        for k, (tok, t) in enumerate(zip(toks, times)):
            end = t + (durs[k] if k < len(durs) and durs[k] > 0 else 0.08)
            lp = lps[k] if k < len(lps) else None
            if tok.startswith((" ", "▁")) or cur is None:
                if cur and cur["w"]:
                    words.append(_close(cur))
                cur = {"w": tok.lstrip(" ▁"), "s": round(off + t, 3), "e": round(off + end, 3), "_lp": []}
            else:
                cur["w"] += tok
                cur["e"] = round(off + end, 3)
            if lp is not None:
                cur["_lp"].append(lp)
        if cur and cur["w"]:
            words.append(_close(cur))
    # a word ends no later than the next one starts
    for a, b in zip(words, words[1:]):
        a["e"] = round(min(max(a["e"], a["s"] + 0.05), b["s"]), 3)
    return {"engine": "sherpa-onnx/parakeet-tdt-0.6b-v3", "language": None, "words": words, "segments": segs}


def _close(w: dict) -> dict:
    import math

    lp = w.pop("_lp")
    w["p"] = round(math.exp(sum(lp) / len(lp)), 3) if lp else None  # geometric-mean token probability
    return w


def _vad_chunks(pcm, sr: int, vad_model: Path, max_len: float = 18.0):
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(vad_model)
    cfg.silero_vad.min_silence_duration = 0.30
    cfg.silero_vad.min_speech_duration = 0.15
    cfg.silero_vad.max_speech_duration = max_len
    cfg.sample_rate = sr
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=600)
    win = cfg.silero_vad.window_size
    out = []
    for i in range(0, len(pcm), win):
        vad.accept_waveform(pcm[i:i + win])
        while not vad.empty():
            out.append((vad.front.start, vad.front.samples))
            vad.pop()
    vad.flush()
    while not vad.empty():
        out.append((vad.front.start, vad.front.samples))
        vad.pop()
    pad = int(0.15 * sr)  # keep word onsets/offsets the VAD trims
    import numpy as np
    return [(max(0, s - pad), np.asarray(pcm[max(0, s - pad): s + len(x) + pad], dtype=np.float32)) for s, x in out]


_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _ts(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000


def import_subtitles(path: Path) -> dict:
    """SRT/VTT cues -> words, distributing each cue's time by character count.

    Approximate: word boundaries are later snapped to real silences in the audio.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    words, segs = [], []
    blocks = re.split(r"\n\s*\n", text.replace("\r", ""))
    for b in blocks:
        m = _TS.search(b)
        if not m:
            continue
        s, e = _ts(*m.groups()[:4]), _ts(*m.groups()[4:])
        body = " ".join(line for line in b[m.end():].strip().splitlines())
        body = re.sub(r"<[^>]+>|\{[^}]+\}", "", body).strip()
        if not body:
            continue
        segs.append({"s": s, "e": e, "text": body})
        toks = body.split()
        total = sum(len(t) + 1 for t in toks)
        t = s
        for tok in toks:
            d = (e - s) * (len(tok) + 1) / total
            words.append({"w": tok, "s": round(t, 3), "e": round(t + d * 0.92, 3), "p": None})
            t += d
    return {"engine": f"import:{Path(path).name}", "language": None, "words": words, "segments": segs,
            "approximate_timing": True}


def import_json(path: Path) -> dict:
    """Accepts {'words':[{'w'|'word'|'text', 's'|'start', 'e'|'end'}]} or WhisperX segments."""
    data = read_json(path)
    raw = data.get("words")
    if raw is None and "segments" in data:
        raw = [w for seg in data["segments"] for w in seg.get("words", [])]
    words = []
    for w in raw or []:
        tok = (w.get("w") or w.get("word") or w.get("text") or "").strip()
        s, e = w.get("s", w.get("start")), w.get("e", w.get("end"))
        if tok and s is not None and e is not None:
            words.append({"w": tok, "s": float(s), "e": float(e), "p": w.get("p", w.get("probability", w.get("score")))})
    return {"engine": f"import:{Path(path).name}", "language": data.get("language"), "words": words,
            "segments": data.get("segments", [])}


def apply_fixes(tr: dict, fixes: dict[str, str]) -> dict:
    """Correct recurrent mis-hearings (e.g. {'idible': 'edible'}) keeping punctuation/case."""
    if not fixes:
        return tr
    low = {k.lower(): v for k, v in fixes.items()}
    for w in tr["words"]:
        m = re.match(r"^([¿¡\"'(]*)(.*?)([.,!?;:\"')]*)$", w["w"])
        pre, core, post = m.groups()
        rep = low.get(core.lower())
        if rep is not None:
            w["w"] = pre + rep + post
    return tr


def apply_edits(tr: dict, edits: list[dict], src: str) -> dict:
    """Replace the words that START inside [s, e] with corrected text
    (multi-word fixes, e.g. 'Amis Art' -> 'am, is, are'). New words share the original
    time span proportionally to their length.
    """
    for ed in edits or []:
        if ed.get("src", src) != src:
            continue
        inside = [w for w in tr["words"] if ed["s"] <= w["s"] <= ed["e"]]
        if not inside:
            continue
        a, b = inside[0]["s"], inside[-1]["e"]
        toks = ed["text"].split()
        total = sum(len(t) + 1 for t in toks) or 1
        new, t = [], a
        for tok in toks:
            d = (b - a) * (len(tok) + 1) / total
            new.append({"w": tok, "s": round(t, 3), "e": round(t + d, 3), "p": 1.0, "edited": True})
            t += d
        keep = [w for w in tr["words"] if w not in inside]
        tr["words"] = sorted(keep + new, key=lambda w: w["s"])
    return tr
