"""Which languages the pixel layer recognises, and why that is a correctness question.

The bug this pins was invisible on an English desktop and total on a Chinese one: with no
languages set, Vision recognises English, so on a `zh-Hans` macOS it read 24 regions of
English fragments and noise and **not one row of the Settings sidebar** -- the exact
controls the pixel layer exists to name. Asking for the machine's own languages read 48,
including `外观`, `通用` and `辅助功能`.
"""

from __future__ import annotations

import pytest

from deskhand.sensors.macos import ocr


class FakeAppKit:
    class NSLocale:
        @staticmethod
        def preferredLanguages() -> list[str]:
            return ["zh-Hans-SG", "en-SG"]


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "_appkit", lambda: FakeAppKit)


class TestPreferredLanguages:
    def test_the_machine_is_asked_what_it_reads(self, mac: None) -> None:
        assert ocr.preferred_languages() == ["zh-Hans-SG", "en-SG"]

    def test_a_machine_with_no_answer_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> object:
            raise RuntimeError("no AppKit")

        monkeypatch.setattr(ocr, "_appkit", boom)
        assert ocr.preferred_languages() == []


class TestWhatASourceAsksFor:
    def test_by_default_it_asks_the_machine(self, mac: None) -> None:
        assert ocr.OCRSource()._recognise() == ["zh-Hans-SG", "en-SG"]

    def test_an_explicit_list_is_used_as_given(self) -> None:
        assert ocr.OCRSource(languages=("en-US", "ja-JP"))._recognise() == ["en-US", "ja-JP"]

    def test_an_explicit_empty_list_means_let_vision_decide(self) -> None:
        """The two are different on purpose: asking the machine, versus asking nobody.

        An ablation needs "recognise whatever Vision does by default", which is not the
        same as "ask the machine first".
        """
        assert ocr.OCRSource(languages=())._recognise() == []

    def test_the_default_is_not_the_same_as_empty(self, mac: None) -> None:
        assert ocr.OCRSource()._recognise() != ocr.OCRSource(languages=())._recognise()


class TestTheDefaultLevel:
    """`fast` is not a cheaper version of the same reading on a localised interface.

    Measured on one Chinese System Settings window, same capture:

    | level | languages | regions | sidebar labels | ms |
    |---|---|---|---|---|
    | fast | unset (English) | 24 | 0 | 154 |
    | fast | system | 16 | 0 | 154 |
    | accurate | system | 40 | 9 | 540 |

    Only the last one can name `通用`, `外观` and `辅助功能`, which are exactly the
    controls the pixel layer is for.
    """

    def test_the_default_is_accurate(self) -> None:
        assert ocr.OCRSource().level == "accurate"

    def test_fast_is_still_available_for_an_ablation(self) -> None:
        assert ocr.OCRSource(level="fast").level == "fast"

    def test_an_unknown_level_is_still_refused(self) -> None:
        with pytest.raises(ValueError, match="level must be"):
            ocr.OCRSource(level="quick")
