"""Pure pixel maths for the vision source. No macOS imports, so it all unit tests."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import blake2b, sha1

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


# --------------------------------------------------------------------------- #
# reading only what changed
# --------------------------------------------------------------------------- #

TILE = 32
"""Side of a comparison tile, in image pixels."""

Rect = tuple[int, int, int, int]
"""``(x, y, w, h)`` in image pixels, top-left origin."""


def tile_digests(
    data: bytes, width: int, height: int, stride: int, *, tile: int = TILE
) -> list[bytes]:
    """One short digest per tile of a 4-byte-per-pixel image, row-major.

    Measured: 32-66 ms for a 1728x1996 to 3840x2100 capture, against 850-1280 ms for
    recognising it -- cheap enough to run before every recognition to find out whether it
    is needed at all.
    """
    digests: list[bytes] = []
    for top in range(0, height, tile):
        rows = [
            data[y * stride : y * stride + width * 4] for y in range(top, min(top + tile, height))
        ]
        for left in range(0, width, tile):
            start, end = left * 4, min(left + tile, width) * 4
            digests.append(
                blake2b(b"".join(row[start:end] for row in rows), digest_size=8).digest()
            )
    return digests


def changed_rect(
    before: Sequence[bytes], after: Sequence[bytes], width: int, height: int, *, tile: int = TILE
) -> Rect | None:
    """The smallest rectangle holding every tile that differs, or ``None`` if none does."""
    columns = -(-width // tile)
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    if not changed:
        return None
    xs = [(i % columns) * tile for i in changed]
    ys = [(i // columns) * tile for i in changed]
    left, top = min(xs), min(ys)
    right, bottom = min(width, max(xs) + tile), min(height, max(ys) + tile)
    return left, top, right - left, bottom - top


def reading_rect(reading: Reading, image_w: float, image_h: float) -> Rect:
    """A reading's rectangle in image pixels (top-left origin), rounded outward."""
    left = math.floor(reading.x * image_w)
    top = math.floor((1.0 - reading.y - reading.h) * image_h)
    right = math.ceil((reading.x + reading.w) * image_w)
    bottom = math.ceil((1.0 - reading.y) * image_h)
    return left, top, right - left, bottom - top


def _meets(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _union(a: Rect, b: Rect) -> Rect:
    left, top = min(a[0], b[0]), min(a[1], b[1])
    right, bottom = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return left, top, right - left, bottom - top


def grow_to_cover(
    rect: Rect, readings: Iterable[Reading], image_w: int, image_h: int, *, pad: int = TILE
) -> Rect:
    """Pad the changed area, then swallow every old reading it cuts through.

    A line of text that straddles the edge of the area would otherwise be read twice: once,
    stale, from the part kept, and once, clipped, from the part re-read.
    """
    left, top = max(0, rect[0] - pad), max(0, rect[1] - pad)
    right = min(image_w, rect[0] + rect[2] + pad)
    bottom = min(image_h, rect[1] + rect[3] + pad)
    grown: Rect = (left, top, right - left, bottom - top)
    pending = [reading_rect(r, image_w, image_h) for r in readings]
    while True:
        cut = [r for r in pending if _meets(r, grown)]
        if not cut:
            return grown
        for piece in cut:
            grown = _union(grown, piece)
            pending.remove(piece)


def outside(readings: Iterable[Reading], rect: Rect, image_w: int, image_h: int) -> list[Reading]:
    """The readings that no part of ``rect`` touches: their pixels did not change."""
    return [r for r in readings if not _meets(reading_rect(r, image_w, image_h), rect)]


def reframe(reading: Reading, crop: Rect, image_w: int, image_h: int) -> Reading:
    """A reading made on a crop, re-expressed in the whole image's normalised space."""
    x0, y0, w, h = crop
    left = x0 + reading.x * w
    top = y0 + (1.0 - reading.y - reading.h) * h
    return Reading(
        text=reading.text,
        x=left / image_w,
        y=1.0 - (top + reading.h * h) / image_h,
        w=reading.w * w / image_w,
        h=reading.h * h / image_h,
        confidence=reading.confidence,
    )
