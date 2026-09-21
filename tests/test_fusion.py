from __future__ import annotations

from deskhand.fusion import dedupe, fuse
from deskhand.types import Box, Target, Verb


def semantic(
    target_id: str, label: str, *, box: Box | None = None, actions: frozenset[Verb] | None = None
) -> Target:
    return Target(
        id=target_id,
        kind="button",
        label=label,
        box=box,
        actions=actions if actions is not None else frozenset({Verb.PRESS}),
        source="ax",
    )


def pixels(label: str, *, box: Box, score: float = 0.9, target_id: str | None = None) -> Target:
    return Target(
        id=target_id or f"px:{label}",
        kind="text",
        label=label,
        box=box,
        actions=frozenset({Verb.PRESS, Verb.OPEN, Verb.MENU}),
        source="ocr",
        visual=True,
        score=score,
    )


class Hit:
    """A hit tester that answers from a fixed map."""

    def __init__(self, answers: dict[tuple[float, float], Target | None] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[float, float]] = []

    def hit(self, x: float, y: float) -> Target | None:
        self.calls.append((x, y))
        return self.answers.get((x, y))


class TestDedupe:
    def test_two_readings_of_one_region_collapse_to_the_richer_one(self) -> None:
        region = Box(0, 0, 100, 20)
        thin = semantic("a", "Play", box=region)
        rich = semantic("b", "Play", box=region, actions=frozenset({Verb.PRESS, Verb.OPEN}))
        kept = dedupe([thin, rich])
        assert len(kept) == 1
        assert kept[0].actions == frozenset({Verb.PRESS, Verb.OPEN})

    def test_a_wordless_reading_gains_a_name_from_its_twin(self) -> None:
        # Accessibility often exposes the control and the pixels supply the words.
        region = Box(0, 0, 100, 20)
        kept = dedupe([semantic("a", "", box=region), semantic("b", "Play", box=region)])
        assert len(kept) == 1
        assert kept[0].label == "Play"

    def test_a_reading_that_can_do_more_wins(self) -> None:
        region = Box(0, 0, 100, 20)
        kept = dedupe(
            [
                semantic("a", "Play", box=region),
                semantic("b", "Play", box=region, actions=frozenset({Verb.PRESS, Verb.OPEN})),
            ]
        )
        assert kept[0].actions == frozenset({Verb.PRESS, Verb.OPEN})

    def test_different_controls_are_left_alone(self) -> None:
        kept = dedupe(
            [
                semantic("a", "Play", box=Box(0, 0, 50, 20)),
                semantic("b", "Pause", box=Box(200, 0, 50, 20)),
            ]
        )
        assert len(kept) == 2


class TestFusion:
    def test_pixels_never_survive_over_an_accessibility_element(self) -> None:
        field = semantic(
            "ax:field", "Search", box=Box(0, 0, 200, 30), actions=frozenset({Verb.PRESS, Verb.TYPE})
        )
        # The hit test returns the same accessibility element.
        fused = fuse(
            [
                (0, (field,)),
                (10, (pixels("Search", box=Box(6, 5, 120, 18), target_id="px:search"),)),
            ],
            hit=Hit({(66.0, 14.0): field}),
        )
        assert not any(t.visual for t in fused.targets)
        assert len(fused.targets) == 1
        assert fused.targets[0].id == "ax:field"
        assert fused.notes["absorbed"] == 1

    def test_the_recognised_words_become_another_name_for_the_control(self) -> None:
        field = semantic("ax:field", "Search", box=Box(0, 0, 200, 30))
        fused = fuse(
            [
                (0, (field,)),
                (10, (pixels("Search Effects", box=Box(6, 5, 120, 18), target_id="px:s"),)),
            ],
            hit=Hit({(66.0, 14.0): field}),
        )
        assert fused.targets[0].labels == ("Search Effects",)

    def test_a_hit_recovers_a_semantic_target_the_walk_missed(self) -> None:
        recovered = semantic("ax:hidden", "Export", box=Box(0, 0, 80, 24))
        fused = fuse(
            [(0, ()), (10, (pixels("Export", box=Box(4, 4, 60, 16), target_id="px:e"),))],
            hit=Hit({(34.0, 12.0): recovered}),
        )
        assert [t.id for t in fused.targets] == ["ax:hidden"]
        assert fused.targets[0].visual is False
        assert fused.notes["recovered"] == 1

    def test_pixels_are_kept_when_nothing_is_there(self) -> None:
        fused = fuse(
            [(0, ()), (10, (pixels("Play", box=Box(0, 0, 40, 16), target_id="px:p"),))],
            hit=Hit({(20.0, 8.0): None}),
        )
        assert [t.id for t in fused.targets] == ["px:p"]
        assert fused.targets[0].visual is True

    def test_pixels_are_kept_when_there_is_no_hit_tester(self) -> None:
        fused = fuse(
            [(0, ()), (10, (pixels("Play", box=Box(0, 0, 40, 16), target_id="px:p"),))], hit=None
        )
        assert fused.targets[0].visual is True
        assert fused.notes["hits"] == 0

    def test_hit_tests_are_bounded_and_reused(self) -> None:
        hit = Hit({})
        targets = tuple(
            pixels(f"w{i}", box=Box(i * 40, 0, 30, 16), target_id=f"px:{i}") for i in range(5)
        )
        fused = fuse([(0, ()), (10, targets)], hit=hit, max_hits=2)
        assert fused.notes["hits"] == 2
        assert len(hit.calls) == 2
        assert len(fused.targets) == 5

    def test_rank_order_decides_who_wins(self) -> None:
        both = (semantic("a", "Play", box=Box(0, 0, 100, 20), actions=frozenset({Verb.PRESS})),)
        low = fuse([(10, both), (0, both)])
        assert len(low.targets) == 1
