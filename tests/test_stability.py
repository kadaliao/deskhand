"""Telling a measurement of an interface apart from a measurement of its user.

Synthetic samples, because the point is the verdict logic: the three outcomes have
different consequences and the difference between them is visible in the data that
was already being collected.
"""

from __future__ import annotations

from deskhand.stability import DISTURBED, MOVED, SAME, Sample, Stability


def sample(*, ms: int = 10, targets: int = 13, shape: str = "aaa", window: str = "w") -> Sample:
    return Sample(ms=ms, targets=targets, shape=shape, window=window)


class TestVerdicts:
    def test_a_steady_window_is_reported_as_undisturbed(self) -> None:
        report = Stability(tuple(sample() for _ in range(6)))
        assert report.kind == SAME
        assert "nothing was disturbing" in report.verdict

    def test_a_changing_window_title_means_somebody_else_was_there(self) -> None:
        samples = [sample() for _ in range(4)]
        samples.append(sample(window="somewhere else"))
        report = Stability(tuple(samples))
        assert report.kind == DISTURBED
        assert "someone else's activity" in report.verdict

    def test_the_same_window_changing_contents_is_reported_differently(self) -> None:
        samples = [sample(), sample(), sample(shape="bbb"), sample(shape="bbb")]
        report = Stability(tuple(samples))
        assert report.kind == MOVED
        assert "the interface itself moved" in report.verdict
        assert "someone else" not in report.verdict

    def test_a_changing_window_wins_over_a_changing_shape(self) -> None:
        # Both happened. The one that invalidates the measurement is the window.
        report = Stability((sample(), sample(shape="bbb", window="elsewhere")))
        assert report.kind == DISTURBED


class TestNumbers:
    def test_the_cold_first_observation_is_excluded_from_the_warm_median(self) -> None:
        samples = [sample(ms=320), sample(ms=9), sample(ms=10), sample(ms=11)]
        report = Stability(tuple(samples))
        assert report.warm_median_ms == 10
        assert report.brief()["first_ms"] == 320

    def test_a_single_observation_still_reports_a_median(self) -> None:
        report = Stability((sample(ms=42),))
        assert report.warm_median_ms == 42
        assert report.kind == SAME

    def test_no_observations_is_not_a_crash(self) -> None:
        report = Stability(())
        assert report.kind == SAME
        assert report.warm_median_ms == 0

    def test_the_report_carries_the_range_and_every_frame(self) -> None:
        report = Stability((sample(targets=13), sample(targets=520), sample(targets=158)))
        brief = report.brief()
        assert brief["targets"] == {"min": 13, "max": 520, "median": 158}
        assert len(brief["per_frame"]) == 3
        assert brief["distinct_shapes"] == 1
