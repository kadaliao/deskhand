"""Choosing the application, tested against the shape of a real machine.

A working machine reports 117 running applications, most of them Chrome helpers,
Dock extras and settings extensions. Activating "the first partial match" picked
one of those, so the filter and the ordering are worth pinning.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from deskhand.errors import CannotDo
from deskhand.sensors.macos import apps as appsmod

REGULAR, ACCESSORY, PROHIBITED = 0, 1, 2


class FakeApp:
    def __init__(
        self, name: str, policy: int = REGULAR, *, pid: int = 1, refuses: bool = False
    ) -> None:
        self._name = name
        self._policy = policy
        self._pid = pid
        self._refuses = refuses
        self.activated = False

    def localizedName(self) -> str:
        return self._name

    def bundleIdentifier(self) -> str:
        return f"com.example.{self._name}"

    def processIdentifier(self) -> int:
        return self._pid

    def activationPolicy(self) -> int:
        return self._policy

    def activateWithOptions_(self, options: int) -> bool:
        del options
        self.activated = not self._refuses
        return self.activated


class FakeWorkspace:
    def __init__(self, running: list[FakeApp], front: FakeApp | None) -> None:
        self._running = running
        self._front = front

    def runningApplications(self) -> list[FakeApp]:
        return self._running

    def frontmostApplication(self) -> FakeApp | None:
        return self._front


class NSWorkspaceProxy:
    """Mirrors ``AppKit.NSWorkspace.sharedWorkspace()``: an attribute you call."""

    def __init__(self, workspace: FakeWorkspace) -> None:
        self._workspace = workspace

    def sharedWorkspace(self) -> FakeWorkspace:
        return self._workspace


class FakeAppKit:
    """Stands in for the AppKit module object."""

    NSApplicationActivateIgnoringOtherApps = 1 << 1

    def __init__(self, running: list[FakeApp], front: FakeApp | None = None) -> None:
        self.NSWorkspace = NSWorkspaceProxy(FakeWorkspace(running, front))


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> list[FakeApp]:
    apps = [
        FakeApp("System Settings"),
        FakeApp("Finder", pid=2),
        FakeApp("HeadphoneSettingsExtension (System Settings)", policy=ACCESSORY, pid=3),
        FakeApp("Google Chrome Helper", policy=PROHIBITED, pid=4),
        FakeApp("Dock Extra", policy=ACCESSORY, pid=5),
    ]
    monkeypatch.setattr(appsmod, "appkit", lambda: FakeAppKit(apps, apps[0]))
    return apps


class TestRunning:
    def test_helpers_and_extensions_are_not_offered(self, machine: list[FakeApp]) -> None:
        del machine
        assert appsmod.running() == ["Finder", "System Settings"]

    def test_the_frontmost_app_is_reported_with_its_pid(self, machine: list[FakeApp]) -> None:
        del machine
        assert appsmod.frontmost() == ("System Settings", 1)


class TestActivate:
    def test_an_exact_name_wins_over_a_partial_one(self, machine: list[FakeApp]) -> None:
        del machine
        assert appsmod.activate("System Settings") == "System Settings"

    def test_matching_is_case_insensitive(self, machine: list[FakeApp]) -> None:
        del machine
        assert appsmod.activate("system settings") == "System Settings"

    def test_a_substring_is_enough(self, machine: list[FakeApp]) -> None:
        del machine
        assert appsmod.activate("finder") == "Finder"

    def test_an_extension_is_never_chosen_for_a_partial_match(self, machine: list[FakeApp]) -> None:
        del machine
        # "Settings" appears in the extension's name too; the regular app must win.
        assert appsmod.activate("Settings") == "System Settings"

    def test_an_unknown_name_lists_what_is_available(self, machine: list[FakeApp]) -> None:
        del machine
        with pytest.raises(CannotDo) as caught:
            appsmod.activate("Spotify")
        assert "Finder" in str(caught.value)
        assert "no running application matches" in str(caught.value)

    def test_an_empty_name_is_rejected(self, machine: list[FakeApp]) -> None:
        del machine
        with pytest.raises(CannotDo, match="empty"):
            appsmod.activate("  ")

    def test_a_refusal_is_reported_rather_than_assumed_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stubborn = FakeApp("Stubborn", refuses=True)
        monkeypatch.setattr(appsmod, "appkit", lambda: FakeAppKit([stubborn], stubborn))
        monkeypatch.delattr(stubborn, "activate", raising=False)
        with pytest.raises(CannotDo, match="refused"):
            appsmod.activate("Stubborn")

    def test_a_successful_activation_is_recorded(self, machine: list[FakeApp]) -> None:
        assert appsmod.activate("Finder") == "Finder"
        assert any(app.activated for app in machine)


class TestFocus:
    """``activate`` reporting success is not the same as the application being in front.

    Measured, not hypothetical: a benchmark run asked for a browser, was told the
    activation succeeded, and then timed the terminal that was still in front. The
    number was plausible and about the wrong application.
    """

    def _patch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        front: list[str],
        resolves: str | None = None,
        activate_side_effect: Callable[[str], None] | None = None,
    ) -> list[str]:
        calls: list[str] = []

        def fake_frontmost() -> tuple[str, int]:
            return (front[-1], 1)

        def fake_activate(name: str) -> str:
            calls.append(name)
            if activate_side_effect is not None:
                activate_side_effect(name)
            return resolves or name

        monkeypatch.setattr(appsmod, "frontmost", fake_frontmost)
        monkeypatch.setattr(appsmod, "activate", fake_activate)
        return calls

    def test_already_frontmost_does_not_activate_anything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._patch(monkeypatch, front=["Ghostty"])
        assert appsmod.focus("Ghostty") == "Ghostty"
        assert calls == []

    def test_it_returns_once_the_application_is_in_front(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        front = ["Ghostty"]

        def move(name: str) -> None:
            del name
            front[-1] = "Google Chrome"

        calls = self._patch(monkeypatch, front=front, activate_side_effect=move)
        assert appsmod.focus("Google Chrome") == "Google Chrome"
        assert calls == ["Google Chrome"]

    def test_a_silent_failure_raises_instead_of_measuring_the_wrong_application(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._patch(monkeypatch, front=["Ghostty"])
        with pytest.raises(CannotDo) as caught:
            appsmod.focus("Google Chrome", attempts=2, wake=lambda _: None)
        assert "did not become frontmost" in str(caught.value)
        assert "Ghostty" in str(caught.value)
        assert "wrong application" in str(caught.value)
        assert calls == ["Google Chrome", "Google Chrome"]  # it did retry

    def test_the_resolved_name_is_what_is_compared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # "Settings" resolves to "System Settings"; the comparison must use the
        # resolved name, because that is what ends up in front.
        front = ["Finder"]

        def move(name: str) -> None:
            del name
            front[-1] = "System Settings"

        self._patch(monkeypatch, front=front, resolves="System Settings", activate_side_effect=move)
        assert appsmod.focus("Settings", wake=lambda _: None) == "System Settings"

    def test_an_empty_name_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, front=["Ghostty"])
        with pytest.raises(CannotDo, match="empty"):
            appsmod.focus("   ")

    def test_no_frontmost_application_at_all_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(appsmod, "frontmost", lambda: None)
        monkeypatch.setattr(appsmod, "activate", lambda name: name)
        with pytest.raises(CannotDo) as caught:
            appsmod.focus("Ghostty", attempts=1, wake=lambda _: None)
        assert "unknown" in str(caught.value)


def test_no_frontmost_application_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appsmod, "appkit", lambda: FakeAppKit([], None))
    assert appsmod.frontmost() is None
    assert appsmod.running() == []


def test_an_app_without_a_name_falls_back_to_its_bundle_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class Nameless(FakeApp):
        def localizedName(self) -> Any:
            return None

    app = Nameless("ignored")
    monkeypatch.setattr(appsmod, "appkit", lambda: FakeAppKit([app], app))
    assert appsmod.running() == ["com.example.ignored"]
