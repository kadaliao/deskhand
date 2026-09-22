"""The loop.

Design rules this file exists to enforce:

1. Nothing a decider or a backend does ends the run by itself. Failures are
   counted, recorded with a reason, and fed back -- up to a budget.
2. Phase timings are per step, so "is this fast?" is answerable.
3. Progress is judged on content, not structure, so text changing in place
   while the layout stays put does not look like nothing happened.
4. DONE requires a Verifier, not a decider's opinion.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Generator
from typing import cast

from .errors import BadChoice, StaleTarget
from .protocols import Decider, Sensor, Verifier
from .types import (
    Action,
    CheckResult,
    Choice,
    Limits,
    Report,
    Status,
    Step,
    Task,
    Times,
    View,
)
from .validate import build_action
from .verify import NoVerifier

logger = logging.getLogger(__name__)


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


class Runner:
    """Drives one task to a terminal status."""

    def __init__(
        self,
        *,
        sensor: Sensor,
        decider: Decider,
        verifier: Verifier | None = None,
        limits: Limits | None = None,
    ) -> None:
        self.sensor = sensor
        self.decider = decider
        self.verifier: Verifier = verifier or NoVerifier()
        self.limits = limits
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Ask the loop to stop after the current step."""
        self._cancel.set()

    # ------------------------------------------------------------------ parts

    @staticmethod
    def _stop(
        *,
        status: Status,
        task: Task,
        view: View,
        steps: list[Step],
        why: str,
        checked: tuple[CheckResult, ...] = (),
    ) -> Report:
        logger.debug("stop status=%s steps=%d why=%s", status, len(steps), why)
        return Report(
            status=status, task=task, view=view, steps=tuple(steps), checked=checked, why=why
        )

    @staticmethod
    def _failure(
        *,
        n: int,
        view: View,
        times: Times,
        exc: BaseException,
        choice: Choice | None = None,
    ) -> Step:
        """A step that did not execute anything. Recorded so the trace stays honest."""
        return Step(
            n=n,
            before=view.revision,
            after=view.revision,
            times=times,
            choice=choice,
            failed=type(exc).__name__,
            why=str(exc) or type(exc).__name__,
        )

    @staticmethod
    def _out_of_budget(failures: int, limits: Limits) -> bool:
        return failures > limits.max_failures

    # ------------------------------------------------------------------- loop

    def steps(self, task: Task) -> Generator[Step, None, Report]:
        """Yield one Step per turn. The return value is the Report.

        Deliberate PEP 380: the outcome travels in ``StopIteration.value``, which
        :meth:`run` reads, so one pass over the loop hands a caller both the step
        stream and the verdict, and dropping the generator early is the only way to
        lose it. The ``no-return-value-in-generator`` rule expects the value to be
        discarded; here it is not, so the rule is declined rather than obeyed.
        """
        limits = self.limits or task.limits
        self._cancel.clear()
        deadline = None if limits.max_ms is None else time.perf_counter() + limits.max_ms / 1000.0

        steps: list[Step] = []
        failures = 0
        quiet = 0

        look_start = time.perf_counter()
        view = self.sensor.observe()
        look_ms = _ms(look_start)
        # Revision digests already seen, and the step each was first seen at. The starting
        # view counts as step 0 so a run that returns to it before doing anything is caught.
        visited: dict[str, int] = {view.revision: 0}
        loops = 0

        def idle(n: int) -> Step:
            return Step(n=n, before=view.revision, after=view.revision, times=Times(look=look_ms))

        while len(steps) < limits.max_steps:
            n = len(steps) + 1

            if self._cancel.is_set():
                step = idle(n)
                steps.append(step)
                yield step
                return self._stop(
                    status=Status.ESCALATE,
                    task=task,
                    view=view,
                    steps=steps,
                    why="cancelled by caller",
                )

            if deadline is not None and time.perf_counter() > deadline:
                step = idle(n)
                steps.append(step)
                yield step
                return self._stop(
                    status=Status.ESCALATE,
                    task=task,
                    view=view,
                    steps=steps,
                    why=f"wall clock budget of {limits.max_ms}ms exhausted",
                )

            # ---- decide -------------------------------------------------
            decide_start = time.perf_counter()
            try:
                choice = self.decider.choose(task=task, view=view, steps=tuple(steps))
            except Exception as exc:
                failures += 1
                step = self._failure(
                    n=n, view=view, times=Times(look=look_ms, decide=_ms(decide_start)), exc=exc
                )
                steps.append(step)
                yield step
                if self._out_of_budget(failures, limits):
                    return self._stop(
                        status=Status.ESCALATE,
                        task=task,
                        view=view,
                        steps=steps,
                        why=f"decider failed {failures} times; last: {type(exc).__name__}: {exc}",
                    )
                look_ms = 0
                continue
            decide_ms = _ms(decide_start)

            # ---- done? ---------------------------------------------------
            if choice.finish is not None:
                step, report = self._terminal(
                    task=task,
                    view=view,
                    choice=choice,
                    steps=steps,
                    n=n,
                    times=Times(look=look_ms, decide=decide_ms),
                )
                yield step
                return report

            # ---- validate ------------------------------------------------
            try:
                action = build_action(choice, view, task)
            except BadChoice as exc:
                failures += 1
                step = self._failure(
                    n=n,
                    view=view,
                    times=Times(look=look_ms, decide=decide_ms),
                    exc=exc,
                    choice=choice,
                )
                steps.append(step)
                yield step
                if self._out_of_budget(failures, limits):
                    return self._stop(
                        status=Status.ESCALATE,
                        task=task,
                        view=view,
                        steps=steps,
                        why=f"decider kept asking for impossible actions; last: {exc}",
                    )
                look_ms = 0
                continue

            label = self._label_of(view, action)
            phase = Times(look=look_ms, decide=decide_ms)

            # ---- aim -----------------------------------------------------
            if self.sensor.is_stale(view, action):
                failures += 1
                step = self._failure(
                    n=n,
                    view=view,
                    times=phase,
                    choice=choice,
                    exc=StaleTarget("target changed between deciding and acting"),
                )
                steps.append(step)
                yield step
                if self._out_of_budget(failures, limits):
                    return self._stop(
                        status=Status.ESCALATE,
                        task=task,
                        view=view,
                        steps=steps,
                        why=f"screen kept changing under the decider ({failures} times)",
                    )
                look_start = time.perf_counter()
                view = self.sensor.observe()
                look_ms = _ms(look_start)
                continue

            # ---- act -----------------------------------------------------
            act_start = time.perf_counter()
            try:
                route = self.sensor.act(view, action)
            except Exception as exc:
                failures += 1
                step = self._failure(
                    n=n,
                    view=view,
                    times=Times(look=look_ms, decide=decide_ms, act=_ms(act_start)),
                    exc=exc,
                    choice=choice,
                )
                steps.append(step)
                yield step
                if self._out_of_budget(failures, limits):
                    return self._stop(
                        status=Status.ESCALATE,
                        task=task,
                        view=view,
                        steps=steps,
                        why=f"backend failed {failures} times; last: {type(exc).__name__}: {exc}",
                    )
                look_start = time.perf_counter()
                view = self.sensor.observe()
                look_ms = _ms(look_start)
                continue
            act_ms = _ms(act_start)

            # ---- settle and judge ----------------------------------------
            settle_start = time.perf_counter()
            after = self.sensor.settle(view, budget_ms=limits.settle_ms)
            step = Step(
                n=n,
                before=view.revision,
                after=after.revision,
                times=Times(look=look_ms, decide=decide_ms, act=act_ms, settle=_ms(settle_start)),
                choice=choice,
                action=action,
                target_label=label,
                via=route,
                changed=after.revision != view.revision,
                progress=after.content != view.content,
            )
            steps.append(step)
            view = after
            yield step

            if step.progress:
                failures = 0
                quiet = 0
            else:
                quiet += 1
                if quiet >= limits.no_progress_steps:
                    return self._stop(
                        status=Status.STUCK,
                        task=task,
                        view=view,
                        steps=steps,
                        why=f"{quiet} steps in a row changed nothing",
                    )

            # The other shape of stuck: the screen keeps changing and keeps coming back.
            # `progress` is content-based, so moving a sidebar selection counts as
            # progress -- which is exactly what a lost decider does, and it means the
            # no-progress bound above never fires. Measured on a real model run: five
            # KEY DOWN, one KEY UP and two PRESS on the same three rows, all "progress",
            # until the step budget absorbed it.
            first_seen = visited.get(after.revision)
            if first_seen is None:
                visited[after.revision] = n
                loops = 0
            else:
                loops += 1
                if loops >= limits.loop_steps:
                    return self._stop(
                        status=Status.STUCK,
                        task=task,
                        view=view,
                        steps=steps,
                        why=(
                            f"the desktop returned to the state from step {first_seen} "
                            f"{loops} times; the decider is going in circles"
                        ),
                    )
            look_ms = 0

        return self._stop(
            status=Status.ESCALATE,
            task=task,
            view=view,
            steps=steps,
            why=f"step budget of {limits.max_steps} exhausted",
        )

    # -------------------------------------------------------------- terminal

    def _terminal(
        self,
        *,
        task: Task,
        view: View,
        choice: Choice,
        steps: list[Step],
        n: int,
        times: Times,
    ) -> tuple[Step, Report]:
        assert choice.finish is not None
        step = Step(
            n=n,
            before=view.revision,
            after=view.revision,
            times=times,
            choice=choice,
            why=choice.why,
        )

        if choice.finish is not Status.DONE:
            return step, self._stop(
                status=choice.finish,
                task=task,
                view=view,
                steps=[*steps, step],
                why=choice.why or f"decider reported {choice.finish}",
            )

        # Every acceptance criterion, never the subset the decider nominated.
        # ``choice.says`` is the decider's own statement of what it believes and is
        # recorded in the trace, but it is not the question: a claim is not allowed to
        # choose what gets checked, or an untrusted decider (a model) could name one
        # easy criterion, have that confirmed, and reach DONE with the real ones never
        # looked at. Naming a subset does not narrow this; it only shows up in the trace.
        checked = self.verifier.confirm(task=task, view=view, claims=task.checks)
        missing = [c.check for c in checked if not c.ok]
        if missing:
            return step, self._stop(
                status=Status.ESCALATE,
                task=task,
                view=view,
                steps=[*steps, step],
                why=f"claimed DONE but these checks are not confirmed: {missing}",
                checked=checked,
            )
        return step, self._stop(
            status=Status.DONE,
            task=task,
            view=view,
            steps=[*steps, step],
            why=choice.why or f"confirmed {len(checked)} check(s)",
            checked=checked,
        )

    @staticmethod
    def _label_of(view: View, action: Action) -> str | None:
        if action.target is None:
            return None
        try:
            return view.target(action.target).label or action.target
        except KeyError:
            return action.target

    # ------------------------------------------------------------------- run

    def run(self, task: Task) -> Report:
        stream = self.steps(task)
        try:
            while True:
                next(stream)
        except StopIteration as stop:
            return cast("Report", stop.value)
