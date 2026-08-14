"""Transcript loading and segment detection.

Sponsor detection is keyword scoring over a sliding window rather than an LLM
call, for three reasons: it runs offline, it is deterministic (the same video
always yields the same segment, which matters when a judge re-runs the demo),
and it is auditable -- when it flags a read you can see exactly which phrases
fired.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Segment, Word

# Phrases weighted by how strongly they indicate a paid read rather than
# ordinary speech. "sponsor" alone is weak: creators say "our sponsor" while
# thanking them mid-video. Multi-word commercial phrasing is much stronger.
SPONSOR_CUES: dict[str, float] = {
    "brought to you by": 3.0,
    "today's sponsor": 3.0,
    "this video is sponsored": 3.0,
    "thanks to our sponsor": 3.0,
    "sponsor of today": 3.0,
    "promo code": 2.5,
    "discount code": 2.5,
    "use code": 2.5,
    "first hundred": 2.0,
    "free trial": 2.0,
    "sign up at": 2.0,
    "link in the description": 1.5,
    "link below": 1.5,
    "percent off": 2.0,
    "% off": 2.0,
    "check them out": 1.5,
    "start your": 1.0,
    "sponsor": 1.2,
    "sponsored": 1.2,
    "subscription": 0.8,
    "risk free": 1.5,
    "money back": 1.5,
}

CTA_CUES: dict[str, float] = {
    "subscribe": 2.0,
    "hit the bell": 2.5,
    "ring the bell": 2.5,
    "like this video": 2.0,
    "smash that like": 2.5,
    "leave a comment": 1.5,
    "let me know in the comments": 1.5,
    "see you next time": 2.0,
    "see you in the next": 2.0,
    "thanks for watching": 2.5,
    "patreon": 1.5,
    "join the channel": 1.5,
}

INTRO_MAX = 45.0
SPONSOR_MIN_SCORE = 3.0
CTA_MIN_SCORE = 2.5

_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_subtitles(path: str | Path) -> list[Word]:
    """Parse SRT or WebVTT into word-level timings.

    Subtitle files give cue-level timing, not word-level, so words inside a cue
    are spread evenly across it. That is accurate enough for a 6-second
    speech-rate window and avoids requiring Whisper for a first run.
    """
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    words: list[Word] = []
    blocks = re.split(r"\n\s*\n", text)

    for block in blocks:
        match = _SRT_TIME.search(block)
        if not match:
            continue
        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        body = _SRT_TIME.sub("", block)
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"^\s*\d+\s*$", " ", body, flags=re.MULTILINE)
        body = re.sub(r"^(WEBVTT|NOTE|Kind:|Language:).*$", " ", body, flags=re.MULTILINE)
        tokens = [w for w in re.split(r"\s+", body.strip()) if w]
        if not tokens:
            continue
        span = max(0.01, end - start) / len(tokens)
        for i, token in enumerate(tokens):
            words.append(Word(token, start + i * span, start + (i + 1) * span))
    return words


def transcribe_whisper(path: str | Path, model: str = "base") -> list[Word]:
    """Word timings via faster-whisper. Optional extra."""
    from faster_whisper import WhisperModel  # type: ignore

    engine = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = engine.transcribe(str(path), word_timestamps=True)
    words: list[Word] = []
    for segment in segments:
        for word in segment.words or []:
            words.append(Word(word.word.strip(), word.start, word.end))
    return words



def locate_cues(words: list[Word], cues: dict[str, float]) -> list[tuple[float, float, str, float]]:
    """Where each cue phrase actually occurs, as (start, end, phrase, weight).

    Scoring windows tell you *whether* a read is present; they are 20s wide and
    make terrible boundaries. A single trigger at second 5 would otherwise mark
    the whole window from zero. This maps phrases back to word timings so the
    segment can be anchored to the language that produced it.
    """
    if not words:
        return []

    spans: list[tuple[int, int, int]] = []  # (char_start, char_end, word_index)
    pieces: list[str] = []
    cursor = 0
    for i, word in enumerate(words):
        token = word.text.lower()
        spans.append((cursor, cursor + len(token), i))
        pieces.append(token)
        cursor += len(token) + 1
    haystack = " ".join(pieces)

    def word_at(char_pos: int) -> int:
        lo, hi = 0, len(spans) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if spans[mid][1] < char_pos:
                lo = mid + 1
            else:
                hi = mid
        return spans[lo][2]

    hits: list[tuple[float, float, str, float]] = []
    for phrase, weight in cues.items():
        start = haystack.find(phrase)
        while start != -1:
            first = word_at(start)
            last = word_at(start + len(phrase) - 1)
            hits.append((words[first].start, words[last].end, phrase, weight))
            start = haystack.find(phrase, start + 1)
    hits.sort()
    return hits



def _cluster(
    hits: list[tuple[float, float, str, float]],
    min_score: float,
    lead: float,
    tail: float,
    gap: float,
    duration: float,
    kind: str,
) -> list[Segment]:
    """Group nearby cue hits into one segment each.

    A read is a cluster of commercial phrasing, not a single keyword. Requiring
    the cluster to clear `min_score` in aggregate is what keeps "check out my
    other video" from being billed as a sponsor.
    """
    if not hits:
        return []

    clusters: list[list[tuple[float, float, str, float]]] = [[hits[0]]]
    for hit in hits[1:]:
        if hit[0] - clusters[-1][-1][1] <= gap:
            clusters[-1].append(hit)
        else:
            clusters.append([hit])

    segments: list[Segment] = []
    for cluster in clusters:
        # one phrase counted once, however many times it repeats
        score = sum({phrase: weight for _, _, phrase, weight in cluster}.values())
        if score < min_score:
            continue
        first = min(h[0] for h in cluster)
        last = max(h[1] for h in cluster)
        strongest = max(cluster, key=lambda h: h[3])[2]
        segments.append(
            Segment(
                kind=kind,
                start=max(0.0, first - lead),
                end=min(duration, last + tail),
                confidence=min(1.0, score / 8.0),
                label=strongest,
            )
        )
    return segments


def detect_segments(words: list[Word], duration: float) -> list[Segment]:
    """Label the intro, any sponsor reads, and end-card style calls to action.

    Boundaries come from where the cue phrases actually sit, padded by a lead
    and tail. A read usually opens a beat before the first trigger ("before we
    continue...") and runs on well past the last one, so the tail is longer.
    """
    # Padding is capped at a share of runtime. On a ten-minute video the fixed
    # 6s/14s bind and are negligible; on a short clip they would otherwise
    # swallow the entire thing and report a video that is 100% sponsor.
    cap = max(1.0, duration * 0.08)

    segments = _cluster(
        locate_cues(words, SPONSOR_CUES),
        min_score=SPONSOR_MIN_SCORE,
        lead=min(6.0, cap),
        tail=min(14.0, cap),
        gap=25.0,
        duration=duration,
        kind="sponsor",
    )

    for cta in _cluster(
        locate_cues(words, CTA_CUES),
        min_score=CTA_MIN_SCORE,
        lead=min(3.0, cap),
        tail=min(8.0, cap),
        gap=20.0,
        duration=duration,
        kind="cta",
    ):
        if any(s.kind == "sponsor" and s.start <= cta.start < s.end for s in segments):
            continue
        segments.append(cta)

    intro_end = min(INTRO_MAX, duration * 0.12)
    if intro_end > 5.0:
        # an intro never overrides a read that starts inside it
        overlap = next((s for s in segments if s.kind == "sponsor" and s.start < intro_end), None)
        if overlap:
            intro_end = min(intro_end, overlap.start)
        if intro_end > 5.0:
            segments.append(Segment(kind="intro", start=0.0, end=intro_end, label="intro"))

    segments.sort(key=lambda s: s.start)
    return segments
