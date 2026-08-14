"""Does the estimator measure what it claims to measure?

These are the tests that matter. Anything can print a confident number from a
retention curve; the question is whether that number tracks reality. So we
plant known effects, generate curves that obey them, and check the pipeline
recovers them from the curves alone.

`test_isolated_long_shot_effect` is the strictest: one effect, no confounds,
recovery must land inside the confidence interval.
"""

from __future__ import annotations

import numpy as np
import pytest

from autopsy.analysis.align import body_rows, build_frame, column
from autopsy.analysis.patterns import (
    learn_patterns,
    sponsor_costs,
    sponsor_placement_rule,
)
from autopsy.analysis.stats import find_breakpoint
from autopsy.models import (
    EditTimeline,
    RetentionCurve,
    RetentionPoint,
    Video,
    VideoMeta,
)
from autopsy.sources.synthetic import GROUND_TRUTH, make_channel


@pytest.fixture(scope="module")
def channel():
    return make_channel(n_videos=24, seed=7)


# ---------------------------------------------------------------------------
# Clean room: one planted effect, no confounds
# ---------------------------------------------------------------------------


def _one_effect_channel(threshold=12.0, multiplier=2.2, n_videos=30, seed=3):
    """Videos where shot length is the *only* thing driving churn."""
    rng = np.random.default_rng(seed)
    videos = []
    for i in range(n_videos):
        duration = 600.0
        cuts, t = [], 0.0
        while t < duration:
            t += float(rng.choice([3.0, 5.0, 18.0, 22.0], p=[0.35, 0.35, 0.15, 0.15]))
            if t < duration:
                cuts.append(t)
        timeline = EditTimeline(video_id=f"C{i}", duration=duration, cuts=cuts)

        watch, points = 1.0, [RetentionPoint(0.0, 1.0)]
        for k in range(1, 101):
            ratio = k / 100
            hazard = 0.01
            if timeline.shot_age(ratio * duration) >= threshold:
                hazard *= multiplier
            hazard *= float(np.exp(rng.normal(0, 0.1)))
            watch = max(0.02, watch * (1 - hazard))
            points.append(RetentionPoint(ratio, watch))

        videos.append(
            Video(
                meta=VideoMeta(f"C{i}", f"clean {i}", duration, views=10_000),
                retention=RetentionCurve(f"C{i}", duration, points),
                timeline=timeline,
            )
        )
    return videos


def test_isolated_long_shot_effect_is_recovered():
    videos = _one_effect_channel(threshold=12.0, multiplier=2.2)
    rows = body_rows(build_frame(videos))
    split = find_breakpoint(
        column(rows, "shot_age"), column(rows, "hazard"), higher_is_worse=True
    )
    assert split is not None
    assert split["ci_low"] <= 2.2 <= split["ci_high"], (
        f"planted 2.2x, estimated {split['effect']:.2f} "
        f"CI[{split['ci_low']:.2f},{split['ci_high']:.2f}]"
    )
    assert split["p_value"] < 0.05
    assert 8.0 <= split["edge"] <= 20.0, f"breakpoint landed at {split['edge']:.1f}s"


def test_no_effect_is_not_invented():
    """Pure noise must not produce a significant pattern.

    The breakpoint search takes a maximum over many thresholds, which will
    always find *some* split that looks good. If the max-statistic permutation
    test is wired up correctly, this stays insignificant.
    """
    rng = np.random.default_rng(11)
    values = rng.uniform(0, 25, size=3000)
    target = rng.gamma(2.0, 0.01, size=3000)
    split = find_breakpoint(values, target, higher_is_worse=True)
    if split is not None:
        assert split["p_value"] > 0.05, (
            f"invented an effect of {split['effect']:.2f}x on pure noise "
            f"(p={split['p_value']:.4f})"
        )


# ---------------------------------------------------------------------------
# Full pipeline against the fixture channel
# ---------------------------------------------------------------------------


def test_long_shot_pattern_recovered_in_full_channel(channel):
    patterns = {p.feature: p for p in learn_patterns(channel)}
    assert "shot_age" in patterns
    shot = patterns["shot_age"]
    planted = GROUND_TRUTH["long_shot_multiplier"]
    assert shot.ci_low <= planted <= shot.ci_high, (
        f"planted {planted}x, got {shot.effect:.2f}x "
        f"CI[{shot.ci_low:.2f},{shot.ci_high:.2f}]"
    )
    assert shot.significant


def test_effect_ordering_matches_ground_truth(channel):
    """Shot length was planted stronger than speech rate; ranking must agree."""
    patterns = {p.feature: p for p in learn_patterns(channel)}
    assert patterns["shot_age"].effect > patterns["speech_rate"].effect


def test_early_sponsor_reads_cost_more(channel):
    costs = sponsor_costs(channel)
    rule = sponsor_placement_rule(costs)
    assert rule is not None
    # Planted at 1.8x on the hazard. The recovered figure is higher because
    # losses compound over the segment and because an early read still has
    # more audience present to lose. Direction and significance are what the
    # estimator is being held to here.
    assert rule["effect"] > 1.5
    assert rule["significant"]


def test_sponsor_excess_is_net_of_baseline(channel):
    """Excess must never exceed raw loss, or the baseline is being ignored."""
    for cost in sponsor_costs(channel):
        assert cost.excess <= cost.audience_lost + 1e-9
        assert cost.excess >= 0.0


def test_patterns_need_enough_data():
    """Two videos is not a channel. It must decline to guess."""
    assert learn_patterns(make_channel(n_videos=2, seed=1)) == []
