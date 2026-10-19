"""Authorization: who may command which experts.

Rules
-----
* OWNER and SYSTEM may act on any expert.
* A MANAGER may act only on experts of their *own* team. A command that
  touches even one foreign expert is rejected as a whole.
"""

from __future__ import annotations

from collections.abc import Iterable

from .errors import AuthorizationError
from .models import Actor, Expert, Role


def authorize_for_experts(actor: Actor, experts: Iterable[Expert]) -> None:
    """Raise ``AuthorizationError`` unless ``actor`` may act on all ``experts``."""
    if actor.role in (Role.OWNER, Role.SYSTEM):
        return
    if actor.role is Role.MANAGER and actor.team is not None:
        foreign = sorted(e.id for e in experts if e.team != actor.team)
        if foreign:
            raise AuthorizationError(
                f"manager {actor.id!r} (team {actor.team!r}) cannot act on "
                f"experts outside their team: {', '.join(foreign)}"
            )
        return
    raise AuthorizationError(f"actor {actor.id!r} has no permission to manage runs")
