<div align="center">

# Retention Autopsy

**YouTube tells you *when* people left. It never tells you *what you did* at that second.**

[![License: MIT](https://img.shields.io/badge/License-MIT-1E5F74?style=for-the-badge)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-165C6B?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-45%20passing-2E6B4F?style=for-the-badge)](tests/)
[![Core dependency](https://img.shields.io/badge/core%20dependency-numpy%20only-B8332A?style=for-the-badge)](pyproject.toml)

*Joins your YouTube retention graph to your actual edit, second by second,<br/>and tells you which editing decisions are costing you viewers.*

</div>

---

Every creator has stared at the retention graph in Studio, seen the cliff at 4:12, and had
no idea why it is there. The graph knows nothing about the video. Your editor knows
everything about the video and nothing about the graph.

This tool joins them. For every retention bucket in every video on a channel, it records
what the edit was doing at that exact moment — how long the current shot had been held,
how fast you were cutting, how fast you were talking, whether the mix dropped, whether a
sponsor read was playing — and then asks which of those things actually predicts people
leaving.

Not for one video. Across the whole catalogue, which is where it stops being an anecdote
and starts being a rule you can edit by.

```
Shots held past 14s without a cut
  lose 2.15x more of the remaining audience per bucket than everything below that line
  (2.34% vs 1.09% churn, n=160 vs 1,860)
  95% CI 2.02–2.27 · p=0.0025
```

---

## Quickstart

```bash
pip install -e .
python -m autopsy demo
open out/report.html
```

That runs the whole pipeline on synthetic fixture data and writes a self-contained HTML
report. No credentials, no API keys, about four seconds. The fixture report is watermarked
— see [Validation](#validation-what-the-tests-actually-prove) for why it exists.

---

## What you get

The report is a single self-contained HTML file. Its signature element is the **strip**:
the retention trace sitting directly on top of the edit timeline.

```
retention  ────────╮
                   ╰──────╮                    ╭─── the cliff
                          ╰────────────────────┴──────────────

edit       ▏▎▏▍▏▎▏▍▏▎  ███████████  ▏▎▏▍▏▎▏▍▏▎▏▍▏▎▏▍▏▎▏▍▏▎▏▍
                       └─ one long shot, no cut
                          0:00                          11:25
```

Each tick in the lower band is a cut; dark blocks are long shots held without cutting.
When a dark block sits directly under a dip in the trace, that *is* the finding — no
interpretation needed. Sponsor reads are shaded, cliffs are marked in red with their
timecode and the share of remaining audience lost.

Three things come out of it:

| Output | What it answers |
| --- | --- |
| **Per-video cliffs** | *"Why did people leave at 4:12 in this specific video?"* |
| **Channel patterns** | *"What do I keep doing wrong across everything I've made?"* |
| **Sponsor economics** | *"What did that read actually cost me, and does placement matter?"* |

---

## Running it on a real channel

Retention is owner-only data, so this needs your own channel (or one you have been given
access to).

**1. Google Cloud, once.** Create a project, enable **YouTube Data API v3** and **YouTube
Analytics API**, create an OAuth client of type *Desktop app*, download
`client_secrets.json`.

> On the OAuth consent screen, add your own Google account under **Test users**. Up to 100
> testers work without the full verification review — which is what makes this feasible in
> a weekend rather than a six-week approval cycle.

**2. Authorise and scan.**

```bash
python -m autopsy auth --secrets client_secrets.json
python -m autopsy scan --secrets client_secrets.json --max-videos 40
```

`scan` pulls metadata and the retention curve for each video and caches everything to
`out/channel.json`. It prints its quota estimate before spending anything.

**3. Add the edit side.** Retention alone gives you the graph you already had. The edit
features are what make it a diagnosis:

```bash
python -m autopsy edit --video-id dQw4w9WgXcQ --file ~/footage/episode12.mp4 \
                       --subtitles ~/footage/episode12.srt
```

Shot boundaries come from the file. Word timings come from your subtitle file if you have
one; otherwise pass `--whisper base` to transcribe locally.

**4. Report.**

```bash
python -m autopsy report
```

Everything is incremental. Run `edit` on three videos, generate a report, run it on ten
more, regenerate. Per-video findings work from the first video; channel-level patterns
need roughly 15 before the intervals mean anything, and the report says so when you have
fewer.

### Command reference

| Command | What it does | Needs credentials |
| --- | --- | :---: |
| `autopsy demo` | Full pipeline on synthetic fixture data | — |
| `autopsy auth` | One-time OAuth, caches a refresh token | yes |
| `autopsy scan` | Pulls metadata and retention for your channel | yes |
| `autopsy edit` | Extracts cuts, audio and transcript from a local file | — |
| `autopsy report` | Analyses the cache and writes the HTML report | — |

---

## What it measures

**Churn, not slope.** The target variable is the share of *remaining* viewers lost per
bucket, not the raw derivative of the curve. Raw slope is dominated by the first thirty
seconds — everybody bleeds viewers there, for reasons that have nothing to do with the
edit — and it makes late-video problems invisible because there is barely anyone left to
lose. Losing 5% of who is left at 8:00 should register as loudly as losing 5% at 0:20.

**Breakpoints, not quartiles.** Asking "do long shots hurt?" with fixed quartiles
systematically understates the answer. If shots only start costing you past 12s but the
top quartile begins at 7s, that bin averages harmless shots together with harmful ones and
the measured effect comes out roughly half its true size. So the tool searches for the
threshold where behaviour actually changes — and reports it, because *"your edge is 14
seconds"* is something an editor can use and *"the top quartile"* is not.

**Honest about the search.** Taking the maximum over ~25 candidate thresholds is a
multiple-comparisons problem: some split will always look good on pure noise. Significance
comes from a max-statistic permutation test — shuffle the target, redo the *entire*
search, and compare against the best ratio the search finds under the null. There is a
test asserting the tool does not invent an effect from random data.

**Sponsor reads, net of baseline.** Any sixty seconds of video loses viewers, so the raw
drop across a read overstates its cost. Each read is compared to the median churn of
ordinary body content in the same video, compounded over the same number of buckets. The
difference — the *excess* — is the part actually attributable to the read.

**Confounds excluded.** Channel patterns are computed on body content only. Sponsor reads
are the single strongest confound in the data: they are quiet, slowly cut, and people
leave. Left in, a sponsor read would masquerade as evidence that slow cutting is fatal.

---

## Architecture

```
sources/     youtube.py    OAuth, Data API, Analytics API (audienceRetention)
             synthetic.py  fixture generator with planted ground truth
extract/     video.py      shot boundaries + audio, via PySceneDetect or ffmpeg
             transcript.py SRT/VTT/Whisper, sponsor + intro + CTA detection
analysis/    align.py      THE JOIN — one row per retention bucket
             findings.py   per-video cliff detection and cause attribution
             patterns.py   cross-catalogue effects, sponsor economics
             stats.py      bootstrap CIs, permutation tests, breakpoint search
report/      html.py       self-contained HTML, inline SVG, no CDN
quota.py                   Data API unit budgeting
```

The pipeline is a straight line, and every stage is independently testable:

```
YouTube Analytics ─┐
                   ├─→  align.py  ─→  findings.py  ─┐
video file ────────┘    (the join)   patterns.py  ──┴─→  html.py
```

Three deliberate choices:

**Sponsor detection is keyword scoring, not an LLM call.** It runs offline, it is
deterministic — the same video always yields the same segment, which matters when a judge
re-runs the demo — and it is auditable: when it flags a read you can see which phrases
fired.

**Two independent paths for shot detection.** PySceneDetect when installed, ffmpeg's scene
filter otherwise. The tool degrades instead of failing on a machine without a Python video
stack.

**The core has one dependency.** numpy. Everything needed to run `demo`, pass the tests
and render a report installs in seconds. Google clients, PySceneDetect and Whisper are
optional extras, because a heavy install that fails on a conference wifi is a demo that
does not happen.

---

## Validation: what the tests actually prove

Anything can print a confident number from a retention curve. The question is whether that
number tracks reality.

`sources/synthetic.py` plants known effects — long shots cost 2.2x, sponsor reads cost 4x,
early reads cost another 1.8x on top — generates retention curves that obey them plus
noise, and the tests check the pipeline recovers those figures *from the curves alone*.

```bash
$ python -m pytest tests/ -q
45 passed
```

| Planted | Recovered | 95% CI | Verdict |
| --- | --- | --- | --- |
| Long shots cost **2.2x** | **2.15x** | 2.02 – 2.27 | inside the interval |
| Pure noise, no effect | not significant | — | no effect invented |

The strict one is `test_isolated_long_shot_effect_is_recovered`: a channel where shot
length is the only thing driving churn, and the planted 2.2x must land inside the estimated
confidence interval. Its companion, `test_no_effect_is_not_invented`, feeds the search pure
noise and asserts it stays insignificant.

`tests/test_youtube.py` covers the one path the fixture cannot reach — the live channel —
by replaying the response shapes a real catalogue produces against a fake API: a livestream
with no duration, a video still processing, a hidden view count, a bucket YouTube declines
to report, a page of uploads that were all deleted. No network, no credentials.

Two bugs found this way during the build, both of which would have shipped silently:

- Quartile binning reported the planted 2.2x effect as **1.39x**, because the true 12s
  threshold sat at the 92nd percentile and the search range was capped at the 85th. The
  tool would have understated a real problem by half.
- `ratio_bootstrap_ci` returned exactly `1.0` on groups too small to resample — rendering
  as *"measured, no effect"* when the truth was *"not measured yet"*. It now returns the
  point estimate with an unbounded interval.

---

## Limits

Worth knowing before you trust a number:

- **Correlational.** These are observational effects on your own catalogue, not
  experiments. Long shots co-occur with slow sections generally; the tool separates
  measurable confounds, not every possible one.
- **Retention needs views.** YouTube withholds `audienceRetention` for videos below a
  privacy threshold. `scan` skips those and tells you how many.
- **Bucket resolution is `duration / 100`.** On a ten-minute video that is six seconds per
  bucket. Sub-second edit decisions are below the noise floor of the data itself.
- **Sponsor detection is English-language keyword scoring.** It finds conventionally
  worded reads reliably and will miss an unconventional one. Every detected segment is
  drawn on the report strip so you can see what it caught.
- **Views are lifetime, not windowed.** "Viewers lost" figures are order-of-magnitude
  estimates for ranking findings against each other, not accounting.

---

## Quota

A default Data API project gets 10,000 units per day. Reads are cheap — a 40-video scan
costs 3 units: one `channels.list` to find the uploads playlist, then `playlistItems.list`
and `videos.list`, which both page 50 at a time. Retention comes from the Analytics API,
which is metered separately and costs no Data API units at all. `autopsy scan` prints its
estimate before spending anything and refuses calls that would overrun the budget.

Writes are the expensive ones at 50 units each. **This tool never writes.**

---

## Install options

```bash
pip install -e .              # core: numpy only
pip install -e '.[youtube]'   # + Google API clients
pip install -e '.[video]'     # + PySceneDetect
pip install -e '.[whisper]'   # + faster-whisper
pip install -e '.[dev]'       # + pytest
```

`ffmpeg` and `ffprobe` must be on PATH for the `edit` command.

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian / Ubuntu
```

---

## Roadmap

- **Export markers to the NLE** — an `.edl` or CSV importable into Premiere or Resolve,
  dropping a marker at each cliff directly on the editor's timeline.
- **Compare against your own best video**, not the channel average. The most useful
  benchmark is not the average of everything you have made; it is the one that worked.
- **Sponsor detection in more languages** — currently English-only keyword scoring,
  extensible by design (it is a dictionary in `transcript.py`).

---

## Team

Built for the Social Media Automation Hackathon.

| Name | Built |
| --- | --- |
| **Arnold Giovanny Wesche Sanchez** | Full pipeline — API client, edit extraction, statistical analysis, and report generation | |

---

## Licence

MIT — see [LICENSE](LICENSE).
