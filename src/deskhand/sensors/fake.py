"""A scripted desktop for tests and the ``demo`` command.

It is a real implementation of ``Sensor``: freshness works, progress detection
works, and an action with no transition raises ``CannotDo`` exactly like a real
backend that cannot do something. That is what makes the failure paths testable.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from ..errors import CannotDo
from ..fusion import box_of
from ..settle import converge
from ..types import (
    Action,
    Box,
    Target,
    Verb,
    View,
    content_digest,
    shape_digest,
    structure_digest,
)

Edge = tuple[str, str, Verb]
"""``(scene, target id, verb) -> next scene``."""


class FakeSensor:
    """A tiny state machine wearing a ``Sensor``."""

    def __init__(
        self,
        scenes: Mapping[str, Sequence[Target]],
        *,
        start: str,
        edges: Mapping[Edge, str] | None = None,
        app: str = "Fake App",
        window: str = "Fake Window",
        stuck_scenes: frozenset[str] = frozenset(),
        frame: Box | None = None,
    ) -> None:
        if start not in scenes:
            raise ValueError(f"start scene {start!r} is not defined")
        self._scenes = {name: tuple(targets) for name, targets in scenes.items()}
        self._edges = dict(edges or {})
        self._stuck = stuck_scenes
        self._app = app
        self._window = window
        self._scene = start
        self._frame = frame
        self.done: list[Action] = []

    @property
    def scene(self) -> str:
        return self._scene

    def view(self, scene: str | None = None) -> View:
        name = scene or self._scene
        targets = self._scenes[name]
        return View(
            app=self._app,
            window=self._window,
            revision=structure_digest(targets),
            targets=targets,
            content=content_digest(targets),
            frame=self._frame or box_of(targets),
            notes={"scene": name},
            at_ms=round(time.time() * 1000),
        )

    def observe(self) -> View:
        return self.view()

    def is_stale(self, view: View, action: Action) -> bool:
        if view.notes.get("scene") != self._scene:
            return True
        if action.target is None or action.guard is None:
            return False
        try:
            target = view.target(action.target)
        except KeyError:
            return True
        return not action.guard.matches(target.fingerprint())

    def act(self, view: View, action: Action) -> str:
        """Perform the action.

        Deliberately does *not* re-run the freshness gate: the runtime already did
        that, and doing it twice doubles the cost of every action (for the pixel
        source, a second window enumeration). What is checked here is only the race
        between the gate and the action, which for a fake desktop means "is it still
        the scene the view was taken from".
        """
        if view.notes.get("scene") != self._scene:
            raise CannotDo(f"desktop moved to {self._scene!r} before the action ran")

        if action.verb is Verb.WAIT:
            self.done.append(action)
            return "wait"
        if action.target is None:
            self.done.append(action)
            return f"global:{action.verb}"
        if self._scene in self._stuck:
            self.done.append(action)
            return "no-op"

        nxt = self._edges.get((self._scene, action.target, action.verb))
        if nxt is None:
            raise CannotDo(
                f"scene {self._scene!r} has no transition for {action.verb} on {action.target!r}"
            )
        self._scene = nxt
        self.done.append(action)
        return "fake"

    def settle(self, before: View, *, budget_ms: int) -> View:
        """Use the same waiting loop as the real sensor.

        Not a no-op on purpose: the fake desktop is what the runner's failure paths
        are tested against, and a settle that returned immediately would be testing
        a different machine from the one that ships.
        """
        result = converge(
            self.view,
            lambda view: shape_digest(view.targets),
            budget_s=budget_ms / 1000.0,
            start_from=shape_digest(before.targets),
        )
        return result.last
