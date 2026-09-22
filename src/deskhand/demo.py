"""A tiny scripted desktop, so the whole loop runs with no permissions at all.

``deskhand demo`` uses this. It is also what several tests drive, including the
failure paths: the first scripted choice aims at a button that does not exist,
and the run is expected to survive it.
"""

from __future__ import annotations

from dataclasses import replace

from .deciders.scripted import ScriptedDecider
from .sensors.fake import Edge, FakeSensor
from .types import Box, Choice, Limits, Status, Target, Task, Verb, View
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


# --------------------------------------------------------------------------- #
# the M2 scenario, on the same fake desktop
# ---------------------------------------------------------------------------
#
# Milestone M2 is "open a media app, search for a track, play it" on a real
# Chromium window, and it needs Screen Recording, a running application and a
# real model. This is the same *shape* of task on a scripted desktop, so the
# decision seam can be exercised -- and its acceptance criteria asserted -- on a
# machine with none of those. It says nothing about whether a real Chromium app
# behaves this way; that is what M2 is still for.
#
# Two targets are called "Play" on purpose. Pressing whatever is called "Play"
# is the single most likely thing a model gets wrong here, and the cost of getting
# it wrong is a click somewhere else. The refusal names both ids, which is what
# lets the next decision be a good one.

PLAYBACK_CHECK = "Midnight City is playing"

PLAY_ALL = Target(
    id="play_all",
    kind="button",
    label="Play",
    actions=frozenset({Verb.PRESS}),
    box=Box(40, 40, 64, 28),
    source="demo",
)
PLAY_ROW = Target(
    id="row_play_button",
    kind="button",
    label="Play",
    actions=frozenset({Verb.PRESS}),
    box=Box(600, 118, 28, 28),
    source="demo",
)
SEARCH_BOX = Target(
    id="search_box",
    kind="text_field",
    label="Search",
    value="",
    actions=frozenset({Verb.TYPE, Verb.PRESS, Verb.SET}),
    box=Box(360, 60, 320, 28),
    source="demo",
)
TRACK = Target(
    id="row_midnight_city",
    kind="row",
    label="Midnight City",
    value="",
    actions=frozenset({Verb.PRESS}),
    box=Box(40, 110, 640, 40),
    source="demo",
)
NOW_PLAYING = Target(
    id="now_playing",
    kind="static_text",
    label="Now playing: Midnight City",
    box=Box(40, 700, 300, 24),
    source="demo",
)

_LIBRARY = (PLAY_ALL, SEARCH_BOX, TRACK, PLAY_ROW)
_SEARCHED = (PLAY_ALL, replace(SEARCH_BOX, value="Midnight City"), TRACK, PLAY_ROW)
_PLAYING = (
    PLAY_ALL,
    replace(SEARCH_BOX, value="Midnight City"),
    replace(TRACK, value="playing", selected=True),
    PLAY_ROW,
    NOW_PLAYING,
)

_PLAYBACK_EDGES: dict[Edge, str] = {
    ("library", SEARCH_BOX.id, Verb.TYPE): "results",
    ("results", TRACK.id, Verb.PRESS): "playing",
}


def playback_sensor() -> FakeSensor:
    return FakeSensor(
        {"library": _LIBRARY, "results": _SEARCHED, "playing": _PLAYING},
        start="library",
        edges=_PLAYBACK_EDGES,
        app="Demo Player",
        window="Demo Player",
    )


def playback_task() -> Task:
    return Task(
        goal="Play Midnight City",
        checks=(PLAYBACK_CHECK,),
        inputs={"query": "Midnight City"},
        notes=("Do not touch any other track.",),
        limits=Limits(max_steps=8, settle_ms=200),
    )
