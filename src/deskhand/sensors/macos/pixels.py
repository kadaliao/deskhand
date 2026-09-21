"""Pure pixel maths for the vision source. No macOS imports, so it all unit tests."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha1

from ...fingerprint import GRID, norm_text
from ...types import Box, Target, Verb


@dataclass(frozen=True, slots=True)
class Reading:
    """One recognised string, in normalised (0..1, bottom-left) image space."""

    text: str
    x: float
    y: float
    w: float
    h: float
    confidence: float


def to_box(reading: Reading, window: Box, image_w: float, image_h: float) -> Box:
    """Vision coordinates -> global screen points.

    Vision uses normalised coordinates with a bottom-left origin; Quartz uses
    top-left screen points, and the captured image may be at Retina scale.
    """
    local_x = reading.x * image_w
    local_y = (1.0 - reading.y - reading.h) * image_h
    scale_x = window.w / image_w
    scale_y = window.h / image_h
    return Box(
        x=window.x + local_x * scale_x,
        y=window.y + local_y * scale_y,
        w=max(1.0, reading.w * image_w * scale_x),
        h=max(1.0, reading.h * image_h * scale_y),
    )


def clean(text: str, *, limit: int = 240) -> str:
    """Collapse whitespace and clip. Keeps the decider payload bounded."""
    squeezed = " ".join(text.split())
    return squeezed[:limit]


def best_per_region(
    readings: Iterable[Reading],
    window: Box,
    *,
    image_w: float,
    image_h: float,
    overlap: float = 0.55,
) -> list[Reading]:
    """Drop competing readings of the same visual region, keep the confident one."""
    ranked = sorted(readings, key=lambda r: -r.confidence)
    kept: list[Reading] = []
    for candidate in ranked:
        box = to_box(candidate, window, image_w, image_h)
        if any(to_box(k, window, image_w, image_h).overlap(box) >= overlap for k in kept):
            continue
        kept.append(candidate)
    return kept


def target_id(reading: Reading, box: Box) -> str:
    """Identity from coarse position plus normalised words.

    Two different readings of the same control (``Whatdoyouwantto play`` vs
    ``What doyou want to plafP``) must not become two targets, and a region whose
    words changed must not keep the old identity.
    """
    words = norm_text(reading.text).replace(" ", "")
    key = f"{round(box.x / GRID)},{round(box.y / GRID)},{words[:24]}"
    return "px:" + sha1(key.encode()).hexdigest()[:12]


def as_target(reading: Reading, box: Box) -> Target:
    return Target(
        id=target_id(reading, box),
        kind="text",
        label=clean(reading.text),
        actions=frozenset({Verb.PRESS, Verb.OPEN, Verb.MENU}),
        box=box,
        source="ocr",
        visual=True,
        score=round(reading.confidence, 3),
        note="",
    )


def dedupe_targets(targets: Sequence[Target], *, overlap: float = 0.55) -> tuple[Target, ...]:
    kept: list[Target] = []
    for candidate in sorted(targets, key=lambda t: -t.score):
        if candidate.box is not None and any(
            k.box is not None and k.box.overlap(candidate.box) >= overlap for k in kept
        ):
            continue
        kept.append(candidate)
    return tuple(kept)
