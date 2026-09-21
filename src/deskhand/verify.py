"""Verification, deliberately outside the decider.

The rule: a decider may claim DONE, but a Verifier has to confirm it. With no
verifier configured the honest answer is "not verified", so the run escalates
instead of reporting success nobody checked.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

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
