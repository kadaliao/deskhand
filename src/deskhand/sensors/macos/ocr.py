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
from collections.abc import Sequence
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
    """Visible text turned into targets.

    Recognises the languages the machine is set to use, not English, and at the accurate
    level. Both distinctions are the difference between the pixel layer working on a
    localised desktop and being blind on one. Measured on one Chinese System Settings
    window, same capture:

    | level | languages | regions | of which sidebar labels | ms |
    |---|---|---|---|---|
    | fast | unset (English) | 24 | 0 | 154 |
    | fast | system | 16 | 0 | 154 |
    | accurate | system | 40 | 9 (`通用`, `外观`, `辅助功能`, ...) | 540 |

    Only the last row can name the controls this source exists to name. `fast` is not a
    cheaper version of the same reading here, it is a different and useless one, so the
    cheap default is not the honest default.
    """

    name = "ocr"
    rank = 10

    def __init__(
        self,
        *,
        level: str = "accurate",
        min_confidence: float = 0.45,
        min_text_height: float = 0.006,
        max_targets: int = 160,
        languages: Sequence[str] | None = None,
    ) -> None:
        if level not in {"fast", "accurate"}:
            raise ValueError("level must be 'fast' or 'accurate'")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        self.level = level
        self.min_confidence = min_confidence
        self.min_text_height = min_text_height
        self.max_targets = max_targets
        # ``None`` means "ask the machine"; an explicit empty tuple means "let Vision
        # decide", which is what an ablation would want.
        self.languages: tuple[str, ...] | None = None if languages is None else tuple(languages)
        self.last_window: Box | None = None

    def _recognise(self) -> list[str]:
        """The languages to hand Vision, in its own vocabulary."""
        if self.languages is not None:
            return list(self.languages)
        return preferred_languages()

    def targets(self, *, pid: int, title: str = "", box: Box | None = None) -> tuple[Target, ...]:
        """Recognise text in the frontmost window of ``pid``."""
        if not screen_capture_allowed():
            raise NoPermission(
                "Screen Recording permission is required for pixel perception. "
                "Grant it in System Settings > Privacy & Security > Screen Recording."
            )
        # The window accessibility described, matched by rectangle before title: the two
        # disagree for an application with several windows, and reading the other one puts
        # every recognised name on the wrong control.
        picked = _pick(pid, title, box)
        if picked is None:
            return ()
        window_id, _, window = picked
        image = _capture(window_id)
        # A CGImage's dimensions are numbers, so float() cannot raise on them.
        # ast-grep-ignore
        width = float(_quartz().CGImageGetWidth(image))
        # ast-grep-ignore
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


def _appkit() -> Any:
    try:
        import AppKit
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo("pyobjc AppKit is unavailable; install the macos extra") from exc
    return AppKit


def _vision() -> Any:
    try:
        import Vision
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo("pyobjc Vision is unavailable; install the macos extra") from exc
    return Vision


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


def _pick(pid: int, title: str, box: Box | None = None) -> tuple[int, str, Box] | None:
    windows = _candidates(pid)
    if not windows:
        return None
    if box is not None:
        # The window accessibility is describing, by where it is: an application with two
        # windows (one per display, say) lists them in an order that need not start with
        # the focused one, and a picture of the other window misaligns every rectangle.
        def distance(window: tuple[int, str, Box]) -> float:
            other = window[2]
            return (
                abs(other.x - box.x)
                + abs(other.y - box.y)
                + abs(other.w - box.w)
                + abs(other.h - box.h)
            )

        closest = min(windows, key=distance)
        if distance(closest) <= MIN_WINDOW_SIDE:
            return closest
    wanted = title.strip().casefold()
    if wanted:
        for window in windows:
            name = window[1].strip().casefold()
            if name and (name == wanted or wanted in name or name in wanted):
                return window
    return windows[0]


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
    # Native resolution, not nominal. On a Retina display the nominal capture is half the
    # pixels, and that is visible in the recognition: the 1x image read `Xingyl Llao` where
    # the 2x one read `Xingyi Liao`, and `AppleCare` and `Siri` came out legible only at 2x.
    # ``to_box`` derives its scale from the image size, so the geometry is unaffected.
    if hasattr(quartz, "kCGWindowImageBestResolution"):
        options |= quartz.kCGWindowImageBestResolution
    elif hasattr(quartz, "kCGWindowImageNominalResolution"):
        options |= quartz.kCGWindowImageNominalResolution
    image = quartz.CGWindowListCreateImage(
        quartz.CGRectNull, quartz.kCGWindowListOptionIncludingWindow, window_id, options
    )
    if image is None:
        raise NoPermission("could not capture the window; check Screen Recording permission")
    return image


def snapshot(
    pid: int, title: str = "", *, box: Box | None = None, quality: float = 0.82
) -> tuple[bytes, Box] | None:
    """One window as JPEG bytes, and the screen box it covers. Read-only.

    For a person to look at (the console draws targets over it), not for recognition:
    JPEG keeps a Retina capture of a large window to a few hundred kilobytes.
    """
    if not screen_capture_allowed():
        raise NoPermission("Screen Recording permission is required to show the window")
    picked = _pick(pid, title, box)
    if picked is None:
        return None
    number, _, box = picked
    image = _capture(number)
    quartz = _quartz()
    data = _appkit().NSMutableData.data()
    destination = quartz.CGImageDestinationCreateWithData(data, "public.jpeg", 1, None)
    if destination is None:
        raise CannotDo("could not create a JPEG encoder")
    quartz.CGImageDestinationAddImage(
        destination, image, {quartz.kCGImageDestinationLossyCompressionQuality: quality}
    )
    if not quartz.CGImageDestinationFinalize(destination):
        raise CannotDo("could not encode the window image")
    return bytes(data), box


# --------------------------------------------------------------------------- #
# recognition
# --------------------------------------------------------------------------- #


def preferred_languages() -> list[str]:
    """The languages this machine reads, as Vision spells them.

    ``NSLocale.preferredLanguages()`` answers with full tags (``zh-Hans-SG``) and Vision
    accepts them; the list is also the honest answer to "what should be recognised here",
    which is a question about the machine rather than about this package.
    """
    try:
        return [str(code) for code in _appkit().NSLocale.preferredLanguages()]
    except Exception:  # no AppKit, or no locale: let Vision decide
        return []


def _read(image: Any, width: float, height: float, source: OCRSource) -> list[Reading]:
    del width, height
    try:
        import objc
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo("pyobjc Vision is unavailable; install the macos extra") from exc
    # Through the accessor, like Quartz: pyobjc ships no stubs for these classes, so
    # reaching them off an Any says "a framework call" once instead of once per call.
    vision = _vision()

    readings: list[Reading] = []
    with objc.autorelease_pool():
        request = vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(
            getattr(vision, "VNRequestTextRecognitionLevelFast", 1)
            if source.level == "fast"
            else getattr(vision, "VNRequestTextRecognitionLevelAccurate", 0)
        )
        request.setUsesLanguageCorrection_(False)
        languages = source._recognise()
        if languages:
            # Left unset, Vision recognises English: on a Chinese macOS that is 24 regions
            # of noise and a sidebar it cannot see at all.
            request.setRecognitionLanguages_(languages)
        if hasattr(request, "setMinimumTextHeight_"):
            # Every float() in this function is converting a CGFloat that pyobjc hands back
            # as a number, not parsing text, so none of them can raise ValueError.
            # ast-grep-ignore
            request.setMinimumTextHeight_(float(source.min_text_height))
        handler = vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
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
            # As above: a confidence from Vision is already a number.
            # ast-grep-ignore
            confidence = float(candidate.confidence())
            if not math.isfinite(confidence) or confidence < source.min_confidence:
                continue
            rect = observation.boundingBox()
            # pyobjc hands a CGRect's components back as floats already, so there is nothing
            # to convert (and nothing here can raise, which is what the int()/float() rule
            # is about).
            readings.append(
                Reading(
                    text=text,
                    x=rect.origin.x,
                    y=rect.origin.y,
                    w=rect.size.width,
                    h=rect.size.height,
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
        "click_only": sum(1 for t in targets if "click-only" in t.note),
        "verbs": sorted({str(v) for t in targets for v in t.actions if v is not Verb.WAIT}),
    }
