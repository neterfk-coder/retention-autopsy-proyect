"""Reconstruct the edit from the video file.

Two independent paths for shot detection, so the tool degrades instead of
failing:

1. PySceneDetect (optional extra) -- content-aware, handles fades.
2. ffmpeg's `select='gt(scene,threshold)'` filter -- ships with ffmpeg, no
   Python video stack needed.

Audio features come from ffmpeg's `astats` on short windows, which is fast and
avoids pulling in librosa for what is ultimately an RMS envelope.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

SCENE_THRESHOLD = 0.30
_TIME_RE = re.compile(r"pts_time:([0-9.]+)")


class FFmpegMissing(RuntimeError):
    pass


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise FFmpegMissing(
            "ffmpeg and ffprobe must be on PATH. macOS: brew install ffmpeg. "
            "Debian/Ubuntu: sudo apt install ffmpeg."
        )


def probe_duration(path: str | Path) -> float:
    """Container duration in seconds, or 0.0 if the file does not report one.

    Returning 0.0 rather than raising is what lets callers fall back to the
    duration YouTube already gave them. A missing ffmpeg still raises, because
    that is actionable and the rest of the command cannot run without it.
    """
    _require_ffmpeg()
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return 0.0
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return 0.0


def detect_cuts_ffmpeg(path: str | Path, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """Shot boundaries via ffmpeg's scene score. Single pass, no decode to disk."""
    _require_ffmpeg()
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-filter:v", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    cuts = [float(m) for m in _TIME_RE.findall(result.stderr)]
    return sorted(set(cuts))


def detect_cuts_scenedetect(path: str | Path, threshold: float = 27.0) -> list[float]:
    """Shot boundaries via PySceneDetect. Better on fades and dissolves."""
    from scenedetect import ContentDetector, SceneManager, open_video  # type: ignore

    video = open_video(str(path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video, show_progress=False)
    return [scene[0].get_seconds() for scene in manager.get_scene_list()][1:]


def detect_cuts(path: str | Path, prefer: str = "auto") -> list[float]:
    if prefer in ("auto", "scenedetect"):
        try:
            return detect_cuts_scenedetect(path)
        except ImportError:
            if prefer == "scenedetect":
                raise
        except Exception:
            if prefer == "scenedetect":
                raise
    return detect_cuts_ffmpeg(path)


def audio_series(
    path: str | Path, duration: float, sample_hz: float = 2.0
) -> tuple[list[float], list[float]]:
    """(loudness dBFS, silence share) sampled on a fixed grid.

    Returns two series aligned to `sample_hz`. Loudness is RMS in dBFS;
    silence share is the fraction of each window below -45 dBFS.
    """
    _require_ffmpeg()
    window = 1.0 / sample_hz
    n = max(1, int(math.ceil(duration * sample_hz)))

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-map", "0:a:0",
            "-af", f"astats=metadata=1:reset={max(1, int(window * 30))},"
                   f"ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )

    levels: list[float] = []
    for line in result.stderr.splitlines():
        if "RMS_level=" in line:
            try:
                levels.append(float(line.split("RMS_level=")[1].strip()))
            except (ValueError, IndexError):
                continue

    if not levels:
        return [-18.0] * n, [0.0] * n

    loudness = [levels[min(int(i * len(levels) / n), len(levels) - 1)] for i in range(n)]
    loudness = [v if math.isfinite(v) else -60.0 for v in loudness]
    silence = [1.0 if v < -45.0 else 0.0 for v in loudness]
    return loudness, silence


def has_music_bed(loudness: list[float], silence: list[float]) -> bool:
    """Heuristic: a continuous bed means almost nothing reads as silent."""
    if not silence:
        return False
    return sum(silence) / len(silence) < 0.02
