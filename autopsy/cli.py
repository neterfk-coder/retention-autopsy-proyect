"""Command line entry point.

    autopsy demo                    run on synthetic fixture data
    autopsy auth  --secrets f.json  one-time OAuth
    autopsy scan  --max-videos 40   pull a real channel, cache to disk
    autopsy edit  --video v.mp4     extract the edit from a local file
    autopsy report --cache c.json   analyse and write the HTML
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import __version__
from .analysis.findings import describe, diagnose_all
from .analysis.patterns import (
    learn_patterns,
    sponsor_costs,
    sponsor_placement_rule,
    sponsor_summary,
)
from .models import (
    EditTimeline,
    Report,
    RetentionCurve,
    RetentionPoint,
    Segment,
    Video,
    VideoMeta,
    Word,
)
from .report.html import write_report

DEFAULT_CACHE = Path("out/channel.json")
DEFAULT_REPORT = Path("out/report.html")


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------


def video_to_dict(video: Video) -> dict:
    return {
        "meta": {
            "video_id": video.meta.video_id,
            "title": video.meta.title,
            "duration": video.meta.duration,
            "published_at": video.meta.published_at,
            "views": video.meta.views,
        },
        "retention": [
            {"ratio": p.ratio, "watch_ratio": p.watch_ratio, "relative": p.relative}
            for p in video.retention.points
        ],
        "timeline": {
            "cuts": video.timeline.cuts,
            "words": [[w.text, w.start, w.end] for w in video.timeline.words],
            "segments": [
                {
                    "kind": s.kind,
                    "start": s.start,
                    "end": s.end,
                    "confidence": s.confidence,
                    "label": s.label,
                }
                for s in video.timeline.segments
            ],
            "loudness": [round(v, 2) for v in video.timeline.loudness],
            "silence": video.timeline.silence,
            "sample_hz": video.timeline.sample_hz,
        },
    }


def video_from_dict(data: dict) -> Video:
    meta = VideoMeta(**data["meta"])
    curve = RetentionCurve(
        video_id=meta.video_id,
        duration=meta.duration,
        points=[RetentionPoint(**p) for p in data["retention"]],
    )
    tl = data["timeline"]
    timeline = EditTimeline(
        video_id=meta.video_id,
        duration=meta.duration,
        cuts=tl.get("cuts", []),
        words=[Word(*w) for w in tl.get("words", [])],
        segments=[Segment(**s) for s in tl.get("segments", [])],
        loudness=tl.get("loudness", []),
        silence=tl.get("silence", []),
        sample_hz=tl.get("sample_hz", 2.0),
    )
    return Video(meta=meta, retention=curve, timeline=timeline)


def save_channel(videos: list[Video], path: Path, channel_title: str, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "channel_title": channel_title,
                "source": source,
                "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "videos": [video_to_dict(v) for v in videos],
            },
            indent=1,
        ),
        encoding="utf-8",
    )


def load_channel(path: Path) -> tuple[list[Video], str, str]:
    if not path.exists():
        raise FileNotFoundError(
            f"no cache at {path}. Run 'python -m autopsy demo' for fixture data, "
            f"or 'python -m autopsy scan --secrets client_secrets.json' for a real channel."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    videos = [video_from_dict(v) for v in data["videos"]]
    return videos, data.get("channel_title", "Unknown channel"), data.get("source", "youtube")


# ---------------------------------------------------------------------------
# analysis + report
# ---------------------------------------------------------------------------


def analyse(videos: list[Video], channel_title: str, source: str) -> tuple[Report, dict, dict]:
    findings = diagnose_all(videos)
    patterns = learn_patterns(videos)
    costs = sponsor_costs(videos)
    rule = sponsor_placement_rule(costs)
    summary = sponsor_summary(costs)

    warnings: list[str] = []
    if len(videos) < 15:
        warnings.append(
            f"Only {len(videos)} videos with retention data. Channel-level patterns need "
            "roughly 15 before the confidence intervals become useful."
        )
    if not costs:
        warnings.append("No sponsor reads detected, so the sponsor section is omitted.")

    # `scan` only pulls the retention curve; the edit side -- cuts, words,
    # loudness -- comes from `autopsy edit` against the actual video file.
    # Without it every cause reads identically ("0.0 cuts per 10s", "0.0
    # words/s") because there is no edit signal to diagnose yet, which looks
    # like a broken tool rather than an unfinished pipeline unless it says so.
    no_edit = sum(1 for v in videos if not v.timeline.cuts and not v.timeline.words)
    if no_edit:
        warnings.append(
            f"{no_edit} of {len(videos)} videos have no edit data yet -- causes will read "
            "identically until you run 'autopsy edit --video-id <id> --file <video>' on "
            "them. Retention findings below are real; the causes attributed to them are not."
        )

    report = Report(
        channel_title=channel_title,
        videos=videos,
        findings=findings,
        patterns=patterns,
        sponsor_costs=costs,
        generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        source=source,
        warnings=warnings,
    )
    return report, rule, summary


def emit(report: Report, rule: dict | None, summary: dict, out: Path, max_videos: int) -> None:
    path = write_report(report, out, rule=rule, summary=summary, max_videos=max_videos)
    print(f"\n  {len(report.videos)} videos, {len(report.findings)} cliffs, "
          f"{len(report.patterns)} patterns")
    if report.findings:
        print("\n  worst moments")
        for finding in report.findings[:5]:
            print(f"    {finding.video_title[:34]:<34} {describe(finding)}")
    print(f"\n  report -> {path.resolve()}\n")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_demo(args: argparse.Namespace) -> int:
    from .sources.synthetic import make_channel

    print(f"generating {args.videos} synthetic videos (fixture data, not a real channel)")
    videos = make_channel(n_videos=args.videos, seed=args.seed)
    save_channel(videos, Path(args.cache), "Synthetic fixture channel", "synthetic")
    report, rule, summary = analyse(videos, "Synthetic fixture channel", "synthetic")
    emit(report, rule, summary, Path(args.out), args.max_videos)
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    from .sources.youtube import TOKEN_PATH, authenticate

    authenticate(args.secrets)
    print(f"authorised. token cached at {TOKEN_PATH}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .extract.transcript import detect_segments
    from .quota import QuotaBudget
    from .sources.youtube import YouTubeClient, authenticate

    budget = QuotaBudget(limit=args.quota)
    estimate = budget.estimate_scan(args.max_videos)
    print(f"estimated cost: {estimate} of {args.quota} units")

    client = YouTubeClient(authenticate(args.secrets), budget=budget)
    channel = client.my_channel()
    title = channel["snippet"]["title"]
    print(f"channel: {title}")

    ids = client.list_uploads(args.max_videos, channel=channel)
    metas = client.video_meta(ids)
    print(f"{len(metas)} videos, fetching retention")

    videos: list[Video] = []
    skipped = 0
    for meta in metas:
        if meta.duration < 90:
            skipped += 1
            continue
        curve = client.retention(meta.video_id, meta.duration, args.start, args.end)
        if curve is None:
            skipped += 1
            continue
        timeline = EditTimeline(video_id=meta.video_id, duration=meta.duration)
        subtitle = Path(args.subtitles or "") / f"{meta.video_id}.srt" if args.subtitles else None
        if subtitle and subtitle.exists():
            from .extract.transcript import parse_subtitles

            timeline.words = parse_subtitles(subtitle)
            timeline.segments = detect_segments(timeline.words, meta.duration)
        videos.append(Video(meta=meta, retention=curve, timeline=timeline))
        print(f"  ok  {meta.video_id}  {meta.title[:46]}")

    print(f"\n{len(videos)} usable, {skipped} skipped (too short or no retention data)")
    print(budget.summary())
    save_channel(videos, Path(args.cache), title, "youtube")
    print(f"cached -> {args.cache}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    from .extract.transcript import detect_segments, parse_subtitles
    from .extract.video import audio_series, detect_cuts, probe_duration

    videos, title, source = load_channel(Path(args.cache))
    index = {v.video_id: v for v in videos}
    if args.video_id not in index:
        print(f"{args.video_id} is not in {args.cache}", file=sys.stderr)
        return 1

    video = index[args.video_id]
    duration = probe_duration(args.file) or video.meta.duration
    print(f"analysing {args.file} ({duration:.0f}s)")

    cuts = detect_cuts(args.file)
    # the grid the series are sampled on has to be the grid the timeline reads
    # them back on, so it is passed explicitly rather than defaulted twice
    sample_hz = 2.0
    loudness, silence = audio_series(args.file, duration, sample_hz=sample_hz)
    print(f"  {len(cuts)} cuts, {len(loudness)} audio samples")

    words = parse_subtitles(args.subtitles) if args.subtitles else []
    if not words and args.whisper:
        from .extract.transcript import transcribe_whisper

        print("  transcribing (this is the slow part)")
        words = transcribe_whisper(args.file, model=args.whisper)

    video.timeline = EditTimeline(
        video_id=video.video_id,
        duration=duration,
        cuts=cuts,
        words=words,
        segments=detect_segments(words, duration) if words else [],
        loudness=loudness,
        silence=silence,
        sample_hz=sample_hz,
    )
    save_channel(videos, Path(args.cache), title, source)
    print(f"  updated {args.cache}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    videos, title, source = load_channel(Path(args.cache))
    report, rule, summary = analyse(videos, title, source)
    emit(report, rule, summary, Path(args.out), args.max_videos)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autopsy", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--cache", default=str(DEFAULT_CACHE))
        p.add_argument("--out", default=str(DEFAULT_REPORT))
        p.add_argument("--max-videos", type=int, default=8, dest="max_videos")

    demo = sub.add_parser("demo", help="run on synthetic fixture data")
    common(demo)
    demo.add_argument("--videos", type=int, default=24)
    demo.add_argument("--seed", type=int, default=7)
    demo.set_defaults(func=cmd_demo)

    auth = sub.add_parser("auth", help="one-time OAuth")
    auth.add_argument("--secrets", required=True, help="client_secrets.json from Google Cloud")
    auth.set_defaults(func=cmd_auth)

    scan = sub.add_parser("scan", help="pull retention for your channel")
    scan.add_argument("--secrets", required=True)
    scan.add_argument("--max-videos", type=int, default=40, dest="max_videos")
    scan.add_argument("--quota", type=int, default=10_000)
    scan.add_argument("--start", default=None, help="YYYY-MM-DD")
    scan.add_argument("--end", default=None, help="YYYY-MM-DD")
    scan.add_argument("--subtitles", default=None, help="dir of <video_id>.srt files")
    scan.add_argument("--cache", default=str(DEFAULT_CACHE))
    scan.set_defaults(func=cmd_scan)

    edit = sub.add_parser("edit", help="extract the edit from a local video file")
    edit.add_argument("--video-id", required=True, dest="video_id")
    edit.add_argument("--file", required=True)
    edit.add_argument("--subtitles", default=None)
    edit.add_argument("--whisper", default=None, help="whisper model name, e.g. base")
    edit.add_argument("--cache", default=str(DEFAULT_CACHE))
    edit.set_defaults(func=cmd_edit)

    rep = sub.add_parser("report", help="analyse a cache and write the HTML")
    common(rep)
    rep.set_defaults(func=cmd_report)
    return parser


def _make_stdio_unicode_safe() -> None:
    """Stop real video titles from crashing the console.

    A real channel's titles are UTF-8 and routinely carry emoji; Windows'
    legacy console encoding (cp1252) cannot represent them and raises
    UnicodeEncodeError on print(), which previously took the whole `scan` down
    mid-run -- after it had already spent quota. Fixture titles are plain
    ASCII, so `demo` never exercised this.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - unusual streams
                pass


def main(argv: list[str] | None = None) -> int:
    _make_stdio_unicode_safe()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # surfaced as a message, not a traceback
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
