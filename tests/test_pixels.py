"""Pixel geometry, testable anywhere: the macOS module imports no frameworks at
module level precisely so that this file can exist."""

from __future__ import annotations

import pytest

from deskhand.sensors.macos.pixels import (
    Reading,
    as_target,
    best_per_region,
    clean,
    target_id,
    to_box,
)
from deskhand.types import Box, Verb

WINDOW = Box(100, 200, 1000, 500)


def reading(
    text: str, x: float, y: float, w: float, h: float, *, confidence: float = 0.9
) -> Reading:
    return Reading(text=text, x=x, y=y, w=w, h=h, confidence=confidence)


class TestToBox:
    def test_vision_coordinates_become_global_points(self) -> None:
        # 2x Retina image for a 1000x500pt window, region in the middle.
        box = to_box(reading("Play", 0.5, 0.5, 0.1, 0.1), WINDOW, 2000.0, 1000.0)
        assert (box.x, box.y) == (600.0, 400.0)
        assert (box.w, box.h) == (100.0, 50.0)

    def test_the_origin_flip_puts_the_reading_at_the_bottom(self) -> None:
        top = to_box(reading("t", 0.0, 0.9, 0.1, 0.1), WINDOW, 1000.0, 500.0)
        bottom = to_box(reading("b", 0.0, 0.0, 0.1, 0.1), WINDOW, 1000.0, 500.0)
        assert top.y < bottom.y

    def test_a_region_never_collapses_to_zero_size(self) -> None:
        box = to_box(reading("x", 0.5, 0.5, 0.0, 0.0), WINDOW, 1000.0, 500.0)
        assert box.w >= 1.0
        assert box.h >= 1.0


class TestBestPerRegion:
    def test_competing_readings_of_one_region_keep_the_confident_one(self) -> None:
        strong = reading("Search", 0.1, 0.1, 0.2, 0.05, confidence=0.95)
        weak = reading("Scarch", 0.1, 0.1, 0.2, 0.05, confidence=0.5)
        kept = best_per_region([weak, strong], WINDOW, image_w=1000.0, image_h=500.0)
        assert [r.text for r in kept] == ["Search"]

    def test_separate_regions_are_all_kept(self) -> None:
        left = reading("Play", 0.05, 0.05, 0.1, 0.05)
        right = reading("Pause", 0.6, 0.05, 0.1, 0.05)
        kept = best_per_region([left, right], WINDOW, image_w=1000.0, image_h=500.0)
        assert len(kept) == 2


class TestTargets:
    def test_a_pixel_target_never_claims_to_be_typable(self) -> None:
        """The whole point: editability is not guessed from words.

        The naive version treats any region whose text looks like "search" or
        "email" as a text field. This asserts the opposite: pixel targets can be
        clicked, and being editable is something only accessibility can say.
        """
        for text in ("Search", "What do you want to play", "Email", "搜索"):
            built = as_target(reading(text, 0.0, 0.0, 0.2, 0.05), Box(0, 0, 200, 50))
            assert Verb.TYPE not in built.actions
            assert built.visual is True
            assert built.actions == frozenset({Verb.PRESS, Verb.OPEN, Verb.MENU})

    def test_identity_follows_position_and_words(self) -> None:
        here = reading("Play", 0.0, 0.0, 0.2, 0.05)
        assert target_id(here, Box(10, 10, 50, 20)) == target_id(here, Box(10, 10, 50, 20))
        assert target_id(here, Box(10, 10, 50, 20)) != target_id(here, Box(500, 10, 50, 20))
        assert target_id(here, Box(10, 10, 50, 20)) != target_id(
            reading("Pause", 0.0, 0.0, 0.2, 0.05), Box(10, 10, 50, 20)
        )

    def test_the_score_is_the_recognition_confidence(self) -> None:
        built = as_target(reading("Play", 0.0, 0.0, 0.2, 0.05, confidence=0.83), Box(0, 0, 100, 20))
        assert built.score == 0.83
        assert built.source == "ocr"


class TestClean:
    def test_whitespace_is_collapsed(self) -> None:
        assert clean("  What   do\nyou want ") == "What do you want"

    def test_very_long_readings_are_clipped(self) -> None:
        assert len(clean("x" * 400)) == 240


@pytest.mark.parametrize("score", [0.0, 1.0])
def test_best_per_region_survives_extreme_confidence(score: float) -> None:
    kept = best_per_region(
        [reading("a", 0.1, 0.1, 0.1, 0.05, confidence=score)], WINDOW, image_w=100.0, image_h=100.0
    )
    assert len(kept) == 1
