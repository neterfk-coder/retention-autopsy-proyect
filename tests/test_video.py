"""extract/video.py logic that does not require ffmpeg or a real file.

Found by running the pipeline against an actual ~5 minute edited video: the
raw ffmpeg scene-score path had no protection against a whip pan or a flash
frame pushing the score over threshold several times within a fraction of a
second, so real footage produced clusters of "cuts" 30-100ms apart -- shots no
editor could have intended, which corrupt every feature derived from shot
length downstream.
"""

from __future__ import annotations

from autopsy.extract.video import MIN_SHOT_LENGTH, _merge_close_cuts


def test_merges_a_burst_of_near_duplicate_triggers():
    # a real cluster observed on real footage: eight triggers inside ~1s.
    # Each kept cut must be at least min_gap past the *previous kept* cut,
    # not past the start of its cluster.
    cuts = [12.913, 13.196, 13.280, 13.363, 13.796, 13.846, 13.880, 13.980]
    merged = _merge_close_cuts(cuts, min_gap=0.4)
    assert merged == [12.913, 13.363, 13.796]
    assert all(b - a >= 0.4 for a, b in zip(merged, merged[1:]))


def test_leaves_genuinely_spaced_cuts_alone():
    cuts = [1.0, 3.5, 9.0, 20.267]
    assert _merge_close_cuts(cuts, min_gap=0.4) == cuts


def test_empty_input():
    assert _merge_close_cuts([], min_gap=0.4) == []


def test_default_min_gap_matches_the_module_constant():
    cuts = [0.0, MIN_SHOT_LENGTH - 0.01, MIN_SHOT_LENGTH + 0.01]
    assert _merge_close_cuts(cuts) == [0.0, MIN_SHOT_LENGTH + 0.01]
