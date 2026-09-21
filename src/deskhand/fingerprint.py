"""Signatures and text comparison used for freshness and progress.

Two different questions need two different digests:

* *structure* changes -> the UI is no longer what we observed (used for settling).
* *content* changes   -> something actually happened (used for progress).

Neither digest may depend on a source's opaque element id: accessibility ids can
churn between observations, and vision ids are derived from pixel geometry. Both
differences would otherwise look like "the desktop changed" forever.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

DEFAULT_ALIKE = 0.5
"""Minimum bigram overlap for two vision readings to count as the same text."""

GRID = 4.0
"""Pixel grid used to absorb sub-pixel jitter when hashing geometry."""

DRIFT = 8
"""Grid cells a vision reading may move before it counts as a different control."""

MIN_PACKED_LEN = 2
"""Below this, a string has no bigrams and is compared as-is."""


def norm_text(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    OCR of the same control alternates between ``Q What do you want to play``,
    ``What doyou want to plafP`` and ``Whatdoyouwantto play``. Comparing
    normalised text is what keeps those from looking like different controls.
    """
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in text).split())


def _bigrams(text: str) -> frozenset[str]:
    packed = norm_text(text).replace(" ", "")
    if len(packed) < MIN_PACKED_LEN:
        return frozenset({packed}) if packed else frozenset()
    return frozenset(packed[i : i + 2] for i in range(len(packed) - 1))


def alike(left: str, right: str, *, threshold: float = DEFAULT_ALIKE) -> bool:
    """Cheap similarity test, used instead of equality for vision readings."""
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return a == b
    if a == b:
        return True
    shared = len(a & b) / len(a | b)
    return shared >= threshold


def grid(value: float) -> int:
    return round(value / GRID)


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """What a target looked like when a decision was made.

    ``semantic`` targets must still exist with the same meaning. ``visual``
    targets only live in pixels, so they are matched loosely: same rough
    neighbourhood and text that still reads alike.
    """

    mode: Literal["semantic", "visual"]
    digest: str
    text: str = ""
    x: int = 0
    y: int = 0

    def matches(self, other: Fingerprint) -> bool:
        if self.mode != other.mode:
            return False
        if self.mode == "semantic":
            return self.digest == other.digest
        if abs(self.x - other.x) > DRIFT or abs(self.y - other.y) > DRIFT:
            return False
        return alike(self.text, other.text)


def digest(rows: Iterable[str]) -> str:
    return sha256("\n".join(rows).encode()).hexdigest()[:16]
