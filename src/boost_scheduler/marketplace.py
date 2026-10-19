"""Marketplace port + an in-memory fake used by the demo and the tests.

The scheduler depends only on the ``Marketplace`` protocol, so a real HTTP
client could replace ``FakeMarketplace`` without touching any business rule.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Protocol

from .errors import InsufficientFunds, NotFound
from .models import Listing


class Marketplace(Protocol):
    def listings_for_machine(self, machine_id: str) -> list[Listing]: ...

    def rank_of(self, listing_id: str) -> int:
        """0-based position in the search results (0 = top)."""
        ...

    def boost_cost(self) -> int: ...

    def balance(self, panel_id: str) -> int: ...

    def boost(self, listing_id: str, now: datetime) -> int:
        """Boost one of *our* listings and return the amount charged."""
        ...


class FakeMarketplace:
    """Deterministic in-memory marketplace (seeded RNG).

    Search results are ordered by most recent boost first, which is how many
    classified-ads sites rank "newest" results.
    """

    def __init__(
        self,
        *,
        boost_cost: int = 10,
        wallets: dict[str, int] | None = None,
        seed: int = 0,
        competitor_boost_prob: float = 0.08,
    ) -> None:
        self._cost = boost_cost
        self._wallets = dict(wallets or {})
        self._rng = random.Random(seed)
        self._p = competitor_boost_prob
        self._listings: dict[str, Listing] = {}
        self._seq: dict[str, int] = {}  # tie-break: later touch ranks higher
        self._counter = 0

    # -- setup ----------------------------------------------------------
    def add_listing(self, listing: Listing) -> None:
        self._listings[listing.id] = listing
        self._touch_seq(listing.id)

    def set_balance(self, panel_id: str, amount: int) -> None:
        self._wallets[panel_id] = amount

    # -- Marketplace protocol -------------------------------------------
    def listings_for_machine(self, machine_id: str) -> list[Listing]:
        return [lst for lst in self._listings.values() if lst.machine_id == machine_id]

    def ranking(self, machine_id: str) -> list[Listing]:
        return sorted(
            self.listings_for_machine(machine_id),
            key=lambda lst: (lst.last_boost_at, self._seq[lst.id]),
            reverse=True,
        )

    def rank_of(self, listing_id: str) -> int:
        listing = self._get(listing_id)
        return [lst.id for lst in self.ranking(listing.machine_id)].index(listing_id)

    def boost_cost(self) -> int:
        return self._cost

    def balance(self, panel_id: str) -> int:
        return self._wallets.get(panel_id, 0)

    def boost(self, listing_id: str, now: datetime) -> int:
        listing = self._get(listing_id)
        if not listing.is_ours or listing.panel_id is None:
            raise ValueError(f"{listing_id} is not one of our listings")
        if self.balance(listing.panel_id) < self._cost:
            raise InsufficientFunds(f"panel {listing.panel_id} cannot afford a boost")
        self._wallets[listing.panel_id] -= self._cost
        self._touch(listing, now)
        return self._cost

    # -- simulation helpers ---------------------------------------------
    def simulate_step(self, now: datetime) -> None:
        """Advance the fake world one step: competitors boost, engagement accrues."""
        for listing in sorted(self._listings.values(), key=lambda lst: lst.id):
            if listing.is_ours:
                rank = self.rank_of(listing.id)
                listing.views += self._rng.randint(0, max(0, 8 - rank))
                if self._rng.random() < 0.05 / (1 + rank):
                    listing.calls += 1
            elif self._rng.random() < self._p:
                self._touch(listing, now)  # competitors boost for free in the fake

    # -- internals --------------------------------------------------------
    def _get(self, listing_id: str) -> Listing:
        try:
            return self._listings[listing_id]
        except KeyError:
            raise NotFound(f"unknown listing {listing_id!r}") from None

    def _touch_seq(self, listing_id: str) -> None:
        self._counter += 1
        self._seq[listing_id] = self._counter

    def _touch(self, listing: Listing, now: datetime) -> None:
        listing.last_boost_at = now
        self._touch_seq(listing.id)
