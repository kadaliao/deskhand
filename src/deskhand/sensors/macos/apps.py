"""Finding the application to work on.

The sensor follows the frontmost application, so something has to make the
intended application frontmost. That is a precondition of every run, and stating
it in one place is better than hoping the caller remembers.
"""

from __future__ import annotations

from typing import Any

from ...errors import CannotDo
from .ax import appkit

REGULAR = 0
"""``NSApplicationActivationPolicyRegular``: has a Dock icon and a real window."""


def _all_apps() -> list[Any]:
    # NSWorkspace owns the list; NSRunningApplication has no class-level accessor.
    return list(appkit().NSWorkspace.sharedWorkspace().runningApplications() or [])


def _name(app: Any) -> str:
    return str(app.localizedName() or app.bundleIdentifier() or f"pid:{app.processIdentifier()}")


def _policy(app: Any) -> int:
    try:
        return int(app.activationPolicy())
    except Exception:
        return REGULAR


def _regular() -> list[Any]:
    """Applications a person would call applications.

    A bare ``runningApplications`` list contains 117 entries on a working machine,
    including Chrome helpers, Dock extras and settings extensions, and a partial
    name match happily picks one of those. Only regular applications have a
    window we could be working on.
    """
    return [app for app in _all_apps() if _policy(app) == REGULAR]


def running() -> list[str]:
    """Localised names of running applications that have a window to show."""
    return sorted({_name(app) for app in _regular()})


def frontmost() -> tuple[str, int] | None:
    app = appkit().NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    return (str(app.localizedName() or "?"), int(app.processIdentifier()))


def activate(name: str) -> str:
    """Bring a running application to the front. Returns its localised name.

    Raises with the list of candidates when nothing matches, because "app not
    found" without the alternatives is the least useful error in this domain.
    """
    wanted = name.strip().casefold()
    if not wanted:
        raise CannotDo("the application name is empty")

    apps = _regular()
    exact = [app for app in apps if _name(app).casefold() == wanted]
    matches = exact or [app for app in apps if wanted in _name(app).casefold()]
    if not matches:
        raise CannotDo(
            f"no running application matches {name!r}. Running: {sorted({_name(a) for a in apps})}"
        )

    app = matches[0]
    options = getattr(appkit(), "NSApplicationActivateIgnoringOtherApps", 1 << 1)
    activated = app.activateWithOptions_(options)
    if not activated and hasattr(app, "activate"):
        activated = bool(app.activate())
    if not activated:
        raise CannotDo(f"macOS refused to activate {_name(app)!r}")
    return _name(app)
