"""Self-contained HTML report.

No JS framework, no CDN charting library, no network at render time. The
retention strips are inline SVG generated here, so the file opens from a USB
stick on a conference wifi that has given up.

Design note: the report is set in a monospace display face because every
number in it is a timecode or a measurement, and the layout borrows the left
gutter of an edit decision list. The signature element is the strip -- the
retention trace sitting directly on top of the cut timeline, which is the
whole thesis of the tool in one picture.
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

from ..models import Finding, Pattern, Report, SponsorCost, Video, timecode

STRIP_W = 900
CURVE_TOP = 14
CURVE_H = 104
TRACK_Y = 138
TRACK_H = 16
STRIP_H = 196
PAD_L = 8
PAD_R = 8

CSS = """
:root{
  --paper:#F6F7F5; --card:#FFFFFF; --ink:#14171A; --slate:#596066;
  --muted:#8A9098; --rule:#E1E5E2; --rule-soft:#EEF1EE;
  --trace:#165C6B; --signal:#B8332A; --sponsor:#A8710F; --ok:#2E6B4F;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:'Archivo',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:15px; line-height:1.65; -webkit-font-smoothing:antialiased;
}
.mono{font-family:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,monospace}
.wrap{max-width:1040px;margin:0 auto;padding:56px 28px 96px}
h1,h2,h3{font-family:'JetBrains Mono',ui-monospace,monospace;font-weight:500;letter-spacing:-.02em;margin:0}
h1{font-size:30px;line-height:1.2}
h2{font-size:19px;margin:0 0 4px}
h3{font-size:15px;margin:0}
p{margin:0 0 12px}
a{color:var(--trace)}
.eyebrow{
  font-family:'JetBrains Mono',ui-monospace,monospace; font-size:11px;
  letter-spacing:.16em; text-transform:uppercase; color:var(--muted);
}
header.masthead{border-bottom:2px solid var(--ink);padding-bottom:22px;margin-bottom:8px}
.sub{color:var(--slate);font-size:14px;margin-top:10px}
.warn{
  background:#FDF3E7;border:1px solid #E8C89A;border-radius:0;
  padding:12px 16px;margin:18px 0;font-size:13px;color:#6B4A16;
}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1px;
  background:var(--rule);border:1px solid var(--rule);margin:32px 0 8px}
.stat{background:var(--card);padding:18px 16px}
.stat .v{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:25px;line-height:1.1}
.stat .k{font-size:12px;color:var(--muted);margin-top:6px}
section{margin-top:56px}
.section-head{display:flex;align-items:baseline;gap:14px;border-bottom:1px solid var(--ink);
  padding-bottom:10px;margin-bottom:24px}
.section-head .n{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--rule);padding:22px 24px;margin-bottom:16px}
.row{display:flex;gap:20px;align-items:flex-start}
.gutter{flex:0 0 74px;font-family:'JetBrains Mono',monospace;font-size:12px;
  color:var(--muted);padding-top:3px;text-align:right}
.body{flex:1;min-width:0}
.tag{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:10.5px;
  letter-spacing:.08em;text-transform:uppercase;padding:3px 7px;border:1px solid;margin-right:6px}
.t-strong{color:#1F4A38;border-color:#9FC4B1;background:#EDF5F0}
.t-moderate{color:#6B4A16;border-color:#E0C08C;background:#FBF3E6}
.t-weak{color:var(--slate);border-color:var(--rule);background:var(--paper)}
.t-signal{color:var(--signal);border-color:#E0A9A3;background:#FBEEEC}
.bars{margin-top:14px;border-top:1px solid var(--rule-soft);padding-top:12px}
.bar-row{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:12px}
.bar-lab{flex:0 0 128px;font-family:'JetBrains Mono',monospace;color:var(--slate);text-align:right}
.bar-track{flex:1;background:var(--rule-soft);height:15px;position:relative}
.bar-fill{height:100%;background:var(--trace)}
.bar-fill.hot{background:var(--signal)}
.bar-val{flex:0 0 74px;font-family:'JetBrains Mono',monospace;color:var(--muted);font-size:11px}
.finding{border-left:2px solid var(--signal);padding:10px 0 10px 16px;margin:14px 0}
.finding .hl{font-family:'JetBrains Mono',monospace;font-size:13.5px}
.finding ul{margin:8px 0 0;padding-left:18px;color:var(--slate);font-size:13.5px}
.quote{font-size:13px;color:var(--muted);font-style:italic;margin-top:8px;
  border-left:1px solid var(--rule);padding-left:12px}
.strip{width:100%;height:auto;display:block;margin:6px 0 2px}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);
  font-family:'JetBrains Mono',monospace;margin-top:2px}
.legend i{display:inline-block;width:16px;height:3px;vertical-align:middle;margin-right:5px}
.vid-head{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap}
.vid-meta{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--muted);white-space:nowrap}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
th{text-align:left;font-family:'JetBrains Mono',monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);font-weight:400;border-bottom:1px solid var(--ink);
  padding:0 10px 7px 0}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--rule-soft);vertical-align:top}
td.num{font-family:'JetBrains Mono',monospace;white-space:nowrap}
footer{margin-top:72px;padding-top:20px;border-top:1px solid var(--rule);
  font-size:12px;color:var(--muted)}
.note{font-size:13px;color:var(--slate);margin-top:10px}
@media (max-width:640px){
  .wrap{padding:32px 16px 64px} h1{font-size:22px}
  .row{flex-direction:column;gap:6px} .gutter{text-align:left}
  .bar-lab{flex:0 0 92px}
}
@media print{body{background:#fff} .card{break-inside:avoid}}
"""


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _blend(low: str, high: str, weight: float) -> str:
    """Linear blend between two hex colours."""
    weight = min(1.0, max(0.0, weight))
    a = tuple(int(low[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(high[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * weight):02X}" for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# The signature element: retention trace over the edit timeline
# ---------------------------------------------------------------------------


def strip_svg(video: Video, findings: list[Finding]) -> str:
    duration = max(1e-6, video.meta.duration)
    inner = STRIP_W - PAD_L - PAD_R

    def x_of(t: float) -> float:
        return PAD_L + inner * min(1.0, max(0.0, t / duration))

    def y_of(value: float) -> float:
        return CURVE_TOP + CURVE_H * (1.0 - min(1.0, max(0.0, value)))

    parts: list[str] = [
        f'<svg class="strip" viewBox="0 0 {STRIP_W} {STRIP_H}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Retention curve over the edit timeline for {esc(video.meta.short_title)}">'
    ]

    # gridlines at 25/50/75%
    for level in (0.25, 0.5, 0.75):
        y = y_of(level)
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{STRIP_W - PAD_R}" y2="{y:.1f}" '
            f'stroke="#E1E5E2" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{STRIP_W - PAD_R}" y="{y - 3:.1f}" text-anchor="end" '
            f'font-family="monospace" font-size="9" fill="#B4BAB6">{int(level * 100)}%</text>'
        )

    # segment shading, drawn first so the trace sits on top
    for segment in video.timeline.segments:
        if segment.kind == "body":
            continue
        fill = {"sponsor": "#F5E7CB", "intro": "#ECEFEC", "cta": "#EAF0EE"}.get(
            segment.kind, "#EEF1EE"
        )
        x0, x1 = x_of(segment.start), x_of(segment.end)
        parts.append(
            f'<rect x="{x0:.1f}" y="{CURVE_TOP}" width="{max(1.0, x1 - x0):.1f}" '
            f'height="{TRACK_Y + TRACK_H - CURVE_TOP:.1f}" fill="{fill}"/>'
        )
        if segment.kind == "sponsor" and (x1 - x0) > 44:
            parts.append(
                f'<text x="{(x0 + x1) / 2:.1f}" y="{CURVE_TOP + 11}" text-anchor="middle" '
                f'font-family="monospace" font-size="9" fill="#A8710F">SPONSOR</text>'
            )

    # Shot-length heatmap rather than one tick per cut. On a fast-cut video
    # 300 ticks across 900px is a solid grey bar carrying no information.
    # Shading each shot by how long it is held makes the point of the whole
    # tool visible at a glance: dark blocks are long shots, and dark blocks
    # sitting under a dip in the trace is the finding.
    boundaries = [0.0] + list(video.timeline.cuts) + [duration]
    for start, end in zip(boundaries, boundaries[1:]):
        length = end - start
        if length <= 0:
            continue
        # 0s -> pale, 14s+ -> full ink
        weight = min(1.0, length / 14.0)
        shade = _blend("#EDF0EE", "#3D4A52", weight)
        x0, x1 = x_of(start), x_of(end)
        parts.append(
            f'<rect x="{x0:.1f}" y="{TRACK_Y}" width="{max(0.6, x1 - x0):.1f}" '
            f'height="{TRACK_H}" fill="{shade}"/>'
        )
    parts.append(
        f'<rect x="{PAD_L}" y="{TRACK_Y}" width="{inner}" height="{TRACK_H}" '
        f'fill="none" stroke="#C9CFCB" stroke-width="1"/>'
    )

    # retention trace
    points = " ".join(
        f"{x_of(t):.1f},{y_of(v):.1f}"
        for t, v in zip(video.retention.times, video.retention.values)
    )
    parts.append(
        f'<polyline points="{points}" fill="none" stroke="#165C6B" '
        f'stroke-width="2" stroke-linejoin="round"/>'
    )

    # cliff markers spanning both lanes
    for finding in findings:
        x0, x1 = x_of(finding.start), x_of(finding.end)
        width = max(2.0, x1 - x0)
        parts.append(
            f'<rect x="{x0:.1f}" y="{CURVE_TOP}" width="{width:.1f}" '
            f'height="{TRACK_Y + TRACK_H - CURVE_TOP:.1f}" fill="#B8332A" opacity="0.10"/>'
        )
        parts.append(
            f'<line x1="{x0:.1f}" y1="{CURVE_TOP}" x2="{x0:.1f}" '
            f'y2="{TRACK_Y + TRACK_H:.1f}" stroke="#B8332A" stroke-width="1.4"/>'
        )
        parts.append(
            f'<text x="{x0:.1f}" y="{TRACK_Y + TRACK_H + 15:.1f}" text-anchor="middle" '
            f'font-family="monospace" font-size="10" fill="#B8332A">'
            f'{esc(timecode(finding.start))}</text>'
        )
        parts.append(
            f'<text x="{x0:.1f}" y="{TRACK_Y + TRACK_H + 27:.1f}" text-anchor="middle" '
            f'font-family="monospace" font-size="9.5" fill="#8A9098">'
            f'-{finding.hazard * 100:.1f}%</text>'
        )

    parts.append(
        f'<text x="{PAD_L}" y="{STRIP_H - 4}" font-family="monospace" font-size="9.5" '
        f'fill="#B4BAB6">0:00</text>'
    )
    parts.append(
        f'<text x="{STRIP_W - PAD_R}" y="{STRIP_H - 4}" text-anchor="end" '
        f'font-family="monospace" font-size="9.5" fill="#B4BAB6">'
        f'{esc(timecode(duration))}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _bars(pattern: Pattern) -> str:
    if not pattern.bins:
        return ""
    peak = max(b["mean_hazard"] for b in pattern.bins) or 1.0
    rows = []
    for b in pattern.bins:
        pct = 100.0 * b["mean_hazard"] / peak
        hot = "hot" if b["mean_hazard"] >= peak * 0.999 else ""
        label = f'{b["low"]:.1f}-{b["high"]:.1f}'
        rows.append(
            f'<div class="bar-row"><div class="bar-lab">{esc(label)}</div>'
            f'<div class="bar-track"><div class="bar-fill {hot}" style="width:{pct:.1f}%"></div></div>'
            f'<div class="bar-val">{b["mean_hazard"]*100:.2f}% / n={b["n"]}</div></div>'
        )
    return f'<div class="bars">{"".join(rows)}</div>'


def _patterns_section(patterns: list[Pattern]) -> str:
    if not patterns:
        return (
            '<section><div class="section-head"><h2>Channel patterns</h2></div>'
            '<p class="note">Not enough retention buckets yet. Patterns need roughly 15 '
            'videos with retention data before any effect can be separated from noise.</p></section>'
        )

    cards = []
    for pattern in patterns:
        tag = f'<span class="tag t-{pattern.confidence_label}">{pattern.confidence_label}</span>'
        cards.append(
            f'<div class="card"><div class="row">'
            f'<div class="gutter">{pattern.effect:.2f}&times;</div>'
            f'<div class="body">'
            f'<h3>{esc(pattern.title)}</h3>'
            f'<p class="note">{esc(pattern.detail)}</p>'
            f'<div>{tag}'
            f'<span class="mono" style="font-size:12px;color:var(--muted)">'
            f'95% CI {pattern.ci_low:.2f}-{pattern.ci_high:.2f} &middot; '
            f'p={pattern.p_value:.4f} &middot; n={pattern.n:,}</span></div>'
            f'{_bars(pattern)}'
            f'</div></div></div>'
        )
    return (
        '<section><div class="section-head"><span class="n">02</span>'
        '<h2>What this channel gets wrong</h2></div>'
        '<p class="note">Effects pooled across every video, measured on ordinary body content '
        'only. Sponsor reads, intros and end cards are excluded here because they would '
        'otherwise masquerade as evidence about pacing.</p>'
        f'{"".join(cards)}</section>'
    )


def _sponsor_section(costs: list[SponsorCost], rule: dict | None, summary: dict) -> str:
    if not costs:
        return ""

    rows = []
    for cost in costs[:12]:
        rows.append(
            f"<tr><td>{esc(cost.video_title[:52])}</td>"
            f'<td class="num">{esc(timecode(cost.start))}</td>'
            f'<td class="num">{cost.position:.0%}</td>'
            f'<td class="num">{cost.audience_lost:.1%}</td>'
            f'<td class="num">{cost.excess:.1%}</td>'
            f'<td class="num">{cost.viewers_lost:,.0f}</td></tr>'
        )

    verdict = ""
    if rule:
        direction = "more" if rule["effect"] > 1 else "less"
        strength = "t-strong" if rule["significant"] else "t-weak"
        confidence = (
            "This holds up statistically."
            if rule["significant"]
            else "This is suggestive but not yet significant -- more sponsored videos will settle it."
        )
        verdict = (
            f'<div class="card"><div class="row">'
            f'<div class="gutter">{rule["effect"]:.2f}&times;</div><div class="body">'
            f'<h3>Reads placed before {rule["split"]:.0%} of runtime cost '
            f'{rule["effect"]:.2f}&times; {direction}</h3>'
            f'<p class="note">Early reads (n={rule["early_n"]}) lose an average of '
            f'{rule["early_mean"]:.2%} of the audience beyond baseline. Later reads '
            f'(n={rule["late_n"]}) lose {rule["late_mean"]:.2%}. {confidence}</p>'
            f'<div><span class="tag {strength}">'
            f'{"significant" if rule["significant"] else "provisional"}</span>'
            f'<span class="mono" style="font-size:12px;color:var(--muted)">'
            f'95% CI {rule["ci_low"]:.2f}-{rule["ci_high"]:.2f} &middot; '
            f'p={rule["p_value"]:.4f}</span></div>'
            f'</div></div></div>'
        )

    total = summary.get("total_viewers_lost", 0.0)
    return (
        '<section><div class="section-head"><span class="n">03</span>'
        '<h2>What your sponsor reads actually cost</h2></div>'
        f'<p class="note">Across {summary.get("n", 0)} detected reads, roughly '
        f'{total:,.0f} viewers left during a sponsor segment. "Excess" is the loss beyond '
        'what an ordinary stretch of the same length costs in the same video, so it is the '
        'part actually attributable to the read.</p>'
        f"{verdict}"
        '<div class="card"><table><thead><tr><th>Video</th><th>Starts</th><th>Position</th>'
        '<th>Audience lost</th><th>Excess</th><th>Viewers</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _video_section(videos: list[Video], findings: list[Finding], limit: int) -> str:
    by_video: dict[str, list[Finding]] = {}
    for finding in findings:
        by_video.setdefault(finding.video_id, []).append(finding)

    ranked = sorted(
        videos,
        key=lambda v: sum(f.viewers_lost for f in by_video.get(v.video_id, [])),
        reverse=True,
    )[:limit]

    blocks = []
    for video in ranked:
        video_findings = sorted(by_video.get(video.video_id, []), key=lambda f: f.start)
        items = []
        for finding in video_findings:
            causes = "".join(f"<li>{esc(c)}</li>" for c in finding.causes)
            quote = (
                f'<div class="quote">{esc(finding.quote)}</div>' if finding.quote else ""
            )
            items.append(
                f'<div class="finding"><div class="hl">'
                f"{esc(timecode(finding.start))}&ndash;{esc(timecode(finding.end))} "
                f"&middot; {finding.hazard:.1%} of remaining viewers left "
                f"(~{finding.viewers_lost:,.0f} people)</div>"
                f"<ul>{causes}</ul>{quote}</div>"
            )
        blocks.append(
            f'<div class="card"><div class="vid-head">'
            f"<h3>{esc(video.meta.short_title)}</h3>"
            f'<span class="vid-meta">{esc(timecode(video.meta.duration))} &middot; '
            f"{video.meta.views:,} views &middot; {len(video.timeline.cuts)} cuts</span></div>"
            f"{strip_svg(video, video_findings)}"
            f'<div class="legend">'
            f'<span><i style="background:#165C6B"></i>retention</span>'
            f'<span><i style="background:#8A9098"></i>cuts</span>'
            f'<span><i style="background:#F5E7CB"></i>sponsor read</span>'
            f'<span><i style="background:#B8332A"></i>cliff</span></div>'
            f'{"".join(items) or "<p class=note>No cliffs above threshold.</p>"}'
            f"</div>"
        )
    return (
        '<section><div class="section-head"><span class="n">04</span>'
        "<h2>Video by video</h2></div>"
        '<p class="note">The trace is retention. The band underneath is the edit: every tick '
        "is a cut. Red marks where the two line up badly.</p>"
        f'{"".join(blocks)}</section>'
    )


def render_report(
    report: Report,
    rule: dict | None = None,
    summary: dict | None = None,
    max_videos: int = 8,
) -> str:
    summary = summary or {}
    generated = report.generated_at or dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    synthetic = report.source == "synthetic"

    banner = ""
    if synthetic:
        banner = (
            '<div class="warn"><strong>Synthetic fixture data.</strong> These numbers come '
            "from the generator in <span class=mono>autopsy/sources/synthetic.py</span>, "
            "which plants known effects so the estimator can be tested against them. "
            "They describe no real channel and must not be presented as results.</div>"
        )
    for warning in report.warnings:
        banner += f'<div class="warn">{esc(warning)}</div>'

    with_retention = len(report.videos)
    total_cuts = sum(len(v.timeline.cuts) for v in report.videos)
    stats = [
        (f"{with_retention}", "videos analysed"),
        (f"{len(report.findings)}", "retention cliffs found"),
        (f"{report.total_viewers_lost:,.0f}", "viewers lost at cliffs"),
        (f"{total_cuts:,}", "cuts inspected"),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="v">{esc(v)}</div><div class="k">{esc(k)}</div></div>'
        for v, k in stats
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Retention autopsy &mdash; {esc(report.channel_title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body><div class="wrap">
<header class="masthead">
  <div class="eyebrow">Retention autopsy &middot; generated {esc(generated)}</div>
  <h1>{esc(report.channel_title)}</h1>
  <div class="sub">Every retention bucket in this channel, joined to what the edit was
  doing at that exact second.</div>
</header>
{banner}
<div class="stats">{stat_html}</div>
<p class="note">Churn is measured as the share of <em>remaining</em> viewers lost per bucket,
not raw slope. Raw slope is dominated by the first thirty seconds and hides everything after.</p>
{_patterns_section(report.patterns)}
{_sponsor_section(report.sponsor_costs, rule, summary)}
{_video_section(report.videos, report.findings, max_videos)}
<footer>
  Retention Autopsy &middot; retention from the YouTube Analytics API
  (<span class="mono">audienceRetention</span> / <span class="mono">elapsedVideoTimeRatio</span>),
  edit features from the video files. Source: {esc(report.source)}.
</footer>
</div></body></html>"""


def write_report(report: Report, path: str | Path, **kwargs) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(report, **kwargs), encoding="utf-8")
    return path
