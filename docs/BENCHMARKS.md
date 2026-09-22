# Benchmarks

Every number here was measured on the machine below with
`uv run deskhand bench`, which is the reproducible version of these notes. The
numbers exist to be re-taken; if they are stale, delete them.

| | |
|---|---|
| machine | Apple silicon MacBook Pro (arm64), macOS, 1920x955 logical window |
| python | 3.12.9 in a `uv` venv, pyobjc 12.2 |
| applications | Ghostty (native AppKit, 13 targets), Google Chrome (Chromium, 65–520 targets) |
| date | 2026-09 (recorded with the commit that added this file) |

## The lesson that came first: a single step in a fresh process measures the wrong thing

```
observe, first frame :    315ms   (the application builds its accessibility tree here)
observe, warm median :      9ms   (samples [10, 9, 9, 9, 10])
probe,   warm median :    5.0ms
```

The first accessibility request to an application costs 300–360 ms on both
applications measured, and every later one costs 9–10 ms for a small tree. macOS
builds the tree lazily: whoever asks first pays for construction. A benchmark that
observes once, or an end-to-end run whose single step is the first thing a fresh
process does, is measuring tree construction and calling it perception cost.

That mistake was made here. An earlier revision of `docs/PLAN.md` announced
"612 ms per step, of which 595 ms is looking around" as the case for this
milestone. 612 ms was real, and it was a cold-start artifact. Corrected numbers
below.

Per round trip, measured over 34 calls in a warm walk: **0.13 ms per accessibility
IPC call**. So the cost model is:
`warm observe ≈ 5 ms + 0.13 ms × (calls)`, with two calls per node (one batched
attribute read, one action-name read), and the action-name read is skipped for
nodes with no name, no value, no geometry and no interactive role.

## Settling, before and after

The waiting loop used to sleep 50 ms before its first look and 50 ms between
looks, so it spent most of its time asleep after the interface had already
settled. It also compared full structure, which includes every element's `value`:
a page with a clock, a counter or a caret in it therefore never looked quiet, and
one Chrome page consumed the entire 2500 ms settle budget on every step.

| | before | after |
|---|---|---|
| Ghostty, quiet desktop | 100 ms of it asleep, ~271 ms measured end to end | **19 ms**, no sleep at all |
| Chrome, live page | **2532 ms** (the full budget, never converged) | **68–670 ms**, capped at 6 walks, reported as `settled: false` |

Three changes did that:

1. **No fixed sleeps.** A warm walk is about 5 ms, so the loop converges by
   looking; it yields only while the interface is actually changing.
2. **Settle on shape, not on values.** `shape_digest` keeps identity, labels,
   enabled state and selection and drops values. A live page stops looking
   permanently busy, and an interface that opens a panel or selects an option
   still changes shape.
3. **`before` seeds the wait.** Without it, a walk taken immediately after acting
   still shows the pre-action state, two more identical walks agree with it, and
   the loop returns having waited for nothing. With it, "still the same as before"
   means "the effect has not appeared yet". An action that never changes the shape
   — a scroll, a typed character, a wait — is given four looks before its silence
   is accepted.
4. **A frame cap.** A page that changes on every read will never agree with itself;
   six walks bounds the damage, and hitting the cap is reported in the view's notes
   rather than presented as a settled interface.

## Steady-state step cost

| phase | Ghostty (native) | Chrome (Chromium) |
|---|---|---|
| observe, warm | 9 ms | 24 ms at 75 targets, 44-53 ms at 404-509 targets (uncontrolled; spikes to 183 ms) |
| act (semantic `ax-press`) | 17 ms | not measured |
| settle, quiet | 19 ms | 68-670 ms, volatile page (uncontrolled) |
| **step, quiet interface** | **≈ 45 ms** | **≈ 90 ms** (uncontrolled) |
| observe, first request to that application | 315-326 ms | 337-356 ms |

The Ghostty column was measured back-to-back on a stable window and is the one to
trust. The Chrome column was taken while a person was using that browser, so its
spread is the page's activity as much as the tooling's cost.

`docs/PLAN.md` asked for under 200 ms per step; a quiet interface meets it with
room, and the first observation of an application does not and cannot.

## Method, and the confound that was found late

**The machine was in active use by a person while most of this was measured.** They
were operating the browser — navigating between pages, switching tabs — at the same
time as the Chrome observations below. On top of that, the tooling itself called
`activate` on the application under test at the start of every run, every time,
whether or not it was already in front.

So for the Chrome numbers: the application, the page and the window were changing
under someone else's hands and under the tooling's own focus changes. The Ghostty
numbers were taken as tight back-to-back loops against a 13-element native window,
and a browser in another window cannot affect an accessibility walk of that tree.
They stand. The Chrome numbers are `uncontrolled` and are labelled that way below.

Two defects came out of noticing this, both fixed:

- **`--focus` could fail silently.** A benchmark run asked for the browser, was told
  the activation had succeeded, and then timed the terminal that was still in front —
  because `activate` reporting success is not the same as the application being in
  front. `--focus` now verifies, retries, and raises rather than measuring the wrong
  application. It also does nothing at all when the application is already frontmost,
  and says whose window it took focus from when it is not.
- **The benchmark could not tell it was disturbed.** It now reports the window title
  and the target count per frame, and prints a warning when either changed during the
  run. That would have caught this in one run instead of several.

A measurement of an interface that a person is using is a measurement of two things
at once, and this file did not say so. It does now.

## A finding that is not a number: one live page is not the same twice

Five consecutive observations of the same Chrome window, same process, same page
**while a person was using it and the tooling was fighting it for focus**:

```
targets = 158, 520, 158, 519, 158
```

Later, on a different page in the same application:

```
eight rapid observations, no gap :  65, 65, 65, 65, 65, 65, 65, 65
eight observations, one second apart: 65, 65, 65, 65, 65, 65, 106, 156
```

**What this establishes:** for *this* page, in *this* environment — a live web
application page being operated by a person while the tooling repeatedly took focus —
the number of accessibility elements exposed for one window
varied by up to 8x between consecutive observations, in a timing-dependent way.

**What it does not establish**, and an earlier revision of this file claimed it:
that this is a property of Chromium in general, or of Electron applications, or
even of this application on a quiet machine. Neither does it establish the
mechanism: lazy tree construction, pruning, and teardown after the last client goes
away are all consistent with what was seen, and re-asserting
`AXEnhancedUserInterface` on every observation produced
`74, 65, 68, 131, 120, 105, 94, 94`, which is inconclusive because the page was
changing on its own throughout.

**So the confound is now measured, not just admitted.** Two control runs were taken
afterwards with the new `deskhand stability` command, which observes the frontmost
window repeatedly, takes no focus of its own, and reports per frame what it saw:

```
Ghostty, 9 frames, one second apart:
  same window, same shape, 13 targets, 7-15ms each
  frame 10: window changed to a different tab -> 14 targets, different shape

ChatGPT (Chromium), 8 frames, one second apart, nobody touching it:
  same window, same shape, 20 targets, 17ms each
  verdict: nothing was disturbing this measurement

X/Twitter (Chromium, a heavy single-page app), 8 frames, a second apart,
measured over ssh from another machine, nobody touching it:
  49 targets on every frame, one shape throughout, warm median 87ms
  verdict: nothing was disturbing this measurement
```

The first of those is the point: **one tab switch, by a person, moved the target
count and the shape of a native application in the middle of a five-frame run.**
The other two are the counterweight, and they were taken on two different machines:
a Chromium window nobody is using held an identical shape for eight consecutive
frames, twice, once at 20 targets and once at 49 on a page as restless as X.

That does not prove the earlier 158/520 alternation was the colleague rather than
Chromium — the two were entangled at the time and stay entangled now — but it moves
the balance a long way towards "a live page being used", which is also what the
differing window titles across those runs suggested.

**To establish it properly** you would need a static page (`about:blank` or an
already-loaded document), nobody touching the machine, no `--focus` churn, a
non-Chromium application measured the same way as a control, and the title and count
recorded per frame — which `deskhand stability` now does.

**Why it still matters, at the smaller size the evidence supports:**

- Id-addressed targets can vanish between the decision and the action on a page that
  is changing. The freshness guard turns that into a retry rather than a wrong click,
  which is the behaviour working as designed, but it costs steps.
- Settling on such a window hits the frame cap, so those steps cost 0.5-0.7 s and
  carry `settled: false`.
- M2's "zero coordinate clicks on Spotify" remains **unverified**, not refuted. It was
  written before any of this was measured, and it should be re-written as "every
  coordinate click is counted and explained".

## Not measured

- **The pixel path at all.** Screen Recording is not granted on the measuring
  machine, and macOS refuses to prompt for it from a command line process, so the
  screenshot, the recognition pass, the hit test and region caching are all
  unverified. Every number above is from `--no-pixels` runs.
- A second display, a non-Chinese interface, or a window larger than one screen.
- Long-run stability: no measurement has been taken over hundreds of steps, so
  nothing here says anything about leaks, memory growth, or drift in the
  accessibility tree over time.
