"""Shared fixtures: a tiny, fully controlled world (no random competitors)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from boost_scheduler import DEFAULT_PLANS, Scheduler
from boost_scheduler.sample_world import EXPERTS, build_world

T0 = datetime(2026, 1, 12, 9, 0, tzinfo=UTC)


@pytest.fixture
def t0() -> datetime:
    return T0


@pytest.fixture
def world():
    # competitor_boost_prob=0 -> competitors only move when a test moves them
    return build_world(T0, seed=1, competitor_boost_prob=0.0)


@pytest.fixture
def scheduler(world) -> Scheduler:
    return Scheduler(market=world.market, experts=EXPERTS, plans=DEFAULT_PLANS)


def push_down(market, machine_id: str, now: datetime, times: int) -> None:
    """Make ``times`` competitor listings boost, pushing our listings down."""
    rivals = [lst for lst in market.listings_for_machine(machine_id) if not lst.is_ours]
    rivals.sort(key=lambda lst: lst.id)
    for i, rival in enumerate(rivals[:times]):
        market._touch(rival, now + timedelta(seconds=i))  # noqa: SLF001 - test helper
