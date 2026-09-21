"""Pixels, as a last resort.

The vision source has exactly one job: find text that accessibility does not
expose. It never decides anything, and it does **not** try to guess which text
is editable -- that question is answered by asking accessibility what is at
that point (see ``fusion``). Guessing from words like "search" or "email" is
what makes the naive version fail the moment the interface is not in English.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from ...errors import CannotDo, NoPermission
from ...types import Box, Target, Verb
from .pixels import Reading, as_target, best_per_region, dedupe_targets, to_box

logger = logging.getLogger(__name__)

MIN_WINDOW_SIDE = 80.0
"""Windows smaller than this are popovers and overlays, not the app surface."""


def screen_capture_allowed() -> bool:
    """Whether the Screen Recording permission is in place."""
    try:
        import Quartz
    except ImportError:
        return False
    preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    if preflight is None:
        return True  # older macOS: no preflight, we will find out on capture
    try:
        return bool(preflight())
    except Exception:  # pragma: no cover - defensive
        return False


class OCRSource:
    """Visible text turned into targets."""

    name = "ocr"
    rank = 10

    def __init__(
        self,
        *,
        level: str = "fast",
        min_confidence: float = 0.45,
        min_text_height: float = 0.006,
        max_targets: int = 160,
    ) -> None:
        if level not in {"fast", "accurate"}:
            raise ValueError("level must be 'fast' or 'accurate'")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        self.level = level
        self.min_confidence = min_confidence
        self.min_text_height = min_text_height
        self.max_targets = max_targets
        self.last_window: Box | None = None

    def targets(self, *, pid: int, title: str = "", box: Box | None = None) -> tuple[Target, ...]:
        """Recognise text in the frontmost window of ``pid``."""
        if not screen_capture_allowed():
            raise NoPermission(
                "Screen Recording permission is required for pixel perception. "
                "Grant it in System Settings > Privacy & Security > Screen Recording."
            )
        window = window_box_of(pid, title) or box
        if window is None:
            return ()
        window_id = _window_id(pid, title)
        if window_id is None:
            return ()
        image = _capture(window_id)
        width = float(_quartz().CGImageGetWidth(image))
        height = float(_quartz().CGImageGetHeight(image))
        if width <= 0 or height <= 0:
            return ()
        self.last_window = window
        readings = _read(image, width, height, self)
        kept = best_per_region(readings, window, image_w=width, image_h=height)[: self.max_targets]
        targets = tuple(
            as_target(reading, to_box(reading, window, width, height)) for reading in kept
        )
        return dedupe_targets(targets)


# --------------------------------------------------------------------------- #
# window lookup and capture
# --------------------------------------------------------------------------- #


def _quartz() -> Any:
    try:
        import Quartz
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo("pyobjc Quartz is unavailable; install the macos extra") from exc
    return Quartz


def _candidates(pid: int) -> list[tuple[int, str, Box]]:
    quartz = _quartz()
    options = quartz.kCGWindowListOptionOnScreenOnly | quartz.kCGWindowListExcludeDesktopElements
    windows = quartz.CGWindowListCopyWindowInfo(options, quartz.kCGNullWindowID) or []
    found: list[tuple[int, str, Box]] = []
    for info in windows:
        try:
            if int(info.get(quartz.kCGWindowOwnerPID, -1)) != pid:
                continue
            if int(info.get(quartz.kCGWindowLayer, 0) or 0) != 0:
                continue
            if float(info.get(quartz.kCGWindowAlpha, 1.0) or 0.0) <= 0:
                continue
            raw = info.get(quartz.kCGWindowBounds) or {}
            box = Box(float(raw["X"]), float(raw["Y"]), float(raw["Width"]), float(raw["Height"]))
            number = int(info.get(quartz.kCGWindowNumber, 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if number <= 0 or box.w < MIN_WINDOW_SIDE or box.h < MIN_WINDOW_SIDE:
            continue
        found.append((number, str(info.get(quartz.kCGWindowName, "") or ""), box))
    return found


def _pick(pid: int, title: str) -> tuple[int, str, Box] | None:
    windows = _candidates(pid)
    if not windows:
        return None
    wanted = title.strip().casefold()
    if wanted:
        for window in windows:
            name = window[1].strip().casefold()
            if name and (name == wanted or wanted in name or name in wanted):
                return window
    return windows[0]


def _window_id(pid: int, title: str) -> int | None:
    picked = _pick(pid, title)
    return None if picked is None else picked[0]


def window_box_of(pid: int, title: str = "") -> Box | None:
    picked = _pick(pid, title)
    return None if picked is None else picked[2]


def _capture(window_id: int) -> Any:
    """Screenshot one window.

    ``CGWindowListCreateImage`` is deprecated on modern macOS; ScreenCaptureKit
    is the replacement and is on the roadmap. Capturing a single window (rather
    than the display) keeps occluded windows readable and keeps the permission
    surface at Screen Recording only.
    """
    quartz = _quartz()
    if not hasattr(quartz, "CGWindowListCreateImage"):
        raise CannotDo(
            "this macOS/pyobjc build no longer exposes CGWindowListCreateImage; "
            "the ScreenCaptureKit backend is required"
        )
    options = quartz.kCGWindowImageBoundsIgnoreFraming
    if hasattr(quartz, "kCGWindowImageNominalResolution"):
        options |= quartz.kCGWindowImageNominalResolution
    elif hasattr(quartz, "kCGWindowImageBestResolution"):
        options |= quartz.kCGWindowImageBestResolution
    image = quartz.CGWindowListCreateImage(
        quartz.CGRectNull, quartz.kCGWindowListOptionIncludingWindow, window_id, options
    )
    if image is None:
        raise NoPermission("could not capture the window; check Screen Recording permission")
    return image


# --------------------------------------------------------------------------- #
# recognition
# --------------------------------------------------------------------------- #


def _read(image: Any, width: float, height: float, source: OCRSource) -> list[Reading]:
    del width, height
    try:
        import objc
        import Vision
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo("pyobjc Vision is unavailable; install the macos extra") from exc

    readings: list[Reading] = []
    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(
            getattr(Vision, "VNRequestTextRecognitionLevelFast", 1)
            if source.level == "fast"
            else getattr(Vision, "VNRequestTextRecognitionLevelAccurate", 0)
        )
        request.setUsesLanguageCorrection_(False)
        if hasattr(request, "setMinimumTextHeight_"):
            request.setMinimumTextHeight_(float(source.min_text_height))
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
        ok = handler.performRequests_error_([request], None)
        if isinstance(ok, tuple):
            ok, error = (ok[0], ok[1] if len(ok) > 1 else None)
        else:
            error = None
        if not ok:
            raise CannotDo(f"Vision OCR failed: {error or 'unknown error'}")

        for observation in list(request.results() or []):
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            text = str(candidate.string() or "").strip()
            if not text:
                continue
            confidence = float(candidate.confidence())
            if not math.isfinite(confidence) or confidence < source.min_confidence:
                continue
            rect = observation.boundingBox()
            readings.append(
                Reading(
                    text=text,
                    x=float(rect.origin.x),
                    y=float(rect.origin.y),
                    w=float(rect.size.width),
                    h=float(rect.size.height),
                    confidence=confidence,
                )
            )
    return readings


def measure_ax_first() -> dict[str, Any]:  # pragma: no cover - diagnostic helper
    from .ax import AXSource

    source = AXSource()
    started = time.perf_counter()
    targets = source.targets()
    semantic = sum(1 for t in targets if not t.visual)
    return {
        "targets": semantic,
        "ms": round((time.perf_counter() - started) * 1000),
        "click_only": sum(1 for t in targets if t.note == "click-only"),
        "verbs": sorted({str(v) for t in targets for v in t.actions if v is not Verb.WAIT}),
    }
