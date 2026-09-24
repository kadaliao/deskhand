# Architecture

```
                    caller
                      |  Task(goal, checks, inputs, notes, limits)
                      v
            +---------------------+
            |       Runner        |   bounded loop, timings, failure budget,
            |                     |   the only thing that may report a status
            +--+--------------+---+
               |              |
      Choice   |              |  Step stream / Report
               v              v
        +-------------+   +----------------+
        |   Decider   |   |   Verifier     |  confirms a DONE claim; refuses by default
        +------+------+   +--------+-------+
               |                   |
               |  ask / one JSON   |  ask / one JSON  (separate transports on purpose)
               v                   v
        +--------------------------------+
        |             Model              |  one prompt -> one JSON object
        |  FakeModel   |   CommandModel  |  no vendor, no key, no HTTP client here
        +--------------------------------+
               ^
               |  View(changed? what does it say?)
        +------+------------------------------------------+
        |                   Sensor                        |
        |  observe()  is_stale()  act()  settle()          |
        +------+------------------------------+-----------+
               |                              |
        +------v------+                +------v------+
        |  AXSource   |  rank 0        |  OCRSource  |  rank 10
        |  semantics  |<---- hit ----- |   pixels    |
        +-------------+   test (x,y)   +-------------+
                            ^
                      fusion resolves a pixel region into an accessibility
                      element, or leaves it as a pixel target
```

## The invariants

These are the properties worth defending. Each one has a test.

1. **A pixel target that resolves to an accessibility element is not a target.**
   It becomes another name for that element (`fusion.py`, `test_fusion.py`).
2. **A decider cannot invent a target, a verb, or a literal.**
   Targets must exist in the live view, verbs must be advertised by the target,
   and values must be a key in `Task.inputs` (`validate.py`).
3. **A decider cannot confirm its own work, and does not choose what is checked.**
   `NoVerifier` returns "unverified" for every claim, and a claim of `DONE` is
   verified against every `Task.checks` criterion rather than the subset the decider
   nominated: `choice.says` is recorded in the trace and never asked about
   (`verify.py`, `runner.py::_terminal`, `test_runner.py`).
4. **No failure ends the run by itself.**
   Decider exceptions, invalid choices, stale targets, unsupported actions:
   counted, recorded with a reason, budgeted (`runner.py`).
5. **Freshness is checked exactly once, immediately before acting.**
   The runtime owns the gate; a sensor must not repeat it (`protocols.py`).
6. **Identity is meaning, never a memory address.** Accessibility ids come from
   role, subrole, label and bucketed geometry, so a re-walk yields the same id
   and a hit test can match a walked element (`ax.py::_identify`).
7. **Two digests, two questions.** `revision` (identity only) answers "does it
   still mean the same?"; `content` (identity + placement) answers "did anything
   happen at all?" (`types.py`).
8. **The cheap signal is used for waiting, the expensive one for deciding.**
   Settling probes accessibility structure only; the screenshot and recognition
   pass happen once, after the desktop is quiet (`screen.py::settle`).
9. **A decider that asks a model still cannot ask for a coordinate click.** The
   reply is data from an untrusted source, so it goes through `json_io` (unknown
   fields and wrongly typed fields refused), `Choice` (exactly one verb, or one
   claim of finished), and `build_action` (the id must exist, the verb must be
   advertised, a literal must come from `Task.inputs`). A prompt that says "do not
   use coordinates" is not one of those layers (`model.py`, `deciders/llm.py`).
10. **Verification cannot see the decision.** `ModelVerifier` is handed the task,
   the live view and the claims -- never the decider's rationale, its action or
   its steps -- and asks one question per claim, so a set of claims cannot be
   confirmed by a single answer (`verify.py`, `test_model_verifier.py`).
11. **Importing the package never imports a framework.** Every pyobjc import is
   inside a function, which is what makes the pixel geometry and the whole loop
   testable off a Mac.

## Data flow of one step

```text
1  observe         AX targets -> OCR targets -> fuse (hit test) -> View
2  decide          Decider sees Task + View + previous Steps  -> Choice
                   (RuleDecider, ScriptedDecider, or LLMDecider over a Model)
3  validate        Choice -> Action, with freshness guards
4  aim             Sensor.is_stale(View, Action)?  -> re-observe and decide again
5  act             Sensor.act(View, Action) -> route name ("ax-press", "click", ...)
6  settle          Sensor.settle(...) probe until quiet -> new View
7  judge           changed = revision differs, progress = content differs
8  repeat          until a terminal status, a budget, or a cancel
```

`Step` records the choice, the action, the route, the phase timings, and the
failure reason if there was one. `Report` records the status, the confirmed
checks, and why the run stopped.

## Vocabulary

| Layer | Name | Contract |
|---|---|---|
| data | `Target` | something aimable: `id`, `kind`, `label`, `labels`, `value`, `actions`, `box`, `visual` |
| data | `View` | one observation: `app`, `window`, `revision`, `content`, `targets`, `frame`, `notes` |
| decision | `Choice` | one `Verb` plus arguments, or a claim of finished |
| execution | `Action` | a validated `Choice` plus `Fingerprint` guards |
| seam | `Source` | one way of seeing: `rank`, `targets()` |
| seam | `HitTester` | a point on screen -> an accessibility target |
| seam | `Sensor` | `observe`, `is_stale`, `act`, `settle` |
| seam | `Decider` | `choose(task, view, steps) -> Choice` |
| seam | `Verifier` | `confirm(task, view, claims) -> CheckResult[]` |
| seam | `Model` | one prompt -> one JSON object: `ask(system, prompt)`, `name` |
| control | `Runner` | `steps()` yields, `run()` returns a `Report` |

Ten verbs, and that is the whole action space: `PRESS`, `OPEN`, `MENU`, `TYPE`,
`SET`, `DRAG`, `KEY`, `CHORD`, `SCROLL`, `WAIT`.

## Perception costs, measured rather than asserted

| Operation | Cost |
|---|---|
| one accessibility observation | one batched attribute read per node (role-first short circuit), plus one action-name read, plus a settability check only for value-ish roles |
| one hit test | one synchronous call into the target application, bounded to 2 seconds, cached per 16 pixel cell, and capped per fusion pass |
| one pixel observation | one window screenshot plus one Vision pass, skipped entirely when accessibility already describes the window richly (`pixels="auto"`) |
| one settle probe | accessibility structure only, no screenshot |
| one stale check | a batched read for that element, or a window identity check for a pixel target |

## File map

```
src/deskhand/
  types.py          Target, View, Choice, Action, Step, Report, Task, Limits, digests
  fingerprint.py    normalisation, similarity, freshness fingerprints
  protocols.py      Source, HitTester, Sensor, Decider, Verifier
  fusion.py         dedupe + the pixel-into-accessibility rule
  validate.py       Choice -> Action, and every reason to refuse
  runner.py         the loop
  model.py          the model seam: Model, FakeModel, CommandModel, model_from_env
  verify.py         NoVerifier, PredicateVerifier, ModelVerifier
  json_io.py        the strict JSON boundary
  paint.py          terminal colour, only for a terminal; which routes count as semantic
  console/          `deskhand app`: desk.py (MacDesk, DemoDesk), server.py (API, runs),
                    static/ (the page: plain HTML, CSS and JS, no dependencies)
  report_html.py    a Report.brief() as one self-contained, escaped HTML page
  demo.py           a scripted desktop, a deliberately wrong first choice, and M2's shape
  cli.py            demo, app, ax, probe, doctor, stability, bench, permit, run, report
  deciders/         rule.py, scripted.py, llm.py
  sensors/
    fake.py         a deterministic desktop (self-checking tests)
    macos/
      ax.py         batched reads, content ids, hit test, native actions
      ocr.py        window capture + Apple Vision
      pixels.py     pure geometry (testable anywhere)
      keys.py       Quartz input, used only when accessibility cannot act
      screen.py     MacSensor: fusion, dispatch, settling
```
