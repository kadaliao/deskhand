from __future__ import annotations

import pytest

from deskhand.fingerprint import Fingerprint, alike, digest, norm_text


def test_norm_text_collapses_punctuation_and_case() -> None:
    assert norm_text("  Q What DO you want, to  play? ") == "q what do you want to play"
    assert norm_text("") == ""
    assert norm_text("!!!") == ""


def test_alike_tolerates_ocr_noise_in_a_long_label() -> None:
    # The three readings Apple Vision actually produced for one Spotify field.
    assert alike("What do you want to play", "What doyou want to plafP")
    assert alike("Q Whatdoyouwantto play", "What do you want to play")


def test_alike_rejects_different_short_labels() -> None:
    assert not alike("Play", "Pause")
    assert not alike("Dark", "Light")
    assert not alike("", "Dark")


def test_semantic_fingerprint_requires_equality() -> None:
    left = Fingerprint("semantic", digest(["kind=button"]))
    right = Fingerprint("semantic", digest(["kind=button"]))
    other = Fingerprint("semantic", digest(["kind=slider"]))
    assert left.matches(right)
    assert not left.matches(other)


def test_visual_fingerprint_matches_a_later_reading_of_the_same_region() -> None:
    early = Fingerprint("visual", "whatever", "Play", x=10, y=20)
    later = Fingerprint("visual", "different-digest", "play", x=11, y=20)
    assert early.matches(later)


def test_visual_fingerprint_rejects_moved_or_rewritten_regions() -> None:
    early = Fingerprint("visual", "d", "Play", x=10, y=20)
    assert not early.matches(Fingerprint("visual", "d", "Pause", x=10, y=20))
    assert not early.matches(Fingerprint("visual", "d", "Play", x=40, y=20))


def test_modes_never_match_each_other() -> None:
    assert not Fingerprint("semantic", "d").matches(Fingerprint("visual", "d"))


@pytest.mark.parametrize(
    ("left", "right"),
    [("ALSO PLAY", "also play"), ("also-play", "also play")],
)
def test_norm_text_makes_casing_and_separators_irrelevant(left: str, right: str) -> None:
    assert norm_text(left) == norm_text(right)
