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
uv run deskhand demo --report demo.html      # a run as a self-contained HTML page
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
    verifier=PredicateVerifier(
        {"Appearance is Dark": lambda t, v: any(x.value == "Dark" for x in v.targets)}
    ),
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
checked. `ModelVerifier` asks a model what the screen shows, and it is handed the task, the
live view and the claims -- never the decider's reasoning, its action or its steps -- one
question per claim, so a set of claims cannot be confirmed by a single answer.

**4. Perception does not cost thirteen round trips per node.**
Accessibility attributes are fetched in one batched call per node, role-first, and nodes
that get discarded are not read at all. Ids come from role, label and bucketed geometry
rather than `repr(ref)` addresses, so they survive a re-walk and can be matched against a
hit test.

**5. The trace tells you how semantic the run was.**
Every step records the route it took (`ax-press`, `ax-set-value`, `ax-focus+keys`, `click`,
`keys`), plus per-phase timings. "0 coordinate clicks" is a measurement here, not a claim.

## Reading a run

Every run prints its trace, and the last lines count how it acted -- through accessibility,
or by aiming the mouse at a point. In a terminal, refusals are red and coordinate clicks
are yellow; piped or captured output stays plain, and `NO_COLOR` is honoured.

A run can also be kept as one self-contained HTML page: status and checks first, every
step with its route and a look / decide / act / settle bar, and the final view as a
filterable table of targets. It loads nothing from the network, so it can be attached to
an issue or archived next to the task file.

```bash
uv run deskhand demo --report demo.html                        # the scripted run, as a page
uv run deskhand run --task examples/appearance.json --report run.html
uv run deskhand run --task examples/appearance.json --json > run.json
uv run deskhand report run.json                                # a saved report, later
```

Everything on the page that came from the desktop or from a model -- a window title, a
model's `why` -- is escaped, because it is untrusted text.

## Letting a model decide

The deciders above are deterministic, which is right for testing and wrong for general use.
A decider can be a model, and so can a verifier, through one seam that names no vendor and
holds no key:

```bash
export DESKHAND_MODEL_COMMAND="ollama run llama3.1"   # or `llm -m gpt-4o`, or a script of yours
uv run deskhand run --task examples/appearance.json --model --dry-run   # ask once, act on nothing
uv run deskhand run --task examples/appearance.json --model            # let it act
uv run --no-sync python examples/model_run.py         # M2's scenario, on a scripted desktop
```

A model decision takes seconds, not milliseconds (9-33 s measured), so with `--model` a
`max_ms` too small for `max_steps` of those is raised, and the new budget is printed.

The command reads the prompt on stdin and must write one JSON object to stdout, so whatever
already holds the key keeps holding it. Two commands are started per run on purpose: a
verifier that shares a conversation with the decider is not an independent check.

Rehearse a model before it touches anything. `--model --dry-run` observes once, asks the
model once, checks the answer against that observation, and executes nothing — so the first
time a model meets a real desktop is not the first time it acts on one.

```python
from deskhand import Runner, Task
from deskhand.deciders import LLMDecider
from deskhand.model import model_from_env
from deskhand.sensors.macos import open_sensor
from deskhand.verify import ModelVerifier

report = Runner(
    sensor=open_sensor(),
    decider=LLMDecider(model_from_env()),
    verifier=ModelVerifier(model_from_env()),
).run(Task(goal="Play Midnight City", checks=("Midnight City is playing",)))

print(report.status, [(c.check, c.ok, c.how) for c in report.checked])
```

What a model is told is `Task.brief()` and `View.brief()`: target ids, kinds, labels, values
and offered verbs. A semantic target carries no position at all; a pixel target carries one
as a fraction of the window frame, because "the first result" means nothing when ids are
opaque. What it can *ask for* is the same ten verbs as everything else, resolved by id or by
label — so a reply that tries to click at a point is refused rather than obeyed, and the
refusal is fed back into the next decision as `steps_so_far`.

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
| whether it is actually done | `Verifier` | defaults to confirming nothing; `ModelVerifier` asks a model |
| how a model is asked | `Model` | one prompt -> one JSON object; `FakeModel`, `CommandModel` |
| the loop | `Runner` | bounded by `Limits` |
| the outcome | `Report` / `Status` | `DONE`, `STUCK`, `ESCALATE` |

Ten verbs, and that is the whole action space: `PRESS`, `OPEN`, `MENU`, `TYPE`, `SET`,
`DRAG`, `KEY`, `CHORD`, `SCROLL`, `WAIT`.

## Permissions

- **Accessibility** — required.
- **Screen Recording** — required for the pixel overlay, and therefore required
  for any interface whose controls accessibility does not name. Run with
  `--no-pixels` to avoid it; `doctor` shows what that would cost you. `--pixels`
  forces the overlay when the automatic choice skips it, which it does above 40
  accessibility targets — including on System Settings, where 27 sidebar rows have
  no name for accessibility to match on at all.

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

**`--focus` starts an application that is not running, and asks LaunchServices to raise
it.** Both go through `open`, and starting is resolved through Spotlight first, because
`open -a` does not accept a localised display name such as `系统设置` on a Chinese macOS.
Raising has to go through LaunchServices too: `NSRunningApplication`'s activation call
returns `True` and does nothing on macOS 26, and the cooperative call that replaced it is
not exposed by pyobjc at all. Reading the screen never needed any of this, which is why a
run can observe a window it cannot bring forward.

## Development

Run the checks through the project's own environment. `python3 -m pytest` from your
shell will happily use a different Python and different tool versions than the ones
CI uses, and a green local run then means nothing: the first version of this file was
committed green locally and red in CI three separate ways.

```bash
uv sync --extra dev
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src
uv run --no-sync pytest -q
```

`--no-sync` matters. Without it, `uv run` re-syncs the environment to the default
set, which drops the `dev` extra, and then cannot find the tool it was asked to run.
`ruff format --check` also covers the Python block in this file, so the example above
is formatted whether anybody remembers to format it or not.

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
The decision seam is in as well -- a model decider and a model verifier over one
vendor-free transport -- verified end to end against the scripted desktop, and not yet
against a real application with a real key.

What is deliberately not done yet: `ScreenCaptureKit` (the current capture call is
deprecated by Apple), event-driven settling via `AXObserver` (settling currently polls an
accessibility-only structural probe), custom-canvas perception, and M2's Chromium run with
a model. See `docs/PLAN.md`.

MIT.
