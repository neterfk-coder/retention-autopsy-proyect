"""Per-video diagnosis: find the cliffs, then say what was on screen.

A cliff is a run of consecutive retention buckets whose hazard sits above a
robust threshold computed *within that video*. Per-video thresholds matter --
a talking-head essay and a fast vlog have completely different baseline churn,
and a global threshold would report every essay as one long catastrophe.
"""

from __future__ import annotations

import numpy as np

from ..models import Finding, Video, timecode
from .align import bucket_seconds, build_rows
from .stats import robust_threshold

SKIP_HEAD = 15.0

#: Attribution rules. Each returns a plain-language cause when its condition
#: holds at the cliff. Order matters: the first two are the loudest signals.
#:
#: Every rule past `sponsor` is gated on the underlying series actually
#: existing (`ctx["has_cuts"]`, `has_words`, `has_audio`). `scan` alone leaves
#: a video with no cuts and no words -- the edit side only comes from running
#: `autopsy edit` against the file -- and without that gate every row reads as
#: "0.0 cuts per 10s" or "0.0 words/s" because the placeholder value and the
#: context computed from other placeholder values always agree. That is a
#: confident-sounding diagnosis manufactured from missing data, not a real one.
CAUSE_RULES = [
    (
        "sponsor",
        lambda r, ctx: r["in_sponsor"] > 0.5,
        "a sponsor read is playing",
    ),
    (
        "long_shot",
        lambda r, ctx: ctx["has_cuts"] and r["shot_age"] >= max(8.0, ctx["shot_age_p80"]),
        "the same shot has been held for {shot_age:.0f}s with no cut",
    ),
    (
        "slow_cutting",
        lambda r, ctx: (
            ctx["has_cuts"] and r["cut_rate"] <= ctx["cut_rate_p20"] and r["cut_rate"] < 1.0
        ),
        "cutting has slowed to {cut_rate:.1f} cuts per 10s",
    ),
    (
        "dead_air",
        lambda r, ctx: ctx["has_audio"] and r["silence"] >= 0.45,
        "{silence:.0%} of the window has no speech",
    ),
    (
        "slow_speech",
        lambda r, ctx: ctx["has_words"] and r["speech_rate"] <= ctx["speech_rate_p20"],
        "delivery has dropped to {speech_rate:.1f} words/s",
    ),
    (
        "mix_drop",
        lambda r, ctx: ctx["has_audio"] and r["loudness_delta"] <= -4.0,
        "the mix drops {loudness_delta:.1f} LU",
    ),
    (
        "cta_early",
        lambda r, ctx: r["in_cta"] > 0.5 and r["position"] < 0.75,
        "an end-card style call to action lands before the video is over",
    ),
]


def _context(rows: list[dict], timeline) -> dict:
    def pct(name: str, q: float) -> float:
        values = np.array([r[name] for r in rows], dtype=float)
        return float(np.quantile(values, q)) if values.size else 0.0

    return {
        "shot_age_p80": pct("shot_age", 0.80),
        "cut_rate_p20": pct("cut_rate", 0.20),
        "speech_rate_p20": pct("speech_rate", 0.20),
        "has_cuts": bool(timeline.cuts),
        "has_words": bool(timeline.words),
        "has_audio": bool(timeline.loudness),
    }


def diagnose(video: Video, k: float = 2.5, max_findings: int = 6) -> list[Finding]:
    rows = build_rows(video, skip_head=SKIP_HEAD)
    if len(rows) < 8:
        return []

    hazards = np.array([r["hazard"] for r in rows], dtype=float)
    threshold = robust_threshold(hazards, k=k, floor=0.012)
    ctx = _context(rows, video.timeline)
    step = bucket_seconds(video)

    findings: list[Finding] = []
    run: list[dict] = []

    def flush(run_rows: list[dict]) -> None:
        if not run_rows:
            return
        # never report a cliff starting before the analysis window opens
        start = max(SKIP_HEAD, run_rows[0]["t"] - step)
        end = run_rows[-1]["t"]
        peak = max(run_rows, key=lambda r: r["hazard"])
        lost = video.retention.audience_between(start, end)
        combined = 1.0 - float(np.prod([1.0 - r["hazard"] for r in run_rows]))

        causes: list[str] = []
        for _, condition, template in CAUSE_RULES:
            if condition(peak, ctx):
                causes.append(template.format(**peak))
        if not causes:
            if not (ctx["has_cuts"] or ctx["has_words"] or ctx["has_audio"]):
                causes.append(
                    "no edit data for this video yet -- run 'autopsy edit "
                    f"--video-id {video.video_id} --file <path>' to diagnose the cause"
                )
            else:
                causes.append("no single edit signal stands out -- worth watching by eye")

        quote = video.timeline.text_between(max(0.0, start - 2.0), end + 1.0)
        if len(quote) > 180:
            quote = quote[:177].rsplit(" ", 1)[0] + "..."

        findings.append(
            Finding(
                video_id=video.video_id,
                video_title=video.meta.title,
                start=start,
                end=end,
                hazard=combined,
                audience_lost=lost,
                viewers_lost=lost * video.meta.views,
                causes=causes,
                evidence={
                    "shot_age": peak["shot_age"],
                    "shot_length": peak["shot_length"],
                    "cut_rate": peak["cut_rate"],
                    "speech_rate": peak["speech_rate"],
                    "silence": peak["silence"],
                    "loudness_delta": peak["loudness_delta"],
                    "position": peak["position"],
                },
                quote=quote,
            )
        )

    for row in rows:
        if row["hazard"] >= threshold:
            run.append(row)
        else:
            flush(run)
            run = []
    flush(run)

    findings.sort(key=lambda f: f.viewers_lost, reverse=True)
    return findings[:max_findings]


def diagnose_all(videos: list[Video], **kwargs) -> list[Finding]:
    findings: list[Finding] = []
    for video in videos:
        findings.extend(diagnose(video, **kwargs))
    findings.sort(key=lambda f: f.viewers_lost, reverse=True)
    return findings


def describe(finding: Finding) -> str:
    """One line an editor can act on."""
    window = f"{timecode(finding.start)}-{timecode(finding.end)}"
    causes = "; ".join(finding.causes)
    return (
        f"{window}  lost {finding.hazard:.1%} of remaining viewers "
        f"(~{finding.viewers_lost:,.0f} people) -- {causes}"
    )
