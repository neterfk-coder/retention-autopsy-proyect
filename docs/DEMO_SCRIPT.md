# Demo script

Judges award bonus points for showing the tool *actually running and producing real
output*, not a recording of it having run. This is built around that.

Target: **3 minutes**. The rule throughout — never run anything slow on camera. Cache the
slow parts, run the fast parts live.

---

## Before you start

Do this the day before, not an hour before.

- [ ] `autopsy scan` completed against a real channel, `out/channel.json` cached
- [ ] `autopsy edit` run on at least 3 videos, so real cuts and real transcripts are in the cache
- [ ] `python -m pytest tests/ -q` passes
- [ ] YouTube Studio open in a browser tab, on the retention graph of one of those videos
- [ ] Terminal font large enough to read on a projector
- [ ] The synthetic demo works as a fallback if the network dies

---

## 0:00 — The problem, from inside Studio

Open on the browser tab, not the terminal.

> This is my retention graph. There is a cliff at 4:12. YouTube has told me *when* people
> left. It has never once told me *what I did* at that second — it knows nothing about
> what is in the video.

Ten seconds. Do not explain the architecture yet.

## 0:15 — Run it live

```bash
python -m autopsy report
```

This reads the cache and does the full analysis: the join, cliff detection, breakpoint
search, permutation tests. It finishes in seconds, in front of them. The terminal prints
the worst moments as it goes.

> Same channel. Every retention bucket in every video, joined to what the edit was doing
> at that exact second.

## 0:40 — The strip

Open `out/report.html`. Scroll to the video that is already open in Studio.

> Top line is the retention you just saw. The band underneath is the edit — dark means a
> long shot held without cutting. Here is 4:12.

Point at the dark block sitting directly under the dip. **This is the moment the whole
project is for.** Let it sit for a beat before you say anything else.

> Fourteen seconds on one shot, delivery dropped to 1.2 words a second, and 8% of everyone
> still watching left.

## 1:20 — The part nobody else has

Scroll up to the patterns section.

> That is one video. This is all of them pooled — about four thousand retention buckets.
> On this channel specifically, shots held past fourteen seconds lose 2.15 times more of
> the remaining audience. Confidence interval 2.02 to 2.27.

> That number does not exist in Studio, it does not exist in TubeBuddy or VidIQ, and it
> cannot exist in any tool that has not seen both my analytics *and* my footage.

## 1:50 — The money slide

Scroll to sponsor economics.

> Sponsor reads. Not what they paid — what they cost. Reads placed in the first 30% of the
> video cost 2.5 times more audience than later ones, net of what a normal stretch of
> video costs anyway.

> That is a pricing decision. That is where the read goes in the next contract.

## 2:20 — Show your work

```bash
python -m pytest tests/ -q
```

> The fixture generator plants known effects — long shots cost 2.2x — and generates
> retention curves that obey them. The tests check the estimator recovers 2.2 from the
> curves alone. There is also a test that feeds it pure noise and asserts it does *not*
> invent an effect.

> Two real bugs came out of this. Quartile binning was reporting that 2.2x as 1.39x,
> because the true threshold sat in the tail where the search could not reach.

Ten seconds on this. It separates you from every project that shows a chart and hopes.

## 2:50 — Close

> Every creator has a retention graph they cannot read. This reads it, and it gets sharper
> the more videos you have.

Stop. Do not do a summary slide.

---

## If something breaks

**No network / OAuth fails.** `python -m autopsy demo`. Say plainly: *"this is the fixture
data the tests run against — the real scan is cached, let me show you that instead."*
Never present synthetic numbers as real. The report watermarks itself and a judge who
notices you glossing over that has stopped believing the rest.

**Report will not open.** The HTML is self-contained; there is no build step and no CDN
dependency. `open out/report.html` from any machine that has the file.

**A question you cannot answer.** "That is in the limits section of the README" is a real
answer, and knowing where your tool is weak reads better than bluffing.

---

## Questions to have an answer ready for

**"Is this not just correlation?"** Yes, and the README says so. Observational effects on
your own catalogue. It separates the measurable confounds — sponsor reads, intros and end
cards are excluded from the pattern analysis — but it does not claim causation. It tells
you where to look, and you still have to watch the tape.

**"Why not use an LLM to find the sponsor?"** Determinism. A judge re-running the demo
gets the same segment every time, it works offline, and when it flags a read you can see
exactly which phrases fired.

**"What if I have five videos?"** Per-video findings work immediately. Channel patterns
need around fifteen, and the report tells you that instead of showing a confident number
built on nothing.

**"Could this run on someone else's channel?"** No, and that is the point. Retention is
owner-only data. It is also why this is defensible — no third-party tool can compute it.
