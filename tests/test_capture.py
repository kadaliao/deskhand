"""Which capture path runs, and how a ScreenCaptureKit answer is waited for.

Measured on this machine (macOS 26.7): CGWindowListCreateImage 38-47 ms for a window,
ScreenCaptureKit 268 ms first and 107-109 ms after, same 3456x2018 pixels. So the legacy
call stays first, and ScreenCaptureKit takes over only when the legacy one is gone.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from deskhand.errors import CannotDo, NoPermission
from deskhand.sensors.macos import ocr


class LegacyQuartz:
    kCGWindowImageBoundsIgnoreFraming = 1
    kCGWindowImageBestResolution = 8
    kCGWindowListOptionIncludingWindow = 8
    CGRectNull = None

    def CGWindowListCreateImage(self, *args: Any) -> str:
        return "legacy image"


class ModernQuartz:
    """A macOS that has removed the legacy call."""


@pytest.fixture
def via_sck(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(
        ocr, "_capture_sck", lambda window_id: calls.append(window_id) or "sck image"
    )
    return calls


def test_the_legacy_call_is_used_while_it_exists(
    monkeypatch: pytest.MonkeyPatch, via_sck: list[int]
) -> None:
    monkeypatch.delenv(ocr.CAPTURE_ENV, raising=False)
    monkeypatch.setattr(ocr, "_quartz", LegacyQuartz)
    assert ocr._capture(7) == "legacy image"
    assert via_sck == []


def test_screencapturekit_takes_over_when_the_legacy_call_is_gone(
    monkeypatch: pytest.MonkeyPatch, via_sck: list[int]
) -> None:
    monkeypatch.delenv(ocr.CAPTURE_ENV, raising=False)
    monkeypatch.setattr(ocr, "_quartz", ModernQuartz)
    assert ocr._capture(7) == "sck image"
    assert via_sck == [7]


def test_it_can_be_forced_so_the_fallback_is_exercised_before_it_is_needed(
    monkeypatch: pytest.MonkeyPatch, via_sck: list[int]
) -> None:
    monkeypatch.setenv(ocr.CAPTURE_ENV, "sck")
    monkeypatch.setattr(ocr, "_quartz", LegacyQuartz)
    assert ocr._capture(7) == "sck image"


class TestWaiting:
    def test_an_answer_from_another_thread_is_returned(self) -> None:
        def start(done: Any) -> None:
            threading.Thread(target=done, args=("image", None)).start()

        assert ocr._await(start, 1.0, "capturing") == "image"

    def test_an_error_is_a_refusal(self) -> None:
        with pytest.raises(NoPermission, match="refused capturing"):
            ocr._await(lambda done: done(None, "user declined"), 1.0, "capturing")

    def test_silence_is_a_timeout_not_a_hang(self) -> None:
        with pytest.raises(CannotDo, match="did not answer"):
            ocr._await(lambda done: None, 0.05, "capturing")
