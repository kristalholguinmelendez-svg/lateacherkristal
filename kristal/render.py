"""Render: cut list + framing/zoom + look + restored audio + graphics/captions -> MP4 (H.264/AAC).

Video frames are reframed in Python (sub-pixel warpAffine) so zooms are smooth and the
face stays framed; FFmpeg decodes, corrects colour, burns ASS and encodes. One pass
writes both deliverables: with captions and without captions (graphics kept).
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np

from . import audio as A
from .faces import FaceTrack
from .timeline import Timeline
from .util import log, run

OUT_W, OUT_H = 1080, 1920


def ease_out_cubic(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_in_out_sine(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return -(math.cos(math.pi * x) - 1) / 2


class Framer:
    """Virtual camera operator: per-clip framing, punch-ins, gentle push, face-safe crop."""

    def __init__(self, brand: dict, vinfo: dict, face: FaceTrack, zooms: list[dict], src: str):
        f = brand["framing"]
        self.f = f
        self.face_y = brand["layout"]["face_center_y"]
        self.W, self.H = vinfo["width"], vinfo["height"]
        if self.W / self.H > 9 / 16:
            self.bw, self.bh = self.H * 9 / 16, float(self.H)
        else:
            self.bw, self.bh = float(self.W), self.W * 16 / 9
        # never upscale real pixels beyond max_upscale (avoids soft, blocky close-ups)
        self.zcap = max(1.0, f["max_upscale"] * self.bw / OUT_W)
        self.face = face
        self.punches = sorted(z["at"] for z in zooms if z.get("src") == src and z.get("kind", "punch") == "punch")
        self.warned = False

    def level(self, name: str | float) -> float:
        return float(name) if isinstance(name, (int, float)) else float(self.f.get(name, 1.0))

    def zoom(self, seg: dict, t_src: float, t_rel: float, clip_dur: float) -> float:
        z = self.level(seg.get("framing", "medium"))
        # slow push-in on longer open shots keeps them alive without being noticeable
        if clip_dur > 2.5 and seg.get("framing") in ("wide", "medium") and seg.get("push", True):
            z *= 1 + 0.03 * (t_rel / clip_dur)
        p = self.f["punch"] * seg.get("punch_scale", 1.0)
        pin, pout, hold = self.f["punch_in_ms"] / 1000, self.f["punch_out_ms"] / 1000, 0.9
        for at in self.punches:
            d = t_src - at
            if 0 <= d < pin:
                z += p * ease_out_cubic(d / pin)
            elif pin <= d < pin + hold:
                z += p
            elif pin + hold <= d < pin + hold + pout:
                z += p * (1 - ease_in_out_sine((d - pin - hold) / pout))
        if z > self.zcap:
            if not self.warned:
                log(f"Zoom limitado a {self.zcap:.2f}× para no perder resolución (fuente {self.W}×{self.H}).")
                self.warned = True
            z = self.zcap
        return z

    def matrix(self, z: float, cx: float, cy: float, scale_pre: float) -> np.ndarray:
        """Affine matrix mapping the (pre-scaled) source frame into the 1080x1920 output."""
        W, H = self.W * scale_pre, self.H * scale_pre
        ww, wh = self.bw * scale_pre / z, self.bh * scale_pre / z
        fx, fy = cx * W, cy * H
        x0 = min(max(fx - ww / 2, 0.0), W - ww)
        y0 = min(max(fy - self.face_y * wh, 0.0), H - wh)
        s = OUT_W / ww
        return np.array([[s, 0, -x0 * s], [0, s, -y0 * s]], dtype=np.float32)


# ---------------------------------------------------------------- audio

def build_audio(tl: Timeline, paths: dict, brand: dict, project: dict, hints: dict, work: Path) -> tuple[Path, dict]:
    """Cut, restore and loudness-normalise the voice. Returns (wav path, applied-processing log)."""
    acfg = project.get("audio", {})
    used = sorted({c.src for c in tl.clips})
    inputs, fc, applied = [], [], {"fuentes": {}}
    edge = brand["audio"]["edge_fade_ms"] / 1000
    labels: dict[str, list[str]] = {}
    for k, src in enumerate(used):
        inputs += ["-i", str(paths[src])]
        h = hints[src]
        pre, desc = A.restoration_chain(brand, h["noise_db"], h["speech_db"] - h["noise_db"], acfg, h.get("hum_hz"))
        applied["fuentes"][src] = desc
        n = sum(1 for c in tl.clips if c.src == src)
        labels[src] = [f"s{k}_{j}" for j in range(n)]
        outs = "".join(f"[{lab}]" for lab in labels[src])
        fc.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,{pre},asplit={n}{outs}" if n > 1
                  else f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,{pre}{outs}")
    counters = {src: 0 for src in used}
    seq = []
    for i, c in enumerate(tl.clips):
        a, b = tl.clip_src_range(c)
        lab = labels[c.src][counters[c.src]]
        counters[c.src] += 1
        d = b - a
        fc.append(f"[{lab}]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS,"
                  f"afade=t=in:d={edge}:curve=qsin,afade=t=out:st={max(0, d - edge):.6f}:d={edge}:curve=qsin[c{i}]")
        seq.append(f"[c{i}]")
    post, pdesc = A.voice_chain(brand, acfg)
    applied.update(pdesc)
    fc.append(f"{''.join(seq)}concat=n={len(seq)}:v=0:a=1,{post},apad=whole_dur={tl.duration:.6f}[voice]")
    last = "[voice]"
    music = acfg.get("music")
    if music:
        mi = len(used)
        inputs += ["-stream_loop", "-1", "-i", str(Path(project["_dir"]) / music["file"])]
        db = music.get("db", -26)
        fc.append(f"[voice]asplit=2[v1][key]")
        fc.append(f"[{mi}:a]aresample=48000,aformat=channel_layouts=mono,volume={db}dB,"
                  f"atrim=0:{tl.duration:.3f}[m0]")
        fc.append("[m0][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[mduck]")
        fc.append("[v1][mduck]amix=inputs=2:duration=first:normalize=0[mixed]")
        last = "[mixed]"
        applied["musica"] = f"{music['file']} a {db} dB con ducking bajo la voz"
    stage1 = work / "voice_stage1.wav"
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", last,
         "-t", f"{tl.duration:.6f}", "-c:a", "pcm_f32le", str(stage1)])
    lufs, tp = brand["audio"]["target_lufs"], brand["audio"]["true_peak"]
    meas = A.measure_loudness(stage1)
    out = work / "voice_final.wav"
    upmix = "pan=stereo|c0=c0|c1=c0"  # dual mono without the -3 dB pan law
    base = lufs - float(meas["input_i"])

    def master(g: float) -> float:
        run(["ffmpeg", "-v", "error", "-y", "-i", str(stage1), "-af",
             f"{upmix},{A.master_chain(g, tp)},aformat=sample_fmts=s16", "-c:a", "pcm_s16le", str(out)])
        return float(A.measure_loudness(out)["input_i"])

    # Limiting shaves loudness off peaky speech: solve gain -> loudness with the secant method.
    g0, l0 = base - 3.0, master(base - 3.0)   # dual-mono reads ~3 LU louder than mono
    g1 = g0 + (lufs - l0)
    l1 = master(g1)
    for _ in range(5):
        if abs(l1 - lufs) <= 0.2 or abs(l1 - l0) < 1e-3:
            break
        g2 = min(g1 + (lufs - l1) * (g1 - g0) / (l1 - l0), base + 3.0)  # ≤ 6 dB of limiting
        g0, l0, g1 = g1, l1, g2
        l1 = master(g1)
    applied["sonoridad"] = (f"ganancia {g1:+.1f} dB + limitador de picos (4× sobremuestreo) → {l1:.1f} LUFS, "
                            f"techo {tp} dBTP (voz antes: {meas['input_i']} LUFS, pico {meas['input_tp']} dBTP)")
    if abs(l1 - lufs) > 0.5:
        log(f"Aviso: la voz tiene picos muy marcados; sonoridad final {l1:.1f} LUFS sin sobre-limitar.")
    applied["bordes"] = f"micro-fundidos de {int(edge * 1000)} ms en cada corte (sin clics)"
    return out, applied


# ---------------------------------------------------------------- video

def render(tl: Timeline, paths: dict, infos: dict, faces: dict, looks: dict, zooms: list[dict], brand: dict,
           audio_wav: Path, graphics_ass: Path, captions_ass: Path | None, out_main: Path, out_clean: Path,
           preview: bool = False) -> None:
    fps = tl.fps
    framers = {src: Framer(brand, infos[src]["video"], faces[src], zooms, src) for src in paths}
    fontsdir = Path(__file__).resolve().parent.parent / brand["fonts"]["dir"]
    sharpen = brand["look"]["sharpen"]

    def ass_filter(p: Path) -> str:
        esc = str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        fd = str(fontsdir).replace(":", r"\:")
        return f"ass='{esc}':fontsdir='{fd}'"

    vf = (f"[0:v]unsharp=5:5:{sharpen}:5:5:0,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
          f"{ass_filter(graphics_ass)}")
    if captions_ass:
        vf += f",split=2[clean][g2];[g2]{ass_filter(captions_ass)}[main]"
    else:
        vf += ",split=2[clean][main]"
    x264 = (["-preset", "veryfast", "-crf", "23"] if preview else
            ["-preset", "slow", "-crf", "17", "-profile:v", "high", "-level:v", "4.2"])
    gop = str(int(round(float(fps) * 2)))
    common = ["-c:v", "libx264", *x264, "-pix_fmt", "yuv420p", "-g", gop, "-bf", "2",
              "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
              "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]
    enc_cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}",
               "-r", f"{fps.numerator}/{fps.denominator}", "-i", "-", "-i", str(audio_wav),
               "-filter_complex", vf,
               "-map", "[main]", "-map", "1:a", *common, "-shortest", str(out_main),
               "-map", "[clean]", "-map", "1:a", *common, "-shortest", str(out_clean)]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)
    import cv2

    try:
        for c in tl.clips:
            src = c.src
            fr = framers[src]
            vinfo = infos[src]["video"]
            a, b = tl.clip_src_range(c)
            seg = c.seg
            dur = b - a
            # pre-scale so the widest window maps ~1:1 to output (less data, no needless upscaling)
            zmax = max(fr.zoom(seg, a + k * dur / 12, k * dur / 12, dur) for k in range(13))
            zmax = max(zmax, max((fr.zoom(seg, p + 0.3, p + 0.3 - a, dur) for p in fr.punches if a <= p < b), default=0))
            s_pre = min(1.0, OUT_W * zmax / fr.bw * 1.02)
            pw = int(round(vinfo["width"] * s_pre / 2) * 2)
            ph = int(round(vinfo["height"] * s_pre / 2) * 2)
            s_pre_x = pw / vinfo["width"]
            look = looks[src]
            chain = f"fps={fps.numerator}/{fps.denominator}" + (f",{look}" if look else "") + \
                    f",scale={pw}:{ph}:flags=lanczos"
            dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{a:.6f}", "-i", str(paths[src]),
                                    "-frames:v", str(c.frames), "-vf", chain, "-an", "-f", "rawvideo",
                                    "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
            fbytes = pw * ph * 3
            steady = fr.face.steady(a, b)
            moving = fr.face.drift(a, b) > 0.12
            sm = steady
            last = None
            for k in range(c.frames):
                raw = dec.stdout.read(fbytes)
                if len(raw) == fbytes:
                    last = np.frombuffer(raw, dtype=np.uint8).reshape(ph, pw, 3)
                elif last is None:
                    last = np.zeros((ph, pw, 3), np.uint8)
                t_rel = k / float(fps)
                t_src = a + t_rel
                if moving:  # follow smoothly (≈0.7 s time constant), never snap
                    cur = fr.face.at(t_src)
                    alpha = 1 - math.exp(-1 / (0.7 * float(fps)))
                    sm = tuple(sm[i] + alpha * (cur[i] - sm[i]) for i in range(3))
                z = fr.zoom(seg, t_src, t_rel, dur)
                M = fr.matrix(z, sm[0], sm[1], s_pre_x)
                interp = cv2.INTER_CUBIC if M[0, 0] >= 1 else cv2.INTER_LINEAR
                frame = cv2.warpAffine(last, M, (OUT_W, OUT_H), flags=interp, borderMode=cv2.BORDER_REPLICATE)
                enc.stdin.write(frame.tobytes())
            dec.stdout.close()
            dec.wait()
            log(f"  clip {c.index + 1}/{len(tl.clips)} {src} {a:.2f}-{b:.2f}s [{seg.get('framing')}]")
        for _ in range(tl.hold_frames):
            enc.stdin.write(frame.tobytes())
    finally:
        enc.stdin.close()
        rc = enc.wait()
    if rc != 0:
        raise RuntimeError("El codificador FFmpeg falló.")
