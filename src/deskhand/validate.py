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

CONTAINERS: frozenset[str] = frozenset(
    {"window", "group", "scrollarea", "toolbar", "splitgroup", "browser"}
)
"""Roles that hold other things rather than being things to operate.

A window advertises ``AXPress`` -- it means "raise me" -- so a window titled like a control
is indistinguishable from that control by name alone. Measured: on a `zh-Hans` macOS the
appearance task's first step, ``PRESS 外观``, resolved to the settings *window*, because its
title is exactly ``外观`` and the sidebar row that the task meant carries that word only as
a pixel-derived alias. ``PRESS`` on a window is not a press: it degraded to a coordinate
click at the window's centre, which is a blind click into whatever happens to be there.
"""


def _is_container(target: Target) -> bool:
    return target.kind.split(":", 1)[0] in CONTAINERS


def _prefer_controls(candidates: list[Target]) -> list[Target]:
    """Drop containers when something operable matched the same name.

        Only when both kinds are present: a window is a legitimate thing to open when nothing
        else matched it, and dropping it unconditionally would turn "open that window" into "no
    target matching". This narrows the field; where the remaining candidates still tie, the
        refusal below is unchanged.
    """
    controls = [target for target in candidates if not _is_container(target)]
    return controls or candidates


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
        matched = [
            target
            for target in view.targets
            if target.enabled
            and (
                norm_text(target.label) == wanted
                or (wanted and wanted in norm_text(target.spoken()))
            )
        ]
        if not matched:
            raise BadChoice(f"no {role} matching {label!r} in the current view")
        # A container is never what was meant when a control matched the same name, whether
        # the container matched it exactly and the control only as a pixel-derived alias or
        # the other way round. Measured: the settings window's title is exactly ``外观`` and
        # the sidebar row that the task meant carried ``外观`` only as an alias, so
        # "exact beats alias" alone chose the window and made the step a blind click.
        operable = _prefer_controls(matched)
        # Among equals, a real label still beats an alias.
        exact = [target for target in operable if norm_text(target.label) == wanted]
        candidates = exact or operable
        if len(candidates) > 1:
            # Name each candidate by id, because "pick one of these" is only
            # actionable if the ids are visible: the fix for an ambiguous label is
            # to choose by id, and this message is fed back to the decider.
            names = ", ".join(f"{t.kind}:{t.label or t.id} ({t.id})" for t in candidates[:4])
            raise BadChoice(f"{role} {label!r} is ambiguous ({len(candidates)}): {names}")
        return candidates[0]

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
