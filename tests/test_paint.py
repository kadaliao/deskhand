"""Colour only for a person reading a terminal; the words carry the meaning either way."""

from __future__ import annotations

import io

import pytest

from deskhand import paint
from deskhand.cli import main


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class TestWhenColourIsOn:
    def test_a_terminal_gets_colour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        assert paint.enabled(Tty()) is True

    def test_a_pipe_does_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        assert paint.enabled(io.StringIO()) is False

    def test_no_color_wins_over_a_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert paint.enabled(Tty()) is False

    def test_force_color_turns_it_on_for_a_pipe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert paint.enabled(io.StringIO()) is True


class TestRoutes:
    def test_accessibility_routes_are_semantic(self) -> None:
        assert paint.route_role("ax-press") == "ok"
        assert paint.route_role("ax-focus+keys") == "ok"

    def test_a_coordinate_click_stands_out(self) -> None:
        assert paint.route_role("click") == "warn"
        assert paint.route_role("double-click") == "warn"

    def test_keys_and_fakes_are_neither(self) -> None:
        assert paint.route_role("key") == "dim"
        assert paint.route_role("fake") == "dim"
        assert paint.route_role(None) == "dim"


def test_captured_output_has_no_escape_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    main(["demo"])
    out = capsys.readouterr().out
    assert "\033[" not in out
    assert "acted 3x: 0 through accessibility, 0 coordinate clicks" in out
