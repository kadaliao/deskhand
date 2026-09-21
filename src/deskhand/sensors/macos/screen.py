"""The macOS sensor: accessibility as the skeleton, pixels as an overlay.

Nothing here invents a second identity space for what is on screen. A recognised
region that resolves to an accessibility element *is* that element, with the
recognised text added as another name for it. What is left as a pixel target is
the part of the screen accessibility genuinely cannot see.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from ...errors import CannotDo, NoPermission, StaleTarget
from ...fusion import fuse
from ...settle import converge
from ...types import (
    Action,
    Box,
    Target,
    Verb,
    View,
    content_digest,
    shape_digest,
    structure_digest,
)
from . import keys
from .ax import AXSource
from .ocr import OCRSource, screen_capture_allowed

logger = logging.getLogger(__name__)

Pixels = bool | Literal["auto"]

FOCUS_RETRIES = (0.06, 0.14)


class MacSensor:
    """The hand on this machine."""

    def __init__(
        self,
        *,
        ax: AXSource | None = None,
        ocr: OCRSource | None = None,
        pixels: Pixels = "auto",
        rich_at: int = 40,
        hit_budget: int = 60,
    ) -> None:
        self.ax = ax or AXSource()
        self.ocr = ocr or OCRSource()
        self.pixels = pixels
        self.rich_at = rich_at
        self.hit_budget = hit_budget

    # ------------------------------------------------------------------ look

    def _want_pixels(self, semantic: int) -> bool:
        if self.pixels is True:
            return True
        if self.pixels is False:
            return False
        # "auto": if accessibility already describes this window richly, the
        # screenshot and the recognition pass would mostly be thrown away.
        return semantic < self.rich_at

    def observe(self) -> View:
        semantic = self.ax.targets()
        if self.ax.frame is None:  # pragma: no cover - targets() always sets it
            raise CannotDo("no frontmost window")
        return self._fuse(semantic, pixels=self._want_pixels(len(semantic)))

    def _fuse(self, semantic: tuple[Target, ...], *, pixels: bool) -> View:
        frame = self.ax.frame
        if frame is None:  # pragma: no cover - the walk always sets it
            raise CannotDo("no frontmost window")

        groups: list[tuple[int, tuple[Target, ...]]] = [(self.ax.rank, semantic)]
        visual: tuple[Target, ...] = ()
        used_pixels = False

        if pixels and screen_capture_allowed():
            used_pixels = True
            try:
                visual = self.ocr.targets(pid=frame.pid, title=frame.title, box=frame.box)
            except CannotDo as exc:
                logger.warning("pixel perception unavailable: %s", exc)
                visual = ()
            groups.append((self.ocr.rank, visual))

        fused = fuse(groups, hit=self.ax, max_hits=self.hit_budget)
        targets = fused.targets
        return View(
            app=frame.app,
            window=frame.title,
            revision=structure_digest(targets),
            targets=targets,
            content=content_digest(targets),
            frame=frame.box,
            notes={
                "pid": frame.pid,
                "semantic": len(semantic),
                "pixels": len(visual),
                "pixels_used": used_pixels,
                "fusion": fused.notes,
                "click_only": sum(1 for t in targets if "click-only" in t.note),
            },
            at_ms=round(time.time() * 1000),
        )

    def probe(self) -> str:
        return self.ax.probe()

    # ----------------------------------------------------------------- settle

    def settle(self, before: View, *, budget_ms: int) -> View:
        """Wait until the desktop stops changing, then observe once more.

        ``before`` seeds the wait. Without it, a walk taken immediately after acting
        still shows the pre-action state, two more identical walks agree with it,
        and the wait ends having learned nothing.

        Settling compares *shape* rather than full structure: a live page with a
        clock, a counter or a caret in it changes a value on every single read, and
        waiting for that to stop spends the entire budget on every step. Whether
        something changed is judged later, on the full digest, where a value change
        does count.

        The waiting itself lives in ``deskhand.settle``, which is where it can be
        tested by the iteration instead of by stopwatch.
        """
        result = converge(
            self.ax.targets,
            shape_digest,
            budget_s=budget_ms / 1000.0,
            start_from=shape_digest(before.targets),
        )
        view = self._fuse(result.last, pixels=self._want_pixels(len(result.last)))
        if not result.stable:
            # Say so rather than presenting a half-settled view as a settled one.
            logger.info(
                "settle gave up after %d walks of %dms: the interface never stopped moving",
                result.frames,
                budget_ms,
            )
            object.__setattr__(
                view, "notes", {**view.notes, "settled": False, "settle_walks": result.frames}
            )
        return view

    # ---------------------------------------------------------------- freshness

    def is_stale(self, view: View, action: Action) -> bool:
        frame = self.ax.frame
        if frame is None or frame.pid != view.notes.get("pid"):
            return True
        if action.guard is None:
            return False
        if action.visual:
            # Pixels have no semantics to re-read. The honest check is that we
            # are still looking at the same window and that the words still read
            # alike, which the fusion pass re-derives on the next observation.
            current = {t.id: t for t in view.targets}
            found = current.get(action.target or "")
            return found is None or not action.guard.matches(found.fingerprint())
        live = self.ax.fingerprint(action.target or "")
        return live is None or not action.guard.matches(live)

    # --------------------------------------------------------------------- act

    def act(self, view: View, action: Action) -> str:
        """Perform the action and report which route was taken.

        The route matters: ``ax-press`` and ``ax-set-value`` are semantic, the
        rest move the real mouse and keyboard. ``Report`` carries it per step so
        "how much of this task was done semantically?" is a number, not a claim.
        """
        if action.target is None:
            return self._whole_screen(action)

        target = view.target(action.target)

        if target.visual:
            return self._by_pixels(view, action, target)

        ref = self.ax.ref(action.target)
        if ref is None:
            raise StaleTarget(f"target {action.target} is no longer live")

        if action.verb is Verb.PRESS:
            return self.ax.press(ref)
        if action.verb is Verb.MENU:
            return self.ax.show_menu(ref)
        if action.verb is Verb.OPEN:
            return self._open(ref, target)
        if action.verb in {Verb.TYPE, Verb.SET}:
            return self._write(ref, action)
        if action.verb is Verb.DRAG:
            return self._drag(view, action, target)
        raise CannotDo(f"{action.verb} is not implemented for accessibility targets")

    def _whole_screen(self, action: Action) -> str:
        if action.verb is Verb.WAIT:
            time.sleep(0.15)
            return "wait"
        if action.verb is Verb.KEY:
            keys.press(action.key or "")
            return "key"
        if action.verb is Verb.CHORD:
            keys.chord(action.chord or "")
            return "chord"
        if action.verb is Verb.SCROLL:
            keys.scroll(action.scroll or "")
            return "scroll"
        raise CannotDo(f"{action.verb} needs a target")

    def _by_pixels(self, view: View, action: Action, target: Target) -> str:
        if target.box is None:
            raise CannotDo("pixel target has no rectangle")
        if action.verb is Verb.PRESS:
            keys.click(target.box)
            return "click"
        if action.verb is Verb.OPEN:
            keys.click(target.box, count=2)
            return "double-click"
        if action.verb is Verb.MENU:
            keys.click(target.box, button="right")
            return "click-menu"
        if action.verb is Verb.DRAG:
            onto = view.target(action.onto or "")
            if onto.box is None:
                raise CannotDo("drag destination has no rectangle")
            keys.drag(target.box, onto.box)
            return "drag"
        raise CannotDo(f"{action.verb} is not supported on a pixel target")

    def _open(self, ref: Any, target: Target) -> str:
        if target.box is not None:
            keys.click(target.box, count=2)
            return "double-click"
        self.ax.press(ref)
        self.ax.press(ref)
        return "ax-press-twice"

    def _drag(self, view: View, action: Action, target: Target) -> str:
        onto = view.target(action.onto or "")
        if target.box is None or onto.box is None:
            raise CannotDo("drag needs a rectangle on both ends")
        keys.drag(target.box, onto.box)
        return "drag"

    def _write(self, ref: Any, action: Action) -> str:
        """Set or type an agent-supplied literal. Never invents text."""
        text = action.value
        if text is None:
            raise CannotDo(f"{action.verb} arrived without a value")
        if action.verb is Verb.TYPE and self.ax.can_set_value(ref):
            return self.ax.set_value(ref, str(text))

        if action.verb is Verb.SET:
            # A slider or stepper cannot be driven by keystrokes, and it wants the
            # value as it was supplied rather than as a spelling of it.
            return self.ax.set_value(ref, text)

        self._focus_or_die(ref)
        keys.select_all()
        time.sleep(0.05)
        keys.type_text(str(text))
        return "ax-focus+keys"

    def _focus_or_die(self, ref: Any) -> None:
        """Put the keyboard focus on the target, or refuse to type.

        Without this check a failed click means the text lands in whatever was
        focused instead -- a different field, or a different application.
        """
        self.ax.focus(ref)
        for wait in FOCUS_RETRIES:
            time.sleep(wait)
            if self.ax.is_focused(ref):
                return
        raise CannotDo("could not confirm keyboard focus on the target; refusing to type")


def open_sensor(*, pixels: Pixels = "auto") -> MacSensor:
    """Build the real thing, with a clear error if a permission is missing."""
    from .ax import require_permission

    require_permission()
    return MacSensor(pixels=pixels)


def permissions() -> dict[str, bool]:
    from .ax import trusted

    return {
        "accessibility": trusted(),
        "screen_recording": screen_capture_allowed(),
    }


def window_box(pid: int, title: str = "") -> Box | None:
    from .ocr import window_box_of

    return window_box_of(pid, title)


__all__ = ["MacSensor", "NoPermission", "open_sensor", "permissions", "window_box"]
