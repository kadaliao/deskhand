"""Choice -> Action.

This is the only place a decision becomes executable, and it is deliberately
pedantic: unknown ids, verbs a target does not advertise, and input keys the
caller never supplied are all rejected. A decider can be wrong, but it cannot
invent.
"""

from __future__ import annotations

from .errors import BadChoice
from .fingerprint import norm_text
from .types import (
    FROM_INPUT,
    MODIFIERS,
    ON_A_TARGET,
    SCROLLS,
    Action,
    Choice,
    Target,
    Task,
    Verb,
    View,
)


def resolve_target(choice: Choice, view: View, *, role: str) -> Target | None:
    """Find the target a choice refers to, by id or by label."""
    target_id = choice.target if role == "target" else choice.onto
    label = choice.target_label if role == "target" else choice.onto_label

    if target_id is not None:
        try:
            return view.target(target_id)
        except KeyError as exc:
            raise BadChoice(f"{role} id not in the current view: {target_id}") from exc

    if label is not None:
        wanted = norm_text(label)
        exact = [t for t in view.targets if t.enabled and norm_text(t.label) == wanted]
        loose = exact or [
            t for t in view.targets if t.enabled and wanted and wanted in norm_text(t.spoken())
        ]
        if not loose:
            raise BadChoice(f"no {role} matching {label!r} in the current view")
        if len(loose) > 1:
            names = ", ".join(f"{t.kind}:{t.label}" for t in loose[:4])
            raise BadChoice(f"{role} {label!r} is ambiguous ({len(loose)}): {names}")
        return loose[0]

    return None


def build_action(choice: Choice, view: View, task: Task) -> Action:
    """Validate one choice and freeze it into an action with freshness guards."""
    verb = choice.verb
    if verb is None:
        raise BadChoice("a finish choice cannot be executed")

    target: Target | None = None
    onto: Target | None = None

    if verb in ON_A_TARGET:
        target = resolve_target(choice, view, role="target")
        if target is None:
            raise BadChoice(f"{verb} needs a target")
        if not target.enabled:
            raise BadChoice(f"target is disabled: {target.label or target.id}")
        if verb not in target.actions:
            offered = ", ".join(sorted(str(a) for a in target.actions)) or "nothing"
            raise BadChoice(
                f"{verb} not offered by {target.kind} {target.label!r}; it offers {offered}"
            )

    if verb is Verb.DRAG:
        onto = resolve_target(choice, view, role="onto")
        if onto is None:
            raise BadChoice("DRAG needs a destination")
        if target is not None and onto.id == target.id:
            raise BadChoice("DRAG source and destination are the same target")

    value: str | None = None
    if verb in FROM_INPUT:
        if not choice.value_from:
            raise BadChoice(f"{verb} requires value_from naming a key in Task.inputs")
        if choice.value_from not in task.inputs:
            raise BadChoice(
                f"Task.inputs has no {choice.value_from!r}; "
                f"available: {sorted(task.inputs) or 'none'}",
            )
        value = str(task.inputs[choice.value_from])

    if verb is Verb.KEY and not choice.key:
        raise BadChoice("KEY requires key")
    if verb is Verb.CHORD:
        _check_chord(choice.chord)
    if verb is Verb.SCROLL and choice.scroll not in SCROLLS:
        raise BadChoice(f"SCROLL direction must be one of {sorted(SCROLLS)}, got {choice.scroll!r}")

    return Action(
        verb=verb,
        target=target.id if target else None,
        onto=onto.id if onto else None,
        value=value,
        key=choice.key,
        chord=choice.chord,
        scroll=choice.scroll,
        guard=target.fingerprint() if target else None,
        onto_guard=onto.fingerprint() if onto else None,
        visual=bool(target.visual) if target else False,
    )


def _check_chord(chord: str | None) -> None:
    if not chord:
        raise BadChoice("CHORD requires chord")
    modifiers, _, key = chord.rpartition("+")
    if not modifiers or not key:
        raise BadChoice(f"CHORD {chord!r} needs a modifier and a key, e.g. CMD+A")
    unknown = [part for part in modifiers.split("+") if part not in MODIFIERS]
    if unknown:
        raise BadChoice(f"unknown modifier(s) {unknown}; allowed {sorted(MODIFIERS)}")
