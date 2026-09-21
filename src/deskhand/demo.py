"""A tiny scripted desktop, so the whole loop runs with no permissions at all.

``deskhand demo`` uses this. It is also what several tests drive, including the
failure paths: the first scripted choice aims at a button that does not exist,
and the run is expected to survive it.
"""

from __future__ import annotations

from .deciders.scripted import ScriptedDecider
from .sensors.fake import Edge, FakeSensor
from .types import Box, Choice, Status, Target, Task, Verb, View
from .verify import PredicateVerifier

EFFECTS = Target(
    id="effects_button",
    kind="button",
    label="Effects",
    actions=frozenset({Verb.PRESS}),
    box=Box(40, 80, 90, 28),
    source="demo",
)
SEARCH = Target(
    id="search_field",
    kind="text_field",
    label="Search Effects",
    value="",
    actions=frozenset({Verb.TYPE, Verb.PRESS}),
    box=Box(40, 130, 320, 28),
    source="demo",
)
BLUR = Target(
    id="blur_button",
    kind="button",
    label="Gaussian Blur",
    actions=frozenset({Verb.PRESS}),
    box=Box(40, 180, 160, 28),
    source="demo",
)
ENABLED_OFF = Target(
    id="enabled_check",
    kind="checkbox",
    label="Enabled",
    value="off",
    actions=frozenset({Verb.PRESS, Verb.SET}),
    box=Box(40, 230, 24, 24),
    source="demo",
)
ENABLED_ON = Target(
    id="enabled_check",
    kind="checkbox",
    label="Enabled",
    value="on",
    actions=frozenset({Verb.PRESS, Verb.SET}),
    box=Box(40, 230, 24, 24),
    source="demo",
    focused=True,
)

_EDGES: dict[Edge, str] = {
    ("home", EFFECTS.id, Verb.PRESS): "panel",
    ("panel", "blur_button", Verb.PRESS): "chosen",
    ("chosen", "enabled_check", Verb.PRESS): "enabled",
}


def sensor() -> FakeSensor:
    return FakeSensor(
        {
            "home": (
                EFFECTS,
                Target(id="clip_1", kind="clip", label="interview.mp4", source="demo"),
            ),
            "panel": (EFFECTS, SEARCH, BLUR),
            "chosen": (EFFECTS, SEARCH, BLUR, ENABLED_OFF),
            "enabled": (EFFECTS, SEARCH, BLUR, ENABLED_ON),
        },
        start="home",
        edges=_EDGES,
    )


def task() -> Task:
    return Task(
        goal="Turn on the Gaussian Blur effect for the selected clip",
        checks=("Gaussian Blur is enabled on the clip",),
        inputs={},
        notes=("Do not touch other clips.",),
    )


def script() -> ScriptedDecider:
    """Deliberately starts with a nonsense choice, then does the real work."""
    return ScriptedDecider(
        [
            Choice(verb=Verb.PRESS, target_label="Nonexistent Control", why="wrong on purpose"),
            Choice(verb=Verb.PRESS, target_label="Effects", why="open the effects panel"),
            Choice(verb=Verb.PRESS, target_label="Gaussian Blur", why="pick the effect"),
            Choice(verb=Verb.PRESS, target_label="Enabled", why="switch it on"),
            Choice(
                finish=Status.DONE,
                says=("Gaussian Blur is enabled on the clip",),
                why="checkbox reads on",
            ),
        ]
    )


def verifier() -> PredicateVerifier:
    def enabled(task: Task, view: View) -> bool:
        del task
        return any(t.label == "Enabled" and t.value == "on" for t in view.targets)

    return PredicateVerifier({"Gaussian Blur is enabled on the clip": enabled})
