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

    def test_the_frontmost_app_is_reported_with_its_pid(
        self, machine: list[FakeApp], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        del machine
        # The window server is asked first, so stand it down to exercise the fallback.
        monkeypatch.setattr(appsmod, "_window_server_front", lambda: None)
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
        running: bool = True,
        starts: list[str] | None = None,
        start_side_effect: Callable[[str], None] | None = None,
        raises: list[str] | None = None,
    ) -> list[str]:
        calls: list[str] = []

        def fake_frontmost() -> tuple[str, int]:
            return (front[-1], 1)

        def fake_activate(name: str) -> str:
            calls.append(name)
            if activate_side_effect is not None:
                activate_side_effect(name)
            return resolves or name

        def fake_matching(name: str) -> list[str]:
            del name
            return ["a running application"] if running else []

        def fake_start(name: str) -> str:
            if starts is not None:
                starts.append(name)
            if start_side_effect is not None:
                start_side_effect(name)
            return resolves or name

        def fake_raise(name: str) -> bool:
            if raises is not None:
                raises.append(name)
            return True

        monkeypatch.setattr(appsmod, "frontmost", fake_frontmost)
        monkeypatch.setattr(appsmod, "activate", fake_activate)
        monkeypatch.setattr(appsmod, "_matching", fake_matching)
        monkeypatch.setattr(appsmod, "start", fake_start)
        monkeypatch.setattr(appsmod, "raise_window", fake_raise)
        return calls

    def test_a_closed_application_is_started_instead_of_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A closed application is not a reason to fail a run.

        Measured: System Settings had been closed, so ``--focus 系统设置`` could not run
        the appearance task at all -- twice -- while one ``open`` would have started it.
        """
        front = ["Ghostty"]
        starts: list[str] = []

        def arrive(name: str) -> None:
            del name
            front[-1] = "System Settings"

        calls = self._patch(
            monkeypatch,
            front=front,
            resolves="System Settings",
            running=False,
            starts=starts,
            start_side_effect=arrive,
        )
        assert appsmod.focus("System Settings", wake=lambda _: None) == "System Settings"
        assert starts == ["System Settings"]
        assert calls == []  # activating a process that does not exist is never tried

    def test_a_running_application_is_never_started(self, monkeypatch: pytest.MonkeyPatch) -> None:
        front = ["Ghostty"]
        starts: list[str] = []

        def move(name: str) -> None:
            del name
            front[-1] = "Google Chrome"

        self._patch(
            monkeypatch, front=front, starts=starts, activate_side_effect=move, running=True
        )
        assert appsmod.focus("Google Chrome", wake=lambda _: None) == "Google Chrome"
        assert starts == []

    def test_a_start_that_fails_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def explode(name: str) -> None:
            del name
            raise CannotDo("could not start 'System Settings': open refused")

        self._patch(monkeypatch, front=["Ghostty"], running=False, start_side_effect=explode)
        with pytest.raises(CannotDo, match="could not start"):
            appsmod.focus("System Settings", attempts=1, wake=lambda _: None)

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

    def test_an_application_that_does_not_come_forward_is_raised_through_launch_services(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``activate`` returning success is not evidence, and it does not work here.

        Measured with an active caller: from a frontmost Ghostty, ``--focus 系统设置``
        still failed. So this module never asked LaunchServices, the one mechanism that
        raises a window, and then blamed macOS for ignoring a request.
        """
        raised: list[str] = []
        self._patch(monkeypatch, front=["Ghostty"], raises=raised)
        with pytest.raises(CannotDo):
            appsmod.focus("Google Chrome", attempts=2, wake=lambda _: None)
        assert raised == ["Google Chrome", "Google Chrome"]

    def test_an_application_that_comes_forward_is_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        front = ["Ghostty"]
        raised: list[str] = []

        def move(name: str) -> None:
            del name
            front[-1] = "Google Chrome"

        self._patch(monkeypatch, front=front, activate_side_effect=move, raises=raised)
        assert appsmod.focus("Google Chrome", wake=lambda _: None) == "Google Chrome"
        assert raised == []

    def test_a_refusal_with_an_active_caller_does_not_blame_the_caller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The diagnosis has to depend on who was asking, or it is just a story."""
        self._patch(monkeypatch, front=["Ghostty"])
        monkeypatch.setattr(appsmod, "responsible_app", lambda: ("Ghostty", 1))
        with pytest.raises(CannotDo) as caught:
            appsmod.focus("Google Chrome", attempts=1, wake=lambda _: None)
        message = str(caught.value)
        assert "is itself in front" in message
        assert "not itself active" not in message

    def test_the_refusal_says_the_caller_is_the_likely_problem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The target is rarely at fault: an inactive caller cannot take focus.

        Measured on macOS 26: every activation mechanism reported success while the
        frontmost application did not change, because this process belonged to an
        application that was not itself active. The message has to say that, or the
        caller goes looking for a bug in the target.
        """
        self._patch(monkeypatch, front=["Ghostty"])
        monkeypatch.setattr(appsmod, "responsible_app", lambda: ("pi", 65025))
        with pytest.raises(CannotDo) as caught:
            appsmod.focus("Google Chrome", attempts=1, wake=lambda _: None)
        message = str(caught.value)
        assert "'pi'" in message
        assert "not itself active" in message
        assert "frontmost terminal" in message

    def test_the_refusal_still_explains_itself_without_an_owner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch(monkeypatch, front=["Ghostty"])
        monkeypatch.setattr(appsmod, "responsible_app", lambda: None)
        with pytest.raises(CannotDo, match="no application at all"):
            appsmod.focus("Google Chrome", attempts=1, wake=lambda _: None)

    def test_no_frontmost_application_at_all_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(appsmod, "frontmost", lambda: None)
        monkeypatch.setattr(appsmod, "activate", lambda name: name)
        with pytest.raises(CannotDo) as caught:
            appsmod.focus("Ghostty", attempts=1, wake=lambda _: None)
        assert "unknown" in str(caught.value)

    def test_the_refusal_survives_a_machine_without_appkit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CI runs this suite where pyobjc does not exist, and the message must not raise.

        Found by CI, not locally. `responsible_app()` reaches for AppKit, so on a machine
        without pyobjc the refusal raised `pyobjc AppKit is unavailable` and *replaced* the
        explanation it was there to add -- every focus failure said the same useless thing.
        """
        self._patch(monkeypatch, front=["Ghostty"])

        def no_appkit() -> object:
            raise CannotDo("pyobjc AppKit is unavailable; install the macos extra")

        monkeypatch.setattr(appsmod, "appkit", no_appkit)
        with pytest.raises(CannotDo) as caught:
            appsmod.focus("Google Chrome", attempts=1, wake=lambda _: None)
        message = str(caught.value)
        assert "did not become frontmost" in message
        assert "no application at all" in message
        assert "install the macos extra" not in message


def test_no_frontmost_application_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appsmod, "appkit", lambda: FakeAppKit([], None))
    monkeypatch.setattr(appsmod, "_window_server_front", lambda: None)
    assert appsmod.frontmost() is None
    assert appsmod.running() == []


class FakeQuartz:
    """Just enough of Quartz to exercise the live frontmost query."""

    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListExcludeDesktopElements = 2
    kCGNullWindowID = 0
    kCGWindowLayer = "layer"
    kCGWindowOwnerPID = "pid"
    kCGWindowOwnerName = "owner"

    def __init__(self, windows: list[dict[str, object]]) -> None:
        self.windows = windows

    def CGWindowListCopyWindowInfo(self, options: int, relative: int) -> list[dict[str, object]]:
        del options, relative
        return self.windows


class TestTheFrontmostQuery:
    """``NSWorkspace.frontmostApplication()`` is not usable in a command line process.

    It learns about activation from notifications delivered on a run loop, and there is
    none, so it answers with whatever was in front when it was first asked. Measured:
    after ``open`` raised System Settings, NSWorkspace still named the application from
    five seconds earlier while the window server and System Events both named System
    Settings -- so every ``--focus`` failed on an activation that had succeeded.
    """

    def test_the_window_server_answer_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            appsmod,
            "_quartz",
            lambda: FakeQuartz(
                [
                    {"layer": 1, "pid": 99, "owner": "SomePanel"},
                    {"layer": 0, "pid": 42, "owner": "系统设置"},
                ]
            ),
        )
        monkeypatch.setattr(
            appsmod,
            "appkit",
            lambda: FakeAppKit([FakeApp("Stale", pid=7)], FakeApp("Stale", pid=7)),
        )
        assert appsmod.frontmost() == ("系统设置", 42)

    def test_a_window_with_no_owner_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tested on the query itself: ``frontmost`` would fall back to NSWorkspace."""
        monkeypatch.setattr(
            appsmod,
            "_quartz",
            lambda: FakeQuartz(
                [{"layer": 0}, {"layer": 1, "pid": 9}, {"layer": 0, "pid": 5, "owner": "X"}]
            ),
        )
        assert appsmod._window_server_front() == ("X", 5)

    def test_a_query_with_no_usable_window_answers_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            appsmod, "_quartz", lambda: FakeQuartz([{"layer": 0}, {"layer": 0, "pid": 0}])
        )
        assert appsmod._window_server_front() is None

    def test_it_falls_back_to_nsworkspace_when_the_window_server_cannot_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> object:
            raise RuntimeError("no window server")

        monkeypatch.setattr(appsmod, "_quartz", boom)
        monkeypatch.setattr(
            appsmod, "appkit", lambda: FakeAppKit([FakeApp("Finder")], FakeApp("Finder"))
        )
        assert appsmod.frontmost() == ("Finder", 1)


def test_an_app_without_a_name_falls_back_to_its_bundle_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class Nameless(FakeApp):
        def localizedName(self) -> Any:
            return None

    app = Nameless("ignored")
    monkeypatch.setattr(appsmod, "appkit", lambda: FakeAppKit([app], app))
    assert appsmod.running() == ["com.example.ignored"]


class TestStartingAClosedApplication:
    """``--focus`` used to refuse an application that was merely closed.

    Measured: System Settings had been closed, so the appearance task could not be run at
    all -- twice -- while one ``open`` would have started it. The diagnosis was correct
    and the answer was useless.
    """

    def test_a_localised_name_is_resolved_through_spotlight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``open -a 系统设置`` fails; Spotlight resolves it to System Settings.app."""
        queries: list[str] = []

        def fake_mdfind(query: str, *, timeout_s: float = 10.0) -> str:
            del timeout_s
            queries.append(query)
            return "/System/Applications/System Settings.app\n"

        monkeypatch.setattr(appsmod, "_mdfind", fake_mdfind)
        assert appsmod._resolve_app_path("系统设置") == "/System/Applications/System Settings.app"
        assert "kMDItemDisplayName == '系统设置'" in queries[0]
        assert "com.apple.application-bundle" in queries[0]

    def test_a_name_with_no_bundle_is_not_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(appsmod, "_mdfind", lambda query, **_: "not an app\n")
        assert appsmod._resolve_app_path("Not An Application") == ""

    def test_spotlight_failing_is_not_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(appsmod, "_mdfind", lambda query, **_: "")
        assert appsmod._resolve_app_path("Anything") == ""

    def test_it_opens_the_resolved_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        opened: list[list[str]] = []

        def opener(argv: list[str]) -> bool:
            opened.append(argv)
            return True

        monkeypatch.setattr(appsmod, "_resolve_app_path", lambda name: "/Applications/X.app")
        assert (
            appsmod.start("X", wake=lambda _: None, opener=opener, running=lambda path: True) == "X"
        )
        assert opened == [["open", "/Applications/X.app"]]

    def test_appearance_is_confirmed_from_the_process_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not from ``NSWorkspace``, whose list is a snapshot in a process with no run loop.

        Measured: ``open`` launched System Settings (``pgrep`` found it within a second)
        while ``NSWorkspace`` in the same process reported it absent for six seconds.
        """
        probes: list[str] = []

        def fake_running(path: str) -> bool:
            probes.append(path)
            return True

        monkeypatch.setattr(appsmod, "_resolve_app_path", lambda name: "/Applications/X.app")
        appsmod.start("X", wake=lambda _: None, opener=lambda argv: True, running=fake_running)
        assert probes == ["/Applications/X.app"]

    def test_it_returns_the_name_the_caller_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Because the system will not tell a run-loop-less process what the app is called."""
        monkeypatch.setattr(appsmod, "_resolve_app_path", lambda name: "/Applications/X.app")
        got = appsmod.start(
            "  本地化名字  ", wake=lambda _: None, opener=lambda argv: True, running=lambda p: True
        )
        assert got == "本地化名字"

    def test_it_falls_back_to_open_dash_a_when_spotlight_knows_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[list[str]] = []

        def opener(argv: list[str]) -> bool:
            opened.append(argv)
            return True

        monkeypatch.setattr(appsmod, "_resolve_app_path", lambda name: "")
        assert (
            appsmod.start("Ghostty", wake=lambda _: None, opener=opener, running=lambda path: False)
            == "Ghostty"
        )
        assert opened == [["open", "-a", "Ghostty"]]

    def test_a_refused_open_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(appsmod, "_resolve_app_path", lambda name: "")
        with pytest.raises(CannotDo, match="was refused"):
            appsmod.start("Nothing", wake=lambda _: None, opener=lambda argv: False)

    def test_an_application_that_never_appears_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(appsmod, "_resolve_app_path", lambda name: "/Applications/X.app")
        with pytest.raises(CannotDo, match="never appeared"):
            appsmod.start(
                "X", wake=lambda _: None, tries=1, opener=lambda argv: True, running=lambda p: False
            )
