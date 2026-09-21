"""Fusion: many ways of seeing, one aimable view.

The rule that makes this different from stacking two perception sources:

    A pixel target that resolves, by hit-test, to an accessibility target is
    not a target at all. It becomes a label on the accessibility target.

So "use accessibility when available, pixels only where accessibility is
blind" is an invariant of the code, not a polite sentence in a prompt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .fingerprint import norm_text
from .protocols import HitTester
from .types import Box, Target

DEFAULT_OVERLAP = 0.55
"""IoU above which two readings are the same thing."""


@dataclass(frozen=True, slots=True)
class Fusion:
    targets: tuple[Target, ...]
    notes: dict[str, Any] = field(default_factory=dict)


def _better(left: Target, right: Target) -> Target:
    """Prefer the reading that can do more, then the one that has a name."""
    if len(right.actions) != len(left.actions):
        return right if len(right.actions) > len(left.actions) else left
    if bool(right.label) != bool(left.label):
        return right if right.label else left
    return right if right.score > left.score else left


def _merge_labels(base: Target, extra: Target) -> Target:
    merged = list(base.labels)
    for candidate in (extra.label, *extra.labels):
        if candidate and candidate != base.label and candidate not in merged:
            merged.append(candidate)
    if not merged:
        return base
    return replace(base, labels=tuple(merged))


def _same_anchor(left: Target, right: Target, overlap: float) -> bool:
    """Same kind, overlapping, and either same words or one of them wordless."""
    if left.kind != right.kind:
        return False
    if left.box is None or right.box is None:
        return False
    if left.box.overlap(right.box) < overlap:
        return False
    left_words, right_words = norm_text(left.spoken()), norm_text(right.spoken())
    return not left_words or not right_words or left_words == right_words


def dedupe(group: Iterable[Target], *, overlap: float = DEFAULT_OVERLAP) -> list[Target]:
    """Collapse several readings of one control inside a single source."""
    ranked = sorted(group, key=lambda t: (-len(t.actions), -t.score))
    kept: list[Target] = []
    for candidate in ranked:
        twin = next((k for k in kept if _same_anchor(k, candidate, overlap)), None)
        if twin is None:
            kept.append(candidate)
        else:
            index = kept.index(twin)
            kept[index] = _merge_labels(_better(twin, candidate), candidate)
    return kept


def fuse(
    groups: Sequence[tuple[int, tuple[Target, ...]]],
    *,
    hit: HitTester | None = None,
    overlap: float = DEFAULT_OVERLAP,
    max_hits: int = 60,
) -> Fusion:
    """Merge perception sources in trust order.

    ``groups`` is ``(rank, targets)`` per source; lower rank is more trusted.
    ``max_hits`` bounds the number of hit tests, because each one is a synchronous
    call into the target application.
    """
    ordered = sorted(groups, key=lambda pair: pair[0])
    kept: list[Target] = []
    notes: dict[str, Any] = {"sources": {}, "absorbed": 0, "recovered": 0, "hits": 0}
    cache: dict[tuple[int, int], Target | None] = {}

    def ask(x: float, y: float) -> Target | None:
        if hit is None or notes["hits"] >= max_hits:
            return None
        key = (round(x / 16), round(y / 16))
        if key not in cache:
            notes["hits"] += 1
            cache[key] = hit.hit(x, y)
        return cache[key]

    for rank, raw in ordered:
        batch = dedupe(raw, overlap=overlap)
        notes["sources"][f"rank{rank}"] = {"seen": len(raw), "kept": len(batch)}
        for candidate in batch:
            if not candidate.visual:
                twin = next((k for k in kept if _same_anchor(k, candidate, overlap)), None)
                if twin is not None:
                    kept[kept.index(twin)] = _merge_labels(_better(twin, candidate), candidate)
                    notes["absorbed"] += 1
                    continue
                kept.append(candidate)
                continue
            _place_visual(candidate, kept, ask, notes, overlap=overlap)

    notes["kept"] = len(kept)
    notes["visual"] = sum(1 for t in kept if t.visual)
    return Fusion(tuple(kept), notes)


def _place_visual(
    candidate: Target,
    kept: list[Target],
    ask: Callable[[float, float], Target | None],
    notes: dict[str, Any],
    *,
    overlap: float,
) -> None:
    """Decide what a pixel target really is. Mutates ``kept`` in place."""
    for existing in kept:
        same_place = (
            existing.box is not None
            and candidate.box is not None
            and existing.box.overlap(candidate.box) >= overlap
        )
        if same_place and (existing.visual or _same_anchor(existing, candidate, overlap)):
            kept[kept.index(existing)] = _merge_labels(existing, candidate)
            notes["absorbed"] += 1
            return

    if candidate.box is None:
        kept.append(candidate)
        return

    x, y = candidate.box.center
    resolved = ask(x, y)
    if resolved is None:
        kept.append(candidate)
        return

    known = next((k for k in kept if k.id == resolved.id), None)
    if known is not None:
        kept[kept.index(known)] = _merge_labels(known, candidate)
    elif resolved.visual:
        kept.append(candidate)
        return
    else:
        # The hit test found a real accessibility element the tree walk missed.
        # The pixels just recovered a semantic target; use it instead.
        kept.append(_merge_labels(resolved, candidate))
        notes["recovered"] += 1
    notes["absorbed"] += 1


def box_of(targets: Iterable[Target]) -> Box | None:
    """Union box of a group of targets."""
    boxes = [t.box for t in targets if t.box is not None]
    if not boxes:
        return None
    left = min(b.x for b in boxes)
    top = min(b.y for b in boxes)
    right = max(b.x + b.w for b in boxes)
    bottom = max(b.y + b.h for b in boxes)
    return Box(left, top, right - left, bottom - top)
