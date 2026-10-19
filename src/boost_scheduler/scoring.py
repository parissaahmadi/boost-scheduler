"""Pick the best listing to boost among several similar ones.

score = w_quality * quality + w_staleness * staleness + w_performance * performance

* quality      listing-quality score scaled to 0..1
* staleness    minutes since the last boost, capped, scaled to 0..1
               (a listing that has waited longer deserves the next boost)
* performance  (views + 5 * calls) relative to the best candidate, 0..1

Ties are broken by the manager-defined expert order. The result carries the
per-factor breakdown so every decision can be explained afterwards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .models import Listing

CALL_WEIGHT = 5  # one phone call is worth five views


@dataclass(frozen=True)
class ScoreWeights:
    quality: float = 0.5
    staleness: float = 0.3
    performance: float = 0.2


@dataclass(frozen=True)
class ScoredListing:
    listing: Listing
    score: float
    quality_part: float
    staleness_part: float
    performance_part: float


def rank_candidates(
    candidates: Sequence[Listing],
    expert_order: Sequence[str],
    now: datetime,
    weights: ScoreWeights | None = None,
    staleness_cap_minutes: float = 120.0,
) -> list[ScoredListing]:
    """Return candidates best-first. Empty input gives an empty list."""
    w = weights or ScoreWeights()
    if not candidates:
        return []

    raw_perf = [c.views + CALL_WEIGHT * c.calls for c in candidates]
    best_perf = max(raw_perf)
    priority = {expert_id: i for i, expert_id in enumerate(expert_order)}

    scored: list[ScoredListing] = []
    for listing, perf in zip(candidates, raw_perf, strict=True):
        quality = max(0.0, min(listing.quality, 100.0)) / 100.0
        waited = max(0.0, (now - listing.last_boost_at).total_seconds() / 60.0)
        staleness = min(waited, staleness_cap_minutes) / staleness_cap_minutes
        performance = perf / best_perf if best_perf > 0 else 0.0
        total = w.quality * quality + w.staleness * staleness + w.performance * performance
        scored.append(ScoredListing(listing, round(total, 9), quality, staleness, performance))

    scored.sort(key=lambda s: (-s.score, priority.get(s.listing.owner_id, len(priority))))
    return scored
