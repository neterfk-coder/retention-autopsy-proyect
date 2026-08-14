"""A synthetic channel with known ground truth.

This is a test fixture, not a demo prop. Nothing here should ever be shown to
anyone as a real result -- every report generated from it is watermarked
SYNTHETIC.

Its job is falsification. We plant specific effects (long shots cost 2.2x,
sponsor reads cost 4x, early reads cost another 1.8x on top), generate
retention curves that obey them plus noise, and then check that the analysis
pipeline recovers those numbers from the curves alone. If `pytest` passes, the
estimator is measuring what it claims to measure. Without this you cannot tell
a working retention analyser from one that prints plausible nonsense.
"""

from __future__ import annotations

import numpy as np

from ..models import (
    EditTimeline,
    RetentionCurve,
    RetentionPoint,
    Segment,
    Video,
    VideoMeta,
    Word,
)

# -- planted ground truth ---------------------------------------------
GROUND_TRUTH = {
    "long_shot_threshold": 12.0,
    "long_shot_multiplier": 2.2,
    "sponsor_multiplier": 4.0,
    "early_sponsor_extra": 1.8,
    "early_sponsor_cutoff": 0.30,
    "silence_multiplier": 1.5,
    "base_hazard": 0.007,
}

TOPICS = [
    "I rebuilt my entire studio for under $2000",
    "Why my last video flopped (honest breakdown)",
    "The camera settings nobody explains properly",
    "I edited for 100 hours so you don't have to",
    "Testing the cheapest lens on the internet",
    "How I write scripts in 40 minutes",
    "My colour grading workflow, start to finish",
    "The lighting mistake I made for three years",
    "Everything wrong with my first 50 videos",
    "A day of shooting, uncut and unglamorous",
    "Why I stopped using presets entirely",
    "The audio chain that fixed my voice",
    "I asked 200 editors how they cut dialogue",
    "Reviewing my subscribers' thumbnails",
    "The one setting that ruined my footage",
    "Building a b-roll library that actually works",
    "How long should your intro really be",
    "I tried editing on a five year old laptop",
    "What 10 million views actually paid me",
    "The retention graph that changed how I edit",
    "Shooting an entire video on one lens",
    "My honest thoughts on faceless channels",
    "Three years of gear, ranked by regret",
    "The pacing trick I stole from documentaries",
]

FILLER = (
    "so the thing is you really want to look at this part carefully because it "
    "matters more than people think and once you see it you cannot unsee it "
    "which is why I keep coming back to this idea again and again in my work"
).split()

SPONSOR_SCRIPT = (
    "before we continue today's sponsor is a company I actually use "
    "you can use code CREATOR for twenty percent off and there is a free trial "
    "with the link in the description so check them out and let me know"
).split()

CTA_SCRIPT = (
    "if you enjoyed this go ahead and subscribe and let me know in the comments "
    "thanks for watching and I will see you next time"
).split()


def _make_cuts(rng: np.random.Generator, duration: float, style: str) -> list[float]:
    """Cut pattern. Some videos are cut fast throughout, some drift slow."""
    fast = style == "fast"
    cuts: list[float] = []
    t = 0.0
    while t < duration:
        if fast:
            gap = rng.gamma(2.0, 1.8) + 0.8
        else:
            gap = rng.gamma(2.4, 3.4) + 1.2
        t += float(gap)
        if t < duration:
            cuts.append(round(t, 2))
    return cuts


def _shot_age_at(cuts: list[float], t: float) -> float:
    prev = 0.0
    for c in cuts:
        if c > t:
            break
        prev = c
    return t - prev


def make_video(
    rng: np.random.Generator,
    index: int,
    n_buckets: int = 100,
) -> Video:
    duration = float(rng.uniform(420, 1150))
    views = int(rng.lognormal(mean=10.2, sigma=0.85))
    style = "fast" if rng.random() < 0.5 else "slow"
    cuts = _make_cuts(rng, duration, style)

    has_sponsor = rng.random() < 0.62
    sponsor_start = 0.0
    sponsor_end = 0.0
    if has_sponsor:
        position = rng.beta(1.6, 2.4)
        sponsor_start = float(np.clip(position * duration, 25.0, duration - 130.0))
        sponsor_end = sponsor_start + float(rng.uniform(38, 82))

    intro_end = min(45.0, duration * 0.12)
    cta_start = duration - float(rng.uniform(18, 40))

    # -- words -------------------------------------------------------
    words: list[Word] = []
    t = 0.0
    while t < duration:
        if has_sponsor and sponsor_start <= t < sponsor_end:
            script, rate = SPONSOR_SCRIPT, 2.1
        elif t >= cta_start:
            script, rate = CTA_SCRIPT, 2.6
        else:
            script, rate = FILLER, (3.0 if style == "fast" else 2.3)
        rate *= float(rng.uniform(0.82, 1.18))
        token = script[rng.integers(0, len(script))]
        step = 1.0 / max(0.6, rate)
        words.append(Word(token, t, t + step * 0.9))
        t += step
        if rng.random() < 0.035:
            t += float(rng.uniform(0.6, 2.4))  # a pause

    # -- audio series ------------------------------------------------
    sample_hz = 2.0
    n_samples = int(duration * sample_hz) + 1
    word_starts = np.array([w.start for w in words])
    loudness: list[float] = []
    silence: list[float] = []
    for i in range(n_samples):
        ts = i / sample_hz
        nearby = int(np.sum((word_starts >= ts - 0.5) & (word_starts < ts + 0.5)))
        if nearby == 0:
            loudness.append(float(rng.uniform(-52, -44)))
            silence.append(1.0)
        else:
            base = -16.0 if not (has_sponsor and sponsor_start <= ts < sponsor_end) else -19.5
            loudness.append(base + float(rng.normal(0, 1.4)))
            silence.append(0.0)

    segments: list[Segment] = [Segment("intro", 0.0, intro_end, label="intro")]
    if has_sponsor:
        segments.append(
            Segment("sponsor", sponsor_start, sponsor_end, confidence=0.9, label="sponsor read")
        )
    segments.append(Segment("cta", cta_start, duration, confidence=0.8, label="outro cta"))

    timeline = EditTimeline(
        video_id=f"SYN{index:03d}",
        duration=duration,
        cuts=cuts,
        words=words,
        segments=segments,
        loudness=loudness,
        silence=silence,
        sample_hz=sample_hz,
    )

    # -- retention curve obeying the planted rules -------------------
    gt = GROUND_TRUTH
    watch = 1.0
    points = [RetentionPoint(ratio=0.0, watch_ratio=1.0)]
    channel_noise = float(rng.uniform(0.85, 1.2))

    for i in range(1, n_buckets + 1):
        ratio = i / n_buckets
        ts = ratio * duration
        hazard = gt["base_hazard"] * channel_noise

        if ts < intro_end:
            hazard *= 4.5 * (1.0 - 0.6 * ts / max(1.0, intro_end))
        if _shot_age_at(cuts, ts) >= gt["long_shot_threshold"]:
            hazard *= gt["long_shot_multiplier"]
        if timeline.silence_at(ts) > 0.5:
            hazard *= gt["silence_multiplier"]
        if has_sponsor and sponsor_start <= ts < sponsor_end:
            hazard *= gt["sponsor_multiplier"]
            if sponsor_start / duration < gt["early_sponsor_cutoff"]:
                hazard *= gt["early_sponsor_extra"]
        if ts >= cta_start:
            hazard *= 2.0

        hazard *= float(np.exp(rng.normal(0, 0.22)))
        hazard = float(np.clip(hazard, 0.0, 0.35))
        watch = max(0.02, watch * (1.0 - hazard))
        points.append(RetentionPoint(ratio=ratio, watch_ratio=watch))

    meta = VideoMeta(
        video_id=f"SYN{index:03d}",
        title=TOPICS[index % len(TOPICS)],
        duration=duration,
        published_at=f"2025-{(index % 12) + 1:02d}-15T12:00:00Z",
        views=views,
    )
    curve = RetentionCurve(video_id=meta.video_id, duration=duration, points=points)
    return Video(meta=meta, retention=curve, timeline=timeline)


def make_channel(n_videos: int = 24, seed: int = 7) -> list[Video]:
    rng = np.random.default_rng(seed)
    return [make_video(rng, i) for i in range(n_videos)]
