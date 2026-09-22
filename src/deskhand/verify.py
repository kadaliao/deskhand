"""Verification, deliberately outside the decider.

The rule: a decider may claim DONE, but a Verifier has to confirm it. With no
verifier configured the honest answer is "not verified", so the run escalates
instead of reporting success nobody checked.

There are two ways to be checked, and they fail in opposite directions.
``PredicateVerifier`` is a caller-supplied predicate: precise, offline, and only
as good as its coverage. ``ModelVerifier`` asks a model about the live view: it
covers any wording, and can be wrong. Both are built to prefer "unconfirmed" over
"confirmed", because the expensive mistake here is a run that reports DONE for
work nobody did.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence

from .model import Model
from .types import CheckResult, Task, View

Check = Callable[[Task, View], bool]
"""A predicate over the live view, one per acceptance criterion."""


class NoVerifier:
    """Default. Never confirms anything -- that is the point."""

    def confirm(self, *, task: Task, view: View, claims: Sequence[str]) -> tuple[CheckResult, ...]:
        del task, view
        return tuple(
            CheckResult(check, False, "no verifier configured; claim not independently checked")
            for check in claims
        )


class PredicateVerifier:
    """Confirms named criteria with predicates the caller supplies.

    Criteria without a registered predicate come back unverified rather than
    assumed true, so partial coverage stays visible.
    """

    def __init__(self, checks: Mapping[str, Check]) -> None:
        self._checks = dict(checks)

    def covers(self, claim: str) -> bool:
        """Whether a predicate exists for this criterion.

        Used by a rehearsal to catch the most common configuration mistake: a
        claim whose wording does not match any registered check key, which would
        silently escalate instead of finishing.
        """
        return claim in self._checks

    def confirm(self, *, task: Task, view: View, claims: Sequence[str]) -> tuple[CheckResult, ...]:
        results: list[CheckResult] = []
        for claim in claims:
            predicate = self._checks.get(claim)
            if predicate is None:
                results.append(CheckResult(claim, False, "no predicate registered for this check"))
                continue
            try:
                ok = bool(predicate(task, view))
            except Exception as exc:  # a broken predicate is not a confirmation
                results.append(
                    CheckResult(claim, False, f"predicate raised {type(exc).__name__}: {exc}")
                )
                continue
            results.append(
                CheckResult(
                    claim,
                    ok,
                    "predicate matched the live view" if ok else "predicate did not match",
                )
            )
        return tuple(results)


# --------------------------------------------------------------------------- #
# asking a model
# --------------------------------------------------------------------------- #

CHECK_SYSTEM = """You check whether a claim about a computer screen is true.

You did not perform the task, and you are not told what another agent did or why. You are
given one claim, the task it belongs to, and one observation of the screen. Judge the claim
against that observation only: if the observation does not show it, it is not confirmed.

Reply with exactly one JSON object, and nothing else:

  {"ok": true, "how": "<what in the observation confirms it>"}
  {"ok": false, "how": "<what is missing or disagrees>"}

"ok" must be a JSON boolean. A sentence, a yes, or a maybe is not a confirmation.
"""


def check_prompt(task: Task, view: View, claim: str) -> tuple[str, str]:
    """The exact question asked about one claim.

    A pure function so a test can read the question instead of trusting it.
    """
    payload: dict[str, object] = {"claim": claim, "task": task.brief(), "view": view.brief()}
    return CHECK_SYSTEM, json.dumps(payload, ensure_ascii=False, indent=1)


class WaivedVerifier:
    """Confirms every claim without checking anything, and says exactly that.

    Reachable only from an explicit ``--trust-decider``, which warns. It exists so the
    unsafe path is *named* in the trace instead of looking like a passing check. The
    seeded predicate it replaces reported "predicate matched the live view", which was
    never true: a predicate that returns ``True`` cannot fail, so the report claimed a
    match nobody made.

    Measured, not hypothetical. On a Chinese macOS the appearance task resolved leading
    ``外观`` to the settings *window* instead of the sidebar row, performed a coordinate
    click on that window, failed to disambiguate ``深色``, changed nothing, and was
    reported as ``DONE`` with a confirmed check while the machine stayed in Light mode.
    """

    def confirm(self, *, task: Task, view: View, claims: Sequence[str]) -> tuple[CheckResult, ...]:
        del task, view
        return tuple(
            CheckResult(claim, True, "WAIVED by --trust-decider: NOT independently checked")
            for claim in claims
        )


class ModelVerifier:
    """Confirms a claim by asking a model what the screen shows.

    Independence here is structural rather than a promise:

    * the runner hands this object the task, the live view and the claims -- never
      the decider's reasoning, its chosen action, or the steps it took;
    * one question per claim, so a model cannot confirm a set with a single answer;
    * a transport error, a missing field, or a non-boolean ``ok`` is *not* a
      confirmation, so the run escalates instead of reporting success nobody checked.

    Sharing one transport with the decider is allowed but weakens the second point:
    two question/answer pairs in one conversation are not independent answers. The
    CLI opens a separate transport for exactly this reason.
    """

    def __init__(self, model: Model) -> None:
        self.model = model

    def covers(self, claim: str) -> bool:
        """A model will attempt any wording, unlike a registered predicate.

        Present so a rehearsal does not warn that a claim has no predicate when a
        model is configured to handle it.
        """
        del claim
        return True

    def confirm(self, *, task: Task, view: View, claims: Sequence[str]) -> tuple[CheckResult, ...]:
        return tuple(self._ask_about(task, view, claim) for claim in claims)

    def _ask_about(self, task: Task, view: View, claim: str) -> CheckResult:
        system, prompt = check_prompt(task, view, claim)
        try:
            reply = self.model.ask(system=system, prompt=prompt)
        except Exception as exc:  # a verifier that could not ask has confirmed nothing
            return CheckResult(
                claim, False, f"could not ask {self.model.name}: {type(exc).__name__}: {exc}"
            )

        verdict = reply.get("ok")
        how = reply.get("how")
        said = how.strip() if isinstance(how, str) and how.strip() else "no reason given"
        # A real JSON boolean, and nothing else. `is True` would be the obvious way
        # to say that, but isinstance says the same thing about both answers in one
        # branch -- and still refuses an int or the string "true".
        if isinstance(verdict, bool):
            matched = "matched" if verdict else "did not match"
            return CheckResult(claim, verdict, f"{self.model.name} {matched} it: {said}")
        return CheckResult(
            claim,
            False,
            f"{self.model.name} did not answer with a boolean 'ok' ({verdict!r}): {said}",
        )
