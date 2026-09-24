"""Recognising only what changed: the geometry, and the decision to reuse, crop or read all.

Measured on real windows (BENCHMARKS, "Reading only what changed"): Finder at rest went
from 1594 ms to 221-271 ms per fused observation with identical targets; a terminal
streaming output was re-read in a crop of 3-21% of its window.
"""

from __future__ import annotations

from typing import Any

import pytest

from deskhand.sensors.macos import ocr as ocrmod
from deskhand.sensors.macos.pixels import (
    Reading,
    changed_rect,
    grow_to_cover,
    outside,
    reading_rect,
    reframe,
    tile_digests,
)
from deskhand.types import Box

W, H = 128, 64  # four tiles across, two down


def image(fill: int = 0, *, dirty: tuple[int, int] | None = None) -> bytes:
    pixels = bytearray([fill] * (W * H * 4))
    if dirty is not None:
        x, y = dirty
        pixels[(y * W + x) * 4] = 255
    return bytes(pixels)


def reading(text: str, x: float, y: float, w: float = 0.1, h: float = 0.1) -> Reading:
    return Reading(text=text, x=x, y=y, w=w, h=h, confidence=0.9)


class TestTiles:
    def test_identical_images_have_no_changed_area(self) -> None:
        a = tile_digests(image(), W, H, W * 4)
        assert changed_rect(a, tile_digests(image(), W, H, W * 4), W, H) is None

    def test_one_pixel_marks_exactly_its_tile(self) -> None:
        a = tile_digests(image(), W, H, W * 4)
        b = tile_digests(image(dirty=(70, 40)), W, H, W * 4)
        assert changed_rect(a, b, W, H) == (64, 32, 32, 32)

    def test_padding_bytes_at_the_end_of_a_row_are_ignored(self) -> None:
        stride = W * 4 + 16
        a = bytearray(stride * H)
        b = bytearray(a)
        b[W * 4 + 3] = 9  # in the padding of the first row
        assert tile_digests(bytes(a), W, H, stride) == tile_digests(bytes(b), W, H, stride)


class TestCoordinates:
    def test_a_crop_reading_lands_where_the_whole_image_would_put_it(self) -> None:
        crop = (32, 16, 64, 32)
        # The whole crop, bottom-left origin -> the crop's own rectangle in the image.
        whole = reframe(reading("x", 0.0, 0.0, 1.0, 1.0), crop, W, H)
        assert reading_rect(whole, W, H) == crop

    def test_top_left_quarter_of_a_crop(self) -> None:
        crop = (32, 16, 64, 32)
        quarter = reframe(reading("x", 0.0, 0.5, 0.5, 0.5), crop, W, H)
        assert reading_rect(quarter, W, H) == (32, 16, 32, 16)


class TestWhatIsKept:
    def test_a_line_cut_by_the_changed_area_is_swallowed_whole(self) -> None:
        line = reading("a long line", 0.0, 0.5, 1.0, 0.1)  # spans the full width
        grown = grow_to_cover((64, 0, 32, 32), [line], W, H, pad=0)
        assert grown[0] == 0 and grown[2] == W

    def test_readings_away_from_the_change_are_kept_and_the_rest_dropped(self) -> None:
        far = reading("far", 0.0, 0.0)
        near = reading("near", 0.6, 0.6)
        kept = outside([far, near], reading_rect(near, W, H), W, H)
        assert [r.text for r in kept] == ["far"]


class FakeQuartz:
    def CGImageCreateWithImageInRect(self, image: Any, rect: Any) -> Any:
        return ("crop", rect)

    def CGRectMake(self, *parts: int) -> tuple[int, ...]:
        return parts


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> tuple[ocrmod.OCRSource, list[Any]]:
    """An OCRSource whose capture is a list of tile digests and whose reader is a log."""
    calls: list[Any] = []
    monkeypatch.setattr(ocrmod, "_digests", lambda image, w, h: image["digests"])
    monkeypatch.setattr(ocrmod, "_quartz", FakeQuartz)

    def read(image: Any, w: float, h: float, src: Any, *, scale: float = 1.0) -> list[Reading]:
        calls.append(image if isinstance(image, tuple) else "full")
        if isinstance(image, tuple):
            return [reading("new", 0.0, 0.0, 1.0, 1.0)]
        return list(image["readings"])

    monkeypatch.setattr(ocrmod, "_read", read)
    return ocrmod.OCRSource(), calls


WINDOW = Box(0, 0, W, H)


def frame(dirty: tuple[int, int] | None, readings: list[Reading] | None = None) -> dict[str, Any]:
    return {"digests": tile_digests(image(dirty=dirty), W, H, W * 4), "readings": readings or []}


class TestTheDecision:
    def test_the_first_look_reads_everything(self, source: tuple[Any, list[Any]]) -> None:
        src, calls = source
        src._read_changed(1, WINDOW, frame(None), W, H)
        assert calls == ["full"] and src.last_read == "full"

    def test_nothing_changed_reads_nothing(self, source: tuple[Any, list[Any]]) -> None:
        src, calls = source
        kept = [reading("kept", 0.0, 0.0)]
        src._read_changed(1, WINDOW, frame(None, kept), W, H)
        again = src._read_changed(1, WINDOW, frame(None), W, H)
        assert calls == ["full"]
        assert again == kept and src.last_read == "reused"

    def test_a_small_change_reads_only_its_crop(self, source: tuple[Any, list[Any]]) -> None:
        src, calls = source
        far = reading("far", 0.0, 0.0, 0.1, 0.1)  # bottom-left corner
        src._read_changed(1, WINDOW, frame(None, [far]), W, H)
        merged = src._read_changed(1, WINDOW, frame((120, 2)), W, H)  # top-right tile
        assert calls[0] == "full" and calls[1][0] == "crop"
        assert {r.text for r in merged} == {"far", "new"}
        assert src.last_read.startswith("partial")

    def test_another_window_or_a_move_reads_everything(self, source: tuple[Any, list[Any]]) -> None:
        src, calls = source
        src._read_changed(1, WINDOW, frame(None), W, H)
        src._read_changed(2, WINDOW, frame(None), W, H)
        src._read_changed(2, Box(5, 0, W, H), frame(None), W, H)
        assert calls == ["full", "full", "full"]

    def test_a_large_change_reads_everything(self, source: tuple[Any, list[Any]]) -> None:
        src, calls = source
        src.partial_max_share = 0.1
        src._read_changed(1, WINDOW, frame(None), W, H)
        src._read_changed(1, WINDOW, frame((70, 40)), W, H)
        assert calls == ["full", "full"]
