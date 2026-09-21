"""The permission messages are the point, so they are tested.

Answers to "which application do I authorize?" are wrong in a specific way far
more often than they are unhelpful: people grant Accessibility to ``python`` or
to ``uv``, because that is the process they can see. macOS grants it to the
application responsible for the process instead.
"""

from __future__ import annotations

import pytest

from deskhand.sensors.macos import permits

BOTH = {permits.ACCESSIBILITY: True, permits.SCREEN_RECORDING: True}
NEITHER = {permits.ACCESSIBILITY: False, permits.SCREEN_RECORDING: False}
PIXELS_ONLY_MISSING = {permits.ACCESSIBILITY: True, permits.SCREEN_RECORDING: False}
NAMED = {"application": "Ghostty", "pid": 4242, "chain": None}
UNNAMED = {"application": None, "pid": None, "chain": ["11 /usr/bin/python3", "10 herdr"]}


class TestMissing:
    def test_nothing_missing(self) -> None:
        assert permits.missing(BOTH) == []

    def test_one_missing(self) -> None:
        assert permits.missing(PIXELS_ONLY_MISSING) == [permits.SCREEN_RECORDING]

    def test_both_missing(self) -> None:
        assert sorted(permits.missing(NEITHER)) == [permits.ACCESSIBILITY, permits.SCREEN_RECORDING]


class TestInstructions:
    def test_nothing_to_do_when_both_are_granted(self) -> None:
        assert permits.to_do(BOTH, NAMED) == ["nothing to grant: both permissions are in place"]

    def test_a_named_owner_is_reported_with_its_pid(self) -> None:
        lines = permits.to_do(PIXELS_ONLY_MISSING, NAMED)
        assert "macOS attributes these permissions to: Ghostty (pid 4242)" in lines

    def test_the_common_mistake_is_named_explicitly(self) -> None:
        lines = " ".join(permits.to_do(NEITHER, NAMED))
        assert "not to python, uv or deskhand" in lines

    def test_only_the_missing_permission_is_listed(self) -> None:
        joined = " ".join(permits.to_do(PIXELS_ONLY_MISSING, NAMED))
        assert "Screen Recording:" in joined
        assert "Accessibility:" not in joined

    def test_a_process_chain_is_shown_when_no_ancestor_is_an_application(self) -> None:
        lines = permits.to_do(PIXELS_ONLY_MISSING, UNNAMED)
        joined = " ".join(lines)
        assert "no ancestor of this process is an application" in joined
        assert "process chain: 11 /usr/bin/python3" in joined
        assert "run `deskhand permit`" in joined

    def test_the_restart_caveat_is_always_present(self) -> None:
        joined = " ".join(permits.to_do(PIXELS_ONLY_MISSING, NAMED))
        assert "restart the application" in joined
        assert "re-reads Screen Recording permission at process start" in joined

    def test_the_user_is_not_told_to_grant_anything_when_both_are_present(self) -> None:
        joined = " ".join(permits.to_do(BOTH, UNNAMED))
        assert "System Settings" not in joined


class TestWhatCanBePrompted:
    def test_screen_recording_says_there_is_no_dialog_to_click(self) -> None:
        joined = " ".join(permits.to_do(PIXELS_ONLY_MISSING, NAMED))
        assert "cannot prompt for Screen Recording" in joined
        assert "add the application in that list with the + button" in joined

    def test_accessibility_does_not_get_the_no_dialog_warning(self) -> None:
        only_accessibility = {permits.ACCESSIBILITY: False, permits.SCREEN_RECORDING: True}
        joined = " ".join(permits.to_do(only_accessibility, NAMED))
        assert "cannot prompt" not in joined

    def test_the_measured_reality_is_recorded_per_permission(self) -> None:
        # Measured on macOS: tccd logs "Service kTCCServiceScreenCapture does not
        # allow prompting; returning denied." for a command line caller.
        assert permits.CAN_PROMPT[permits.ACCESSIBILITY] is True
        assert permits.CAN_PROMPT[permits.SCREEN_RECORDING] is False

    def test_every_permission_has_a_settings_page(self) -> None:
        # The anchors are the macOS contract; the display names differ from them
        # ("Screen Recording" vs the pane's Privacy_ScreenCapture).
        anchors = {
            permits.ACCESSIBILITY: "Privacy_Accessibility",
            permits.SCREEN_RECORDING: "Privacy_ScreenCapture",
        }
        for name, anchor in anchors.items():
            assert permits.PANE_URL[name].startswith("x-apple.systempreferences:")
            assert permits.PANE_URL[name].endswith(anchor)
            assert permits.FACE[name]


class TestRequests:
    def test_an_unknown_permission_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError, match="unknown permission"):
            permits.request("full_disk_access")
        with pytest.raises(ValueError, match="unknown permission"):
            permits.open_pane("full_disk_access")


# `blame()` and `status()` are deliberately not unit tested: they call into
# frameworks, and a fake of NSWorkspace would only test the fake. What they return
# is covered by `to_do`, which is pure.
