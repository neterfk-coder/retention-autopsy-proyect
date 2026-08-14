"""Core data structures shared across the pipeline.

The whole tool is a join between two timelines that YouTube never puts in the
same place:

    retention  -- how many viewers were still watching at time t
    edit       -- what the editor actually did at time t

Everything here exists to make that join well defined.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

SegmentKind = Literal["intro", "sponsor", "cta", "body"]


def timecode(seconds: float) -> str:
    """Format seconds as m:ss or h:mm:ss, the way an editor reads it."""
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    title: str
    duration: float
    published_at: str = ""
    views: int = 0

    @property
    def short_title(self) -> str:
        return self.title if len(self.title) <= 58 else self.title[:55] + "..."


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Segment:
    """A named stretch of the video: the intro, a sponsor read, an end card."""

    kind: SegmentKind
    start: float
    end: float
    confidence: float = 1.0
    label: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


@dataclass
class EditTimeline:
    """What the editor did, reconstructed from the video file.

    `cuts` are the timestamps of shot boundaries. `loudness` and `silence` are
    sampled on a fixed grid (`sample_hz`) so we can read a value at any t
    without re-scanning the file.
    """

    video_id: str
    duration: float
    cuts: list[float] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    loudness: list[float] = field(default_factory=list)
    silence: list[float] = field(default_factory=list)
    sample_hz: float = 2.0
    has_music_bed: bool = False

    def __setattr__(self, name: str, value) -> None:
        """Keep derived state in step with the fields it is derived from.

        `cuts` and `words` are assigned *after* construction in the scan and
        edit paths, so normalising them once in __post_init__ was a silent bug:
        a reassigned `words` left the bisect index empty and every scanned video
        reported a speech rate of exactly zero. Normalising on assignment means
        it cannot drift out of step again.
        """
        duration = getattr(self, "duration", None)
        if name == "cuts" and duration is not None:
            value = sorted(float(c) for c in value if 0.0 <= float(c) <= duration)
        super().__setattr__(name, value)
        if name == "words":
            super().__setattr__("_word_starts", [w.start for w in value])

    # -- shot geometry -------------------------------------------------

    def shot_bounds(self, t: float) -> tuple[float, float]:
        """(start, end) of the shot containing t."""
        i = bisect.bisect_right(self.cuts, t)
        start = self.cuts[i - 1] if i > 0 else 0.0
        end = self.cuts[i] if i < len(self.cuts) else self.duration
        return start, end

    def shot_age(self, t: float) -> float:
        """Seconds the current shot has been on screen at time t.

        This is the feature that matters, not shot length. A viewer at t has
        experienced `shot_age` seconds of unbroken frame -- they cannot know
        the cut is coming.
        """
        start, _ = self.shot_bounds(t)
        return max(0.0, t - start)

    def shot_length(self, t: float) -> float:
        start, end = self.shot_bounds(t)
        return max(0.0, end - start)

    def cut_rate(self, t: float, window: float = 10.0) -> float:
        """Cuts per 10s in the window ending at t."""
        lo = max(0.0, t - window)
        left = bisect.bisect_left(self.cuts, lo)
        right = bisect.bisect_right(self.cuts, t)
        span = max(1e-6, t - lo)
        return (right - left) * 10.0 / span

    # -- speech --------------------------------------------------------

    def speech_rate(self, t: float, window: float = 6.0) -> float:
        """Words per second in the window centred on t."""
        if not self.words:
            return 0.0
        lo, hi = t - window / 2, t + window / 2
        left = bisect.bisect_left(self._word_starts, lo)
        right = bisect.bisect_right(self._word_starts, hi)
        return (right - left) / window

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.text for w in self.words if start <= w.start < end)

    # -- sampled series ------------------------------------------------

    def _sample(self, series: Sequence[float], t: float, default: float) -> float:
        if not series:
            return default
        i = int(t * self.sample_hz)
        i = min(max(i, 0), len(series) - 1)
        return float(series[i])

    def loudness_at(self, t: float) -> float:
        return self._sample(self.loudness, t, -18.0)

    def silence_at(self, t: float) -> float:
        return self._sample(self.silence, t, 0.0)

    def loudness_delta(self, t: float, window: float = 8.0) -> float:
        """Change in loudness versus the preceding window. Negative = it got quiet."""
        return self.loudness_at(t) - self.loudness_at(max(0.0, t - window))

    # -- segments ------------------------------------------------------

    def segment_at(self, t: float) -> Segment | None:
        for seg in self.segments:
            if seg.contains(t):
                return seg
        return None

    def segments_of(self, kind: SegmentKind) -> list[Segment]:
        return [s for s in self.segments if s.kind == kind]


@dataclass(frozen=True)
class RetentionPoint:
    """One bucket of YouTube's audience retention report.

    `ratio` is elapsedVideoTimeRatio (0..1), `watch_ratio` is audienceWatchRatio
    -- the share of viewers still watching. `relative` is
    relativeRetentionPerformance when available.
    """

    ratio: float
    watch_ratio: float
    relative: float | None = None


@dataclass
class RetentionCurve:
    video_id: str
    duration: float
    points: list[RetentionPoint]

    def __post_init__(self) -> None:
        self.points = sorted(self.points, key=lambda p: p.ratio)

    @property
    def times(self) -> list[float]:
        return [p.ratio * self.duration for p in self.points]

    @property
    def values(self) -> list[float]:
        return [p.watch_ratio for p in self.points]

    def at(self, t: float) -> float:
        """Linearly interpolated watch ratio at time t."""
        if not self.points:
            return 0.0
        ts, vs = self.times, self.values
        if t <= ts[0]:
            return vs[0]
        if t >= ts[-1]:
            return vs[-1]
        i = bisect.bisect_right(ts, t)
        t0, t1 = ts[i - 1], ts[i]
        v0, v1 = vs[i - 1], vs[i]
        span = t1 - t0
        return v0 if span <= 0 else v0 + (v1 - v0) * (t - t0) / span

    def hazard(self) -> list[float]:
        """Per-bucket churn: share of the *remaining* audience lost.

        This is the target variable for the whole tool, and choosing it over a
        raw derivative matters. Raw slope is dominated by the first 30 seconds,
        where everyone bleeds viewers, and it makes late-video problems
        invisible because there is barely anyone left to lose. Hazard
        normalises by the audience still present, so losing 5% of the remaining
        viewers at 8:00 registers as strongly as losing 5% at 0:20.
        """
        vs = self.values
        out = [0.0]
        for prev, cur in zip(vs, vs[1:]):
            out.append(0.0 if prev <= 1e-9 else max(0.0, (prev - cur) / prev))
        return out

    def audience_between(self, t0: float, t1: float) -> float:
        """Fraction of the starting audience lost between t0 and t1."""
        return max(0.0, self.at(t0) - self.at(t1))


@dataclass
class Video:
    meta: VideoMeta
    retention: RetentionCurve
    timeline: EditTimeline

    @property
    def video_id(self) -> str:
        return self.meta.video_id


@dataclass
class Finding:
    """One diagnosed retention cliff in one video."""

    video_id: str
    video_title: str
    start: float
    end: float
    hazard: float
    audience_lost: float
    viewers_lost: float
    causes: list[str] = field(default_factory=list)
    evidence: dict[str, float] = field(default_factory=dict)
    quote: str = ""

    @property
    def timecode(self) -> str:
        return timecode(self.start)

    @property
    def headline(self) -> str:
        pct = self.hazard * 100
        return f"{pct:.1f}% of remaining viewers left at {self.timecode}"


@dataclass
class Pattern:
    """A rule learned across the whole catalogue, with an honest error bar."""

    feature: str
    title: str
    detail: str
    effect: float
    ci_low: float
    ci_high: float
    p_value: float
    n: int
    edge: float = 0.0
    bins: list[dict] = field(default_factory=list)

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05 and self.n >= 200

    @property
    def confidence_label(self) -> str:
        if not self.significant:
            return "weak"
        if self.p_value < 0.01 and self.n >= 800:
            return "strong"
        return "moderate"


@dataclass
class SponsorCost:
    video_id: str
    video_title: str
    start: float
    end: float
    position: float
    audience_lost: float
    viewers_lost: float
    baseline_lost: float

    @property
    def excess(self) -> float:
        """Audience lost beyond what a normal stretch of the same length costs."""
        return max(0.0, self.audience_lost - self.baseline_lost)


@dataclass
class Report:
    channel_title: str
    videos: list[Video]
    findings: list[Finding]
    patterns: list[Pattern]
    sponsor_costs: list[SponsorCost]
    generated_at: str = ""
    source: str = "youtube"
    warnings: list[str] = field(default_factory=list)

    @property
    def total_viewers_lost(self) -> float:
        return sum(f.viewers_lost for f in self.findings)


def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def safe_log(x: float, floor: float = 1e-9) -> float:
    return math.log(max(x, floor))
