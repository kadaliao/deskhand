"""Regression tests for the two bugs that only a real machine revealed.

Both were invisible in a fake-desktop test and invisible in review, and both
silently deleted or corrupted most of the accessibility tree:

1. ``AXValueGetType`` answers ``0`` for anything that is not an ``AXValue`` --
   including element references and child arrays -- instead of raising. Reading
   that as "an AXValue of kind 0" mapped every element and every child list to
   ``None``, and perception returned an empty desktop.
2. An unsupported attribute comes back as an ``AXValue`` wrapping an error code,
   not as null. Unwrapped, it became the string
   ``<AXValue ... {value = error:-25212 ...}>``, which then appeared as an
   element's subrole, as its label, and inside its identity hash.

``AXPosition`` is an ``AXValueRef`` with no ``.x`` attribute, so the obvious
``value.x`` raises and quietly loses every rectangle. These tests pin all three.
"""

from __future__ import annotations

from typing import Any

import pytest

from deskhand.sensors.macos import ax as axmod
from deskhand.types import Box


class FakeValue:
    """Stands in for an ``AXValueRef``."""

    def __init__(self, kind: int, payload: Any = None) -> None:
        self.kind = kind
        self.payload = payload


class FakePoint:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class FakeSize:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height


AX_VALUE_TYPE_ID = 121
OTHER_TYPE_ID = 7


class FakeAX:
    kAXValueTypeIllegal = 0
    kAXValueCGPointType = 1
    kAXValueCGSizeType = 2
    kAXValueAXErrorType = 5

    def AXValueGetTypeID(self) -> int:
        return AX_VALUE_TYPE_ID

    def CFGetTypeID(self, value: Any) -> int:
        return AX_VALUE_TYPE_ID if isinstance(value, FakeValue) else OTHER_TYPE_ID

    def AXValueGetType(self, value: Any) -> int:
        # Exactly what the real API does: 0 for anything that is not an AXValue.
        return value.kind if isinstance(value, FakeValue) else self.kAXValueTypeIllegal

    def AXValueGetValue(self, value: Any, kind: int, _out: Any) -> tuple[bool, Any]:
        del kind
        return (True, value.payload)


@pytest.fixture(autouse=True)
def fake_ax(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAX()
    monkeypatch.setattr(axmod, "ax", lambda: fake)


class TestUnwrap:
    def test_a_plain_string_is_left_alone(self) -> None:
        assert axmod.unwrap("AXWindow") == "AXWindow"

    def test_numbers_and_booleans_are_left_alone(self) -> None:
        assert axmod.unwrap(3) == 3
        assert axmod.unwrap(True) is True

    def test_an_element_reference_is_not_mistaken_for_an_axvalue(self) -> None:
        """The regression: a real element reference survived as itself."""
        ref = object()
        assert axmod.unwrap(ref) is ref

    def test_a_child_array_is_not_mistaken_for_an_axvalue(self) -> None:
        children = ["a", "b"]
        assert axmod.unwrap(children) == ["a", "b"]

    def test_an_error_value_becomes_none_rather_than_a_string(self) -> None:
        assert axmod.unwrap(FakeValue(FakeAX.kAXValueAXErrorType, "error:-25212")) is None

    def test_a_point_becomes_a_pair(self) -> None:
        point = FakeValue(FakeAX.kAXValueCGPointType, FakePoint(-0.0, 30.0))
        assert axmod.unwrap(point) == (0.0, 30.0)

    def test_a_size_becomes_a_pair(self) -> None:
        size = FakeValue(FakeAX.kAXValueCGSizeType, FakeSize(1920.0, 956.0))
        assert axmod.unwrap(size) == (1920.0, 956.0)


class TestGeometry:
    def test_a_point_is_read_from_an_axvalue(self) -> None:
        assert axmod.careful_point(FakeValue(FakeAX.kAXValueCGPointType, FakePoint(5.0, 6.0))) == (
            5.0,
            6.0,
        )

    def test_a_point_that_is_already_a_pair_is_accepted(self) -> None:
        assert axmod.careful_point((5.0, 6.0)) == (5.0, 6.0)

    def test_the_wrong_kind_of_axvalue_is_not_read_as_a_point(self) -> None:
        assert axmod.careful_point(FakeValue(FakeAX.kAXValueCGSizeType, FakeSize(1.0, 1.0))) is None

    def test_a_size_is_read_from_an_axvalue(self) -> None:
        assert axmod.careful_size(FakeValue(FakeAX.kAXValueCGSizeType, FakeSize(10.0, 20.0))) == (
            10.0,
            20.0,
        )

    def test_an_unreadable_value_does_not_raise(self) -> None:
        assert axmod.careful_point(object()) is None
        assert axmod.careful_size(None) is None

    def test_a_box_needs_both_halves(self) -> None:
        point = (10.0, 20.0)
        size = (100.0, 40.0)
        assert axmod._box(point, size) == Box(10.0, 20.0, 100.0, 40.0)
        assert axmod._box(point, None) is None
        assert axmod._box(None, size) is None

    def test_a_zero_sized_box_is_not_a_box(self) -> None:
        assert axmod._box((0.0, 0.0), (0.0, 40.0)) is None
        assert axmod._box((0.0, 0.0), (100.0, 0.0)) is None


class TestIdentify:
    def test_elements_without_geometry_are_still_told_apart(self) -> None:
        first = axmod._identify("AXGroup", "", "", None, "0.1.4")
        second = axmod._identify("AXGroup", "", "", None, "0.1.5")
        assert first != second

    def test_geometry_wins_over_the_walk_path(self) -> None:
        boxed = axmod._identify("AXButton", "", "Save", Box(10, 10, 20, 20), "0.1")
        moved = axmod._identify("AXButton", "", "Save", Box(10, 10, 20, 20), "9.9")
        assert boxed == moved
