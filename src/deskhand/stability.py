"""Is this measurement of an interface, or of the person using it?

A benchmark of a window that somebody is typing in measures two things at once.
That is not a theory: a Chromium measurement in this project's own notes was taken
while a colleague was using the browser, and its spread was written up as a
property of Chromium. Five consecutive observations had returned 158, 520, 158,
519 and 158 elements, and the window titles recorded along the way had differed
between runs, which is the evidence that was sitting in the output the whole time.

This module is the small amount of bookkeeping that would have caught it: record
what each observation saw, then say plainly which of three things happened.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

SAME = "same"
MOVED = "moved"
DISTURBED = "disturbed"


@dataclass(frozen=True, slots=True)
class Sample:
    ms: int
    targets: int
    shape: str
    window: str


@dataclass(frozen=True, slots=True)
class Stability:
    samples: tuple[Sample, ...]

    @property
    def counts(self) -> list[int]:
        return [s.targets for s in self.samples]

    @property
    def titles(self) -> set[str]:
        return {s.window for s in self.samples}

    @property
    def shapes(self) -> set[str]:
        return {s.shape for s in self.samples}

    @property
    def kind(self) -> str:
        if len(self.titles) > 1:
            return DISTURBED
        # One observation, or none, cannot disagree with itself.
        return SAME if len(self.shapes) <= 1 else MOVED

    @property
    def verdict(self) -> str:
        if self.kind == DISTURBED:
            return (
                f"the window changed {len(self.titles)} times during the run, so these "
                f"numbers include someone else's activity"
            )
        if self.kind == MOVED:
            return (
                f"same window, but its contents changed on {len(self.shapes)} of "
                f"{len(self.samples)} observations: the interface itself moved"
            )
        return "same window, same contents throughout: nothing was disturbing this measurement"

    @property
    def warm_median_ms(self) -> int:
        """Median of everything after the first observation.

        The first observation of an application pays for macOS building its tree,
        which is a different cost from the one being measured here.
        """
        warm = [s.ms for s in self.samples[1:]] or [s.ms for s in self.samples]
        return int(statistics.median(warm)) if warm else 0

    def brief(self) -> dict[str, Any]:
        counts = self.counts
        return {
            "verdict": self.verdict,
            "kind": self.kind,
            "samples": len(self.samples),
            "targets": {
                "min": min(counts),
                "max": max(counts),
                "median": statistics.median(counts),
            },
            "distinct_windows": sorted(self.titles),
            "distinct_shapes": len(self.shapes),
            "first_ms": self.samples[0].ms if self.samples else 0,
            "warm_median_ms": self.warm_median_ms,
            "per_frame": [
                {"ms": s.ms, "targets": s.targets, "shape": s.shape, "window": s.window}
                for s in self.samples
            ],
        }
