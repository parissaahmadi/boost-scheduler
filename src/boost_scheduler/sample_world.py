"""A small fictional world shared by the CLI demo and the tests.

An industrial-equipment dealer with two sales teams, two prepaid wallets
(panels) shared across teams, two used machines for sale and eight competitor
listings per machine. Brands, models and people are all invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .marketplace import FakeMarketplace
from .models import Actor, Expert, Listing, Role

OWNER = Actor("owner", Role.OWNER)
MANAGER_A = Actor("manager-metal", Role.MANAGER, team="metalworking")
MANAGER_B = Actor("manager-utilities", Role.MANAGER, team="utilities")

EXPERTS = (
    Expert("nora", "Nora", "metalworking", "panel-1"),
    Expert("omar", "Omar", "metalworking", "panel-2"),
    Expert("lena", "Lena", "utilities", "panel-1"),
    Expert("ravi", "Ravi", "utilities", "panel-2"),
)

# machine id -> sales engineers who advertise it
MACHINES = {"cnc-01": ("nora", "omar"), "comp-02": ("lena", "ravi")}

# Human-readable titles (fictional brands) used only for display.
MACHINE_TITLES = {
    "cnc-01": "Vortex CNC-300 turning center (2019, 11,000 h)",
    "comp-02": "Helix RS-75 screw air compressor (2021, 7,500 h)",
}


@dataclass
class World:
    market: FakeMarketplace
    experts: tuple[Expert, ...]


def build_world(
    start: datetime,
    *,
    seed: int = 7,
    wallets: dict[str, int] | None = None,
    competitor_boost_prob: float = 0.08,
    competitors_per_car: int = 8,
) -> World:
    market = FakeMarketplace(
        boost_cost=10,
        wallets=wallets or {"panel-1": 300, "panel-2": 260},
        seed=seed,
        competitor_boost_prob=competitor_boost_prob,
    )
    experts = {e.id: e for e in EXPERTS}
    for machine_id, owners in MACHINES.items():
        # Competitors were boosted at staggered times before the simulation starts.
        for i in range(competitors_per_car):
            market.add_listing(
                Listing(
                    id=f"{machine_id}-rival-{i + 1}",
                    machine_id=machine_id,
                    owner_id=f"rival-{i + 1}",
                    is_ours=False,
                    quality=50.0 + 5 * (i % 5),
                    last_boost_at=start - timedelta(minutes=10 * (competitors_per_car - i)),
                )
            )
        for j, owner_id in enumerate(owners):
            market.add_listing(
                Listing(
                    id=f"{machine_id}-{owner_id}",
                    machine_id=machine_id,
                    owner_id=owner_id,
                    is_ours=True,
                    quality=60.0 + 12 * j,
                    last_boost_at=start - timedelta(hours=3 + j),
                    panel_id=experts[owner_id].panel_id,
                )
            )
    return World(market=market, experts=EXPERTS)
