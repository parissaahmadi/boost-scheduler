"""Run lifecycle rules.

The transition table is the single source of truth: both the scheduler and
the tests read it, so an illegal status change is impossible by construction.

    SCHEDULED --> ACTIVE --> COMPLETED
        |           |   \\
        v           v    v
      PAUSED <--> (PAUSED)  CANCELLED
"""

from __future__ import annotations

from .errors import InvalidTransition
from .models import Run, RunStatus

ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.SCHEDULED: frozenset(
        {RunStatus.ACTIVE, RunStatus.PAUSED, RunStatus.CANCELLED, RunStatus.COMPLETED}
    ),
    RunStatus.ACTIVE: frozenset({RunStatus.PAUSED, RunStatus.CANCELLED, RunStatus.COMPLETED}),
    RunStatus.PAUSED: frozenset(
        {RunStatus.SCHEDULED, RunStatus.ACTIVE, RunStatus.CANCELLED, RunStatus.COMPLETED}
    ),
    RunStatus.CANCELLED: frozenset(),  # terminal
    RunStatus.COMPLETED: frozenset(),  # terminal
}


def can_transition(src: RunStatus, dst: RunStatus) -> bool:
    return dst in ALLOWED[src]


def transition(run: Run, dst: RunStatus) -> RunStatus:
    """Move ``run`` to ``dst`` and return the previous status.

    Raises ``InvalidTransition`` (and leaves the run untouched) if the move
    is not allowed.
    """
    src = run.status
    if not can_transition(src, dst):
        raise InvalidTransition(f"{run.id}: cannot go from {src.value} to {dst.value}")
    run.status = dst
    return src
