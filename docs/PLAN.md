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
exist they are internal identifiers — an app pane identifier of the form
`<reverse.dns.bundle>*<PaneName>`, or `<Something>_Title` — which is why such a name
is now flagged `from-identifier` instead of being passed off as a label.

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

### Screen Recording is granted: the first real runs

The permission M1 was waiting on is now in place, so the pixel path is measurable rather
than pending -- and the answers are not the ones this milestone hoped for. The fusion
invariant itself holds on real hardware: every recognised region resolved through
`AXUIElementCopyElementAtPosition` to a real element, so the words became extra names for
that element instead of a second identity space. Measured twice, on two windows, with the
overlay forced:

| Window | semantic | recognised | absorbed | left as pixels | `hits` | ms |
|---|---|---|---|---|---|---|
| System Settings | 104 | 15 | **15** | **0** | 15 | 912 |
| Finder Quick Look | 60 | 30 | **30** | **0** | 29 | -- |

### But M1's revised acceptance is not met, and the run says why

The criterion is not "pixels are absorbed". It is "**the settings sidebar row for the pane
is addressable *because pixels named it***", and on this machine it is not:

- The 27 sidebar rows are there exactly as M1 described -- `row:outlinerow`, 198x32,
  stacked -- and **not one of them has a label**. The original finding reproduces.
- Of the 15 regions absorbed on that window, **none landed on any of those rows**. The
  names went to other elements, several badly garbled (`'Liquffj Glas5'`, `'o*&*'`), so
  the recognition pass is absorbed correctly and is not yet reading the sidebar usefully.
- The default configuration could never have shown this either way: `pixels="auto"` is
  `semantic < rich_at` with `rich_at = 40`, and System Settings exposes 104 targets, so the
  screenshot never happens. Skipping the overlay on the one window this milestone is about
  is what made the milestone untestable, which is why `--pixels` now exists.

### The reason, found later: the pixel layer could not read the interface

None of that was a fusion problem, and the invariant it worried about was never in doubt.
The pixel layer was **language-blind**: `_read` never told Vision which languages to
recognise, so it recognised English -- and on a `zh-Hans` macOS that is 24 regions of
English fragments and noise, and not one row of the sidebar. It was also reading them at
half the available resolution, because the capture preferred
`kCGWindowImageNominalResolution` (1x) over the native one.

Both are fixed. The same probe now reads 41 regions with **all 41 absorbed**, and an AX
element whose only label is the internal identifier
`com.apple.Appearance-Settings.extension` comes back carrying the name `外观`. Numbers and
the three-way comparison are in `docs/BENCHMARKS.md`.

So the criterion is now *reachable*, and what stops it being claimed is narrower than
before: in the window state measured afterwards there was no `row:outlinerow` in the tree
at all, so the exact target the criterion names -- a sidebar row named by recognised text
at its own geometry -- has not been observed yet. It has not been observed as false
either; it has not been observed.

The scripted task then produced a **false DONE**, which is the most useful result of the
session. The trace, verbatim:

```text
step 1: PRESS  外观    via=click
step 2: PRESS  深色    failed=BadChoice: target '深色' is ambiguous (2): button:深色 (ax:33dec1ca19da), button:深色 (ax:9976d8a93369)
step 3: DONE           why: 深色选项已选中
check True   外观为深色 (Appearance is Dark)   (predicate matched the live view)
```

while `defaults read -g AppleInterfaceStyle` reports the key absent -- still Light. Three
separate defects in one trace:

1. **`外观` resolved to the settings *window*.** It is the only target with that exact
   label, because the sidebar rows have none, so step 1 pressed the window -- and `PRESS`
   on a window degraded to a **coordinate click at its centre** (`via=click`). A blind
   click that "coordinate clicks are counted" was never written to catch, because it
   expected the click to land on the *row*.
2. **`深色` was ambiguous** (two buttons, one of them presumably the accent-colour swatch).
   The run survived it, as designed.
3. **Nothing changed and the run reported DONE.** `--trust-decider` seeded a predicate that
   returns `True` and therefore cannot fail, while the report said "predicate matched the
   live view". A waiver now reports `WAIVED ... NOT independently checked`.

The invariant that a region becomes a name is verified; the claim that the names are the
*right* ones is not. M1's revised acceptance therefore stays open, and now has a specific
measurable reason instead of a missing permission.

The earlier note in this section said the permission could not be granted from here
because the process chain is detached and no ancestor is a terminal `.app`. That
part is still true of *prompting*, but the grant has since been made by hand, and
the two obstacles that appeared the moment it worked are worth more than the note:

1. **`--focus` had no mechanism that could raise a window.**
   `NSRunningApplication.activateWithOptions_` returned `True` and the frontmost
   application did not change; the cooperative `activate()` that replaced it is not
   exposed by pyobjc at all, so the `hasattr` fallback was dead code; and for an
   application that was already running nothing else was ever tried. `raise_window`
   now asks LaunchServices, which is the mechanism that raises windows and the one
   that does work here.

   The first explanation written here was wrong and is worth more than the note it
   replaced: "macOS ignores an activation request from an application that is not
   itself active" fitted every measurement taken from an inactive caller, and was
   disproved the moment the same failure appeared with an *active* one -- run from a
   frontmost Ghostty, the frontmost application was that same Ghostty, and the
   activation still did nothing. See `docs/DECISIONS.md`.

   Reading the screen never needed any of this, which is why every observation kept
   working while every attempt to take focus failed, and why the two looked like
   different bugs.

2. **A model can now be rehearsed, which it could not before.** `--model --dry-run`
   observes once, asks the model once, validates the answer against that view, and
   executes nothing. This closed a real hole: the first thing a model ever did on a
   real desktop was act on it, in a tool whose README opens with "rehearse before you
   let it touch anything".

A real model (`deepseek-flash`) rehearsed against a real Chrome window on this
machine, reading its real fused view:

```text
asked command:.../deepseek_model.py, which said: 'Focus the address and search bar so it is ready for typing.'
rehearsal against the current view -- nothing was executed
view: app=Google Chrome window='<a real window title>'
  1 ok   PRESS ax:f4e6a670fb6b        would PRESS text_field 'Address and search bar' at 194,1164; it offers MENU,OPEN,PRESS,SET,TYPE

verdict: step 1 is executable as written
```

So a real model read a real fused view, named a real element by its id, and had that
answer validated against the live screen without touching it. What is still not
demonstrated is the other half of M4 -- a model *acting*, and its DONE being
confirmed by an independent verifier -- because acting needs focus, and focus is the
obstacle above.

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
criteria below.** Five consecutive observations of one Chrome window returned 158,
520, 158, 519 and 158 elements. Those were taken while a colleague was using that
browser and while the tooling was repeatedly taking focus from it, and a later
control run showed that a Chromium window nobody is using is perfectly steady (20
targets, one shape, 17 ms, eight frames). So the variation is most likely a live
page under somebody's hands, and it is **not established as a property of
Chromium**.

The consequence is narrower than it first looked, and still real: on a page that is
changing, id-addressed targets can disappear between deciding and acting, and
settling will hit its frame cap. "Zero coordinate clicks" was written before any of
this was measured, and should become "every coordinate click is counted and
explained" — a criterion that survives a page moving under the agent.

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

## M4 — the decision seam · written

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

Both are in. The new code needed no change to the loop to make room for it --
`Decider` and `Verifier` are one method each, which is the case for having written
them as protocols. Review did change one line of `runner.py`, for a different
reason, and that is recorded below.

### The seam: `deskhand.model`

A model is anything that turns one prompt into one JSON object. That is the whole
interface, so every interesting failure path is testable with a list of strings
instead of a network, and no vendor appears anywhere in the package. Two
transports ship: `FakeModel` (a script) and `CommandModel`, which runs *your*
command, from `DESKHAND_MODEL_COMMAND`, with the prompt on stdin and reads one
JSON object from stdout. The key stays wherever the command already keeps it --
`ollama run`, `llm`, a vendor CLI, a shell script -- and the dependency list stays
empty.

That variable is the only configuration, and there is no default: a run that
quietly fell back to a deterministic decider would be a lie about what decided, so
a missing command is an error that says how to set one.

### What the model is told, and what it structurally cannot ask for

The payload is `Task.brief()`, `View.brief()` and the recent steps -- asserted as
data in `tests/test_llm_decider.py`, not as a string. Semantic targets carry no
position at all; a pixel target carries one as a *fraction of the window frame*,
which is the one exception and it is deliberate (an unaimable pixel target is
useless, and "the first result" means nothing when ids are opaque).

"Never a coordinate click" is not a sentence in the prompt, it is three layers:

| Layer | What it refuses |
|---|---|
| `json_io.choice_from_dict` | an unknown field, and a wrongly typed one: `"target": [4123, 2897]` is refused, not coerced |
| `Choice` | anything that is not exactly one verb or one claim of finished |
| `validate.build_action` | an id not in the live view, a verb the target does not advertise, a literal the caller did not supply |

The prompt says it as well, but the prompt is the layer that is allowed to be
wrong.

### Result, and the honest limit

```bash
DESKHAND_MODEL_COMMAND="ollama run llama3.1" uv run --no-sync python examples/model_run.py
```

That runs M2's shape of task -- open a media app, search for a track, play it --
with a model deciding and a second model confirming. The first answer is wrong on
purpose:

```text
  1 FAIL BadChoice: target 'Play' is ambiguous (2): button:Play (play_all), button:Play (row_play_button)
  2 TYPE    Search                 search by name
  3 PRESS   Midnight City          open the matching track
  4 FINISH                         the track is playing

status: DONE  steps: 4  why: the track is playing
  check ok  Midnight City is playing  (command:... matched it: the now-playing line names it)
```

Two controls are called "Play", which is the most likely way to click the wrong
thing in this scenario. The refusal names both ids, that reason appears in the
*next* question as `steps_so_far`, and the next answer names a target id that
exists. That is the feedback loop the failure budget was always for, now carrying
a model's mistake instead of a rule's -- and it is why the ambiguous-target
message now names ids instead of just saying "ambiguous (2)".

**What this does not establish, and M2 still has to:** the desktop is
`demo.playback_sensor()`, a state machine, and the model above is a scripted
stand-in. Nothing here says a real Chromium window behaves this way, or that a
real model picks good targets on one. The run is offline, reproducible, and
pinned by `tests/test_playback_run.py`, which also asserts the parts of the
acceptance that *are* structural: the verifier is a separate transport, no
question contains a position, and every id the model acted on was one it had been
offered.

### The real-model run on a real desktop

`deepseek-flash`, `--pixels`, against the real System Settings on this machine, with the
wall-clock budget raised because these task files were written for a decider that costs
nothing:

- **The model found the unnamed row.** Rehearsed, it chose `PRESS row:outlinerow '' at
  8,1552 [click-only]` -- a sidebar row with **no label at all** -- reasoning that "外观 is
  the item right after 通用". M1's premise is that those rows are reachable only through
  what the pixels name *around* them, and a real model got there by id.
- **It could not act on it.** Over eight steps it tried `KEY DOWN` five times, `KEY UP`
  once, and `PRESS` on the row twice; the run escalated on the step budget and the machine
  stayed in Light mode. Two of those presses were coordinate clicks delivered into a window
  that was not in front, so nothing happened -- and after each one the model observed,
  correctly, that 通用 was still selected, and tried the sidebar again.

| per-step cost, model decider | ms |
|---|---|
| look | 0-687 |
| **decide** | **8 648-32 908** |
| act | 0-22 |
| settle | 391-538 |

Three things this measured that were not known before:

1. **A model decision is ~21 s at the median** against 0 ms for a deterministic decider, so
   `decide` is 97% of the wall clock and `max_ms: 40000` is exhausted by two steps. The
   budget is a `Task` field precisely so a caller can size it; these files were sized for
   the scripted decider.
2. **The loop could not tell circling from progress.** Each `KEY DOWN` moved the sidebar
   selection, which changes the content digest, so all eight steps counted as progress and
   `no_progress_steps` never fired -- the bound meant for exactly that could not see it, and
   the step budget absorbed it instead. `Limits.loop_steps` now catches the other shape of
   stuck: how many times the desktop may return to a state it has already been in, tracked
   by `revision` digest, with the step it kept returning to named in the verdict. The two
   bounds are complements -- one for a screen that stops changing, one for a screen that
   keeps changing and keeps coming back.
3. **A verb is not a trace.** `KEY` alone does not say *which* key, which is the one detail
   needed to read a run of `KEY, KEY, KEY, PRESS`; the step line shows it now. And `--focus`
   with `--json` put a human line in front of the report, making it unparseable; that line
   goes to stderr now.

### What an independent review found

The change was reviewed before it was called done, and the review found two things
the 312 tests did not -- both of them a component quietly choosing its own answer,
and neither reachable from the happy path:

1. **`says` chose what was verified.** `_terminal` verified
   `tuple(choice.says) or task.checks`, so a decider could name one easy criterion and
   reach DONE with the real one never looked at. Harmless while every decider was a
   rule this project wrote; not harmless once the decider is a model. `task.checks` is
   now the only thing asked about and `says` is recorded rather than consulted, pinned
   by `test_runner.py::test_a_subset_claim_cannot_hide_an_unmet_criterion`.
2. **`parse_object` acted on the first object it found.** A reply that echoed the
   format example in a fence and then answered in prose had the *example* acted on, and
   so did a draft written before the final answer. Two different objects are now refused
   and the reason is fed back into the next decision.

The reviewer was a fresh independent session but the same model family as the one
that wrote the change (this account's key can only use DeepSeek models, so no
cross-family reviewer was available): context-isolated, not a cross-family check. It
still paid for itself, which is the useful data point -- the tests were green and
the two holes were in the parts the tests trusted.

### Acceptance met, on a real machine with a real key

After the two bugs above were fixed, the same run completes:

```text
  1  3801ms PRESS 深色   via=ax-press  changed progress
  2 50596ms DONE

status: DONE  steps: 2
  check ok  外观为深色 (Appearance is Dark)
            (command:... matched it: The button labeled '深色' is selected (selected: true)
             in the 外观 window, confirming Dark appearance is active.)
  routes: {'ax-press': 1}
```

A real model read a real fused view with the pixel overlay forced, chose the right one of
**two** controls labelled 深色 by id, and the setting changed through `ax-press` -- zero
coordinate clicks. A second, separate transport confirmed `DONE` by looking at the same
screen, and `defaults read -g AppleInterfaceStyle` agrees independently. Two steps.

That also settles M1's semantic criterion for this task: the setting change is a labelled
button pressed with `ax-press`, in 2 steps, with the coordinate-click count reported as
zero. What it does not settle is M1's *first* criterion, because this run did not need the
sidebar -- the pane was already open. The unnamed sidebar row is still only reachable when
the pixels happen to name its neighbours, and that remains open.

### A suspicion that was wrong: this is not a multi-display coordinate bug

The first acting failures looked like the documented multi-display risk ("coordinates are
global, so it should work, but the visibility filter assumes one window frame. Needs a
second display to check"). This machine has a second display, the window sits at y=1113 on
it, and clicks appeared to do nothing -- so multi-display geometry was the obvious suspect.

It is not the cause. Measured both ways on the same window: `AXPosition`/`AXSize` and
`CGWindowListCopyWindowInfo` report **identical** geometry (`(0, 1113)`, `723x1001`), and
`CGDisplayBounds` shows the second display at `(0, 1080, 1728, 1117)`, so the window is
exactly where it should be. The clicks were landing; the model was wandering, and the pane
had in fact changed by the end of the run.

Driving the last step by hand, `PRESS 深色 -> route ax-press`, switched the machine to Dark
on the first attempt. The lesson is the one this file keeps relearning: the plausible cause
that fits the symptom is not the cause until it is measured, and here the measurement was
cheap -- two APIs, one window, one comparison.

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
