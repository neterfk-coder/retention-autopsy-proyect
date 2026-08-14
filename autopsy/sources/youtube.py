"""YouTube Data + Analytics API client.

Audience retention is owner-only data. It lives in the *Analytics* API
(`reports.query`), not the Data API, under the `audienceRetention` report with
the `elapsedVideoTimeRatio` dimension -- which is why no third-party tool can
show it for a channel you do not own.

Scopes needed:
    https://www.googleapis.com/auth/youtube.readonly
    https://www.googleapis.com/auth/yt-analytics.readonly
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path

from ..models import RetentionCurve, RetentionPoint, VideoMeta
from ..quota import QuotaBudget

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

TOKEN_PATH = Path.home() / ".config" / "retention-autopsy" / "token.json"


class YouTubeUnavailable(RuntimeError):
    pass


def _import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise YouTubeUnavailable(
            "Google client libraries are not installed. Run:\n"
            "  pip install -e '.[youtube]'"
        ) from exc
    return Request, Credentials, InstalledAppFlow, build


def authenticate(client_secrets: str | Path, token_path: Path = TOKEN_PATH):
    """Run the installed-app OAuth flow, caching the refresh token.

    While the Cloud project is unverified, add your own Google account under
    OAuth consent screen -> Test users. Up to 100 testers work without the
    full verification review, which is what makes this feasible in a weekend.
    """
    Request, Credentials, InstalledAppFlow, _ = _import_google()
    token_path = Path(token_path)
    creds = None

    if token_path.exists():
        # A truncated or hand-edited token should send you back through the
        # consent screen, not abort the command with a parse error.
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except (ValueError, KeyError, json.JSONDecodeError):
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_path)  # else every run refreshes again
        except Exception:
            creds = None

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        creds = flow.run_local_server(port=0)
        _save_token(creds, token_path)
    return creds


def _save_token(creds, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:  # pragma: no cover - filesystems without POSIX modes
        pass


class YouTubeClient:
    def __init__(self, credentials, budget: QuotaBudget | None = None) -> None:
        _, _, _, build = _import_google()
        self.data = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        self.analytics = build(
            "youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False
        )
        self.budget = budget or QuotaBudget()

    # -- channel ------------------------------------------------------

    def my_channel(self) -> dict:
        self.budget.spend("channels.list")
        response = self.data.channels().list(part="snippet,contentDetails", mine=True).execute()
        items = response.get("items", [])
        if not items:
            raise YouTubeUnavailable("This account has no channel.")
        return items[0]

    def list_uploads(self, max_videos: int = 50, channel: dict | None = None) -> list[str]:
        # Callers that already fetched the channel pass it back in, so a scan
        # spends one channels.list unit rather than two and matches the estimate
        # printed before it started.
        channel = channel or self.my_channel()
        playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        ids: list[str] = []
        page_token = None

        while len(ids) < max_videos:
            self.budget.spend("playlistItems.list")
            response = (
                self.data.playlistItems()
                .list(
                    part="contentDetails",
                    playlistId=playlist,
                    maxResults=min(50, max_videos - len(ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            items = response.get("items", [])
            ids.extend(
                item["contentDetails"]["videoId"]
                for item in items
                if item.get("contentDetails", {}).get("videoId")
            )
            page_token = response.get("nextPageToken")
            # A page can come back empty while still handing over a token --
            # deleted and private uploads are filtered out server side. Paging
            # on regardless is an infinite loop that spends the entire daily
            # quota, which is the exact failure QuotaBudget exists to prevent.
            if not page_token or not items:
                break
        return ids[:max_videos]

    def video_meta(self, video_ids: list[str]) -> list[VideoMeta]:
        metas: list[VideoMeta] = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            self.budget.spend("videos.list")
            response = (
                self.data.videos()
                .list(part="snippet,contentDetails,statistics", id=",".join(batch))
                .execute()
            )
            for item in response.get("items", []):
                # Every field here is read defensively on purpose. A catalogue
                # of any size contains a video that is still processing, has
                # its view count hidden, or is a livestream with no duration,
                # and one KeyError three videos in would abandon the whole scan.
                if not item.get("id"):
                    continue  # nothing to join retention or a file to
                snippet = item.get("snippet") or {}
                details = item.get("contentDetails") or {}
                metas.append(
                    VideoMeta(
                        video_id=item["id"],
                        title=snippet.get("title", "(untitled)"),
                        duration=_parse_iso8601(details.get("duration", "")),
                        published_at=snippet.get("publishedAt", ""),
                        views=_safe_int(item.get("statistics", {}).get("viewCount")),
                    )
                )
        return metas

    # -- retention ----------------------------------------------------

    def retention(
        self,
        video_id: str,
        duration: float,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> RetentionCurve | None:
        """Audience retention for one video.

        Returns None when YouTube withholds the report, which it does for
        videos below its privacy threshold of views. That is expected on small
        channels and is not an error.
        """
        today = dt.date.today()
        start_date = start_date or (today - dt.timedelta(days=365)).isoformat()
        end_date = end_date or today.isoformat()

        self.budget.spend("reports.query")
        response = (
            self.analytics.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="audienceWatchRatio,relativeRetentionPerformance",
                dimensions="elapsedVideoTimeRatio",
                filters=f"video=={video_id};audienceType==ORGANIC",
                sort="elapsedVideoTimeRatio",
            )
            .execute()
        )

        rows = response.get("rows") or []

        # Analytics returns null for a metric it cannot report on a given
        # bucket. Dropping those rows keeps the curve usable; letting float()
        # raise would lose the entire video, and with it the whole scan.
        points = []
        for row in rows:
            if len(row) < 2:
                continue
            ratio = _safe_float(row[0])
            watch = _safe_float(row[1])
            if ratio is None or watch is None:
                continue
            points.append(
                RetentionPoint(
                    ratio=ratio,
                    watch_ratio=watch,
                    relative=_safe_float(row[2]) if len(row) > 2 else None,
                )
            )

        if len(points) < 10:
            return None
        return RetentionCurve(video_id=video_id, duration=duration, points=points)


def _safe_float(value) -> float | None:
    """A float, or None when the API sent something that is not one."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_iso8601(value: str) -> float:
    """PT1H2M3S -> seconds. Returns 0.0 for anything unparseable.

    The time part is optional: YouTube reports a livestream as `P0D` and can
    report a whole-day duration with no `T` at all.
    """
    match = re.match(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$",
        (value or "").strip(),
    )
    if not match:
        return 0.0
    days, hours, minutes, seconds = (float(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def save_cache(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_cache(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
