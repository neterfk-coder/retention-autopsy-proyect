"""What this channel, specifically, gets wrong.

One video's cliff is an anecdote. Forty videos pooled is roughly 4,000 rows,
and that is enough to ask: across everything this creator has published, which
editing decisions actually correlate with people leaving?

The output is deliberately not a model. A fitted regression that says
"coefficient -0.0312" is useless to an editor. Binned effects with a ratio and
a confidence interval -- "shots over 12s cost 2.4x more, +/-0.4" -- are a note
you can tape to your monitor.
"""

from __future__ import annotations

import numpy as np

from ..models import Pattern, SponsorCost, Video
from .align import (
    FEATURE_SPECS,
    NUMERIC_FEATURES,
    body_rows,
    build_frame,
    column,
)
from .stats import find_breakpoint, permutation_test, ratio_bootstrap_ci, spearman

MIN_ROWS = 200
MIN_GROUP = 40


def learn_patterns(videos: list[Video], n_bins: int = 4) -> list[Pattern]:
    rows = body_rows(build_frame(videos))
    if len(rows) < MIN_ROWS:
        return []

    hazard = column(rows, "hazard")
    patterns: list[Pattern] = []

    for feature in NUMERIC_FEATURES:
        values = column(rows, feature)
        if float(np.std(values)) < 1e-9:
            continue

        spec = FEATURE_SPECS[feature]
        edges = list(np.quantile(values, np.linspace(0, 1, n_bins + 1)))
        edges = sorted(set(round(e, 6) for e in edges))
        if len(edges) < 3:
            continue

        bins = []
        for lo, hi in zip(edges, edges[1:]):
            mask = (values >= lo) & (values <= hi if hi == edges[-1] else values < hi)
            if mask.sum() < MIN_GROUP:
                continue
            group = hazard[mask]
            bins.append(
                {
                    "low": float(lo),
                    "high": float(hi),
                    "n": int(mask.sum()),
                    "mean_hazard": float(np.mean(group)),
                    "values": group,
                }
            )
        if len(bins) < 2:
            continue

        # Where does this feature actually start to hurt? Quartile edges are
        # arbitrary; the breakpoint is the number the editor can act on.
        split = find_breakpoint(
            values, hazard, higher_is_worse=spec["higher_is_worse"], min_group=MIN_GROUP
        )
        if split is None:
            continue

        rho = spearman(values, hazard)
        description = spec["describe"].format(edge=split["edge"])
        comparison = "below" if spec["higher_is_worse"] else "above"

        patterns.append(
            Pattern(
                feature=feature,
                title=description[0].upper() + description[1:],
                detail=(
                    f"{description} lose {split['effect']:.2f}x more of the remaining "
                    f"audience per bucket than everything {comparison} that line "
                    f"({split['worse_mean']:.2%} vs {split['better_mean']:.2%} churn, "
                    f"n={split['n_worse']:,} vs {split['n_better']:,}). "
                    f"Rank correlation with churn: {rho:+.2f}."
                ),
                effect=split["effect"],
                ci_low=split["ci_low"],
                ci_high=split["ci_high"],
                p_value=split["p_value"],
                n=len(rows),
                edge=split["edge"],
                bins=[
                    {
                        "low": b["low"],
                        "high": b["high"],
                        "n": b["n"],
                        "mean_hazard": b["mean_hazard"],
                    }
                    for b in bins
                ],
            )
        )

    patterns.sort(key=lambda p: (p.significant, p.effect), reverse=True)
    return patterns


# ---------------------------------------------------------------------------
# Sponsor economics
# ---------------------------------------------------------------------------


def sponsor_costs(videos: list[Video]) -> list[SponsorCost]:
    """What each sponsor read actually cost in audience.

    The baseline is the key idea. Any 60 seconds of video loses some viewers,
    so the raw drop across a sponsor read overstates its cost. We compare it to
    the median hazard of ordinary body content in the same video, compounded
    over the same number of buckets, and report the excess.
    """
    costs: list[SponsorCost] = []
    for video in videos:
        rows = build_frame([video])
        body = body_rows(rows)
        if not body:
            continue
        baseline_hazard = float(np.median([r["hazard"] for r in body]))

        for segment in video.timeline.segments_of("sponsor"):
            inside = [r for r in rows if segment.start <= r["t"] < segment.end]
            if len(inside) < 2:
                continue
            lost = video.retention.audience_between(segment.start, segment.end)
            start_audience = video.retention.at(segment.start)
            baseline_lost = start_audience * (
                1.0 - (1.0 - baseline_hazard) ** len(inside)
            )
            costs.append(
                SponsorCost(
                    video_id=video.video_id,
                    video_title=video.meta.title,
                    start=segment.start,
                    end=segment.end,
                    position=segment.start / max(1e-6, video.meta.duration),
                    audience_lost=lost,
                    viewers_lost=lost * video.meta.views,
                    baseline_lost=baseline_lost,
                )
            )
    costs.sort(key=lambda c: c.excess, reverse=True)
    return costs


def sponsor_placement_rule(costs: list[SponsorCost], split: float = 0.30) -> dict | None:
    """Does moving the read later actually help on this channel?

    Returns None rather than a guess when there is not enough data. A tool that
    invents a placement rule from four sponsor reads is worse than one that
    says it does not know yet.
    """
    if len(costs) < 6:
        return None

    early = np.array([c.excess for c in costs if c.position < split], dtype=float)
    late = np.array([c.excess for c in costs if c.position >= split], dtype=float)
    if early.size < 3 or late.size < 3:
        return None

    early_mean = float(np.mean(early))
    late_mean = float(np.mean(late))
    if late_mean <= 1e-9:
        return None

    effect, lo, hi = ratio_bootstrap_ci(early, late, n_boot=1500)
    p_value = permutation_test(early, late, n_perm=3000)

    return {
        "split": split,
        "early_n": int(early.size),
        "late_n": int(late.size),
        "early_mean": early_mean,
        "late_mean": late_mean,
        "effect": effect,
        "ci_low": lo,
        "ci_high": hi,
        "p_value": p_value,
        "significant": bool(p_value < 0.05),
    }


def sponsor_summary(costs: list[SponsorCost]) -> dict:
    if not costs:
        return {"n": 0}
    excess = np.array([c.excess for c in costs], dtype=float)
    viewers = np.array([c.viewers_lost for c in costs], dtype=float)
    return {
        "n": len(costs),
        "mean_excess": float(np.mean(excess)),
        "median_excess": float(np.median(excess)),
        "total_viewers_lost": float(np.sum(viewers)),
        "worst": costs[0],
    }
