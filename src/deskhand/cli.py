"""Command line entry point.

``demo`` runs anywhere. ``ax``, ``probe``, ``doctor`` and ``run`` need macOS and
the Accessibility permission; ``doctor`` is the first thing to run, because it
tells you how much of the current app accessibility can actually describe.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import warnings
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from . import demo, json_io
from .deciders.llm import LLMDecider
from .deciders.scripted import ScriptedDecider
from .errors import DeskhandError
from .model import ENV_COMMAND as MODEL_COMMAND_ENV
from .model import model_from_env
from .protocols import Decider, Verifier
from .rehearse import Rehearsal, rehearse
from .runner import Runner
from .types import Choice, Report, Status, Step, Task, shape_digest
from .verify import ModelVerifier, WaivedVerifier

OK, FAILED, ENVIRONMENT = 0, 1, 2

THIN_AX = 15
"""Below this many accessibility targets, pixels are carrying real weight."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskhand", description="A hand for desktop agents.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="run the scripted desktop end to end (no permissions needed)")

    ax_cmd = sub.add_parser("ax", help="dump accessibility targets for the frontmost app")
    _output_flags(ax_cmd)

    probe = sub.add_parser("probe", help="dump the fused accessibility + pixel view")
    _output_flags(probe)
    _pixel_flags(probe)
    probe.add_argument("--frames", type=int, default=1, help="observe this many times")

    doctor = sub.add_parser("doctor", help="permissions, coverage and timing for the frontmost app")
    doctor.add_argument("--json", action="store_true")
    _pixel_flags(doctor)

    stability = sub.add_parser(
        "stability",
        help="observe the frontmost window repeatedly and say whether it was steady",
    )
    stability.add_argument("--frames", type=int, default=10)
    stability.add_argument("--gap", type=float, default=1.0, help="seconds between observations")
    stability.add_argument(
        "--focus", default=None, help="bring this application to the front first"
    )
    _pixel_flags(stability)
    stability.add_argument("--json", action="store_true")

    bench = sub.add_parser("bench", help="measure what an observation and a settle actually cost")
    bench.add_argument(
        "--focus", default=None, help="bring this running application to the front first"
    )
    bench.add_argument("--json", action="store_true")
    _pixel_flags(bench)
    bench.add_argument("--frames", type=int, default=8, help="how many observations to time")
    bench.add_argument("--settle-frames", type=int, default=5, help="how many settles to time")
    bench.add_argument("--budget", type=int, default=2500, help="settle budget in ms")

    permit = sub.add_parser(
        "permit", help="ask macOS for the missing permissions and name the app to toggle"
    )
    permit.add_argument("--json", action="store_true")
    permit.add_argument(
        "--open",
        action="store_true",
        help="open the System Settings page for whatever is missing",
    )

    run = sub.add_parser("run", help="run a task file against the real desktop")
    run.add_argument("--task", required=True, help="JSON task, optionally with a steps script")
    run.add_argument("--json", action="store_true")
    _pixel_flags(run)
    run.add_argument(
        "--focus",
        default=None,
        help="bring this running application to the front before observing",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="observe once and report what each step would do, without doing anything",
    )
    run.add_argument(
        "--trust-decider",
        action="store_true",
        help="accept the decider's own DONE claim without a verifier (unsafe)",
    )
    run.add_argument(
        "--model",
        action="store_true",
        help=(
            f"let a model decide, and have a second model confirm DONE (needs {MODEL_COMMAND_ENV})"
        ),
    )
    args = parser.parse_args(argv)
    handlers = {
        "demo": _demo,
        "ax": _ax,
        "probe": _probe,
        "doctor": _doctor,
        "permit": _permit,
        "bench": _bench,
        "stability": _stability,
        "run": _run,
    }
    try:
        return int(handlers[args.command](args))
    except (DeskhandError, OSError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return ENVIRONMENT


def _permit(args: argparse.Namespace) -> int:
    """Trigger macOS's own dialogs, which name the application to authorize."""
    from .sensors.macos import blame, request, status, to_do
    from .sensors.macos.permits import open_pane

    current = status()
    owner = blame()
    outstanding = [name for name, granted in current.items() if not granted]
    asked = {name: request(name) for name in outstanding}
    if args.open:
        for name in outstanding:
            open_pane(name)
    report: dict[str, Any] = {
        "granted": current,
        "requested": {
            name: {"granted": granted, "note": note} for name, (granted, note) in asked.items()
        },
        "attributed_to": owner,
        "instructions": to_do(current, owner),
        "recheck": status(),
    }
    if args.json:
        print(json_io.dump(report))
    else:
        for line in report["instructions"]:
            print(line)
        for name, (granted, note) in asked.items():
            print(f"\n{name}: {note}")
            if granted:  # pragma: no cover - only when a permission is already in place
                print("  (it was granted while we were asking)")
    return OK if not outstanding else ENVIRONMENT


def _stability(args: argparse.Namespace) -> int:
    """Measure the interface, and whether somebody else was measuring it too.

    Takes no focus of its own unless asked: the point of the command is to find out
    whether the window is being disturbed, and stealing focus would disturb it.
    """
    from .stability import Sample, Stability

    _focus(args)
    sensor = _mac_source(args)
    samples: list[Sample] = []
    for index in range(max(1, args.frames)):
        started = time.perf_counter()
        view = sensor.observe()
        samples.append(
            Sample(
                ms=round((time.perf_counter() - started) * 1000),
                targets=len(view.targets),
                shape=shape_digest(view.targets)[:8],
                window=view.window,
            )
        )
        if index + 1 < args.frames:
            time.sleep(max(0.0, args.gap))

    report = Stability(tuple(samples))
    if args.json:
        print(json_io.dump(report.brief()))
        return OK

    print(f"{'#':>3} {'ms':>6} {'targets':>8} {'shape':>9}  window")
    for index, sample in enumerate(samples, start=1):
        window = sample.window[:44]
        print(f"{index:>3} {sample.ms:>6} {sample.targets:>8} {sample.shape:>9}  {window!r}")
    brief = report.brief()
    targets = brief["targets"]
    print(f"\ntargets   : min={targets['min']} max={targets['max']} median={targets['median']}")
    print(f"first={brief['first_ms']}ms  warm median={brief['warm_median_ms']}ms")
    print(f"\nverdict: {report.verdict}")
    return OK if report.kind != "disturbed" else FAILED


def _bench(args: argparse.Namespace) -> int:
    """Time the parts of a step. Nothing here acts on anything.

    The first observation of an application is not like the others: macOS builds
    an accessibility tree lazily, so the first request pays for construction and
    every later one is an order of magnitude cheaper. Reporting only one number
    for "cost of an observation" is how a project ends up optimising the wrong
    thing, so this reports the first frame and the rest separately.
    """
    _focus(args)
    sensor = _mac_source(args)

    frames = max(1, args.frames)
    observes: list[int] = []
    counts: list[int] = []
    titles: list[str] = []

    def take() -> Any:
        """One timed observation, recorded in the three sample lists.

        A named first frame instead of an assignment inside a loop: the same
        number of observations and the same per-frame timing, but which view the
        numbers describe is no longer something a reader (or a checker) has to
        infer from ``max(1, ...)``.
        """
        started = time.perf_counter()
        observed = sensor.observe()
        observes.append(round((time.perf_counter() - started) * 1000))
        counts.append(len(observed.targets))
        titles.append(observed.window)
        return observed

    view = take()
    for _ in range(frames - 1):
        view = take()
    targets = counts[-1]

    probes: list[int] = []
    for _ in range(frames):
        started = time.perf_counter()
        sensor.probe()
        probes.append(round((time.perf_counter() - started) * 1000))

    settles: list[int] = []
    for _ in range(max(1, args.settle_frames)):
        started = time.perf_counter()
        view = sensor.settle(view, budget_ms=args.budget)
        settles.append(round((time.perf_counter() - started) * 1000))

    warm = observes[1:] or observes
    observe_warm = round(statistics.median(warm))
    report = {
        "app": view.app,
        "window": view.window,
        "targets": targets,
        "observe_first_ms": observes[0],
        "observe_warm_ms": observe_warm,
        "targets_per_frame": counts,
        "distinct_windows": sorted(set(titles)),
        "probe_ms": round(statistics.median(probes), 1),
        "settle_quiet_ms": round(statistics.median(settles), 1),
        "samples": {"observe": observes, "probe": probes, "settle": settles},
    }
    if args.json:
        print(json_io.dump(report))
        return OK

    print(f"app={view.app} window={view.window!r} targets={targets}")
    print(f"observe, first frame : {observes[0]:>6}ms   (the application builds its tree here)")
    print(f"observe, warm median : {observe_warm:>6}ms   (samples {warm})")
    print(f"probe,   warm median : {report['probe_ms']:>6}ms")
    print(f"settle, quiet desktop: {report['settle_quiet_ms']:>6}ms   (samples {settles})")
    print(f"\nsteady-state step floor: {observe_warm + report['settle_quiet_ms']}ms + act")
    if len(set(titles)) > 1:
        print(
            f"note: the window changed {len(set(titles))} times during this run, so these"
            " numbers include whatever was happening to it"
        )
    if len(set(counts)) > 1:
        print(f"note: target count varied {min(counts)}..{max(counts)} across frames")
    return OK


def _output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--focus", default=None, help="bring this running application to the front first"
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=25, help="how many targets to print")
    parser.add_argument(
        "--grep", default=None, help="only targets whose name or kind contains this text"
    )


# --------------------------------------------------------------------------- #
# demo
# --------------------------------------------------------------------------- #


def _demo(args: argparse.Namespace) -> int:
    del args
    sensor = demo.sensor()
    runner = Runner(sensor=sensor, decider=demo.script(), verifier=demo.verifier())
    report = runner.run(demo.task())
    _print_report(report, json_output=False)
    return OK if report.status is Status.DONE else FAILED


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #


def _pixel_flags(parser: argparse.ArgumentParser) -> None:
    """How much of the pixel overlay to use.

    ``--no-pixels`` is accessibility only. ``--pixels`` forces the screenshot and the
    recognition pass even when accessibility looks rich. Both exist because the
    default is a heuristic, and the heuristic is wrong for exactly the window M1 is
    about: System Settings exposes 100+ targets (above ``rich_at``), so ``auto`` skips
    the overlay entirely, while the 27 sidebar rows that matter have no name at all.
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--no-pixels", action="store_true", help="accessibility only")
    group.add_argument(
        "--pixels",
        action="store_true",
        help="force the pixel overlay even when accessibility looks rich",
    )


def _mac_source(args: argparse.Namespace) -> Any:
    from .sensors.macos.screen import open_sensor

    if getattr(args, "no_pixels", False):
        return open_sensor(pixels=False)
    if getattr(args, "pixels", False):
        return open_sensor(pixels=True)
    return open_sensor(pixels="auto")


def _ax(args: argparse.Namespace) -> int:
    from .sensors.macos.ax import AXSource

    _focus(args)

    source = AXSource()
    started = time.perf_counter()
    targets = source.targets()
    elapsed = round((time.perf_counter() - started) * 1000)
    frame = source.frame
    shown = _grep(targets, args.grep)
    if args.json:
        print(
            json_io.dump(
                {
                    "ms": elapsed,
                    "frame": _frame_brief(frame),
                    "shown": len(shown),
                    "total": len(targets),
                    "targets": [t.brief() for t in shown],
                }
            )
        )
        return OK
    app = frame.app if frame else "?"
    window = frame.title if frame else "?"
    print(f"app={app} window={window} targets={len(targets)} ms={elapsed}")
    _print_targets(shown, args.limit)
    return OK


def _probe(args: argparse.Namespace) -> int:
    _focus(args)
    sensor = _mac_source(args)
    views = []
    for _ in range(max(1, args.frames)):
        started = time.perf_counter()
        view = sensor.observe()
        views.append((round((time.perf_counter() - started) * 1000), view))
    last = views[-1][1]
    shown = _grep(last.targets, args.grep)
    if args.json:
        print(
            json_io.dump(
                {
                    "ms": [ms for ms, _ in views],
                    "notes": dict(last.notes),
                    "shown": len(shown),
                    "total": len(last.targets),
                    "view": last.brief(),
                }
            )
        )
        return OK
    for ms, view in views:
        print(f"observe ms={ms} app={view.app} window={view.window}")
    print(f"notes: {dict(last.notes)}")
    _print_targets(shown, args.limit)
    return OK


def _doctor(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "modules": _modules(),
    }
    from .sensors.macos.screen import permissions

    report["permissions"] = permissions()

    if not report["modules"]["ApplicationServices"]:
        report["verdict"] = "install the macos extra: uv sync --extra macos"
        _print_doctor(report, args.json)
        return ENVIRONMENT

    if not report["permissions"]["accessibility"]:
        report["verdict"] = (
            "grant Accessibility to your terminal in System Settings > Privacy & Security"
        )
        _print_doctor(report, args.json)
        return ENVIRONMENT

    from .sensors.macos.ax import AXSource

    source = AXSource()
    started = time.perf_counter()
    targets = source.targets()
    report["ax"] = {
        "app": source.frame.app if source.frame else None,
        "window": source.frame.title if source.frame else None,
        "targets": len(targets),
        "ms": round((time.perf_counter() - started) * 1000),
        "click_only": sum(1 for t in targets if "click-only" in t.note),
        "verbs": sorted({str(v) for t in targets for v in t.actions}),
        "nudge_attributes": [
            a
            for a in ("AXManualAccessibility", "AXEnhancedUserInterface")
            if a in source.attribute_names(_app_ref())
        ],
    }

    if report["permissions"]["screen_recording"]:
        sensor = _mac_source(args)
        started = time.perf_counter()
        fused = sensor.observe()
        report["fused"] = {
            "targets": len(fused.targets),
            "ms": round((time.perf_counter() - started) * 1000),
            "notes": dict(fused.notes),
            "visual": sum(1 for t in fused.targets if t.visual),
        }

    report["verdict"] = _verdict(report)
    _print_doctor(report, args.json)
    return OK


def _app_ref() -> Any:
    from .sensors.macos.ax import appkit, ax

    pid = appkit().NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
    return ax().AXUIElementCreateApplication(pid)


def _verdict(report: dict[str, Any]) -> str:
    ax_info = report.get("ax", {})
    semantic = ax_info.get("targets", 0)
    if semantic == 0:
        return "accessibility exposes nothing for this app: pixels are carrying the whole load"
    if semantic < THIN_AX:
        return "accessibility is thin here: the pixel overlay matters, expect coordinate fallbacks"
    nudge = ax_info.get("nudge_attributes") or []
    if "AXManualAccessibility" in nudge:
        return "this is a Chromium-family app: its tree only exists because we asked for it"
    if nudge:
        return "this app exposes an accessibility-request flag; its tree was requested with it"
    return "accessibility describes this app well: most actions should be semantic"


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #


def _focus(args: argparse.Namespace) -> None:
    """Bring the named application forward, if one was named and is not already.

    Taking focus is a side effect on the person using the machine, so it is only
    done when it is actually needed, and it says whose window it took it from.
    """
    name = getattr(args, "focus", None)
    if not name:
        return
    # Machine-readable output goes to stdout, so anything meant for a person goes to
    # stderr when --json is on: a human line in front of the report made it unparseable.
    sink = sys.stderr if getattr(args, "json", False) else sys.stdout
    from .sensors.macos.apps import focus, frontmost

    before = frontmost()
    if before and before[0] == name:
        print(f"already frontmost: {name} (pid {before[1]})", file=sink)
        return
    activated = focus(name)
    print(f"took focus: {activated} (was {before[0] if before else 'nothing'})", file=sink)


MODEL_STEP_MS = 35_000
"""Wall clock to allow per step when a model decides.

Measured: one decision took 8.6-32.9 s (median about 21 s) against 0 ms for a rule, so
the example tasks' ``max_ms: 40000`` -- sized for a decider that costs nothing -- was
exhausted by the second step of every model run, which then read as a model failure.
"""


def _budget_for_a_model(task: Task) -> Task:
    """Raise a wall-clock budget that a model could not finish inside, and say so."""
    limits = task.limits
    floor = limits.max_steps * MODEL_STEP_MS
    if limits.max_ms is None or limits.max_ms >= floor:
        return task
    print(
        f"--model: raised max_ms {limits.max_ms} -> {floor} "
        f"({limits.max_steps} steps x {MODEL_STEP_MS} ms; one model decision measured 9-33 s)",
        file=sys.stderr,
    )
    return replace(task, limits=replace(limits, max_ms=floor))


def _run(args: argparse.Namespace) -> int:
    task, choices = json_io.load_task(args.task)
    if not choices and not args.model:
        print(
            "task file has no 'steps' script; use --model to let a model decide instead",
            file=sys.stderr,
        )
        return ENVIRONMENT
    # Built before anything touches the desktop, so a missing model is reported
    # without first taking focus away from whoever is using the machine.
    decider: Decider
    verifier: Verifier | None = None
    model_label: str | None = None
    if args.model:
        task = _budget_for_a_model(task)
        decider_model = model_from_env()
        model_label = decider_model.name
        decider = LLMDecider(decider_model)
        # A second, separate transport on purpose: a verifier that shares a
        # conversation with the decider is not an independent check.
        verifier = ModelVerifier(model_from_env())
    else:
        decider = ScriptedDecider(choices)
    if args.trust_decider:
        warnings.warn(
            "--trust-decider: DONE is accepted without independent verification", stacklevel=1
        )
        verifier = WaivedVerifier()

    _focus(args)
    sensor = _mac_source(args)
    if args.dry_run:
        view = sensor.observe()
        planned: tuple[Choice, ...]
        if args.model:
            # A model rehearsal: one observation, one question, nothing executed. The
            # whole point of rehearsing is to ask before acting, and a model is exactly
            # what should not be let loose on a real desktop untried. Asking twice is
            # also avoided on purpose -- this is one call, not a run.
            try:
                planned = (decider.choose(task=task, view=view, steps=()),)
            except Exception as exc:
                print(f"the model could not be asked: {type(exc).__name__}: {exc}", file=sys.stderr)
                return FAILED
            if not args.json:
                print(f"asked {model_label}, which said: {planned[0].why!r}")
        else:
            planned = choices
        rehearsal = rehearse(task, planned, view, verifier=verifier)
        if args.json:
            print(json_io.dump(rehearsal.brief()))
        else:
            _print_rehearsal(rehearsal)
        return FAILED if rehearsal.first_blocked else OK

    runner = Runner(sensor=sensor, decider=decider, verifier=verifier)
    report = runner.run(task)
    _print_report(report, json_output=args.json)
    return OK if report.status is Status.DONE else FAILED


# --------------------------------------------------------------------------- #
# printing
# --------------------------------------------------------------------------- #


def _grep(targets: Sequence[Any], needle: str | None) -> list[Any]:
    if not needle:
        return list(targets)
    wanted = needle.casefold()
    return [t for t in targets if wanted in f"{t.label} {t.kind} {t.spoken()}".casefold()]


def _frame_brief(frame: Any) -> dict[str, Any] | None:
    if frame is None:
        return None
    return {"app": frame.app, "window": frame.title, "pid": frame.pid, "box": frame.box}


def _modules() -> dict[str, bool]:
    found: dict[str, bool] = {}
    for name in ("ApplicationServices", "Quartz", "AppKit", "Vision"):
        try:
            __import__(name)
        except Exception:
            found[name] = False
        else:
            found[name] = True
    return found


def _print_targets(targets: Sequence[Any], limit: int) -> None:
    print(f"{'id':<16}{'kind':<26}{'verbs':<34}label")
    for target in targets[:limit]:
        verbs = ",".join(sorted(str(v) for v in target.actions))
        mark = " [visual]" if target.visual else ""
        print(f"{target.id:<16}{target.kind:<26}{verbs:<34}{target.label}{mark}")
    if len(targets) > limit:
        print(f"... {len(targets) - limit} more")


def _print_rehearsal(rehearsal: Rehearsal) -> None:
    print("rehearsal against the current view -- nothing was executed")
    print(f"view: app={rehearsal.app} window={rehearsal.window!r}")
    for finding in rehearsal.findings:
        mark = "ok  " if finding.ok else "FAIL"
        print(f"{finding.n:>3} {mark} {finding.what:<28} {finding.detail}")
    print(f"\nverdict: {rehearsal.verdict}")


def _print_report(report: Report, *, json_output: bool) -> None:
    if json_output:
        print(json_io.dump(json_io.report_to_dict(report)))
        return
    for step in report.steps:
        _print_step(step)
    print(f"\nstatus: {report.status}  steps: {report.steps_taken}  why: {report.why}")
    for check in report.checked:
        print(f"  check {'ok ' if check.ok else 'NO '} {check.check}  ({check.how})")
    if report.steps:
        routes: dict[str, int] = {}
        for step in report.steps:
            if step.via:
                routes[step.via] = routes.get(step.via, 0) + 1
        print(f"  routes: {routes or 'none'}")


def _print_step(step: Step) -> None:
    choice = step.choice.brief() if step.choice else {}
    label = step.target_label or choice.get("target_label") or ""
    times = f"{step.times.total:>5}ms"
    phases = step.times
    detail = f"look={phases.look} decide={phases.decide} act={phases.act} settle={phases.settle}"
    if step.failed:
        print(f"{step.n:>3} {times} FAIL {step.failed}: {step.why}")
        return
    what = str(choice.get("verb") or choice.get("finish"))
    if not label:
        # A verb is not the whole action: "KEY" on its own hid which key was pressed,
        # which is exactly what a trace of KEY, KEY, KEY, PRESS needs in order to be read.
        label = " ".join(
            str(choice[key]) for key in ("key", "chord", "scroll", "value_from") if choice.get(key)
        )
    if step.action is None:
        # A finish step: nothing was acted on, so movement would be noise.
        print(f"{step.n:>3} {times} {what:<8} {'':<24} {step.why}")
        return
    moved = "changed" if step.changed else "still  "
    moved += " progress" if step.progress else " no-progress"
    print(f"{step.n:>3} {times} {what:<8} {label:<24} via={step.via or '-':<16} {moved}  {detail}")


def _print_doctor(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json_io.dump(report))
        return
    print(f"python {report['python']} on {report['platform']}")
    print(
        "pyobjc: " + ", ".join(f"{k}={'yes' if v else 'NO'}" for k, v in report["modules"].items())
    )
    perms = report.get("permissions", {})
    print(
        "permissions: "
        + ", ".join(f"{k}={'granted' if v else 'MISSING'}" for k, v in perms.items())
    )
    ax_info = report.get("ax")
    if ax_info:
        print(f"\naccessibility: app={ax_info['app']} window={ax_info['window']!r}")
        print(f"  targets={ax_info['targets']} in {ax_info['ms']}ms")
        print(f"  click-only={ax_info['click_only']}")
        print(f"  verbs={ax_info['verbs']}")
        if ax_info["nudge_attributes"]:
            print(f"  chromium nudge present: {ax_info['nudge_attributes']}")
    fused = report.get("fused")
    if fused:
        notes = fused["notes"]
        print(f"\nfused: {fused['targets']} targets ({fused['visual']} from pixels)")
        print(f"  observed in {fused['ms']}ms")
        print(f"  semantic={notes.get('semantic')} pixels={notes.get('pixels')}")
        print(f"  pixels used={notes.get('pixels_used')}")
        print(f"  fusion={notes.get('fusion')}")
    print(f"\nverdict: {report.get('verdict')}")
