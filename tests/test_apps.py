"""Choosing the application, tested against the shape of a real machine.

A working machine reports 117 running applications, most of them Chrome helpers,
Dock extras and settings extensions. Activating "the first partial match" picked
one of those, so the filter and the ordering are worth pinning.
"""

from __future__ import annotations

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
