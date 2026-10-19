"""Shared helpers: paths, subprocess, ffprobe, JSON and colour conversion."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BRAND_FILE = REPO / "brand" / "style.json"


def log(msg: str) -> None:
    print(f"[kristal] {msg}", file=sys.stderr, flush=True)


def require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise SystemExit(f"Falta la herramienta '{tool}'. Instálala y vuelve a intentarlo.")
    return path


def run(cmd: list[str], *, capture: bool = False, check: bool = True, **kw) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    res = subprocess.run(cmd, capture_output=capture, text=capture, **kw)
    if check and res.returncode != 0:
        err = res.stderr[-3000:] if capture and res.stderr else ""
        raise RuntimeError(f"Command failed ({res.returncode}): {' '.join(cmd[:6])} ...\n{err}")
    return res


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_brand() -> dict:
    return read_json(BRAND_FILE)


def font_path(brand: dict, key: str) -> Path:
    return REPO / brand["fonts"]["dir"] / brand["fonts"][f"{key}_file"]


# ---------------------------------------------------------------- media probe

def probe(path: Path) -> dict:
    """Return a compact description of a media file using ffprobe."""
    require("ffprobe")
    res = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
               "-show_streams", str(path)], capture=True)
    raw = json.loads(res.stdout)
    info: dict = {"path": str(path), "duration": float(raw["format"].get("duration", 0.0)),
                  "video": None, "audio": None}
    for s in raw["streams"]:
        if s["codec_type"] == "video" and info["video"] is None and s.get("disposition", {}).get("attached_pic", 0) == 0:
            w, h = int(s["width"]), int(s["height"])
            rot = 0
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(sd["rotation"])
            rot = int(s.get("tags", {}).get("rotate", rot))
            if abs(rot) % 180 == 90:  # phones store portrait video as rotated landscape
                w, h = h, w
            fps = Fraction(s.get("avg_frame_rate") or s.get("r_frame_rate") or "30/1")
            if fps == 0:
                fps = Fraction(s.get("r_frame_rate", "30/1"))
            info["video"] = {"codec": s["codec_name"], "width": w, "height": h,
                             "fps": f"{fps.numerator}/{fps.denominator}", "fps_float": float(fps),
                             "pix_fmt": s.get("pix_fmt"), "rotation": rot,
                             "color_transfer": s.get("color_transfer"),
                             "frames": int(s.get("nb_frames", 0) or 0)}
        elif s["codec_type"] == "audio" and info["audio"] is None:
            info["audio"] = {"codec": s["codec_name"], "sample_rate": int(s["sample_rate"]),
                             "channels": int(s["channels"])}
    return info


def is_hdr(vinfo: dict | None) -> bool:
    return bool(vinfo) and vinfo.get("color_transfer") in ("smpte2084", "arib-std-b67")


# ---------------------------------------------------------------- colours

def hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def ass_color(hex_color: str, alpha: float = 1.0) -> str:
    """'#RRGGBB' + opacity (1 = opaque) -> ASS '&HAABBGGRR'."""
    r, g, b = hex_rgb(hex_color)
    a = int(round((1.0 - alpha) * 255))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def ass_rgb(hex_color: str) -> str:
    """'#RRGGBB' -> ASS inline colour '&HBBGGRR&'."""
    r, g, b = hex_rgb(hex_color)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def ass_alpha(alpha: float) -> str:
    return f"&H{int(round((1.0 - alpha) * 255)):02X}&"


def fmt_ts(t: float) -> str:
    """Seconds -> 'MM:SS.mmm' for human-readable logs."""
    t = max(0.0, t)
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:06.3f}"
