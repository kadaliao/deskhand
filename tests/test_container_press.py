"""A container chosen by id must not degrade into a blind click at its centre.

Resolving by label already prefers a control over a window of the same name
(``validate.resolve_target``). An id bypasses that: it names exactly one thing, and
validation cannot know whether the application will honour ``AXPress`` on it. A window
advertises ``AXPress`` -- it means "raise me" -- and when that failed, ``press`` used to
fall back to a click at the window's centre, which lands on whatever happens to be drawn
there and is still reported as an executed step with a route.
"""

from __future__ import annotations

from typing import Any

import pytest

from deskhand.errors import CannotDo
from deskhand.sensors.macos import ax as axmod
from deskhand.sensors.macos.screen import MacSensor
from deskhand.types import Action, Box, Target, Verb, View


class RecordingAX:
    """Only what ``MacSensor.act`` touches on the accessibility side."""

    def __init__(self) -> None:
        self.pressed: list[dict[str, Any]] = []

    def ref(self, target_id: str) -> object:
        return object()

    def press(self, ref: object, **kwargs: Any) -> str:
        self.pressed.append(kwargs)
        return "ax-press"


def _view(*targets: Target) -> View:
    return View(app="App", window="w", revision="r", targets=targets)


def _target(target_id: str, kind: str) -> Target:
    return Target(
        id=target_id,
        kind=kind,
        label="外观",
        actions=frozenset({Verb.PRESS}),
        box=Box(0, 0, 100, 100),
    )


def _press(kind: str) -> dict[str, Any]:
    recorder = RecordingAX()
    sensor = MacSensor(ax=recorder, ocr=object(), pixels=False)  # type: ignore[arg-type]
    target = _target("t1", kind)
    sensor.act(_view(target), Action(verb=Verb.PRESS, target="t1"))
    return recorder.pressed[0]


class TestTheSensorSaysWhichPressesMayFallBack:
    def test_a_window_may_not_fall_back_to_a_click(self) -> None:
        assert _press("window:standard") == {"click_fallback": False}

    def test_a_group_may_not_fall_back_to_a_click(self) -> None:
        assert _press("group") == {"click_fallback": False}

    def test_a_control_still_may(self) -> None:
        """Real controls often answer a click without implementing AXPress."""
        assert _press("row:outlinerow") == {"click_fallback": True}


class FailingPress:
    def AXUIElementPerformAction(self, ref: object, action: str) -> int:
        return -25200


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> tuple[axmod.AXSource, list[Box]]:
    clicks: list[Box] = []
    monkeypatch.setattr(axmod, "ax", FailingPress)
    monkeypatch.setattr(axmod, "click", lambda box, **_: clicks.append(box))
    monkeypatch.setattr(axmod, "_box", lambda position, size: Box(10, 10, 80, 40))
    found = axmod.AXSource.__new__(axmod.AXSource)
    monkeypatch.setattr(found, "_actions", lambda ref: ["AXPress"], raising=False)
    monkeypatch.setattr(found, "_one", lambda ref, name: None, raising=False)
    return found, clicks


class TestAFailedPress:
    def test_on_a_container_is_refused_without_moving_the_mouse(
        self, source: tuple[axmod.AXSource, list[Box]]
    ) -> None:
        found, clicks = source
        with pytest.raises(CannotDo, match="container"):
            found.press(object(), click_fallback=False)
        assert clicks == []

    def test_on_a_control_still_clicks_its_centre(
        self, source: tuple[axmod.AXSource, list[Box]]
    ) -> None:
        found, clicks = source
        assert found.press(object()) == "click"
        assert clicks == [Box(10, 10, 80, 40)]
