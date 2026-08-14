"""YouTube Data API quota accounting.

A default project gets 10,000 units a day. Reads are cheap; the numbers below
are the published costs. This class exists because the failure mode without it
is brutal: a scan that dies two thirds of the way through a catalogue, on the
morning of a demo, with no quota left until midnight Pacific.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Documented unit costs, YouTube Data API v3.
COSTS: dict[str, int] = {
    "playlistItems.list": 1,
    "videos.list": 1,
    "channels.list": 1,
    "captions.list": 50,
    "captions.download": 200,
    "videos.update": 50,
    "search.list": 100,
}

#: The Analytics API is metered separately by request rate, not Data API units.
ANALYTICS_FREE = {"reports.query"}

DEFAULT_DAILY_UNITS = 10_000


class QuotaExceeded(RuntimeError):
    def __init__(self, operation: str, used: int, limit: int) -> None:
        super().__init__(
            f"'{operation}' would exceed the daily quota ({used}/{limit} units used). "
            f"Quota resets at midnight Pacific. Run with --max-videos to scan fewer, "
            f"or request more quota in the Google Cloud console."
        )
        self.operation = operation
        self.used = used
        self.limit = limit


@dataclass
class QuotaBudget:
    limit: int = DEFAULT_DAILY_UNITS
    used: int = 0
    reserve: int = 0
    log: dict[str, int] = field(default_factory=dict)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.reserve - self.used)

    def cost(self, operation: str) -> int:
        return 0 if operation in ANALYTICS_FREE else COSTS.get(operation, 1)

    def can_afford(self, operation: str, count: int = 1) -> bool:
        return self.cost(operation) * count <= self.remaining

    def spend(self, operation: str, count: int = 1) -> int:
        amount = self.cost(operation) * count
        if amount > self.remaining:
            raise QuotaExceeded(operation, self.used, self.limit)
        self.used += amount
        self.log[operation] = self.log.get(operation, 0) + count
        return amount

    def estimate_scan(self, n_videos: int) -> int:
        """Units a full read-only scan of n videos will cost.

        playlistItems.list pages 50 at a time, videos.list batches 50 ids per
        call, and the Analytics retention report is free of Data API units.
        """
        pages = -(-n_videos // 50)
        return pages * self.cost("playlistItems.list") + pages * self.cost("videos.list")

    def summary(self) -> str:
        lines = [f"quota: {self.used}/{self.limit} units used ({self.remaining} left)"]
        for operation, count in sorted(self.log.items()):
            lines.append(f"  {operation}: {count} calls @ {self.cost(operation)}u")
        return "\n".join(lines)
