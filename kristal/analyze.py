"""Editorial analysis: fillers, stutters, pauses, repeated takes, best-take choice, hook candidates.

Produces `analysis.md` (human-readable report with timestamps and reasons) and a
draft edit (segments, framing, punch-ins, graphics) inside project.json. The draft
is a starting point: the editor (Claude or Kristal) reviews it before rendering.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .audio import Envelope, detect_hum
from .util import fmt_ts, log

# Pure vocal hesitations: removed automatically.
HESITATIONS = {"eh", "ehh", "ehm", "em", "emm", "mm", "mmm", "hmm", "um", "umm", "uh", "uhm", "ah", "ahh", "este..."}
# Discourse fillers: reported for review, only removed when isolated by pauses.
SOFT_FILLERS = {"este", "pues", "bueno", "osea", "o sea", "entonces", "like", "so", "okay", "ok", "vale"}
HOOK_CUES = ["nunca", "error", "mal", "no digas", "deja de", "incorrect", "wrong", "never", "stop", "diferencia",
             "difference", "vs", "versus", "sabías", "sabias", "secreto", "nadie", "todos", "casi todos", "?",
             "no es", "no se dice", "se dice", "nativos", "native", "truco", "realmente", "en realidad"]

# Openers that delay the lesson: dropped from the draft (listed in the report, easy to restore).
GREETINGS = ["hola", "bienvenidos", "bienvenidas", "hey chicos", "que tal", "hoy vamos a", "en este video",
             "en el video de hoy", "hello everyone", "hi guys", "welcome"]

PHRASE_GAP = 0.45     # silence that ends a phrase (s)
SPLIT_GAP = 0.70      # silence inside a kept phrase that gets tightened (s)
PAD_IN, PAD_OUT = 0.10, 0.16


def norm(token: str) -> str:
    t = unicodedata.normalize("NFD", token.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9'\-]", "", t)


def _ends_sentence(tok: str) -> bool:
    return bool(re.search(r"[.!?…]$", tok))


def annotate_words(words: list[dict]) -> None:
    for i, w in enumerate(words):
        n = norm(w["w"]).strip("-")
        nxt = words[i + 1] if i + 1 < len(words) else None
        prv = words[i - 1] if i > 0 else None
        gap_before = w["s"] - prv["e"] if prv else 9
        gap_after = nxt["s"] - w["e"] if nxt else 9
        w["n"] = n
        w["flags"] = []
        if n in HESITATIONS or re.fullmatch(r"(e+h*|m+|e+m+|u+h+m*|a+h+)", n or "x"):
            w["flags"].append("hesitation")
        elif n in SOFT_FILLERS:
            w["flags"].append("soft_filler_isolated" if gap_before > 0.25 and gap_after > 0.25 else "soft_filler")
        if nxt is not None and gap_after < 0.6:
            nn = norm(nxt["w"]).strip("-")
            if n and n == nn and len(n) > 1 and not _ends_sentence(w["w"]):
                w["flags"].append("repeat")
            elif w["w"].rstrip(".,").endswith("-") or (2 <= len(n) < len(nn) - 1 and nn.startswith(n)):
                w["flags"].append("fragment")


def split_phrases(words: list[dict]) -> list[dict]:
    phrases, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w["s"] - cur[-1]["e"]
            if gap > PHRASE_GAP or _ends_sentence(cur[-1]["w"]) or w["src"] != cur[-1]["src"]:
                phrases.append(cur)
                cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    out = []
    for k, ws in enumerate(phrases):
        out.append({"id": f"p{k + 1:03d}", "src": ws[0]["src"], "words": ws,
                    "s": ws[0]["s"], "e": ws[-1]["e"],
                    "text": " ".join(w["w"] for w in ws),
                    "tokens": [w["n"] for w in ws if "hesitation" not in w["flags"] and w["n"]]})
    return out


def score_phrase(ph: dict, env: Envelope | None) -> dict:
    ws = ph["words"]
    dur = max(0.2, ph["e"] - ph["s"])
    hes = sum("hesitation" in w["flags"] for w in ws)
    stut = sum(("repeat" in w["flags"]) or ("fragment" in w["flags"]) for w in ws)
    probs = [w["p"] for w in ws if w.get("p") is not None]
    conf = sum(probs) / len(probs) if probs else 0.85
    wps = len(ph["tokens"]) / dur
    complete = _ends_sentence(ws[-1]["w"]) or (len(ph["tokens"]) >= 4)
    lvl = env.level(ph["s"], ph["e"]) if env else {"snr_db": 30.0, "clip_ratio": 0.0, "speech_db": -20}
    pace = 1.0 - min(1.0, abs(wps - 2.8) / 2.5)          # natural explanation pace ~2.3-3.3 wps
    score = (3.0 * conf + 1.5 * pace + (0.8 if complete else -1.5)
             - 0.9 * hes - 1.2 * stut
             + 0.04 * min(lvl["snr_db"], 45) - 25 * lvl["clip_ratio"])
    return {"score": round(score, 3), "hes": hes, "stut": stut, "conf": round(conf, 3), "wps": round(wps, 2),
            "complete": complete, **{k: round(v, 3) for k, v in lvl.items()}}


def cluster_takes(phrases: list[dict]) -> list[list[int]]:
    """Group phrases that are versions of the same explanation (retakes / false starts)."""
    parent = list(range(len(phrases)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(phrases):
        if len(a["tokens"]) < 2:
            continue
        for j in range(i + 1, min(len(phrases), i + 8)):   # retakes happen close together
            b = phrases[j]
            if len(b["tokens"]) < 2:
                continue
            ratio = SequenceMatcher(None, a["tokens"], b["tokens"]).ratio()
            short, long_ = (a, b) if len(a["tokens"]) <= len(b["tokens"]) else (b, a)
            k = len(short["tokens"])
            prefix = k >= 2 and long_["tokens"][:k] == short["tokens"] and short is a  # false start, redone
            if ratio >= 0.6 or prefix:
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(phrases)):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values()]


def _is_greeting(ph: dict) -> bool:
    t = " " + " ".join(ph["tokens"]) + " "
    return any(f" {norm_phrase(g)} " in t for g in GREETINGS) and len(ph["tokens"]) <= 14


def norm_phrase(text: str) -> str:
    return " ".join(norm(x) for x in text.split())


def hook_score(ph: dict, vocab: list[str]) -> float:
    t = " " + " ".join(ph["tokens"]) + " "
    raw = ph["text"].lower()
    s = 0.0
    for cue in HOOK_CUES:
        if cue == "?":
            s += 1.5 if "?" in raw else 0
        elif f" {cue} " in t:
            s += 1.0
    s += sum(1.2 for v in vocab if norm(v) in ph["tokens"])
    n = len(ph["tokens"])
    s += 0.8 if 4 <= n <= 14 else -0.8                     # a hook must be short
    return round(s, 2)


def analyze(project: dict, transcripts: dict[str, dict], envs: dict[str, Envelope]) -> tuple[dict, str]:
    vocab = project.get("vocab", [])
    words = []
    for src in project["sources"]:
        for w in transcripts[src]["words"]:
            words.append({**w, "src": src})
    annotate_words(words)
    phrases = split_phrases(words)
    for ph in phrases:
        ph["q"] = score_phrase(ph, envs.get(ph["src"]))
        ph["hook"] = hook_score(ph, vocab)

    # ---- takes
    clusters = cluster_takes(phrases)
    dropped: dict[int, str] = {}
    take_report = []
    for g in clusters:
        if len(g) < 2:
            continue
        best = max(g, key=lambda i: (phrases[i]["q"]["score"] + 0.15 * g.index(i)))  # tie -> later take
        take_report.append((g, best))
        for i in g:
            if i != best:
                dropped[i] = f"versión repetida de {phrases[best]['id']} (score {phrases[i]['q']['score']} < {phrases[best]['q']['score']})"
    for i, ph in enumerate(phrases):
        if i not in dropped and not ph["tokens"]:
            dropped[i] = "solo muletillas"
        elif i not in dropped and i <= 2 and _is_greeting(ph):
            dropped[i] = "saludo / introducción que retrasa la lección"

    kept = [i for i in range(len(phrases)) if i not in dropped]

    # ---- hook: most interesting kept phrase; moved to the front if it is not already there
    hook_idx = None
    if kept:
        cand = max(kept, key=lambda i: phrases[i]["hook"])
        if phrases[cand]["hook"] >= 2.0 and kept.index(cand) > 0:
            hook_idx = cand
    order = ([hook_idx] if hook_idx is not None else []) + [i for i in kept if i != hook_idx]

    # ---- segments (source-anchored), fillers and long pauses cut out
    segments = []
    for rank, i in enumerate(order):
        ph = phrases[i]
        env = envs.get(ph["src"])
        pieces, cur = [], []
        for w in ph["words"]:
            drop = "hesitation" in w["flags"] or "repeat" in w["flags"] or "fragment" in w["flags"] \
                or "soft_filler_isolated" in w["flags"]
            if drop:
                if cur:
                    pieces.append(cur)
                cur = []
                continue
            if cur and w["s"] - cur[-1]["e"] > SPLIT_GAP:
                pieces.append(cur)
                cur = []
            cur.append(w)
        if cur:
            pieces.append(cur)
        for k, pc in enumerate(pieces):
            t_in, t_out = pc[0]["s"] - PAD_IN, pc[-1]["e"] + PAD_OUT
            if env:
                t_in = env.snap(t_in, side="left")
                t_out = env.snap(t_out, side="right")
            segments.append({"src": ph["src"], "in": round(max(0.0, t_in), 3), "out": round(t_out, 3),
                             "phrase": ph["id"], "text": " ".join(w["w"] for w in pc)})
    _merge_overlaps(segments)
    _assign_roles_and_framing(segments, phrases, order, hook_idx)

    edit = {
        "segments": segments,
        "zooms": _draft_punches(segments, words, vocab),
        "graphics": project.get("graphics") or _draft_graphics(segments, words, vocab),
    }
    hum = {src: detect_hum(env) for src, env in envs.items()}
    edit["audio_hints"] = {src: {"noise_db": round(env.noise_db, 1), "speech_db": round(env.speech_db, 1),
                                 "hum_hz": hum[src]} for src, env in envs.items()}
    report = _report(project, phrases, dropped, take_report, hook_idx, segments, edit["audio_hints"])
    return edit, report


def _merge_overlaps(segments: list[dict]) -> None:
    i = 0
    while i < len(segments) - 1:
        a, b = segments[i], segments[i + 1]
        if a["src"] == b["src"] and 0 <= b["in"] - a["out"] < 0.06 and a["phrase"] == b["phrase"]:
            a["out"], a["text"] = b["out"], a["text"] + " " + b["text"]
            segments.pop(i + 1)
        elif a["src"] == b["src"] and b["in"] < a["out"] and b["in"] > a["in"]:
            mid = round((a["out"] + b["in"]) / 2, 3)
            a["out"], b["in"] = mid, mid
            i += 1
        else:
            i += 1


def _assign_roles_and_framing(segments, phrases, order, hook_idx):
    cycle = ["medium", "wide", "medium", "close"]
    hook_id = phrases[hook_idx]["id"] if hook_idx is not None else None
    last_phrase = segments[-1]["phrase"] if segments else None
    k = 0
    prev_phrase = None
    for n, seg in enumerate(segments):
        if seg["phrase"] == hook_id or (hook_id is None and n == 0):
            seg["role"], seg["framing"] = "hook", "close"
        elif seg["phrase"] == last_phrase:
            seg["role"], seg["framing"] = "payoff", "close"
        else:
            seg["role"] = "curiosity" if n <= 2 else "explanation"
            if seg["phrase"] != prev_phrase:
                k += 1
            seg["framing"] = cycle[k % len(cycle)]
        prev_phrase = seg["phrase"]
    # vary the shot into the payoff: never close -> close at the final reveal
    for n in range(1, len(segments)):
        a, b = segments[n - 1], segments[n]
        if b["role"] == "payoff" and a["role"] != "hook" and a["framing"] == "close" and a["phrase"] != b["phrase"]:
            a["framing"] = "medium"


def _draft_punches(segments, words, vocab):
    """Punch-in on the first spoken mention of each lesson term inside kept material."""
    zooms, done, last_o = [], set(), -99.0
    targets = {norm(v): v for v in vocab}
    offset = 0.0  # output time of the current segment (segments may be reordered)
    for seg in segments:
        for w in words:
            if w["src"] == seg["src"] and seg["in"] <= w["s"] < seg["out"] and w.get("n") in targets:
                o = offset + w["s"] - seg["in"]
                if w["n"] in done or o - last_o < 4.0:
                    continue
                zooms.append({"src": seg["src"], "at": round(w["s"] - 0.06, 3), "kind": "punch",
                              "why": f"énfasis en '{targets[w['n']]}'"})
                done.add(w["n"])
                last_o = o
        offset += seg["out"] - seg["in"]
    return zooms


def _draft_graphics(segments, words, vocab):
    """Only uses terms Kristal listed in project.vocab (never invents examples)."""
    if not vocab:
        return []
    mentions = []
    for seg in segments:
        for w in words:
            if w["src"] == seg["src"] and seg["in"] <= w["s"] < seg["out"] and w.get("n") in {norm(v) for v in vocab}:
                mentions.append((seg, w))
    if not mentions:
        return []
    seg, w = mentions[0]
    if len(vocab) >= 2:
        return [{"type": "vs", "src": seg["src"], "start": round(w["s"], 3), "dur": 4.0,
                 "left": vocab[0].upper(), "right": vocab[1].upper(), "left_tag": "", "right_tag": "",
                 "note": "BORRADOR: revisa etiquetas ✓/✗ y duración"}]
    return [{"type": "word", "src": seg["src"], "start": round(w["s"], 3), "dur": 3.0,
             "word": vocab[0].upper(), "label": "", "note": "BORRADOR"}]


def _report(project, phrases, dropped, take_report, hook_idx, segments, hints) -> str:
    L = [f"# Análisis editorial — {project.get('title', project.get('name', ''))}", ""]
    L.append("## Audio por fuente")
    for src, h in hints.items():
        L.append(f"- **{src}**: piso de ruido {h['noise_db']} dBFS, voz {h['speech_db']} dBFS, "
                 f"SNR ≈ {round(h['speech_db'] - h['noise_db'], 1)} dB"
                 + (f", zumbido {h['hum_hz']} Hz" if h["hum_hz"] else ""))
    L += ["", "## Transcripción completa con timestamps", "",
          "`✂` = descartada · `⟳` = toma repetida · `~` = muletilla · `‖` = tartamudeo", ""]
    for i, ph in enumerate(phrases):
        marks = []
        for w in ph["words"]:
            f = w["flags"]
            tok = w["w"]
            if "hesitation" in f or "soft_filler_isolated" in f:
                tok = f"~{tok}~"
            elif "repeat" in f or "fragment" in f:
                tok = f"‖{tok}‖"
            marks.append(tok)
        status = f"✂ {dropped[i]}" if i in dropped else "✔"
        hook = f" · hook {ph['hook']}" if ph["hook"] >= 2 else ""
        q = ph["q"]
        L.append(f"- `{ph['id']}` [{ph['src']} {fmt_ts(ph['s'])}–{fmt_ts(ph['e'])}] {' '.join(marks)}  \n"
                 f"  {status} · score {q['score']} (conf {q['conf']}, {q['wps']} pal/s, "
                 f"muletillas {q['hes']}, tartamudeos {q['stut']}, SNR {q['snr_db']} dB){hook}")
    L += ["", "## Tomas repetidas y selección", ""]
    if not take_report:
        L.append("No se detectaron explicaciones repetidas.")
    for g, best in take_report:
        L.append(f"- Elegida **{phrases[best]['id']}**: \"{phrases[best]['text']}\"")
        for i in g:
            if i != best:
                L.append(f"  - descartada {phrases[i]['id']}: \"{phrases[i]['text']}\" — {dropped.get(i, '')}")
    L += ["", "## Hook", ""]
    ranked = sorted(phrases, key=lambda p: -p["hook"])[:5]
    for p in ranked:
        L.append(f"- {p['id']} ({p['hook']}): \"{p['text']}\"")
    if hook_idx is not None:
        L.append(f"\n→ Se movió **{phrases[hook_idx]['id']}** al inicio como hook.")
    else:
        L.append("\n→ El inicio original ya funciona como hook (o ningún candidato fue claramente mejor).")
    L += ["", "## Borrador de cortes (orden final)", ""]
    t = 0.0
    for n, s in enumerate(segments):
        d = s["out"] - s["in"]
        L.append(f"{n + 1}. `{fmt_ts(t)}` ← {s['src']} {fmt_ts(s['in'])}–{fmt_ts(s['out'])} "
                 f"[{s['role']}, {s['framing']}] {s['text']}")
        t += d
    L.append(f"\nDuración estimada: {t:.1f} s")
    return "\n".join(L) + "\n"


def load_envelopes(project: dict, footage_paths: dict) -> dict[str, Envelope]:
    envs = {}
    for src, path in footage_paths.items():
        log(f"Analizando audio de {src} ...")
        envs[src] = Envelope(path)
    return envs


# ---------------------------------------------------------------- editor's plan -> precise segments

PRE_ROLL, POST_ROLL = 0.07, 0.10   # breathing room around real speech
SPEECH_MARGIN_DB = 9.0             # frames this far above the noise floor count as voice


def _smooth(env, i0: int, i1: int):
    import numpy as np
    seg = env.db[max(0, i0 - 2): i1 + 3]
    k = np.ones(3) / 3
    return np.convolve(seg, k, mode="same")[2: 2 + (i1 - i0) + 1] if len(seg) > 4 else env.db[i0:i1 + 1]


def speech_onset(env, w: dict, prev: dict | None) -> float:
    """Real start of the voice for word w (ASR timestamps lag by up to ~0.3 s).

    Walks back from the ASR start to the first quiet frame; in connected speech (no quiet
    frame before the previous word) returns the energy valley between the two words.
    """
    import numpy as np
    lo_t = (prev["s"] + 0.5 * (prev["e"] - prev["s"])) if prev else w["s"] - 0.5
    lo_t = max(lo_t, w["s"] - 0.5, 0.0)
    i0, i1 = env.idx(lo_t), env.idx(w["s"] + 0.06)
    if i1 <= i0:
        return w["s"]
    sm = _smooth(env, i0, i1)
    thr = env.noise_db + SPEECH_MARGIN_DB
    quiet = np.flatnonzero(sm < thr)
    if len(quiet):
        return (i0 + quiet[-1] + 1) * 0.01
    return (i0 + int(np.argmin(sm))) * 0.01


def speech_offset(env, w: dict, nxt: dict | None, end_limit: float) -> float:
    """Real end of the voice for word w (walks forward to the first quiet frame)."""
    import numpy as np
    hi_t = nxt["s"] + 0.05 if nxt else w["e"] + 0.6
    hi_t = min(hi_t, w["e"] + 0.6, end_limit)
    i0, i1 = env.idx(max(w["s"] + 0.05, w["e"] - 0.08)), env.idx(hi_t)
    if i1 <= i0:
        return w["e"]
    sm = _smooth(env, i0, i1)
    thr = env.noise_db + SPEECH_MARGIN_DB
    quiet = np.flatnonzero(sm < thr)
    if len(quiet):
        return (i0 + quiet[0]) * 0.01
    return (i0 + int(np.argmin(sm))) * 0.01


ALT_FRAMING = {"wide": "medium", "medium": "close", "close": "medium"}


def plan_to_segments(plan: list[dict], transcripts: dict[str, dict], envs: dict, max_gap: float = 0.75,
                     keep_gap: float = 0.32) -> list[dict]:
    """Turn an editor's word-level plan into frame-ready segments.

    Plan item: {"src", "from", "to", "skip": [[t0, t1], ...], "role", "framing", "note"}
    `from`/`to` are start times of the first/last kept word; `skip` drops words whose start
    lies in a range. Cut points sit in the quietest spot between a kept and a dropped word
    (never inside a neighbour), and pauses longer than `max_gap` are tightened to `keep_gap`.
    """
    out = []
    for item in plan:
        src = item["src"]
        words = transcripts[src]["words"]
        env = envs.get(src)
        idx = [i for i, w in enumerate(words) if item["from"] - 0.02 <= w["s"] <= item["to"] + 0.02
               and not any(a - 0.02 <= w["s"] <= b + 0.02 for a, b in item.get("skip", []))]
        if not idx:
            raise ValueError(f"Plan vacío: {item}")
        runs_, cur = [], [idx[0]]
        for i in idx[1:]:
            if i == cur[-1] + 1 and words[i]["s"] - words[cur[-1]]["e"] <= max_gap:
                cur.append(i)
            else:
                runs_.append(cur)
                cur = [i]
        runs_.append(cur)
        base = item.get("framing", "medium")
        for k, r in enumerate(runs_):
            first, last = words[r[0]], words[r[-1]]
            prev = words[r[0] - 1] if r[0] > 0 else None
            nxt = words[r[-1] + 1] if r[-1] + 1 < len(words) else None
            end_limit = env.duration if env else last["e"] + 1
            if env:
                t_in = speech_onset(env, first, prev) - PRE_ROLL
                t_out = speech_offset(env, last, nxt, end_limit) + POST_ROLL
                if prev is not None:
                    t_in = max(t_in, speech_offset(env, prev, first, end_limit) + 0.02)
                if nxt is not None:
                    t_out = min(t_out, speech_onset(env, nxt, last) - 0.02)
                t_in = min(t_in, first["s"])
                t_out = min(max(t_out, last["e"]), end_limit)
            else:
                t_in, t_out = first["s"] - 0.12, last["e"] + 0.15
            seg = {"src": src, "in": round(max(0.0, t_in), 3), "out": round(t_out, 3),
                   "role": item.get("role", "explanation"),
                   # alternate the shot on every internal cut so jump cuts read as intentional reframes
                   "framing": base if k % 2 == 0 else ALT_FRAMING.get(base, base),
                   "text": " ".join(words[i]["w"] for i in r), "phrase": item.get("id", "")}
            for k in ("punch_scale", "push"):
                if k in item:
                    seg[k] = item[k]
            # editor-fixed edges (verified by ear/recogniser) win over automatic placement
            if "in" in item and r is runs_[0]:
                seg["in"], seg["lock_in"] = item["in"], True
            if "out" in item and r is runs_[-1]:
                seg["out"], seg["lock_out"] = item["out"], True
            out.append(seg)
    # contiguous material from the same take: cut at one shared point (a pure reframe,
    # no repeated or skipped audio)
    for a, b in zip(out, out[1:]):
        if a["src"] == b["src"] and b["in"] < a["out"] and b["in"] > a["in"]:
            mid = round((a["out"] + b["in"]) / 2, 3)
            a["out"] = b["in"] = mid
    return out
