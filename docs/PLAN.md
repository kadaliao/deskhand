# Plan

Milestones are ordered by risk, not by ambition. Each one has an acceptance
criterion that is a measurement, not an opinion.

Status: `done`, `written` (code exists, needs a real machine), `todo`.

---

## M0 — the skeleton, testable without a Mac · done

The loop, the seams, the fusion invariant, the failure paths, the JSON boundary,
the CLI, the docs. Everything below runs anywhere:

```bash
python -m deskhand demo          # or: uv run deskhand demo
python -m pytest -q              # 108 tests
python -m ruff check .           # clean
python -m mypy src               # strict, clean
```

Acceptance (met): the whole loop is exercised by tests, including a decider that
raises, an impossible target, an action the backend cannot do, a stale target, a
budget exhaustion, a cancel, and a `DONE` claim that must be refused.

---

## M1 — a real application, and the measurement that changed this milestone · written

Original goal: prove the semantic path on System Settings with pixels disabled.
**A real machine disproved that goal**, and the measurement is more useful than
the goal was.

### What was measured

`deskhand ax --focus 系统设置` on a **Chinese** macOS, and `--focus ChatGPT` /
`--focus Ghostty` for comparison:

| Measurement | Value |
|---|---|
| Native app (Ghostty) | 22 targets in **298–436 ms** |
| Chromium app (ChatGPT) | **38 nodes**, only **8** implement `AXPress`, 35 have geometry, 38 unique ids |
| System Settings, Chinese | **112–113 targets**; 39 sidebar rows; **0 of them have a label**; 25 names came from `AXIdentifier`; 76 are click-only |
| Accessibility calls per node | **2** (one batched read, one action-name read); 4 single reads for a 38 node tree. The unbatched shape was ~13 per node |
| Screen Recording | not granted, so the pixel path is unverified |

The sidebar finding is structural, not a quirk: each of the 39 rows has geometry
(198×32, stacked), one child, and that child is an unnamed `AXCell`. On a
localised macOS the names of those rows exist **only as pixels**. Where names do
exist they are internal identifiers — `微信_Title`,
`com.apple.systempreferences.AppleIDSettings*AppleIDSettings` — which is why such
a name is now flagged `from-identifier` instead of being passed off as a label.

### Revised acceptance

The task is still "switch Appearance to Dark", because it is a good task. What
changed is what it has to prove:

- The settings sidebar row for the pane is addressable **because pixels named
  it**: a `row:outlinerow` target whose extra names came from recognised text at
  its own geometry. `doctor` must show `fusion.absorbed > 0`.
- The actual setting change is **semantic**: the Light/Dark control is a labelled
  radio button pressed with `ax-press`, not a coordinate click.
- Coordinate clicks are allowed for the row itself, and the count is reported —
  the point is that they are counted, not that they are zero.
- 3 steps or fewer, with the per-step breakdown reported. The target for total
  step cost moved to M3, because M1 measured it at 612 ms and the number is a
  perception cost, not an action cost.

Needs from the user:
1. **Accessibility** — already granted on this machine.
2. **Screen Recording** — now required for M1, because of the finding above, and
   it cannot be granted from here. Measured with `log show`:

   ```
   tccd: service=kTCCServiceScreenCapture, preflight=no
   tccd: Service kTCCServiceScreenCapture does not allow prompting; returning denied.
   ```

   macOS **refuses to prompt** for Screen Recording when the caller is a command
   line or daemon process, so there is no dialog to click and no name to read off
   one. `deskhand permit` reports this instead of pretending to have asked, prints
   the responsible application when the process chain contains one, and
   `--open` jumps straight to the settings page.

   The practical consequence on this machine: the chain is
   `python <- uv <- bash <- pi <- fish <- herdr`, and `herdr` runs detached from
   its terminal, so no ancestor is the terminal the command was typed in and there
   is no `.app` to add to the list with `+`. The way through is to run deskhand
   from a plain terminal window (Ghostty, iTerm, Terminal) opened directly, so the
   terminal application is responsible, grant *that* Screen Recording, and restart
   it. Accessibility, by contrast, does prompt, and asking for it also adds the
   application to the Accessibility list as a pending entry.
3. The target application frontmost, or `--focus <name>`.

Before running anything, rehearse it. This is what caught the localisation
problem, and it clicks nothing:

```bash
uv run deskhand run --task examples/appearance.zh-CN.json --dry-run --focus 系统设置
```

### Execution verified on a real machine

`examples/verify_execution.py` switches a terminal tab and switches back: the
smallest real action that exercises observe, validate, freshness, act, settle,
judge and restore, while changing nothing a person would miss. It refuses to act
on anything that is not a `radiobutton:tabbutton` (the "Close tab" buttons are one
attribute away in the same row), and it refuses to guess which tab is selected.

| | |
|---|---|
| observe | 24 targets in **324 ms** (Ghostty, 3 tabs) |
| act | **17 ms**, route **`ax-press`** -- semantic, no coordinate click |
| settle | **271 ms** |
| **step total** | **612 ms** |
| outcome | switched, `revision` and `content` both changed, and the original tab was **restored and verified** |

`verdict: PASS (switched=True, restored=True, semantic=True)`

So the execution path works on real hardware, and the plan's original "per-step
under 300 ms" criterion **is not met: it is 612 ms**, of which 595 ms is looking
around rather than acting. Acting is nearly free; perception is not. That is the
whole case for M3, and it is now a measurement instead of a suspicion.

### Four bugs the real machine found

All fixed, all pinned by regression tests, all invisible to review and to
fake-desktop tests:

1. `AXValueGetType` answers `0` for anything that is not an `AXValue` — including
   element references and child arrays. Reading that as "an AXValue of kind 0"
   deleted every element and every child list, and perception returned an empty
   desktop.
2. An unsupported attribute arrives as an `AXValue` wrapping an error code, not
   as null, so it became the string `<AXValue ... {value = error:-25212 ...}>` in
   an element's subrole and inside its identity hash.
3. `AXPosition` is an `AXValueRef` with no `.x` attribute, so the obvious
   `value.x` raised and every rectangle in the tree was silently lost.
4. **"Which one is selected" was being thrown away**, in two different ways.
   AppKit expresses selection on the *parent* (`AXSelectedChildren`,
   `AXSelectedRows`) and children often say nothing; and where a control does
   answer, it answers with a boolean `AXValue` -- Ghostty's tabs are
   `AXRadioButton` with `AXValue` True for the active tab. Reading only
   `AXChildren` lost the first, and stringifying values into `value="True"` turned
   the second from a boolean into a spelling. Both are fixed, and selection is now
   part of what a target means: it appears in the freshness fingerprint and in
   both digests, so *choosing* an option registers as a change. That matters
   because the whole task is "choose Dark".

Also from this run: `--focus` needed a retry, because Chrome (rather than the
user) stole focus back on the first attempt. The verification script checks the
frontmost application before acting and aborts when it is not the intended one.

## M2 — Electron, where accessibility has to be asked · written, premise in doubt

**Read the Chromium finding in `docs/BENCHMARKS.md` before trusting the acceptance
criteria below.** Consecutive observations of one Chrome window returned 158, 520,
158, 519 and 158 elements, and the reason is not established. Id-addressed targets
can therefore disappear between deciding and acting. "Zero coordinate clicks" was
written before that was measured, and may need to become "every coordinate click is
counted and explained".

Goal: turn the reference project's pixel-heavy Spotify example into a mostly
semantic one.

Target scenario: open Spotify, search for a track, play it.

```bash
python -m deskhand doctor --json    # before/after the Chromium nudge
```

Acceptance:
- `doctor` shows the app exposes `AXManualAccessibility` (or
  `AXEnhancedUserInterface`) and that the target count **rises** after the nudge
  is applied. Numbers before and after go in the commit message.
- The search field is a `text_field` target with `TYPE` offered, and `TYPE`
  executes through `ax-set-value` or `ax-focus+keys` — never a blind coordinate
  click followed by typing into whatever had focus.
- **Zero `click` routes** for the play action. Every button pressed through
  `ax-press`.
- Pixel targets that remain are only regions accessibility genuinely cannot see
  (album art text, the now-playing ticker), and the fusion notes show how many
  were absorbed and how many were recovered.

Risk: Chromium builds its tree asynchronously, so the first observation after the
nudge is thin. Already handled with a wait.

Measured on one Chromium application so far: enabling the tree gave 38 nodes,
but the web content itself was **not** in it — only window chrome, two overflow
buttons and a splitter. That is a real result about the thesis, not a bug: for
Chromium, accessibility supplies identity and geometry, and the pixels still
supply most of the meaning. `AXScrollToVisible` is available on almost every
element and is worth modelling as a verb before clicking something scrolled out
of view.

The label lesson from M1 applies here too: names that arrive from
`AXIdentifier` are flagged `from-identifier`, so nothing downstream treats an
internal id as a name a person would recognise.

---

## M3 — perception cost and settling · done, with the premise corrected

Baseline measured in M1: "612 ms per step, of which 595 ms is looking around".
**That baseline was wrong**, and finding out why was most of the value of this
milestone. 612 ms was a single step in a fresh process, and the first
accessibility request to an application costs 300-360 ms on both applications
measured because macOS builds the tree lazily. Every later walk is 9-53 ms. Details
and method in `docs/BENCHMARKS.md`.

Done:

- **Fixed sleeps removed from settling.** The loop slept 50 ms before its first
  look and 50 ms between looks. It now looks, and yields only while the interface
  is actually changing.
- **Settling compares shape, not values.** A page with a clock, a counter or a
  caret in it changes a value on every read and therefore never looked quiet: one
  Chrome page spent the whole 2500 ms budget on every step. `shape_digest` keeps
  identity, labels, enabled state and selection, and drops values.
- **`before` seeds the wait.** A walk taken right after acting still shows the
  pre-action state, and the old loop would happily agree with it twice and declare
  the interface settled.
- **A frame cap, reported honestly.** A page that changes on every read gets six
  walks and then a view marked `settled: false`, instead of a silent 2.5 second
  stall or a half-settled view presented as a settled one.
- **The waiting loop is now a tested unit.** `deskhand/settle.py` takes its clock
  and its sleep as arguments, so it is tested by the iteration rather than by
  stopwatch. It had no tests before, which is why it stayed wrong.
- **`deskhand bench`** so these numbers can be re-taken rather than trusted.
- **Fewer round trips per node:** action names are not fetched for nodes with no
  name, no value, no geometry and no interactive role.

Result:

| | before | after |
|---|---|---|
| Ghostty, settle | ~271 ms (100 ms of it asleep) | **19 ms** |
| Chrome live page, settle | 2532 ms (full budget, never converged) | 68-670 ms, capped, `settled: false` |
| step, quiet interface | 612 ms | **≈ 45 ms** |
| step, Chromium window | — | ≈ 90 ms |

The `<200 ms per step` target is met for a quiet interface. The first observation
of an application costs ~320 ms and cannot be avoided; it is paid once per
application per process.

Still open in M3, in the order the measurements now justify them:

1. **Region-scoped recognition** and **`ScreenCaptureKit`** — unchanged, and
   unmeasurable here until Screen Recording is granted.
2. **Notification-driven waiting** (`AXObserver`). Much less valuable than it
   looked: polling a warm tree costs 5 ms, so event-driven settling would save
   single-digit milliseconds on a quiet interface. It is now justified by the
   Chromium finding instead — on a tree that changes on every read, an event stream
   is the only way to know whether anything is *still* happening.
3. **Incremental observation.** With notifications naming the element that changed,
   a step could patch the previous view instead of walking the tree. This is the
   only route to a materially faster step, and it depends on (2).

## M4 — the decision seam · todo

The shipped deciders are deterministic, which is right for testing and wrong for
general use. Two things to add, in this order:

1. **A verifier that can read a screen.** A `Verifier` that asks a model whether
   each check holds against the live view, so `DONE` is independently confirmed
   instead of refused. This is the honest version of what the reference project
   claimed with `verification`.
2. **An `LLMDecider` adapter.** Small: `Decider` is one method. It must send the
   `View.brief()` payload, never coordinates, and must return a `Choice` by
   target id or by label. Provider-agnostic, key from the environment, no
   hard-coded vendor default.

Acceptance: an end-to-end run on M2's scenario with a model decider and a model
verifier, where the trace shows the decision was made from structured state and
the verification was independent of it.

---

## M5 — perception beyond windows · todo

Custom canvases (timelines, node graphs, CAD) expose no semantics and no useful
text. They need their own `Source`, with `rank` somewhere above pixels, and they
must go through the same fusion and the same `Target` shape. This is the road
the reference project's roadmap also points at; doing it here means only adding
a source, because the seam already exists.

---

## Guardrails

Things that must not regress, each pinned by a test:

- the pixel-into-accessibility fusion rule,
- refusals: unknown target, unadvertised verb, unsupplied input key,
- `DONE` without a confirming verifier escalates,
- every failure path is survivable and budgeted,
- freshness checked once per action, not twice,
- identity from meaning, never from an address,
- `mypy --strict` clean and `ruff` clean in CI.

## Open questions

- **Multi-window applications.** The sensor follows the frontmost window. A
  window selector belongs on `Task`, not in the sensor.
- **Multi-display.** Coordinates are global, so it should work, but the
  visibility filter assumes one window frame. Needs a second display to check.
- **Non-English interfaces.** Nothing in the pixel path assumes English any more.
  The accessibility path never did. Worth an actual test on a Chinese interface.
- **The `TYPE` fallback.** `ax-focus+keys` verifies focus before typing, but it
  is still a keystroke path. If a field is focused but a stray modifier is
  stuck, that is invisible. An `AXSelectedText`-based path would be stronger;
  needs a real field that is not `AXValue`-settable to test against.
