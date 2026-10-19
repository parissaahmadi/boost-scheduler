from datetime import timedelta

from conftest import T0

from boost_scheduler import Listing
from boost_scheduler.scoring import ScoreWeights, rank_candidates


def listing(owner: str, *, quality=50.0, waited_min=0, views=0, calls=0) -> Listing:
    return Listing(
        id=f"l-{owner}", machine_id="machine", owner_id=owner, is_ours=True, quality=quality,
        last_boost_at=T0 - timedelta(minutes=waited_min), views=views, calls=calls,
        panel_id="p",
    )


def test_empty_input_gives_empty_output():
    assert rank_candidates([], [], T0) == []


def test_higher_quality_wins_when_everything_else_is_equal():
    ranked = rank_candidates([listing("a", quality=40), listing("b", quality=90)], ["a", "b"], T0)
    assert [r.listing.owner_id for r in ranked] == ["b", "a"]


def test_longer_wait_wins():
    ranked = rank_candidates([listing("a", waited_min=10), listing("b", waited_min=100)],
                             ["a", "b"], T0)
    assert ranked[0].listing.owner_id == "b"


def test_calls_count_more_than_views():
    ranked = rank_candidates(
        [listing("a", views=40), listing("b", views=0, calls=10)], ["a", "b"], T0,
        weights=ScoreWeights(quality=0, staleness=0, performance=1),
    )
    assert ranked[0].listing.owner_id == "b"


def test_staleness_is_capped():
    ranked = rank_candidates([listing("a", waited_min=500)], ["a"], T0)
    assert ranked[0].staleness_part == 1.0


def test_ties_follow_the_expert_order():
    a, b = listing("a"), listing("b")
    assert rank_candidates([a, b], ["b", "a"], T0)[0].listing.owner_id == "b"
    assert rank_candidates([a, b], ["a", "b"], T0)[0].listing.owner_id == "a"


def test_breakdown_adds_up_to_score():
    r = rank_candidates([listing("a", quality=80, waited_min=60, views=5)], ["a"], T0)[0]
    w = ScoreWeights()
    expected = (w.quality * r.quality_part + w.staleness * r.staleness_part
                + w.performance * r.performance_part)
    assert abs(r.score - expected) < 1e-6
