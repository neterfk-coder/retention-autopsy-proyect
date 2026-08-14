"""Unit tests for the pieces the recovery tests depend on."""

from __future__ import annotations

import pytest

from autopsy.analysis.align import build_rows
from autopsy.analysis.findings import diagnose
from autopsy.extract.transcript import detect_segments, locate_cues, parse_subtitles
from autopsy.models import (
    EditTimeline,
    RetentionCurve,
    RetentionPoint,
    Segment,
    Video,
    VideoMeta,
    Word,
    timecode,
)
from autopsy.quota import QuotaBudget, QuotaExceeded


# -- timeline geometry ------------------------------------------------


def test_shot_age_resets_at_each_cut():
    tl = EditTimeline(video_id="x", duration=100.0, cuts=[10.0, 30.0, 31.0])
    assert tl.shot_age(5.0) == pytest.approx(5.0)
    assert tl.shot_age(10.0) == pytest.approx(0.0)
    assert tl.shot_age(29.0) == pytest.approx(19.0)
    assert tl.shot_age(30.5) == pytest.approx(0.5)


def test_shot_length_covers_final_shot_to_end_of_video():
    tl = EditTimeline(video_id="x", duration=100.0, cuts=[10.0, 30.0])
    assert tl.shot_length(50.0) == pytest.approx(70.0)


def test_cut_rate_counts_only_the_trailing_window():
    tl = EditTimeline(video_id="x", duration=100.0, cuts=[1.0, 2.0, 3.0, 50.0])
    # window [55, 65] holds no cuts; the one at 50.0 has fallen out of it
    assert tl.cut_rate(65.0, window=10.0) == pytest.approx(0.0)
    # window [50, 60] still includes the cut sitting on its left edge
    assert tl.cut_rate(60.0, window=10.0) == pytest.approx(1.0)
    assert tl.cut_rate(10.0, window=10.0) > 0.0


def test_timecode_formatting():
    assert timecode(0) == "0:00"
    assert timecode(75) == "1:15"
    assert timecode(3725) == "1:02:05"


# -- retention maths --------------------------------------------------


def _curve(values, duration=100.0):
    points = [
        RetentionPoint(ratio=i / (len(values) - 1), watch_ratio=v)
        for i, v in enumerate(values)
    ]
    return RetentionCurve(video_id="x", duration=duration, points=points)


def test_hazard_is_share_of_remaining_not_raw_slope():
    """Losing half of what is left reads as 0.5 whether it happens early or late."""
    curve = _curve([1.0, 0.5, 0.25, 0.125])
    hazards = curve.hazard()
    assert hazards[1] == pytest.approx(0.5)
    assert hazards[2] == pytest.approx(0.5)
    assert hazards[3] == pytest.approx(0.5)


def test_hazard_never_goes_negative_on_a_rewatched_bump():
    curve = _curve([1.0, 0.8, 0.9, 0.7])
    assert all(h >= 0.0 for h in curve.hazard())


def test_interpolation_between_buckets():
    curve = _curve([1.0, 0.0], duration=100.0)
    assert curve.at(50.0) == pytest.approx(0.5, abs=0.01)


# -- the join ---------------------------------------------------------


def test_words_assigned_after_construction_still_measure_speech():
    """The scan path fills `words` in after building the timeline.

    Deriving the bisect index only in __post_init__ meant every video scanned
    from a real channel reported a speech rate of exactly zero -- silently, and
    only on the path that never runs against fixture data.
    """
    tl = EditTimeline(video_id="x", duration=100.0)
    tl.words = [Word("w", float(i), float(i) + 0.4) for i in range(100)]
    assert tl.speech_rate(50.0) > 0.5


def test_cuts_assigned_after_construction_are_still_ordered():
    tl = EditTimeline(video_id="x", duration=100.0)
    tl.cuts = [50.0, 10.0, 250.0, -4.0]  # unsorted, and two outside the runtime
    assert tl.cuts == [10.0, 50.0]
    assert tl.shot_age(60.0) == pytest.approx(10.0)


def test_build_rows_skips_the_opening_seconds():
    tl = EditTimeline(video_id="x", duration=100.0, cuts=[10.0])
    video = Video(
        meta=VideoMeta("x", "t", 100.0, views=100),
        retention=_curve([1.0] * 21),
        timeline=tl,
    )
    rows = build_rows(video, skip_head=15.0)
    assert rows and min(r["t"] for r in rows) >= 15.0


def test_rows_carry_segment_flags():
    tl = EditTimeline(
        video_id="x",
        duration=100.0,
        segments=[Segment("sponsor", 40.0, 60.0)],
    )
    video = Video(
        meta=VideoMeta("x", "t", 100.0, views=100),
        retention=_curve([1.0 - i * 0.01 for i in range(101)]),
        timeline=tl,
    )
    rows = build_rows(video)
    inside = [r for r in rows if 40.0 <= r["t"] < 60.0]
    assert inside and all(r["in_sponsor"] == 1.0 for r in inside)
    assert all(r["segment"] == "sponsor" for r in inside)


# -- findings ---------------------------------------------------------


def test_diagnose_finds_a_planted_cliff():
    values = [1.0]
    for i in range(1, 101):
        drop = 0.35 if i == 60 else 0.004
        values.append(values[-1] * (1 - drop))
    tl = EditTimeline(video_id="x", duration=600.0, cuts=[float(c) for c in range(0, 600, 4)])
    video = Video(
        meta=VideoMeta("x", "t", 600.0, views=10_000),
        retention=_curve(values, duration=600.0),
        timeline=tl,
    )
    findings = diagnose(video)
    assert findings
    top = findings[0]
    assert 340.0 <= top.start <= 380.0, f"cliff located at {top.start:.0f}s"
    assert top.hazard > 0.2


def test_no_edit_data_does_not_invent_a_cause():
    """`scan` alone leaves cuts and words empty until `edit` runs on the file.

    Every cause rule keyed on cut_rate or speech_rate used to fire on every
    cliff in that state, because the placeholder value (0.0) and the context
    computed from other placeholder rows (also 0.0) always agreed. That reads
    as a confident diagnosis ("cutting has slowed to 0.0 cuts per 10s")
    manufactured from data that was never collected.
    """
    values = [1.0]
    for i in range(1, 101):
        drop = 0.35 if i == 60 else 0.004
        values.append(values[-1] * (1 - drop))
    tl = EditTimeline(video_id="x", duration=600.0)  # no cuts, no words
    video = Video(
        meta=VideoMeta("x", "t", 600.0, views=10_000),
        retention=_curve(values, duration=600.0),
        timeline=tl,
    )
    findings = diagnose(video)
    assert findings
    causes = " ".join(findings[0].causes)
    assert "cuts per 10s" not in causes
    assert "words/s" not in causes
    assert "held for" not in causes
    assert "autopsy edit" in causes


def test_flat_retention_yields_no_findings():
    tl = EditTimeline(video_id="x", duration=600.0, cuts=[float(c) for c in range(0, 600, 4)])
    video = Video(
        meta=VideoMeta("x", "t", 600.0, views=1000),
        retention=_curve([1.0 - i * 0.001 for i in range(101)], duration=600.0),
        timeline=tl,
    )
    assert diagnose(video) == []


# -- segment detection ------------------------------------------------


def _words(text, start=0.0, rate=2.5):
    out, t = [], start
    for token in text.split():
        out.append(Word(token, t, t + 1 / rate))
        t += 1 / rate
    return out


def test_sponsor_read_detected_from_commercial_phrasing():
    words = _words("so anyway here is the thing about lenses " * 6)
    end = words[-1].end
    words += _words(
        "today's sponsor is a company use code CREATOR for twenty percent off "
        "and there is a free trial with the link in the description",
        start=end,
    )
    tail = words[-1].end
    words += _words("okay back to the lenses and what I was saying " * 6, start=tail)

    segments = detect_segments(words, duration=words[-1].end)
    sponsors = [s for s in segments if s.kind == "sponsor"]
    assert len(sponsors) == 1
    assert sponsors[0].start <= end + 25
    assert sponsors[0].confidence > 0.3


def test_ordinary_speech_is_not_flagged_as_a_sponsor():
    words = _words("today I want to talk about how I plan my videos " * 20)
    segments = detect_segments(words, duration=words[-1].end)
    assert not [s for s in segments if s.kind == "sponsor"]


def test_sponsor_boundaries_anchor_to_the_cue_phrases():
    """A read must not extend across the whole video from one keyword."""
    lead = _words("here is a long stretch of completely ordinary content " * 14)
    start = lead[-1].end
    read = _words(
        "today's sponsor is a company use code CREATOR for twenty percent off "
        "with a free trial and the link in the description",
        start=start,
    )
    tail_start = read[-1].end
    tail = _words("and now back to the ordinary content again " * 14, start=tail_start)
    words = lead + read + tail
    duration = words[-1].end

    sponsors = [s for s in detect_segments(words, duration) if s.kind == "sponsor"]
    assert len(sponsors) == 1
    seg = sponsors[0]
    assert seg.start > lead[len(lead) // 2].start, "segment reaches back too far"
    assert seg.end < duration - 10, "segment runs to the end of the video"
    assert seg.duration < duration * 0.55


def test_padding_never_swallows_a_short_clip():
    """On a 26s clip the fixed 6s/14s padding would cover everything."""
    words = _words(
        "okay so about pacing today's sponsor is a company use code CREATOR "
        "for twenty percent off and there is a free trial with the link in the "
        "description right back to pacing now"
    )
    duration = words[-1].end
    sponsors = [s for s in detect_segments(words, duration) if s.kind == "sponsor"]
    assert sponsors
    assert sponsors[0].duration < duration, "the read consumed the entire clip"


def test_cue_locations_map_back_to_word_timings():
    words = _words("nothing here yet " * 10)
    offset = words[-1].end
    words += _words("use code CREATOR now", start=offset)
    hits = locate_cues(words, {"use code": 2.5})
    assert len(hits) == 1
    assert hits[0][0] == pytest.approx(offset, abs=0.5)


def test_srt_parsing_spreads_words_across_the_cue(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text(
        "1\n00:00:10,000 --> 00:00:14,000\nhello there friend\n\n"
        "2\n00:00:14,000 --> 00:00:16,000\nsecond cue\n",
        encoding="utf-8",
    )
    words = parse_subtitles(srt)
    assert [w.text for w in words] == ["hello", "there", "friend", "second", "cue"]
    assert words[0].start == pytest.approx(10.0)
    assert words[2].start == pytest.approx(10.0 + 8 / 3, abs=0.1)


def test_vtt_header_lines_are_not_treated_as_words(tmp_path):
    vtt = tmp_path / "a.vtt"
    vtt.write_text(
        "WEBVTT\nKind: captions\nLanguage: en\n\n"
        "00:00:01.000 --> 00:00:03.000\n<v Speaker>real words here</v>\n",
        encoding="utf-8",
    )
    words = parse_subtitles(vtt)
    assert [w.text for w in words] == ["real", "words", "here"]


# -- quota ------------------------------------------------------------


def test_quota_blocks_before_overspending():
    budget = QuotaBudget(limit=100)
    budget.spend("videos.update")  # 50
    with pytest.raises(QuotaExceeded):
        budget.spend("captions.download")  # 200


def test_scan_estimate_pages_by_fifty():
    budget = QuotaBudget()
    # one channels.list to find the uploads playlist, then a playlistItems page
    # and a videos.list batch per 50 videos
    assert budget.estimate_scan(50) == 3
    assert budget.estimate_scan(51) == 5


def test_scan_estimate_matches_what_a_scan_actually_spends():
    """The estimate is printed before any quota is spent, so it has to be true.

    Tying it to the real call sequence is what stops the two drifting: the
    estimate previously omitted channels.list and under-reported by half.
    """
    for n_videos in (1, 50, 51, 120):
        pages = -(-n_videos // 50)
        spent = QuotaBudget()
        spent.spend("channels.list")               # my_channel
        spent.spend("playlistItems.list", pages)   # list_uploads
        spent.spend("videos.list", pages)          # video_meta
        spent.spend("reports.query", n_videos)     # retention, free
        assert QuotaBudget().estimate_scan(n_videos) == spent.used


def test_analytics_queries_cost_no_data_api_units():
    budget = QuotaBudget()
    budget.spend("reports.query", 200)
    assert budget.used == 0
