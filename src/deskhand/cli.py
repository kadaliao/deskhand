"""Command line entry point.

``demo`` runs anywhere. ``ax``, ``probe``, ``doctor`` and ``run`` need macOS and
the Accessibility permission; ``doctor`` is the first thing to run, because it
tells you how much of the current app accessibility can actually describe.
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from collections.abc import Sequence
from typing import Any

from . import demo, json_io
from .deciders.scripted import ScriptedDecider
from .errors import DeskhandError
from .rehearse import Rehearsal, rehearse
from .runner import Runner
from .types import Report, Status, Step
from .verify import PredicateVerifier

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
    probe.add_argument("--no-pixels", action="store_true", help="accessibility only")
    probe.add_argument("--frames", type=int, default=1, help="observe this many times")

    doctor = sub.add_parser("doctor", help="permissions, coverage and timing for the frontmost app")
    doctor.add_argument("--json", action="store_true")

    permit = sub.add_parser(
        "permit", help="ask macOS for the missing permissions and name the app to toggle"
    )
    permit.add_argument("--json", action="store_true")

    run = sub.add_parser("run", help="run a task file against the real desktop")
    run.add_argument("--task", required=True, help="JSON task, optionally with a steps script")
    run.add_argument("--json", action="store_true")
    run.add_argument("--no-pixels", action="store_true")
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

    args = parser.parse_args(argv)
    handlers = {
        "demo": _demo,
        "ax": _ax,
        "probe": _probe,
        "doctor": _doctor,
        "permit": _permit,
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

    current = status()
    owner = blame()
    outstanding = [name for name, granted in current.items() if not granted]
    for name in outstanding:
        # Shows the system dialog. A decision does not happen inside this call.
        request(name)
    report = {
        "granted": current,
        "requested": outstanding,
        "attributed_to": owner,
        "instructions": to_do(current, owner),
        "recheck": status(),
    }
    if args.json:
        print(json_io.dump(report))
    else:
        for line in report["instructions"]:
            print(line)
        if outstanding:
            print(f"\nasked macOS for: {', '.join(outstanding)}")
            print("if a dialog appeared, the name on it is the app to toggle")
    return OK if not outstanding else ENVIRONMENT


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


def _mac_source(no_pixels: bool = False) -> Any:
    from .sensors.macos.screen import open_sensor

    return open_sensor(pixels=False if no_pixels else "auto")


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
    sensor = _mac_source(no_pixels=args.no_pixels)
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
        sensor = _mac_source(no_pixels=False)
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

    pid = int(appkit().NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier())
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
    """Bring the named application forward, if one was named."""
    name = getattr(args, "focus", None)
    if not name:
        return
    from .sensors.macos.apps import activate, frontmost

    activated = activate(name)
    time.sleep(0.4)  # give the window server a moment to make it frontmost
    current = frontmost()
    print(f"focused: {activated} (frontmost is now {current[0] if current else '?'})")


def _run(args: argparse.Namespace) -> int:
    task, choices = json_io.load_task(args.task)
    if not choices:
        print("task file has no 'steps' script; nothing to run", file=sys.stderr)
        return ENVIRONMENT
    _focus(args)
    sensor = _mac_source(no_pixels=args.no_pixels)
    verifier = None
    if args.trust_decider:
        warnings.warn(
            "--trust-decider: DONE is accepted without independent verification", stacklevel=1
        )
        verifier = PredicateVerifier(dict.fromkeys(task.checks, _always))
    if args.dry_run:
        view = sensor.observe()
        rehearsal = rehearse(task, choices, view, verifier=verifier)
        if args.json:
            print(json_io.dump(rehearsal.brief()))
        else:
            _print_rehearsal(rehearsal)
        return FAILED if rehearsal.first_blocked else OK

    runner = Runner(sensor=sensor, decider=ScriptedDecider(choices), verifier=verifier)
    report = runner.run(task)
    _print_report(report, json_output=args.json)
    return OK if report.status is Status.DONE else FAILED


def _always(task: Any, view: Any) -> bool:  # noqa: ARG001
    return True


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
