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
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        token_path.chmod(0o600)
    return creds


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

    def list_uploads(self, max_videos: int = 50) -> list[str]:
        channel = self.my_channel()
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
            ids.extend(item["contentDetails"]["videoId"] for item in response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
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
                metas.append(
                    VideoMeta(
                        video_id=item["id"],
                        title=item["snippet"]["title"],
                        duration=_parse_iso8601(item["contentDetails"]["duration"]),
                        published_at=item["snippet"].get("publishedAt", ""),
                        views=int(item.get("statistics", {}).get("viewCount", 0)),
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
        if len(rows) < 10:
            return None

        points = [
            RetentionPoint(
                ratio=float(row[0]),
                watch_ratio=float(row[1]),
                relative=float(row[2]) if len(row) > 2 and row[2] is not None else None,
            )
            for row in rows
        ]
        return RetentionCurve(video_id=video_id, duration=duration, points=points)


def _parse_iso8601(value: str) -> float:
    """PT1H2M3S -> seconds."""
    import re

    match = re.match(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value or ""
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
