"""Quartz input primitives: the "hands" part of the hand.

Everything here moves the real cursor or the real keyboard, so it is used only
when an accessibility action is not available. Anything that *can* be done with
an accessibility action should be done with one, because those do not need the
window to be frontmost and cannot land on the wrong window.
"""

from __future__ import annotations

import time
from typing import Any

from ...errors import CannotDo
from ...types import MODIFIERS, Box

HID = "kCGHIDEventTap"

KEYS: dict[str, int] = {
    "RETURN": 36,
    "ENTER": 36,
    "TAB": 48,
    "SPACE": 49,
    "ESCAPE": 53,
    "ESC": 53,
    "DELETE": 51,
    "BACKSPACE": 51,
    "FORWARD_DELETE": 117,
    "UP": 126,
    "DOWN": 125,
    "LEFT": 123,
    "RIGHT": 124,
    "HOME": 115,
    "END": 119,
    "PAGE_UP": 116,
    "PAGE_DOWN": 121,
    "A": 0,
    "C": 8,
    "F": 3,
    "V": 9,
    "Z": 6,
    "S": 1,
    "W": 13,
    "Q": 12,
}

_FLAGS = {
    "CMD": "kCGEventFlagMaskCommand",
    "SHIFT": "kCGEventFlagMaskShift",
    "ALT": "kCGEventFlagMaskAlternate",
    "CTRL": "kCGEventFlagMaskControl",
    "FN": "kCGEventFlagMaskSecondaryFn",
}


def quartz() -> Any:
    try:
        import Quartz
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo("pyobjc Quartz is unavailable; install the macos extra") from exc
    return Quartz


def _post(event: Any) -> None:
    quartz().CGEventPost(getattr(quartz(), HID), event)


def _flags(chord: str) -> int:
    flags = 0
    for part in chord.split("+")[:-1]:
        if part not in MODIFIERS:
            raise CannotDo(f"unknown modifier {part!r}")
        flags |= getattr(quartz(), _FLAGS[part])
    return flags


def key_code(key: str) -> int:
    code = KEYS.get(key.upper())
    if code is None:
        raise CannotDo(f"no key code for {key!r}; known: {sorted(KEYS)}")
    return code


def press(key: str, flags: int = 0) -> None:
    code = key_code(key)
    for down in (True, False):
        event = quartz().CGEventCreateKeyboardEvent(None, code, down)
        if flags:
            quartz().CGEventSetFlags(event, flags)
        _post(event)


def chord(spec: str) -> None:
    """``CMD+SHIFT+Z`` -> modifier flags plus the final key."""
    modifiers, _, key = spec.rpartition("+")
    if not modifiers or not key:
        raise CannotDo(f"chord {spec!r} needs a modifier and a key, like CMD+A")
    press(key, flags=_flags(spec))


def select_all() -> None:
    press("A", flags=getattr(quartz(), _FLAGS["CMD"]))


def type_text(text: str, *, pause_every: int = 24, pause_s: float = 0.001) -> None:
    """Type literal text one character at a time.

    Modifier flags are cleared on every event so a preceding Cmd+A cannot leak
    into the text, and the keyboard layout is bypassed entirely by setting the
    unicode payload of each event.
    """
    if not text:
        return
    q = quartz()
    for index, character in enumerate(text):
        down = q.CGEventCreateKeyboardEvent(None, 0, True)
        up = q.CGEventCreateKeyboardEvent(None, 0, False)
        if down is None or up is None:  # pragma: no cover - defensive
            raise CannotDo("could not create unicode keyboard events")
        units = len(character.encode("utf-16-le")) // 2
        for event in (down, up):
            q.CGEventSetFlags(event, 0)
            q.CGEventKeyboardSetUnicodeString(event, units, character)
        _post(down)
        _post(up)
        if pause_every and index % pause_every == pause_every - 1:
            time.sleep(pause_s)


def click(box: Box, *, count: int = 1, button: str = "left") -> None:
    q = quartz()
    x, y = box.center
    if button == "right":
        mouse_button, down_kind, up_kind = (
            q.kCGMouseButtonRight,
            q.kCGEventRightMouseDown,
            q.kCGEventRightMouseUp,
        )
    else:
        mouse_button, down_kind, up_kind = (
            q.kCGMouseButtonLeft,
            q.kCGEventLeftMouseDown,
            q.kCGEventLeftMouseUp,
        )

    _post(q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, (x, y), mouse_button))
    for index in range(count):
        state = index + 1 if count > 1 else 1
        down = q.CGEventCreateMouseEvent(None, down_kind, (x, y), mouse_button)
        up = q.CGEventCreateMouseEvent(None, up_kind, (x, y), mouse_button)
        q.CGEventSetIntegerValueField(down, q.kCGMouseEventClickState, state)
        q.CGEventSetIntegerValueField(up, q.kCGMouseEventClickState, state)
        _post(down)
        _post(up)
        if index + 1 < count:
            time.sleep(0.05)


def drag(source: Box, destination: Box, *, steps: int = 12) -> None:
    q = quartz()
    start_x, start_y = source.center
    end_x, end_y = destination.center
    _post(
        q.CGEventCreateMouseEvent(
            None, q.kCGEventMouseMoved, (start_x, start_y), q.kCGMouseButtonLeft
        )
    )
    _post(
        q.CGEventCreateMouseEvent(
            None, q.kCGEventLeftMouseDown, (start_x, start_y), q.kCGMouseButtonLeft
        )
    )
    for step in range(1, steps + 1):
        x = start_x + (end_x - start_x) * step / steps
        y = start_y + (end_y - start_y) * step / steps
        _post(
            q.CGEventCreateMouseEvent(
                None, q.kCGEventLeftMouseDragged, (x, y), q.kCGMouseButtonLeft
            )
        )
        time.sleep(0.012)
    _post(
        q.CGEventCreateMouseEvent(None, q.kCGEventLeftMouseUp, (end_x, end_y), q.kCGMouseButtonLeft)
    )


def scroll(direction: str) -> None:
    q = quartz()
    vertical = {"UP": 600, "DOWN": -600}.get(direction, 0)
    horizontal = {"LEFT": 600, "RIGHT": -600}.get(direction, 0)
    if not vertical and not horizontal:
        raise CannotDo(f"unknown scroll direction {direction!r}")
    event = q.CGEventCreateScrollWheelEvent(
        None, q.kCGScrollEventUnitPixel, 2, vertical, horizontal
    )
    _post(event)
