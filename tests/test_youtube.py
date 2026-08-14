"""The real-channel path, exercised without a network or credentials.

This module is the one part of the tool that cannot be covered by the synthetic
fixture, because it only runs when a real channel is attached -- which is
exactly why it needed covering. Every case below is a response shape a real
catalogue produces: a livestream with no duration, a video still processing, a
hidden view count, a metric YouTube declines to report, a page of uploads that
were all deleted.

The failure they share is that a single bad video used to abandon the entire
scan, on the one code path a demo cannot rehearse.
"""

from __future__ import annotations

from autopsy.quota import QuotaBudget
from autopsy.sources.youtube import (
    YouTubeClient,
    _parse_iso8601,
    _safe_float,
    _safe_int,
)

# -- fakes ------------------------------------------------------------


class _Request:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _Endpoint:
    """Returns each payload in turn, repeating the last one forever."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def list(self, **kwargs):
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return _Request(payload)

    query = list


class _Data:
    def __init__(self, videos=None, playlist=None, channels=None):
        self._videos = _Endpoint(videos or [{}])
        self._playlist = _Endpoint(playlist or [{}])
        self._channels = _Endpoint(channels or [{}])

    def videos(self):
        return self._videos

    def playlistItems(self):
        return self._playlist

    def channels(self):
        return self._channels


class _Analytics:
    def __init__(self, payloads):
        self._reports = _Endpoint(payloads)

    def reports(self):
        return self._reports


def _client(data=None, analytics=None, limit=10_000) -> YouTubeClient:
    """A client with the Google build() call bypassed."""
    client = YouTubeClient.__new__(YouTubeClient)
    client.budget = QuotaBudget(limit=limit)
    client.data = data
    client.analytics = analytics
    return client


def _videos(*items) -> _Data:
    return _Data(videos=[{"items": list(items)}])


# -- duration parsing -------------------------------------------------


def test_parses_iso8601_durations():
    assert _parse_iso8601("PT1H2M3S") == 3723
    assert _parse_iso8601("PT45S") == 45
    assert _parse_iso8601("PT1M30.5S") == 90.5
    assert _parse_iso8601("P1DT2H3M4S") == 93784


def test_duration_without_a_time_part_is_still_parsed():
    """YouTube reports a livestream as P0D, and a day-long video has no T."""
    assert _parse_iso8601("P0D") == 0.0
    assert _parse_iso8601("P1D") == 86400


def test_unparseable_duration_is_zero_not_an_exception():
    for value in ("", None, "banana", "1:02:03"):
        assert _parse_iso8601(value) == 0.0


def test_safe_coercions_reject_nonsense():
    assert _safe_float(None) is None
    assert _safe_float("nan") is None
    assert _safe_float("inf") is None
    assert _safe_float("0.5") == 0.5
    assert _safe_int(None) == 0
    assert _safe_int("1,234") == 0
    assert _safe_int("1234") == 1234


# -- video metadata ---------------------------------------------------


def test_video_missing_duration_survives_as_zero():
    """A video still processing has no duration; cmd_scan then skips it."""
    metas = _client(_videos(
        {"id": "a", "snippet": {"title": "t"}, "contentDetails": {}}
    )).video_meta(["a"])
    assert metas[0].duration == 0.0


def test_video_missing_title_or_statistics_survives():
    metas = _client(_videos(
        {"id": "a", "snippet": {}, "contentDetails": {"duration": "PT5M"}},
    )).video_meta(["a"])
    assert metas[0].title == "(untitled)"
    assert metas[0].views == 0


def test_one_malformed_video_does_not_lose_the_others():
    metas = _client(_videos(
        {"id": "good1", "snippet": {"title": "a"}, "contentDetails": {"duration": "PT5M"}},
        {},  # nothing usable at all
        {"id": "good2", "snippet": {"title": "b"}, "contentDetails": {"duration": "PT6M"}},
    )).video_meta(["good1", "bad", "good2"])
    assert [m.video_id for m in metas] == ["good1", "good2"]
    assert [m.duration for m in metas] == [300.0, 360.0]


# -- retention --------------------------------------------------------


def _rows(n=21, watch=lambda i: 1.0 - i * 0.01):
    return {"rows": [[i / (n - 1), watch(i)] for i in range(n)]}


def test_retention_reads_a_normal_report():
    curve = _client(analytics=_Analytics([_rows()])).retention("v", 600.0)
    assert curve is not None and len(curve.points) == 21


def test_retention_drops_null_metrics_rather_than_raising():
    payload = _rows()
    payload["rows"][5][1] = None  # YouTube declines to report this bucket
    curve = _client(analytics=_Analytics([payload])).retention("v", 600.0)
    assert curve is not None
    assert len(curve.points) == 20


def test_retention_is_none_when_too_little_survives():
    payload = {"rows": [[i / 20.0, None] for i in range(21)]}
    assert _client(analytics=_Analytics([payload])).retention("v", 600.0) is None


def test_retention_is_none_when_withheld():
    """Below its privacy threshold YouTube returns no rows. Not an error."""
    assert _client(analytics=_Analytics([{}])).retention("v", 600.0) is None


# -- pagination -------------------------------------------------------


CHANNEL = {
    "snippet": {"title": "t"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
}


def test_empty_page_with_a_token_does_not_spin_the_quota_away():
    """Deleted uploads are filtered server side, leaving a page empty.

    Paging on regardless is an infinite loop that spends the whole daily quota
    -- the precise failure QuotaBudget exists to prevent.
    """
    client = _client(_Data(playlist=[{"items": [], "nextPageToken": "tok"}]))
    assert client.list_uploads(40, channel=CHANNEL) == []
    assert client.budget.used <= 1


def test_pagination_stops_at_max_videos():
    page = {
        "items": [{"contentDetails": {"videoId": f"v{i}"}} for i in range(50)],
        "nextPageToken": "tok",
    }
    client = _client(_Data(playlist=[page]))
    assert len(client.list_uploads(60, channel=CHANNEL)) == 60


def test_playlist_entries_without_a_video_id_are_skipped():
    page = {"items": [
        {"contentDetails": {"videoId": "v1"}},
        {"contentDetails": {}},
        {},
        {"contentDetails": {"videoId": "v2"}},
    ]}
    client = _client(_Data(playlist=[page]))
    assert client.list_uploads(10, channel=CHANNEL) == ["v1", "v2"]
