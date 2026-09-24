# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

deskhand is a Python (>=3.12) library + CLI that lets a desktop agent act on macOS: it fuses
the Accessibility tree with Apple Vision OCR into one aimable `View`, runs a bounded
decide → validate → act → settle → verify loop, and survives failures instead of ending on them.
No runtime dependencies; pyobjc lives in the `macos` extra.

Read `README.md` for usage, `docs/ARCHITECTURE.md` for the invariants, `docs/PLAN.md` for
milestone status. `docs/BENCHMARKS.md` holds measurements (with method and what was not
measured); `docs/DECISIONS.md` is a design/mistake log — adding to it when something turns out
wrong is the convention. `docs/HANDOVER.md`, if present, is a dated snapshot; trust code and
PLAN.md over it.

## Commands

```bash
uv sync --extra dev                 # add --extra macos to use the real sensors
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src           # strict
uv run --no-sync pytest -q
uv run --no-sync pytest tests/test_runner.py -q -k stale   # single file / test
uv run --no-sync deskhand demo      # full loop on a fake desktop, no permissions
uv run --no-sync deskhand app --demo  # the web console on the fake desktop
```

`--no-sync` is required: a bare `uv run` re-syncs to the default set, drops the `dev` extra, and
then cannot find ruff/mypy/pytest. These four gates are exactly what CI runs.

CI runs the suite on **Linux, where pyobjc does not exist**, so a passing macOS run can still fail
CI. Before pushing changes that touch `sensors/macos/`, run the tests with pyobjc imports blocked:

```bash
uv run --no-sync python -c "
import sys, builtins, pytest
BLOCKED={'objc','AppKit','Quartz','Vision','ApplicationServices','Foundation','CoreFoundation'}
real=builtins.__import__
def blocked(name,*a,**k):
    if name.split('.')[0] in BLOCKED: raise ImportError(name)
    return real(name,*a,**k)
builtins.__import__=blocked
sys.exit(pytest.main(['-q','tests']))
"
```

Real-desktop commands (`doctor`, `probe`, `ax`, `stability`, `bench`, `run`, `permit`) need the
`macos` extra and Accessibility/Screen Recording permissions. `run --dry-run` rehearses a task
file against the live view without acting. `--focus <app>` steals focus from the user — the user
works at this machine, so prefer read-only commands and ask before anything that takes focus or
acts on the desktop.

A model decider/verifier is any shell command in `DESKHAND_MODEL_COMMAND` (one prompt in, one
JSON object out); enable with `run --model`. Model steps take ~10–30 s each, so the
`examples/*.json` budgets (`max_ms: 40000`) are too small for model runs.

## Architecture

The loop (`runner.py`) talks only to protocols in `protocols.py`:

- `Sensor` (`observe`, `is_stale`, `act`, `settle`) — `sensors/fake.py` for tests,
  `sensors/macos/screen.py::MacSensor` for real. MacSensor combines `AXSource` (`ax.py`, rank 0)
  and `OCRSource` (`ocr.py`, rank 10) through `fusion.py`; `keys.py` is Quartz input, used only
  when accessibility cannot act; `pixels.py` is pure geometry.
- `Decider` (`choose(task, view, steps) -> Choice`) — `deciders/rule.py`, `scripted.py`,
  `llm.py` (via the `Model` seam in `model.py`).
- `Verifier` (`confirm(task, view, claims)`) — `verify.py`: `NoVerifier`, `PredicateVerifier`,
  `ModelVerifier`.
- `validate.py` turns a `Choice` into an `Action` or refuses it with a reason; `json_io.py` is the
  strict boundary for untrusted model output. Core data types are in `types.py`.
- Output: `cli.py` prints traces (colour via `paint.py`, only on a TTY, `NO_COLOR` honoured);
  `report_html.py` renders `Report.brief()` -- the `run --json` payload -- as a self-contained
  page (`--report PATH` on `run`/`demo`, or `deskhand report run.json`). Everything on it that
  came from the desktop or a model is escaped; it loads nothing external.
- Console (`console/`): `server.py` is a stdlib HTTP server over a `Desk` (`MacDesk` or the
  permission-free `DemoDesk`); `static/` is the page, plain DOM, all text via `textContent`.
  Every `/api` and `/runs` request needs the per-launch token and a local `Host`; starting a
  run needs `"confirm": true`. Desktop access is serialised by `Console.lock`. Tests drive it
  over real HTTP on the demo desk (`tests/test_console.py`).
- Task files may carry `expect` (check -> declared state), judged by `verify.ExpectVerifier`.
  `--app NAME` pins `AXSource` to one application; `MacSensor.act` then refuses anything
  but accessibility actions while that app is not frontmost.

Invariants worth defending (each has a test; full list in `docs/ARCHITECTURE.md`):

- **Importing the package never imports a framework.** Every pyobjc import is inside a function
  (hence the `PLC0415` ruff ignores for `cli.py` and `sensors/macos/*`). Breaking this breaks CI.
- A pixel region that hit-tests to an accessibility element becomes an extra name for that
  element, not a separate target (`fusion.py`).
- A decider cannot invent a target, verb, or literal: target ids must be in the live view, verbs
  must be advertised by the target, values must come from `Task.inputs`. No coordinate clicks
  from a model.
- A decider cannot confirm its own `DONE`: every `Task.checks` item is verified; with no verifier
  the run escalates. `ModelVerifier` never sees the decider's reasoning, and asks one question
  per claim.
- No single failure ends a run: decider exceptions, invalid choices, stale targets, unsupported
  actions are recorded with a reason, fed back to the decider, and budgeted by `Limits`.
- Freshness is checked once, by the runner, immediately before acting — sensors must not repeat it.
- Target ids derive from role/subrole/label/bucketed geometry, never memory addresses.
  `revision` digest = identity only; `content` digest = identity + placement.
- `Runner.steps()` is a generator that returns the `Report` via `StopIteration.value`
  (read by `run()`); this is deliberate, don't "fix" it.
- Each step records its route (`ax-press`, `ax-set-value`, `ax-focus+keys`, `click`, `keys`) and
  per-phase timings.

## Conventions

- Claims about behaviour or performance are measured; the numbers go in the commit message or
  `docs/BENCHMARKS.md`. When diagnosing, compare two ways of asking the same question before
  picking a cause (e.g. for "what is frontmost now", use the window server, not
  `NSWorkspace.frontmostApplication()`, which is stale in a process with no run loop).
- Refuse rather than guess (unknown fields, ambiguous labels, stale targets), with a reason.
- Don't add config or code that nothing reads.
- Commit messages have long, specific bodies explaining why; check `git log` for the style.
- The gates are ruff, `ruff format` and `mypy --strict`. Editor ast-grep findings (e.g. on every
  `int(...)`) are not project gates; suppress with `# ast-grep-ignore` on the line above plus a
  reason, as in `apps.py`/`ax.py`/`ocr.py`.
