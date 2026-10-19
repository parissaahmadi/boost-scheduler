"""Plain data types shared across the package.

Everything here is a small dataclass or enum: no behaviour beyond a few
convenience properties, so the rest of the code stays easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"  # may manage every team
    MANAGER = "manager"  # may manage only the experts of their own team
    SYSTEM = "system"  # the scheduler itself (automatic pauses, completion)


class RunStatus(StrEnum):
    SCHEDULED = "scheduled"  # created, window has not started yet
    ACTIVE = "active"  # inside the window, boosting
    PAUSED = "paused"  # temporarily stopped, can be resumed
    CANCELLED = "cancelled"  # terminal: ended by a command
    COMPLETED = "completed"  # terminal: window elapsed


@dataclass(frozen=True)
class Actor:
    """Whoever issues a command."""

    id: str
    role: Role
    team: str | None = None


SYSTEM_ACTOR = Actor(id="system", role=Role.SYSTEM)


@dataclass(frozen=True)
class Expert:
    """A sales expert who owns listings through a paid marketplace panel."""

    id: str
    name: str
    team: str
    panel_id: str  # wallet that pays for this expert's boosts


@dataclass(frozen=True)
class Plan:
    """How aggressively to boost.

    ``passes_required`` is the number of competitor listings that must rank
    above our best-ranked listing before the next boost is triggered.
    """

    name: str
    passes_required: int


@dataclass
class Listing:
    """An advertisement for one used machine on the marketplace."""

    id: str
    machine_id: str
    owner_id: str  # expert id for ours, competitor id otherwise
    is_ours: bool
    quality: float  # 0..100 listing-quality score
    last_boost_at: datetime
    views: int = 0
    calls: int = 0
    panel_id: str | None = None  # only set for our own listings


@dataclass
class Run:
    """One boost mission: a machine, a plan, an expert order and a time window."""

    id: str
    machine_id: str
    plan: Plan
    expert_ids: tuple[str, ...]  # order doubles as the tie-break priority
    start: datetime
    end: datetime
    created_by: str
    status: RunStatus = RunStatus.SCHEDULED
    boosts_done: int = 0
    spent: int = 0
    last_boost_at: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in (RunStatus.CANCELLED, RunStatus.COMPLETED)


@dataclass(frozen=True)
class BoostRecord:
    """What the scheduler did during one tick (returned for visibility/tests)."""

    run_id: str
    listing_id: str
    expert_id: str
    cost: int
    rank_before: int
    score: float
    at: datetime
