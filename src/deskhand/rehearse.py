"""Rehearse a task script against the live view without touching anything.

A script of `Choice`s is written against labels, and labels differ between
application versions and languages. Running it blind means the first thing you
learn about a wrong label is that the machine did something unexpected. This
checks each step against one observation and says what would happen, which is
the difference between "the script is wrong" and "my computer is doing something
weird right now".

The honest limit: only the *first* action step is a real verdict. Later steps
run after earlier ones have changed the screen, so they can only be reported as
"present in the current view" or "not visible yet".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import BadChoice
from .types import Choice, Status, Target, Task, View
from .validate import build_action, resolve_target
from .verify import NoVerifier, WaivedVerifier


@dataclass(frozen=True, slots=True)
class Finding:
    n: int
    what: str
    ok: bool
    detail: str
    target: Target | None = None

    def brief(self) -> dict[str, Any]:
        # Every key is always present: a consumer should not have to guess whether a
        # finding carries a target.
        return {
            "step": self.n,
            "what": self.what,
            "ok": self.ok,
            "detail": self.detail,
            "target": None if self.target is None else _target_brief(self.target),
        }


def _target_brief(target: Target) -> dict[str, Any]:
    return {
        "id": target.id,
        "kind": target.kind,
        "label": target.label,
        "actions": sorted(str(a) for a in target.actions),
        "at": None if target.box is None else [round(target.box.x), round(target.box.y)],
    }


@dataclass(frozen=True, slots=True)
class Rehearsal:
    app: str
    window: str
    findings: tuple[Finding, ...]

    @property
    def first(self) -> Finding | None:
        return next(
            (f for f in self.findings if not f.what.startswith(("DONE", "STUCK", "ESCALATE"))), None
        )

    @property
    def blocked(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.ok)

    @property
    def first_blocked(self) -> bool:
        first = self.first
        return first is not None and not first.ok

    @property
    def verdict(self) -> str:
        first = self.first
        if first is None:
            return "there is no action step in this script"
        if not first.ok:
            return f"step {first.n} cannot run as written: {first.detail}"
        return (
            f"step {first.n} is executable as written; "
            f"{len(self.findings) - 1} later step(s) can only be re-checked after it runs"
        )

    def brief(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "window": self.window,
            "verdict": self.verdict,
            "findings": [f.brief() for f in self.findings],
        }


def rehearse(
    task: Task, choices: tuple[Choice, ...], view: View, *, verifier: object | None = None
) -> Rehearsal:
    """What each step would do against this one observation. Executes nothing."""
    findings: list[Finding] = []
    for number, choice in enumerate(choices, start=1):
        if choice.finish is not None:
            findings.append(_finish_finding(number, choice, task, verifier))
        else:
            findings.append(_action_finding(number, choice, task, view))
    return Rehearsal(app=view.app, window=view.window, findings=tuple(findings))


def _finish_finding(number: int, choice: Choice, task: Task, verifier: object | None) -> Finding:
    what = f"{choice.finish}"
    if choice.finish is not Status.DONE:
        return Finding(number, what, True, choice.why or "the decider stops here", None)

    # The decider's own nomination is not the question here either: a rehearsal has to
    # report on exactly what a real run would verify.
    claims = task.checks
    if isinstance(verifier, WaivedVerifier):
        # A waiver is not a confirmation, and a rehearsal that called it one would be
        # promising the same false success the waiver produces.
        return Finding(
            number,
            what,
            True,
            f"{len(claims)} claim(s) would be WAIVED rather than checked (--trust-decider)",
            None,
        )
    if verifier is None or isinstance(verifier, NoVerifier):
        return Finding(
            number,
            what,
            False,
            f"claims {list(claims)}, but no verifier is configured: "
            f"this would escalate rather than finish",
            None,
        )
    covers = getattr(verifier, "covers", None)
    unconfirmable = [c for c in claims if callable(covers) and not covers(c)]
    if unconfirmable:
        return Finding(
            number,
            what,
            False,
            f"no predicate registered for {unconfirmable}: DONE would escalate",
            None,
        )
    return Finding(number, what, True, f"{len(claims)} claim(s) look confirmable", None)


def _action_finding(number: int, choice: Choice, task: Task, view: View) -> Finding:
    what = f"{choice.verb} {choice.target_label or choice.target or ''}".strip()

    # Report the *candidate*, then validate. The candidate is what makes a
    # "no target matching" message actionable.
    target: Target | None = None
    try:
        target = resolve_target(choice, view, role="target")
    except BadChoice:
        target = None

    # Computed out here rather than inside the handler: it describes the situation, not
    # the error, and an `and` in an except clause's body is what the S5714 check reads as
    # "a boolean expression in an except statement" (it searches the whole clause, not
    # just the exception types).
    hidden_behind_an_earlier_step = target is None and number > 1

    try:
        action = build_action(choice, view, task)
    except BadChoice as exc:
        detail = str(exc)
        if hidden_behind_an_earlier_step:
            detail += " (not in the current view; an earlier step may reveal it)"
        return Finding(number, what, False, detail, None)

    assert target is not None  # build_action succeeded, so it resolved
    where = "no geometry" if target.box is None else f"at {target.box.x:.0f},{target.box.y:.0f}"
    suffix = f" [{target.note}]" if target.note else ""
    offered = ",".join(sorted(str(v) for v in target.actions))
    return Finding(
        number,
        what,
        True,
        f"would {action.verb} {target.kind} {target.label!r} {where}; it offers {offered}{suffix}",
        target,
    )
