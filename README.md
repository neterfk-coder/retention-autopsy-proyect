<div align="center">

# Retention Autopsy

**YouTube tells you *when* people left.**
**It never tells you *what you did* at that second.**

*This tool joins the two.*

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-black.svg)](https://www.python.org/)
[![Core dependency: numpy](https://img.shields.io/badge/Core%20dependency-numpy%20only-black.svg)](https://numpy.org/)

</div>

---

## The problem

Every creator has done this. You open YouTube Studio, look at the retention graph, and see a cliff at 4:12. A fifth of your audience walked out at that exact moment.

And you have no idea why.

The graph knows *when* people left. It has never seen your video. It doesn't know that at 4:12 you were holding a single shot for eighteen seconds without cutting, that your delivery had slowed to a crawl, or that a sponsor read was playing.

Your editing timeline knows everything about the video and nothing about the audience.

**Nobody joins the two.** TubeBuddy, VidIQ, Studio itself — they can all show you the curve, but none of them have ever seen the footage. So the single most important question an editor can ask has no tool that answers it.

That gap is this project.

---

## What it does

For every retention bucket YouTube reports, Retention Autopsy records what the edit was doing at that exact timestamp — and puts both in the same row.

```
video_id   t       hazard   shot_age   cut_rate   speech_rate   in_sponsor
dQw4       182.4   0.031    11.2       1.0        2.4           0
```

From there it produces four things.

### Per-video cliff detection

Finds the runs of buckets where you're losing audience faster than that video's own baseline, then attributes a cause in plain language.

```
4:05–5:15   lost 35.7% of remaining viewers (~8,573 people)
            — a sponsor read is playing

1:30–2:36   lost 37.3% of remaining viewers (~11,455 people)
            — the same shot has been held for 17s with no cut;
              cutting has slowed to 0.0 cuts per 10s
```

Thresholds are computed **per video**, not globally. A talking-head essay and a fast vlog have completely different baseline churn, and a global threshold would report every essay as one long catastrophe.

### Cross-catalogue patterns

This is where it stops being an anecdote. Pooling every video gives thousands of data points — enough to ask which decisions predict people leaving *on this specific channel*.

The output isn't a regression coefficient. It's a rule you can tape to your monitor:

```
Shots held past 14s without a cut
  lose 2.15x more of the remaining audience per bucket than everything below that line
  (2.34% vs 1.09% churn, n=160 vs 1,860)
  95% CI 2.02–2.27 · p=0.0025
```

### Sponsor economics

Not what a sponsor *paid* you — what the read **cost** you in audience, net of the churn any sixty seconds of video causes anyway.

And whether placement makes a measurable difference. That's a pricing decision for the next contract.

### A self-contained HTML report

Inline SVG. No CDN, no build step, no network at render time. It opens from a USB stick on conference wifi that has given up.

The signature element is the strip: the retention trace sitting directly on top of the edit timeline. A dark block — a long shot held — sitting under a dip in the curve *is* the finding, visible at a glance.

---

## Quickstart

```bash
pip install -e .
python -m autopsy demo
open out/report.html
```

Four seconds. No credentials, no API keys. The full pipeline runs on synthetic fixture data and writes a complete HTML report.

That report is **watermarked as synthetic** — see [Validation](#validation) for why it exists and why it is not a demo prop.

---

## Running it on a real channel

Retention is owner-only data, so this needs your own channel.

### 1. Google Cloud, once

Create a project, enable **YouTube Data API v3** and **YouTube Analytics API**, create an OAuth client of type *Desktop app*, and download `client_secrets.json`.

> **The step that breaks demos at 3 AM:** on the OAuth consent screen, add your own Google account under **Test users**. Up to 100 testers work without the full verification review — which is what makes this feasible in a weekend rather than a six-week approval cycle.

### 2. Authorise and scan

```bash
python -m autopsy auth --secrets client_secrets.json
python -m autopsy scan --secrets client_secrets.json --max-videos 40
```

`scan` pulls metadata and the retention curve for each video, caching everything to `out/channel.json`. It prints its quota estimate **before** spending anything.

### 3. Add the edit side

Retention alone just gives you back the graph you already had. The edit features are what turn it into a diagnosis.

```bash
python -m autopsy edit --video-id VIDEO_ID \
                       --file episode12.mp4 \
                       --subtitles episode12.srt
```

Shot boundaries come from the file. Word timings come from your subtitle file if you have one; otherwise pass `--whisper base` to transcribe locally.

### 4. Report

```bash
python -m autopsy report
```

**Everything is incremental.** Run `edit` on three videos, generate a report, run it on ten more, regenerate. Per-video findings work from the very first video. Channel-level patterns need roughly fifteen before the intervals mean anything — and the report says so when you have fewer, instead of bluffing.

---

## Methodology

Four decisions that shaped every number this tool produces.

### Churn, not slope

The target variable is the share of **remaining** viewers lost per bucket, not the raw derivative of the curve.

Raw slope is dominated by the first thirty seconds — everybody bleeds viewers there, for reasons that have nothing to do with the edit — and it makes late-video problems invisible because there's barely anyone left to lose.

Losing 5% of who's left at 8:00 should register as loudly as losing 5% at 0:20. Hazard does that. Slope does not.

### Breakpoints, not quartiles

Asking *"do long shots hurt?"* with fixed quartiles systematically understates the answer.

If shots only start costing you past 12s but the top quartile begins at 7s, that bin averages harmless shots together with harmful ones — and the measured effect comes out roughly half its true size.

So the tool searches ~25 candidate thresholds for where behaviour **actually** changes, and reports it. *"Your edge is 14 seconds"* is something an editor can act on. *"The top quartile"* is not.

### Honest about the search

Taking the maximum over 25 candidate thresholds is a multiple-comparisons problem: some split will always look impressive, even on pure noise.

Significance therefore comes from a **max-statistic permutation test** — shuffle the target, redo the *entire* search, and compare the observed ratio against the best one the search finds under the null.

There is a test that feeds the search random data and asserts it does **not** invent an effect.

### Confounds excluded

Channel patterns are computed on body content only.

Sponsor reads are the single strongest confound in the data: they are quiet, slowly cut, and people leave. Left in, a sponsor read would masquerade as evidence that slow cutting is fatal in general.

---

## Architecture

```
sources/     youtube.py      OAuth, Data API, Analytics API (audienceRetention)
             synthetic.py    fixture generator with planted ground truth

extract/     video.py        shot boundaries + audio, via ffmpeg or PySceneDetect
             transcript.py   SRT/VTT/Whisper, sponsor + intro + CTA detection

analysis/    align.py        THE JOIN — one row per retention bucket
             findings.py     per-video cliff detection and cause attribution
             patterns.py     cross-catalogue effects, sponsor economics
             stats.py        bootstrap CIs, permutation tests, breakpoint search

report/      html.py         self-contained HTML, inline SVG, no CDN

quota.py                     YouTube Data API unit budgeting
```

### Three deliberate choices

**Sponsor detection is keyword scoring, not an LLM call.**
It runs offline. It is deterministic — the same video always yields the same segment, which matters when a judge re-runs the demo. And it is auditable: when it flags a read, you can see exactly which phrases fired.

**Two independent paths for shot detection.**
PySceneDetect when installed, ffmpeg's scene filter otherwise. The tool degrades instead of failing on a machine without a Python video stack.

**The core has one dependency: numpy.**
Everything needed to run the demo, pass the tests and render a report installs in seconds. Google clients, PySceneDetect and Whisper are optional extras — because a heavy install that fails on conference wifi is a demo that doesn't happen.

---

## Validation

Anything can print a confident number from a retention curve. The question is whether that number **tracks reality**.

`sources/synthetic.py` plants known effects — long shots cost 2.2x, sponsor reads cost 4x, early reads cost another 1.8x on top — generates retention curves that obey them plus noise, and the tests check the pipeline recovers those figures *from the curves alone*, with nothing telling it the ground truth.

```bash
$ python -m pytest tests/ -q
45 passed
```

**The strict one** is `test_isolated_long_shot_effect_is_recovered`: a channel where shot length is the only thing driving churn, and the planted 2.2x must land **inside** the estimated confidence interval.

**Its companion** is `test_no_effect_is_not_invented`: it feeds the search pure noise and asserts the result stays insignificant.

`tests/test_youtube.py` covers the one path the fixture cannot reach — the live channel — by replaying the response shapes a real catalogue produces against a fake API: a livestream with no duration, a video still processing, a hidden view count, a bucket YouTube declines to report. No network, no credentials.

### Two bugs this caught

Both would have shipped silently.

> **Quartile binning reported the planted 2.2x effect as 1.39x.**
> The true 12s threshold sat at the 92nd percentile, and the search range was capped at the 85th — so the search literally could not reach it. The tool would have understated a real editing problem **by half**, while looking perfectly confident. This is what led to the breakpoint search.

> **`ratio_bootstrap_ci` returned exactly `1.0` on groups too small to resample.**
> That renders as *"measured, no effect"* when the truth is *"not measured yet"* — a silent lie, and the worst kind, because it's indistinguishable from a real finding. It now returns the point estimate with an unbounded interval.

---

## Limits

Worth knowing before you trust a number.

| Limit | What it means |
|---|---|
| **Correlational, not causal** | Observational effects on your own catalogue. The tool excludes measurable confounds — sponsor reads, intros, end cards — but does not claim causation. |
| **Retention needs views** | YouTube withholds `audienceRetention` below its own privacy threshold. `scan` skips those videos and reports how many. |
| **Bucket resolution is `duration / 100`** | On a ten-minute video that's ~6 seconds per bucket. Sub-second edit decisions are below the noise floor of the data itself. |
| **Sponsor detection is English-only** | Keyword scoring catches conventional phrasing reliably and will miss unconventional wording. Extensible — it's a dictionary in `transcript.py`. |
| **Patterns need sample size** | A handful of short videos produces wide, non-significant intervals. The report labels these *weak* rather than rounding up. |
| **Views are lifetime, not windowed** | "Viewers lost" figures rank findings against each other. They are not accounting. |

---

## Quota

A default Data API project gets **10,000 units per day**.

Reads are cheap. A 40-video scan costs a handful of units — `playlistItems.list` and `videos.list` both page 50 at a time, and retention comes from the Analytics API, which is metered separately and costs **no Data API units at all**.

`autopsy scan` prints its estimate before spending anything and refuses calls that would overrun the budget.

Writes are the expensive operation at 50 units each. **This tool never writes.**

---

## Installation

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

## Command reference

| Command | What it does |
|---|---|
| `autopsy demo` | Full pipeline on synthetic fixture data. No credentials. |
| `autopsy auth` | One-time OAuth. Caches a refresh token. |
| `autopsy scan` | Pulls metadata and retention for your channel. |
| `autopsy edit` | Extracts cuts, audio and transcript from a local video file. |
| `autopsy report` | Analyses the cache and writes the HTML report. |

---

## Roadmap

**Export markers to the NLE** — a `.edl` or CSV importable into Premiere or Resolve, dropping a marker at each cliff directly on the editor's timeline. Seeing the problems inside the software where you fix them is the natural endpoint of this tool.

**Compare against your own best video** — the most useful benchmark isn't the average of everything you've made. It's the one that worked.

**Sponsor detection in more languages** — currently English-only keyword scoring, extensible by design.

---



---

## License

MIT — see [LICENSE](LICENSE).
