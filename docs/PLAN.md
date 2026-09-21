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

## M1 — accessibility only, on a real native app · written

Goal: prove the semantic path works end to end before pixels are allowed to
carry anything.

Target scenario: System Settings → switch Appearance to Dark (or Light).

```bash
python -m deskhand doctor --json          # record the baseline
python -m deskhand ax --limit 40          # what accessibility sees
python -m deskhand probe --no-pixels      # the fused view, pixels disabled
python -m deskhand run --task examples/appearance.json --trust-decider
```

Acceptance:
- `doctor` reports `accessibility: granted` and a non-zero target count for the app.
- The task completes in **≤ 3 steps** (open the pane, click the choice, done).
- **Every executed step's route is `ax-press`** — no `click`, no `keys`.
- Per-step `ms.total` under **300 ms** on this machine, with the breakdown visible
  in the trace.
- Running with `--no-pixels` changes nothing, which is the point.

Needs from the user:
1. Grant **Accessibility** to the terminal in System Settings → Privacy &
   Security → Accessibility.
2. Leave the target app frontmost when running (the sensor follows the frontmost
   application by design).

### Measured so far, on a real machine

Perception, identity, geometry and freshness are verified against real
applications. Actual task execution has **not** been run yet, because that takes
control of the machine.

| Measurement | Value |
|---|---|
| Native app (Ghostty, 22 element tree) | 22 targets in **298–436 ms** |
| Chromium app (ChatGPT, 1920×956 window) | **38 nodes**, of which only **8** implement `AXPress`; **35** have geometry; **38 unique ids** |
| After the interactivity gate | 20 targets, **15 aimable**, 6 click-only |
| Accessibility calls per node | **2** (one batched read, one action-name read); 4 single reads for a 38 node tree. The unbatched shape was ~13 per node |
| Screen Recording | not granted, so the pixel path is **unverified** |

Three bugs came out of this that review and fake-desktop tests both missed, and
each is now pinned by a regression test:
`AXValueGetType` answers `0` for anything that is not an `AXValue` (so a naive
unwrap deleted every element reference and child array), an unsupported attribute
arrives as an `AXValue` wrapping an error code rather than null (so it became an
element's subrole and got hashed into its identity), and `AXPosition` is an
`AXValueRef` with no `.x` attribute (so every rectangle was silently lost).

Blocks: nothing. This is the gate for everything else.

---

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
