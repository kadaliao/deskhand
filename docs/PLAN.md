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
- 3 steps or fewer, per-step `ms.total` reported.

Needs from the user:
1. **Accessibility** — already granted on this machine.
2. **Screen Recording** — now required for M1, because of the finding above.
   System Settings → Privacy & Security → Screen Recording.
3. The target application frontmost, or `--focus <name>`.

Before running anything, rehearse it. This is what caught the localisation
problem, and it clicks nothing:

```bash
uv run deskhand run --task examples/appearance.zh-CN.json --dry-run --focus 系统设置
```

### Three bugs the real machine found

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

## M2 — Electron, where accessibility has to be asked · written

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

## M3 — perception cost and settling · todo

The two known inefficiencies, in order of payoff.

1. **Event-driven settling.** Subscribe with `AXObserver` to window, focus,
  value and children notifications instead of polling a structural probe. This
  removes one accessibility walk per poll from every step.
2. **Region-scoped recognition.** Cache the last screenshot; only re-run
  recognition on regions whose window rectangle changed, instead of the whole
  window on every observation.
3. **`ScreenCaptureKit`.** `CGWindowListCreateImage` is deprecated by Apple and
   the code already says so. This is the replacement.

Acceptance:
- Idle settle time per step drops below **50 ms** (measured by `ms.settle`).
- A full observation on a dense window costs less than the current baseline;
  the number is recorded in `docs/BENCHMARKS.md` with the hardware.

---

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
