"""Accessibility: the semantic truth.

Two things here are deliberately different from the obvious implementation.

**Attribute reads are batched.** Reading role, title, value, position, size,
children and eight more fields one call at a time costs roughly thirteen
synchronous round trips *per node*, including nodes that turn out to be
anonymous containers and get dropped. One ``AXUIElementCopyMultipleAttributeValues``
per node, role-first short-circuit, and no reads at all for nodes we discard.

**Ids are derived from meaning, not from object identity.** ``repr(ref)``
contains a memory address and changes between walks, which makes every
observation look like a brand new desktop. Ids here come from role, label and
bucketed geometry, so they survive a re-walk and can be matched against the
result of a hit test.

**Chromium needs to be asked.** Electron and Chromium expose a stub tree until a
client sets ``AXManualAccessibility``. The attribute is probed for rather than
assumed, so this costs one call on apps that do not have it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from hashlib import sha1
from typing import Any

from ...errors import CannotDo, NoPermission
from ...fingerprint import GRID, Fingerprint, digest, norm_text
from ...types import Box, Target, Verb
from .keys import click

logger = logging.getLogger(__name__)

TEXT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"})
VALUE_ROLES = TEXT_ROLES | frozenset(
    {"AXSlider", "AXIncrementor", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXStepper"}
)

INTERACTIVE_ROLES = TEXT_ROLES | frozenset(
    {
        "AXButton",
        "AXLink",
        "AXMenuItem",
        "AXMenuBarItem",
        "AXCheckBox",
        "AXRadioButton",
        "AXTab",
        "AXTabGroup",
        "AXCell",
        "AXRow",
        "AXOutline",
        "AXDisclosureTriangle",
        "AXPopUpButton",
        "AXComboBox",
        "AXMenuButton",
        "AXSlider",
        "AXIncrementor",
        "AXStepper",
        "AXColorWell",
        "AXSplitter",
        "AXSegmentedControl",
    }
)
"""Roles worth aiming at with the mouse when they expose no native action.

Measured on a real Chromium application: of 38 navigation-order elements, only
the eight real controls (buttons, a splitter) implement ``AXPress``. The rest are
anonymous containers whose only actions are ``AXScrollToVisible`` and
``AXShowMenu``. Offering a coordinate click for those would fill the action space
with things nobody means, so a plain geometric element with no name and no
interactive role is not made clickable.
"""

WANTED = (
    "AXRole",
    "AXSubrole",
    "AXTitle",
    "AXDescription",
    "AXHelp",
    "AXValue",
    "AXEnabled",
    "AXFocused",
    "AXSelected",
    "AXExpanded",
    "AXIdentifier",
    "AXPosition",
    "AXSize",
    "AXHidden",
    "AXChildren",
    # Selection is expressed by the *parent* on AppKit: a tab view, table, outline
    # and browser all report which of their children is chosen, and the children
    # themselves often report nothing. Reading only AXChildren loses "which one is
    # selected", which is the entire fact behind "choose the Dark option".
    "AXSelectedChildren",
    "AXSelectedRows",
)

FALLBACK = (
    "AXRole",
    "AXTitle",
    "AXValue",
    "AXEnabled",
    "AXPosition",
    "AXSize",
    "AXChildren",
    "AXSelectedChildren",
    "AXSelectedRows",
)

SELECTION_ROLES = frozenset(
    {
        "AXRadioButton",
        "AXCheckBox",
        "AXMenuItem",
        "AXSwitch",
        "AXToggleButton",
    }
)
"""Roles whose boolean ``AXValue`` *is* the selection state.

Measured on Ghostty: its tabs are ``AXRadioButton`` with subrole ``AXTabButton``,
they expose no ``AXSelected`` at all, and their ``AXValue`` is ``True`` for the
selected tab and ``False`` for the others. Nothing else in the tree says which tab
is active.
"""

MAX_SELECTION_READS = 8
"""Reading the selected children costs a call each. More than a handful is a
table with a block selected, and the block is not what we are aiming at."""

PAIR = 2
"""A point or a size is two numbers."""

MAX_LABEL_CHARS = 400
MAX_VALUE_CHARS = 300

HITTEST_TIMEOUT_S = 2.0
ENABLE_WAIT_S = 0.25


def ax() -> Any:
    try:
        import ApplicationServices
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo(
            "pyobjc ApplicationServices is unavailable; install the macos extra"
        ) from exc
    return ApplicationServices


def appkit() -> Any:
    try:
        import AppKit
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo("pyobjc AppKit is unavailable; install the macos extra") from exc
    return AppKit


def trusted() -> bool:
    """Whether this process has the Accessibility permission."""
    try:
        return bool(ax().AXIsProcessTrusted())
    except Exception:  # pragma: no cover - defensive
        return False


def require_permission() -> None:
    if not trusted():
        raise NoPermission(
            "Accessibility permission is required. Grant it to your terminal in "
            "System Settings > Privacy & Security > Accessibility, then restart the app."
        )


@dataclass(frozen=True, slots=True)
class Frame:
    """The application surface we are looking at."""

    pid: int
    app: str
    title: str
    box: Box | None


class AXSource:
    """Perception *and* native execution for one frontmost application."""

    name = "ax"
    rank = 0

    def __init__(self, *, max_elements: int = 900, max_depth: int = 14) -> None:
        self.max_elements = max_elements
        self.max_depth = max_depth
        self.frame: Frame | None = None
        self._refs: dict[str, Any] = {}
        self._batch_broken = False
        self._enabled: set[int] = set()

    # ---------------------------------------------------------------- front

    def _frontmost(self) -> tuple[int, str, Any]:
        """The application in front *now*, from the window server.

        Deliberately not ``NSWorkspace.frontmostApplication()``: in a process with no run
        loop that serves a snapshot taken at first access, so it can name an application
        that is no longer in front. That is not merely a stale reading, it is an
        inconsistency with teeth: a coordinate click is aimed at the window being
        *observed*, so observing one application while another is actually in front sends
        the click into whatever is there. Measured during a model run -- the sensor read
        System Settings while Chrome was in front, and two clicks intended for a sidebar
        row had no effect at all.
        """
        from .apps import frontmost as live_frontmost  # lazy: apps imports this module

        current = live_frontmost()
        if current is None:
            raise CannotDo("no frontmost application")
        name, pid = current
        return pid, name, ax().AXUIElementCreateApplication(pid)

    def _window_of(self, app_ref: Any) -> tuple[Any, str, Box | None]:
        """Focused window element, its title and its frame.

        ``AXFocusedWindow`` answers ``kAXErrorCannotComplete`` when an application
        is busy. Treating that as "no window" would collapse perception to the
        application element and return an empty tree, so fall back to the first
        window the application will admit to having.
        """
        window = self._one(app_ref, "AXFocusedWindow")
        if window is None:
            windows = _iter(self._one(app_ref, "AXWindows"))
            window = windows[0] if windows else None
            if window is not None:
                logger.debug("AXFocusedWindow unavailable; using the first AXWindows entry")
        root = window if window is not None else app_ref
        title = str(self._one(root, "AXTitle") or "")
        box = _box(self._one(root, "AXPosition"), self._one(root, "AXSize"))
        return root, title, box

    def _enable_full_tree(self, app_ref: Any, pid: int) -> None:
        """Ask Chromium-based apps to actually build an accessibility tree.

        The tree is built asynchronously after the attribute flips, so the first
        observation right after enabling is expected to be thin.
        """
        if pid in self._enabled:
            return
        names = set(self.attribute_names(app_ref))
        for attribute in ("AXManualAccessibility", "AXEnhancedUserInterface"):
            if attribute in names:
                try:
                    ax().AXUIElementSetAttributeValue(app_ref, attribute, True)
                except Exception as exc:  # pragma: no cover - depends on the target app
                    logger.debug("could not set %s on pid %d: %s", attribute, pid, exc)
                else:
                    logger.info("enabled %s for pid %d", attribute, pid)
                    time.sleep(ENABLE_WAIT_S)
        self._enabled.add(pid)

    # ------------------------------------------------------------ raw reads

    def attribute_names(self, ref: Any) -> list[str]:
        try:
            error, names = ax().AXUIElementCopyAttributeNames(ref, None)
        except Exception:
            return []
        if error != 0 or not names:
            return []
        return [str(n) for n in names]

    def _batch(self, ref: Any, names: tuple[str, ...]) -> list[Any] | None:
        if self._batch_broken:
            return None
        try:
            result = ax().AXUIElementCopyMultipleAttributeValues(ref, list(names), 0, None)
        except Exception as exc:
            logger.debug("batch read unavailable: %s", exc)
            self._batch_broken = True
            return None
        try:
            error, values = result[0], result[1]
        except (TypeError, IndexError):
            self._batch_broken = True
            return None
        if error != 0 or values is None:
            return None
        return list(values)

    def _one(self, ref: Any, name: str) -> Any:
        try:
            error, value = ax().AXUIElementCopyAttributeValue(ref, name, None)
        except Exception:
            return None
        return unwrap(value) if error == 0 else None

    def _details(self, ref: Any) -> dict[str, Any]:
        names = FALLBACK if self._batch_broken else WANTED
        values = self._batch(ref, names)
        if values is None:
            return {name: self._one(ref, name) for name in names}
        # Unsupported attributes come back as an AXValue carrying an error code,
        # not as null, so every value goes through the same unwrapping.
        return {name: unwrap(value) for name, value in zip(names, values, strict=False)}

    def _selection_of_children(self, details: dict[str, Any]) -> frozenset[str]:
        """The ids of this element's selected children, if it says which they are.

        The children are re-read to compute their ids the same way the walk will,
        so the two can be matched. Only elements that answer at all pay for this:
        a tab view, a table, an outline, a browser.
        """
        refs = [
            *_iter(details.get("AXSelectedChildren")),
            *_iter(details.get("AXSelectedRows")),
        ][:MAX_SELECTION_READS]
        ids: set[str] = set()
        for child in refs:
            child_details = self._details(child)
            role = child_details.get("AXRole")
            if role is None:
                continue
            label, _ = _label(child_details)
            ids.add(
                _identify(
                    str(role),
                    str(child_details.get("AXSubrole") or ""),
                    label,
                    _box(child_details.get("AXPosition"), child_details.get("AXSize")),
                )
            )
        return frozenset(ids)

    def _actions(self, ref: Any) -> set[str]:
        try:
            error, names = ax().AXUIElementCopyActionNames(ref, None)
        except Exception:
            return set()
        if error != 0 or not names:
            return set()
        return {str(n) for n in names}

    def _settable(self, ref: Any, attribute: str) -> bool:
        try:
            error, ok = ax().AXUIElementIsAttributeSettable(ref, attribute, None)
        except Exception:
            return False
        return error == 0 and bool(ok)

    # ---------------------------------------------------------------- walk

    def targets(self) -> tuple[Target, ...]:
        """Careful read of the frontmost app. Also refreshes the live refs."""
        require_permission()
        pid, name, app_ref = self._frontmost()
        self._timeout(app_ref)
        self._enable_full_tree(app_ref, pid)

        root, title, box = self._window_of(app_ref)
        self.frame = Frame(pid=pid, app=name, title=title, box=box)

        refs: dict[str, Any] = {}
        found: list[Target] = []
        self._walk(root, refs, found, set(), depth=0, budget=self.max_elements, path="0")

        self._refs = refs
        logger.debug("ax observe app=%r targets=%d", name, len(found))
        return tuple(found)

    def _timeout(self, ref: Any) -> None:
        try:
            ax().AXUIElementSetMessagingTimeout(ref, HITTEST_TIMEOUT_S)
        except Exception:
            logger.debug("AXUIElementSetMessagingTimeout is unavailable")

    def _walk(
        self,
        ref: Any,
        refs: dict[str, Any],
        found: list[Target],
        seen: set[str],
        *,
        depth: int,
        budget: int,
        path: str,
        selected_ids: frozenset[str] = frozenset(),
    ) -> None:
        if depth > self.max_depth or len(found) >= budget:
            return
        key = repr(ref)
        if key in seen:
            return
        seen.add(key)

        details = self._details(ref)
        role = details.get("AXRole")
        if role is None:
            # Not an accessibility element we can reason about. Do not fetch more.
            return
        role = str(role)

        target = self._target(ref, role, details, path=path, selected_ids=selected_ids)
        if target is not None:
            unique = target.id
            n = 2
            while unique in refs:  # two elements can still hash alike; never overwrite one
                unique, n = f"{target.id}#{n}", n + 1
            found.append(target if unique == target.id else replace(target, id=unique))
            refs[unique] = ref

        for index, child in enumerate(_iter(details.get("AXChildren"))):
            if len(found) >= budget:
                return
            self._walk(
                child,
                refs,
                found,
                seen,
                depth=depth + 1,
                budget=budget,
                path=f"{path}.{index}",
            )

    def probe(self) -> str:
        """Cheap structural signal for settling: accessibility only, no pixels.

        OCR is the expensive part of an observation, so the settle loop must not
        pay for it. This walks a shallower tree and hashes structure only.
        """
        pid, name, app_ref = self._frontmost()
        self._timeout(app_ref)
        self._enable_full_tree(app_ref, pid)
        root, title, box = self._window_of(app_ref)
        self.frame = Frame(pid=pid, app=name, title=title, box=box)
        found: list[Target] = []
        self._walk(root, {}, found, set(), depth=0, budget=min(self.max_elements, 400), path="0")
        # id()/float() here are converting bools and pids that are already numbers, not
        # parsing text: the unchecked-throwing-call rule cannot see that.
        # ast-grep-ignore
        return digest(f"{t.kind}|{t.label}|{int(t.focused)}|{int(t.enabled)}" for t in found)

    # -------------------------------------------------------------- one node

    def _target(
        self,
        ref: Any,
        role: str,
        details: dict[str, Any],
        *,
        path: str = "",
        selected_ids: frozenset[str] = frozenset(),
    ) -> Target | None:
        # AXHidden is a CFBoolean, so pyobjc hands back a real bool: `is True` is exact.
        # ast-grep-ignore
        if details.get("AXHidden") is True:
            return None

        subrole = str(details.get("AXSubrole") or "")
        label, provenance = _label(details)
        value = _coerce(details.get("AXValue"))
        box = _box(details.get("AXPosition"), details.get("AXSize"))

        window = self.frame.box if self.frame is not None else None
        if box is not None:
            if box.w <= 0 or box.h <= 0:
                box = None
            elif window is not None and not window.holds(*box.center):
                # Scrolled out of view or parked offscreen.
                return None

        named = bool(label) or value not in (None, "")
        interactive = role in INTERACTIVE_ROLES

        # Action names cost a round trip of their own and cannot be batched with the
        # attributes. A node with no name, no value, no geometry and no interactive
        # role cannot be aimed at by anything that picks targets by name, so it is
        # not worth asking about. This is also where the anonymous-container noise
        # would have come from, so it is a correctness improvement that happens to
        # be a saving: measured at roughly a third of the calls on a Chromium tree.
        worth_asking = named or interactive or box is not None
        actions = self._actions(ref) if worth_asking else set()

        aimable = named or interactive
        caps: set[Verb] = set()
        if "AXPress" in actions:
            caps.add(Verb.PRESS)
        elif box is not None and aimable:
            # Real controls often respond to a click without implementing AXPress.
            caps.add(Verb.PRESS)
        if box is not None and aimable:
            caps.add(Verb.OPEN)
        if "AXShowMenu" in actions or (box is not None and aimable):
            caps.add(Verb.MENU)
        if role in TEXT_ROLES:
            # Text entry is offered for text roles even when AXValue is not
            # settable: focus plus keystrokes is still a real path, and it is
            # verified before a single character is sent.
            caps.add(Verb.TYPE)
        if role in VALUE_ROLES and self._settable(ref, "AXValue"):
            caps.add(Verb.SET)
        if "AXShowMenu" in actions:
            caps.add(Verb.MENU)

        if not caps and not named and box is None:
            return None  # anonymous container; children are walked regardless

        flags: list[str] = []
        if provenance:
            flags.append(provenance)
        if Verb.PRESS in caps and "AXPress" not in actions:
            flags.append("click-only")
        elif not caps:
            flags.append("not-aimable")
        note = ",".join(flags)

        target_id = _identify(role, subrole, label, box, path)
        # A parent that tells us which of its children is chosen is authoritative:
        # the child itself usually says nothing. No parent answer means "unknown",
        # not "not selected".
        own = details.get("AXSelected")
        raw_value = details.get("AXValue")
        if selected_ids:
            selected: bool | None = target_id in selected_ids
        elif isinstance(raw_value, bool) and role in SELECTION_ROLES:
            selected = raw_value
        else:
            selected = None if own is None else bool(own)
        return Target(
            id=target_id,
            kind=_kind(role, subrole),
            label=label,
            value=value if isinstance(value, (str, int, float, bool)) else None,
            actions=frozenset(caps),
            box=box,
            source="ax",
            visual=False,
            enabled=True if details.get("AXEnabled") is None else bool(details.get("AXEnabled")),
            focused=bool(details.get("AXFocused")),
            selected=selected,
            expanded=None if details.get("AXExpanded") is None else bool(details.get("AXExpanded")),
            note=note,
        )

    # ------------------------------------------------------------- hit test

    def hit(self, x: float, y: float) -> Target | None:
        """Which accessibility element is at this point, if any."""
        system = ax().AXUIElementCreateSystemWide()
        self._timeout(system)
        try:
            error, element = ax().AXUIElementCopyElementAtPosition(system, float(x), float(y), None)
        except Exception:
            return None
        if error != 0 or element is None:
            return None
        details = self._details(element)
        role = details.get("AXRole")
        if role is None:
            return None
        return self._target(element, str(role), details)

    # ------------------------------------------------------------ act (native)

    def ref(self, target_id: str) -> Any | None:
        return self._refs.get(target_id)

    def fingerprint(self, target_id: str) -> Fingerprint | None:
        """Re-derive a target's meaning from the live element, cheaply."""
        ref = self._refs.get(target_id)
        if ref is None:
            return None
        details = self._details(ref)
        role = details.get("AXRole")
        if role is None:
            return None
        found = self._target(ref, str(role), details)
        return None if found is None else found.fingerprint()

    def press(self, ref: Any, *, click_fallback: bool = True) -> str:
        """``AXPress``, or a click at the element's centre when it has none.

        ``click_fallback=False`` is for containers: the centre of a window or a group is
        not the thing that was meant, it is whatever happens to be drawn there, so a
        container that will not press natively is refused rather than clicked blind.
        """
        actions = self._actions(ref)
        if "AXPress" in actions:
            error = ax().AXUIElementPerformAction(ref, "AXPress")
            if error == 0:
                return "ax-press"
        if not click_fallback:
            raise CannotDo(
                "a container did not accept AXPress, and a click at its centre would land on"
                " whatever is drawn there; choose the control inside it"
            )
        box = _box(self._one(ref, "AXPosition"), self._one(ref, "AXSize"))
        if box is None:
            raise CannotDo("element is neither AXPress-able nor on screen")
        click(box)
        return "click"

    def show_menu(self, ref: Any) -> str:
        if "AXShowMenu" in self._actions(ref):
            error = ax().AXUIElementPerformAction(ref, "AXShowMenu")
            if error == 0:
                return "ax-menu"
        box = _box(self._one(ref, "AXPosition"), self._one(ref, "AXSize"))
        if box is None:
            raise CannotDo("element has no position for a context menu")
        click(box, button="right")
        return "click-menu"

    def set_value(self, ref: Any, value: Any) -> str:
        """Replace a control's value through the accessibility API.

        The value keeps its native type: a slider wants a number, a checkbox wants
        a boolean, a text field wants a string.
        """
        if not self._settable(ref, "AXValue"):
            raise CannotDo("AXValue is not settable on this element")
        error = ax().AXUIElementSetAttributeValue(ref, "AXValue", value)
        if error != 0:
            raise CannotDo(f"setting AXValue failed with error {error}")
        return "ax-set-value"

    def can_set_value(self, ref: Any) -> bool:
        return self._settable(ref, "AXValue")

    def focus(self, ref: Any) -> None:
        ax().AXUIElementSetAttributeValue(ref, "AXFocused", True)

    def is_focused(self, ref: Any) -> bool:
        """Whether the keyboard focus really is on this element.

        Checked before any keystroke fallback, so text can never be typed into
        whatever happened to be focused instead.
        """
        system = ax().AXUIElementCreateSystemWide()
        focused = self._one(system, "AXFocusedUIElement")
        if focused is None:
            return False
        if focused == ref:
            return True
        # Chromium nests the real editor one level below the element we focused.
        return any(child == ref for child in _iter(self._one(focused, "AXChildren")))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _iter(children: Any) -> list[Any]:
    if not children:
        return []
    try:
        return list(children)
    except TypeError:
        return []


def _label(details: dict[str, Any]) -> tuple[str, str]:
    """The best name for an element, and where it came from.

    ``AXIdentifier`` is the last resort and it is not a display name: on a
    localised macOS, settings pane text comes back as internal identifiers, of the
    shape ``<reverse.dns.bundle>*<PaneName>`` or ``<Something>_Title``. It is still
    the only handle such an element has, so it is used -- but it is flagged, so
    nothing downstream mistakes it for a name a person would read.
    """
    for key in ("AXTitle", "AXDescription", "AXHelp"):
        raw = details.get(key)
        if raw:
            text = " ".join(str(raw).split())
            if text:
                return text[:MAX_LABEL_CHARS], ""
    raw = details.get("AXIdentifier")
    if raw:
        text = " ".join(str(raw).split())
        if text:
            return text[:MAX_LABEL_CHARS], "from-identifier"
    return "", ""


def _coerce(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    text = str(value)
    return text if len(text) <= MAX_VALUE_CHARS and not text.startswith("<") else None


def is_ax_value(value: Any) -> bool:
    """Whether this really is an AXValue.

    ``AXValueGetType`` answers 0 (``kAXValueTypeIllegal``) for anything that is
    not an AXValue -- including element references and child arrays -- instead of
    raising. Testing the CoreFoundation type id is the only reliable check, and
    getting it wrong silently deletes every element in the tree.
    """
    try:
        return int(ax().CFGetTypeID(value)) == int(ax().AXValueGetTypeID())
    except Exception:
        return False


def _ax_kind(value: Any) -> int | None:
    """Which kind of AXValue this is, or None if it is not an AXValue at all."""
    if not is_ax_value(value):
        return None
    try:
        return int(ax().AXValueGetType(value))
    except Exception:
        return None


def _ax_payload(value: Any, kind_name: str) -> Any:
    try:
        ok, payload = ax().AXValueGetValue(value, getattr(ax(), kind_name), None)
    except Exception:
        return None
    return payload if ok else None


def careful_point(value: Any) -> tuple[float, float] | None:
    """A position, whether it arrives as an AXValue or as a plain pair.

    Note: ``AXPosition`` is an ``AXValueRef``. It has no ``.x`` attribute, so the
    obvious ``value.x`` raises and quietly loses every rectangle in the tree.
    """
    if isinstance(value, tuple) and len(value) == PAIR:
        # A pair from an AXValue is numeric, so float() cannot raise on it.
        # ast-grep-ignore
        return (float(value[0]), float(value[1]))
    if _ax_kind(value) != getattr(ax(), "kAXValueCGPointType", 1):
        return None
    payload = _ax_payload(value, "kAXValueCGPointType")
    if payload is None:
        return None
    try:
        return (float(payload.x), float(payload.y))
    except (AttributeError, TypeError, ValueError):
        return None


def careful_size(value: Any) -> tuple[float, float] | None:
    """A size, whether it arrives as an AXValue or as a plain pair."""
    if isinstance(value, tuple) and len(value) == PAIR:
        # As in careful_point: numeric input, so float() cannot raise.
        # ast-grep-ignore
        return (float(value[0]), float(value[1]))
    if _ax_kind(value) != getattr(ax(), "kAXValueCGSizeType", 2):
        return None
    payload = _ax_payload(value, "kAXValueCGSizeType")
    if payload is None:
        return None
    try:
        return (float(payload.width), float(payload.height))
    except (AttributeError, TypeError, ValueError):
        return None


def unwrap(value: Any) -> Any:
    """Turn an attribute value into plain Python, mapping error values to None.

    ``AXUIElementCopyMultipleAttributeValues`` answers "this attribute does not
    apply here" with an ``AXValue`` wrapping an error code. Left alone it becomes
    the string ``<AXValue ... {value = error:-25212 ...}>``, which then shows up
    as an element's subrole, as its label, and inside its identity hash.
    """
    if value is None:
        return None
    kind = _ax_kind(value)
    if kind is None:
        return value  # strings, numbers, booleans, child arrays, element refs
    if kind == getattr(ax(), "kAXValueAXErrorType", 5):
        return None
    if kind == getattr(ax(), "kAXValueCGPointType", 1):
        return careful_point(value)
    if kind == getattr(ax(), "kAXValueCGSizeType", 2):
        return careful_size(value)
    return None  # ranges and other structs are not useful as a value here


def _box(position: Any, size: Any) -> Box | None:
    point = careful_point(position)
    extent = careful_size(size)
    if point is None or extent is None:
        return None
    x, y = point
    w, h = extent
    if w <= 0 or h <= 0:
        return None
    return Box(x, y, w, h)


def _identify(role: str, subrole: str, label: str, box: Box | None, path: str = "") -> str:
    if box is None:
        place = f"at:{path}"  # no geometry: the walk path keeps siblings apart
    else:
        place = ",".join(str(round(v / GRID)) for v in (box.x, box.y, box.w, box.h))
    key = f"{role}|{subrole}|{norm_text(label)[:40]}|{place}"
    return "ax:" + sha1(key.encode()).hexdigest()[:12]


def _kind(role: str, subrole: str) -> str:
    trimmed = role.removeprefix("AX")
    if role in TEXT_ROLES:
        return "text_field"
    if subrole:
        return f"{trimmed.lower()}:{subrole.removeprefix('AX').lower()}"
    return trimmed.lower()
