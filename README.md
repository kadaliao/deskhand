# deskhand

**A hand for desktop agents.** Accessibility is the skeleton, pixels are an overlay, and the
loop survives failures instead of ending on them.

```bash
uv sync --extra macos
uv run deskhand demo        # the whole loop, no permissions needed
uv run deskhand doctor      # what can accessibility actually see right now?
uv run deskhand probe       # the fused view, as the decider sees it
uv run deskhand ax --grep 外观 --focus 系统设置   # find the right label
uv run deskhand stability --frames 10        # is this window steady, or is somebody using it?
uv run deskhand bench                        # what does an observation actually cost?
```

Rehearse before you let it touch anything. `--dry-run` observes once and reports
what every step would do, pressing nothing:

```bash
uv run deskhand run --task examples/appearance.zh-CN.json --dry-run --focus 系统设置
```

```
rehearsal against the current view -- nothing was executed
view: app=系统设置 window='录屏与系统录音'
  1 FAIL PRESS 外观      no target matching '外观' in the current view
  2 FAIL PRESS 深色      no target matching '深色' ... (not in the current view; an earlier step may reveal it)
  3 FAIL DONE           claims [...], but no verifier is configured: this would escalate rather than finish

verdict: step 1 cannot run as written: no target matching '外观' in the current view
```

That output is a real result from a real machine, and it is why this project
keeps pixels around: on a localised macOS the settings sidebar has 39 rows that
accessibility exposes with **no names at all**. Labels also differ per macOS
version and language, so a script written against a guess fails at the first
step instead of halfway through a click sequence.

A caller hands over a bounded task; deskhand does the looking, aiming, clicking and checking.

```python
from deskhand import Runner, Task
from deskhand.deciders import RuleDecider, has, press, rule
from deskhand.sensors.macos import open_sensor
from deskhand.types import Choice, Status
from deskhand.verify import PredicateVerifier

task = Task(
    goal="Switch macOS appearance to Dark",
    checks=("Appearance is Dark",),
)

decider = RuleDecider(
    [
        rule(when=has("Appearance"), then=press("Appearance")),
        rule(when=has("Dark"), then=press("Dark")),
    ],
    fallback=Choice(finish=Status.ESCALATE, why="nothing matched"),
)

report = Runner(
    sensor=open_sensor(),
    decider=decider,
    verifier=PredicateVerifier({"Appearance is Dark": lambda t, v: any(x.value == "Dark" for x in v.targets)}),
).run(task)

print(report.status, report.steps_taken, [s.via for s in report.steps])
```

## The five things this does differently

**1. Pixels are a pointer to accessibility, not a second identity space.**
Every recognised text region is hit-tested against accessibility
(`AXUIElementCopyElementAtPosition`). If a real element is there, that element *is* the
target and the recognised words become another name for it. Only the regions that
accessibility genuinely cannot see survive as pixel targets. That invariant lives in
`fusion.py`, not in a sentence in a prompt.

**2. Nothing a decider or a backend does ends the run.**
A bad target, an unsupported action, a stale fingerprint, a decider that raises: each is
counted, recorded with a reason, and fed back into the next decision, up to a budget. There
is a test for each of those paths.

**3. Verification is not the decider's job.**
A decider may claim `DONE`; a `Verifier` has to confirm it. With no verifier configured the
honest answer is "not verified", so the run escalates rather than reporting success nobody
checked.

**4. Perception does not cost thirteen round trips per node.**
Accessibility attributes are fetched in one batched call per node, role-first, and nodes
that get discarded are not read at all. Ids come from role, label and bucketed geometry
rather than `repr(ref)` addresses, so they survive a re-walk and can be matched against a
hit test.

**5. The trace tells you how semantic the run was.**
Every step records the route it took (`ax-press`, `ax-set-value`, `ax-focus+keys`, `click`,
`keys`), plus per-phase timings. "0 coordinate clicks" is a measurement here, not a claim.

## Vocabulary

| Concept | Name | Note |
|---|---|---|
| something on screen you can aim at | `Target` | `visual=True` means it exists only in pixels |
| one observation | `View` | `revision` = structure, `content` = what it says |
| what to do | `Choice` | one `Verb`, or a claim of `DONE` |
| a validated, executable choice | `Action` | carries freshness guards |
| one way of seeing | `Source` | `AXSource` (rank 0), `OCRSource` (rank 10) |
| seeing + doing, with settling | `Sensor` | `MacSensor`, `FakeSensor` |
| what to do next | `Decider` | ships with `RuleDecider` and `ScriptedDecider` |
| whether it is actually done | `Verifier` | defaults to confirming nothing |
| the loop | `Runner` | bounded by `Limits` |
| the outcome | `Report` / `Status` | `DONE`, `STUCK`, `ESCALATE` |

Ten verbs, and that is the whole action space: `PRESS`, `OPEN`, `MENU`, `TYPE`, `SET`,
`DRAG`, `KEY`, `CHORD`, `SCROLL`, `WAIT`.

## Permissions

- **Accessibility** — required.
- **Screen Recording** — required for the pixel overlay, and therefore required
  for any interface whose controls accessibility does not name. Run with
  `--no-pixels` to avoid it; `doctor` shows what that would cost you.

Whichever app you launch `deskhand` from needs both, and macOS decides which app
that is by looking up the process chain — not by looking at `python`, `uv` or
this package. Get it wrong and the permission appears granted while the call
still fails. Ask macOS instead of guessing:

```bash
uv run deskhand permit
```

It reports the application the permissions will be attributed to, fires the
official system dialogs (which name that application), and prints the exact
System Settings path for whatever is missing. Screen Recording is only re-read
when a process starts, so restart that application afterwards.

## Slow networks

The default package index can stall badly on some connections; the first sync of
this project sat for 40 minutes on two wheels that a mirror delivered in 4
seconds. If that happens:

```bash
UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ uv sync --extra macos
```

Nothing in the project depends on a mirror; it is only a workaround for the
download.

## Status

Early, but the execution path is verified on a real machine: `examples/verify_execution.py`
switches a terminal tab and switches it back, reporting the route it took and the cost of
each phase.

```
observe: 13 targets in 315ms     (the first request to an application builds its tree)
acted:   route='ax-press' in 17ms | settled in 19ms | step ≈45ms
```

Acting is nearly free and so, after the settling rewrite, is waiting. Numbers, method and
what is *not* measured are in `docs/BENCHMARKS.md`; `uv run deskhand bench` re-takes them.

What works today: the whole loop, the protocol seams, the fusion rule, the batch
attribute reader, the hit-test path, and the scripted desktop with full test coverage.

What is deliberately not done yet: `ScreenCaptureKit` (the current capture call is
deprecated by Apple), event-driven settling via `AXObserver` (settling currently polls an
accessibility-only structural probe), and custom-canvas perception. See `docs/PLAN.md`.

MIT.
