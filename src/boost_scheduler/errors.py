"""Exception hierarchy for the scheduler.

Every failure the scheduler can *deliberately* raise derives from
``SchedulerError`` so callers can catch one base class.
"""

from __future__ import annotations


class SchedulerError(Exception):
    """Base class for all scheduler errors."""


class AuthorizationError(SchedulerError):
    """The actor is not allowed to perform the requested command."""


class PreconditionFailed(SchedulerError):  # noqa: N818 - reads better than "...Error"
    """One or more business preconditions were not met.

    All reasons are collected and reported together, and *nothing* is
    changed when this is raised (commands are all-or-nothing).
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


class InvalidTransition(SchedulerError):
    """The requested run status change is not allowed by the state machine."""


class InsufficientFunds(SchedulerError):
    """A panel wallet cannot cover the cost of a boost."""


class NotFound(SchedulerError):
    """A referenced entity (run, listing, ...) does not exist."""
