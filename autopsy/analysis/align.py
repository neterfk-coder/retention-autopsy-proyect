"""The join.

YouTube Studio shows a retention curve with no idea what is in the video.
An NLE shows the edit with no idea who was watching. This module puts a row in
one table for every retention bucket, carrying both.

One row = one bucket of one video:

    video_id  t      hazard   shot_age  cut_rate  speech_rate  in_sponsor ...
    dQw4      182.4  0.031    11.2      1.0       2.4          0
"""

from __future__ import annotations

import numpy as np

from ..models import Video

#: Features extracted at each retention bucket. The `higher_is_worse` flag is
#: only used to phrase findings in plain language.
FEATURE_SPECS: dict[str, dict] = {
    "shot_age": {
        "label": "seconds since the last cut",
        "unit": "s",
        "higher_is_worse": True,
        "describe": "shots held past {edge:.0f}s without a cut",
    },
    "cut_rate": {
        "label": "cuts per 10s",
        "unit": "/10s",
        "higher_is_worse": False,
        "describe": "stretches cut slower than {edge:.1f} times per 10s",
    },
    "speech_rate": {
        "label": "words per second",
        "unit": "w/s",
        "higher_is_worse": False,
        "describe": "passages spoken slower than {edge:.1f} words/s",
    },
    "silence": {
        "label": "share of the window with no speech",
        "unit": "",
        "higher_is_worse": True,
        "describe": "windows more than {edge:.0%} silent",
    },
    "loudness_delta": {
        "label": "loudness change vs 8s earlier",
        "unit": "LU",
        "higher_is_worse": False,
        "describe": "moments where the mix drops more than {edge:.1f} LU",
    },
}

NUMERIC_FEATURES = list(FEATURE_SPECS)
SEGMENT_FLAGS = ["in_sponsor", "in_intro", "in_cta"]


def build_rows(video: Video, skip_head: float = 15.0) -> list[dict]:
    """One row per retention bucket.

    `skip_head` drops the opening seconds. Everyone loses a large share of the
    audience there for reasons that have nothing to do with the edit -- wrong
    click, autoplay, thumbnail mismatch -- and leaving it in makes every
    feature look catastrophic simply because it co-occurs with second zero.
    """
    curve = video.retention
    timeline = video.timeline
    hazards = curve.hazard()
    times = curve.times
    duration = max(1e-6, video.meta.duration)
    rows: list[dict] = []

    for i, (t, hz) in enumerate(zip(times, hazards)):
        if i == 0 or t < skip_head or t > duration - 1.0:
            continue
        segment = timeline.segment_at(t)
        rows.append(
            {
                "video_id": video.video_id,
                "video_title": video.meta.title,
                "index": i,
                "t": float(t),
                "position": float(t / duration),
                "hazard": float(hz),
                "watch_ratio": float(curve.values[i]),
                "views": float(video.meta.views),
                "shot_age": float(timeline.shot_age(t)),
                "shot_length": float(timeline.shot_length(t)),
                "cut_rate": float(timeline.cut_rate(t)),
                "speech_rate": float(timeline.speech_rate(t)),
                "silence": float(timeline.silence_at(t)),
                "loudness_delta": float(timeline.loudness_delta(t)),
                "in_sponsor": 1.0 if segment and segment.kind == "sponsor" else 0.0,
                "in_intro": 1.0 if segment and segment.kind == "intro" else 0.0,
                "in_cta": 1.0 if segment and segment.kind == "cta" else 0.0,
                "segment": segment.kind if segment else "body",
            }
        )
    return rows


def build_frame(videos: list[Video], skip_head: float = 15.0) -> list[dict]:
    rows: list[dict] = []
    for video in videos:
        rows.extend(build_rows(video, skip_head=skip_head))
    return rows


def column(rows: list[dict], name: str) -> np.ndarray:
    return np.array([r[name] for r in rows], dtype=float)


def body_rows(rows: list[dict]) -> list[dict]:
    """Rows outside sponsor reads, intros and end cards.

    Sponsor reads have their own dedicated analysis, and they are also the
    single strongest confound in the data: they are quiet, slowly cut, and
    people leave. Leaving them in would let a sponsor read masquerade as
    evidence that slow cutting is fatal.
    """
    return [r for r in rows if r["segment"] == "body"]


def bucket_seconds(video: Video) -> float:
    """Wall-clock seconds covered by one retention bucket."""
    n = max(1, len(video.retention.points) - 1)
    return video.meta.duration / n
