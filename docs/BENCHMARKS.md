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
| observe, warm | 9 ms | 24 ms at 75 targets, 44–53 ms at 404–509 targets |
| act (semantic `ax-press`) | 17 ms | not measured |
| settle, quiet | 19 ms | 68–670 ms, volatile page |
| **step, quiet interface** | **≈ 45 ms** | **≈ 90 ms** |
| observe, first request to that application | 315–326 ms | 337–356 ms |

`docs/PLAN.md` asked for under 200 ms per step; a quiet interface meets it with
room, and the first observation of an application does not and cannot.

## A finding that is not a number: Chromium's tree is not the same twice

Five consecutive observations of **the same Chrome window**, same process, same
page, no interaction in between:

```
targets = 158, 520, 158, 519, 158
```

Later, on a different page in the same application:

```
eight rapid observations, no gap :  65, 65, 65, 65, 65, 65, 65, 65
eight observations, one second apart: 65, 65, 65, 65, 65, 65, 106, 156
```

**What this establishes:** for a Chromium application, the number and identity of
accessibility elements exposed for one window varies by up to 8x between
consecutive observations, and the variation depends on timing.

**What it does not establish:** *why*. Re-asserting `AXEnhancedUserInterface` on
every observation produced `74, 65, 68, 131, 120, 105, 94, 94`, which is
inconclusive — the measurement was confounded by the page itself changing. Lazy
tree construction, pruning, and teardown after the last client goes away are all
consistent with what was observed, and none of them has been demonstrated.

**Why it matters:**

- Id-addressed targets can vanish between the decision and the action. The
  freshness guard turns that into a retry rather than a wrong click, which is the
  behaviour working as designed, but it costs steps.
- Settling on such a window will usually hit the frame cap, so those steps cost
  0.5–0.7 s and carry `settled: false`.
- It puts a question mark on M2's "zero coordinate clicks on Spotify", which was
  written before this was measured.

## Not measured

- **The pixel path at all.** Screen Recording is not granted on the measuring
  machine, and macOS refuses to prompt for it from a command line process, so the
  screenshot, the recognition pass, the hit test and region caching are all
  unverified. Every number above is from `--no-pixels` runs.
- A second display, a non-Chinese interface, or a window larger than one screen.
- Long-run stability: no measurement has been taken over hundreds of steps, so
  nothing here says anything about leaks, memory growth, or drift in the
  accessibility tree over time.
