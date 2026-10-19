"""The orchestrator: commands in, boosts and audit events out.

Design rules
------------
* Time is always passed in (``now``). Nothing reads the wall clock, so the
  whole system is deterministic and trivially testable.
* Commands are all-or-nothing: every check runs *before* any state changes.
  A rejected command is itself written to the audit log.
* The scheduler never talks HTTP; it uses the ``Marketplace`` protocol.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import state_machine
from .audit import AuditLog
from .authz import authorize_for_experts
from .errors import (
    AuthorizationError,
    InsufficientFunds,
    InvalidTransition,
    NotFound,
    PreconditionFailed,
)
from .marketplace import Marketplace
from .models import (
    SYSTEM_ACTOR,
    Actor,
    BoostRecord,
    Expert,
    Plan,
    Role,
    Run,
    RunStatus,
)
from .scoring import ScoreWeights, rank_candidates

DEFAULT_PLANS = (
    Plan("aggressive", passes_required=1),
    Plan("standard", passes_required=3),
    Plan("relaxed", passes_required=5),
)


@dataclass(frozen=True)
class SchedulerConfig:
    min_boost_interval: timedelta = timedelta(minutes=5)  # per-listing cooldown
    low_balance_threshold: int = 30                       # warn when wallet drops below
    weights: ScoreWeights = ScoreWeights()


class Scheduler:
    def __init__(
        self,
        *,
        market: Marketplace,
        experts: Iterable[Expert],
        plans: Iterable[Plan] = DEFAULT_PLANS,
        audit: AuditLog | None = None,
        config: SchedulerConfig | None = None,
    ) -> None:
        self._market = market
        self._experts = {e.id: e for e in experts}
        self._plans = {p.name: p for p in plans}
        self.audit = audit or AuditLog()
        self._config = config or SchedulerConfig()
        self._runs: dict[str, Run] = {}
        self._run_seq = 0
        self._low_balance_warned: set[str] = set()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @property
    def runs(self) -> dict[str, Run]:
        return dict(self._runs)

    def get_run(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError:
            raise NotFound(f"unknown run {run_id!r}") from None

    # ------------------------------------------------------------------
    # Commands (issued by people)
    # ------------------------------------------------------------------
    def create_run(
        self,
        actor: Actor,
        *,
        machine_id: str,
        plan_name: str,
        expert_ids: Iterable[str],
        start: datetime,
        end: datetime,
        now: datetime,
        replace: bool = False,
    ) -> Run:
        """Create a run after validating *every* precondition.

        Raises ``AuthorizationError`` or ``PreconditionFailed``; in both cases
        nothing is created or modified, and the rejection is audited.
        """
        expert_ids = tuple(expert_ids)
        reasons: list[str] = []

        known: list[Expert] = []
        for expert_id in expert_ids:
            expert = self._experts.get(expert_id)
            if expert is None:
                reasons.append(f"unknown expert: {expert_id}")
            else:
                known.append(expert)
        if not expert_ids:
            reasons.append("at least one expert is required")
        if len(set(expert_ids)) != len(expert_ids):
            reasons.append("duplicate expert ids")

        # Authorization is a hard failure and is checked before anything else.
        self._authorize(actor, known, now, "create_run", machine_id=machine_id)

        plan = self._plans.get(plan_name)
        if plan is None:
            reasons.append(f"unknown plan: {plan_name}")

        if start.tzinfo is None or end.tzinfo is None or now.tzinfo is None:
            reasons.append("start, end and now must be timezone-aware")
        else:
            if end <= start:
                reasons.append("window end must be after window start")
            if end <= now:
                reasons.append("window end is already in the past")

        our_owners = {
            lst.owner_id for lst in self._market.listings_for_machine(machine_id) if lst.is_ours
        }
        for expert in known:
            if expert.id not in our_owners:
                reasons.append(f"{expert.id} has no listing for machine {machine_id}")

        cost = self._market.boost_cost()
        for panel_id in sorted({e.panel_id for e in known}):
            if self._market.balance(panel_id) < cost:
                reasons.append(f"panel {panel_id} cannot afford even one boost")

        conflicts = [
            r for r in self._runs.values() if not r.is_terminal and r.machine_id == machine_id
        ]
        if conflicts and not replace:
            ids = ", ".join(r.id for r in conflicts)
            reasons.append(f"machine {machine_id} already has a live run ({ids}); use replace=True")
        if conflicts and replace:
            for old in conflicts:  # the actor must also be allowed to cancel what it replaces
                self._authorize(
                    actor, self._run_experts(old), now, "replace_run", machine_id=machine_id
                )

        if reasons:
            self._reject(actor, now, "create_run", reasons, machine_id=machine_id)
            raise PreconditionFailed(reasons)

        # ---- all checks passed: from here on we mutate ----
        assert plan is not None
        for old in conflicts:
            self._apply(old, RunStatus.CANCELLED, actor, now, "run_cancelled", reason="replaced")
        self._run_seq += 1
        run = Run(
            id=f"run-{self._run_seq}",
            machine_id=machine_id,
            plan=plan,
            expert_ids=expert_ids,
            start=start,
            end=end,
            created_by=actor.id,
        )
        self._runs[run.id] = run
        self.audit.record(
            ts=now,
            actor_id=actor.id,
            action="run_created",
            run_id=run.id,
            machine_id=machine_id,
            plan=plan.name,
            experts=list(expert_ids),
            start=start.isoformat(),
            end=end.isoformat(),
            replaced=[r.id for r in conflicts],
        )
        return run

    def pause_run(self, actor: Actor, run_id: str, now: datetime, reason: str = "manual") -> Run:
        run = self.get_run(run_id)
        self._authorize(actor, self._run_experts(run), now, "pause_run", run_id=run_id)
        self._apply(run, RunStatus.PAUSED, actor, now, "run_paused", reason=reason)
        return run

    def resume_run(self, actor: Actor, run_id: str, now: datetime) -> Run:
        run = self.get_run(run_id)
        self._authorize(actor, self._run_experts(run), now, "resume_run", run_id=run_id)
        if run.status is not RunStatus.PAUSED:
            self._reject(actor, now, "resume_run", [f"run is {run.status.value}, not paused"],
                         run_id=run_id)
            raise PreconditionFailed([f"{run_id} is {run.status.value}, not paused"])
        if now >= run.end:
            self._reject(actor, now, "resume_run", ["window already elapsed"], run_id=run_id)
            raise PreconditionFailed([f"{run_id}: window already elapsed"])
        target = RunStatus.SCHEDULED if now < run.start else RunStatus.ACTIVE
        self._apply(run, target, actor, now, "run_resumed", to=target.value)
        return run

    def cancel_run(self, actor: Actor, run_id: str, now: datetime, reason: str = "manual") -> Run:
        run = self.get_run(run_id)
        self._authorize(actor, self._run_experts(run), now, "cancel_run", run_id=run_id)
        self._apply(run, RunStatus.CANCELLED, actor, now, "run_cancelled", reason=reason)
        return run

    def stop_all(self, actor: Actor, now: datetime, team: str | None = None) -> list[str]:
        """Pause every live run in scope (an owner's "global stop").

        * OWNER: all runs, or only ``team`` if given.
        * MANAGER: only their own team (asking for another team is rejected).
        """
        if actor.role is Role.MANAGER:
            if team is not None and team != actor.team:
                reason = f"manager {actor.id!r} cannot stop team {team!r}"
                self._reject(actor, now, "stop_all", [reason])
                raise AuthorizationError(reason)
            team = actor.team
        elif actor.role is not Role.OWNER:
            self._authorize(actor, [], now, "stop_all")

        paused: list[str] = []
        for run in sorted(self._runs.values(), key=lambda r: r.id):
            if run.status not in (RunStatus.SCHEDULED, RunStatus.ACTIVE):
                continue
            experts = self._run_experts(run)
            if team is not None and any(e.team != team for e in experts):
                continue  # not entirely inside the requested scope
            self._apply(run, RunStatus.PAUSED, actor, now, "run_paused", reason="stop_all")
            paused.append(run.id)
        self.audit.record(ts=now, actor_id=actor.id, action="stop_all", team=team, paused=paused)
        return paused

    # ------------------------------------------------------------------
    # The clock: called periodically by whoever drives the scheduler
    # ------------------------------------------------------------------
    def tick(self, now: datetime) -> list[BoostRecord]:
        """Advance every live run to ``now`` and execute due boosts."""
        records: list[BoostRecord] = []
        for run in sorted(self._runs.values(), key=lambda r: r.id):
            if run.is_terminal:
                continue
            if now >= run.end:
                self._apply(run, RunStatus.COMPLETED, SYSTEM_ACTOR, now, "run_completed",
                            reason="window_elapsed", boosts=run.boosts_done, spent=run.spent)
                continue
            if run.status is RunStatus.SCHEDULED and now >= run.start:
                self._apply(run, RunStatus.ACTIVE, SYSTEM_ACTOR, now, "run_started")
            if run.status is RunStatus.ACTIVE:
                self._maybe_boost(run, now, records)
        return records

    def _maybe_boost(self, run: Run, now: datetime, records: list[BoostRecord]) -> None:
        market = self._market
        ours = [
            lst
            for lst in market.listings_for_machine(run.machine_id)
            if lst.is_ours and lst.owner_id in run.expert_ids
        ]
        if not ours:
            return

        # Trigger: enough competitors have overtaken our best-ranked listing.
        top = min(ours, key=lambda lst: market.rank_of(lst.id))
        rank_before = market.rank_of(top.id)
        if rank_before < run.plan.passes_required:
            return

        # Cooldown: never boost the same listing twice in quick succession.
        eligible = [
            lst for lst in ours
            if now - lst.last_boost_at >= self._config.min_boost_interval
        ]
        ranked = rank_candidates(eligible, run.expert_ids, now, self._config.weights)
        if not ranked:
            return
        best = ranked[0]
        expert = self._experts[best.listing.owner_id]

        cost = market.boost_cost()
        if market.balance(expert.panel_id) < cost:
            self._pause_for_funds(run, expert, now)
            return
        try:
            charged = market.boost(best.listing.id, now)
        except InsufficientFunds:  # lost a race with another consumer of the wallet
            self._pause_for_funds(run, expert, now)
            return

        run.boosts_done += 1
        run.spent += charged
        run.last_boost_at = now
        self.audit.record(
            ts=now,
            actor_id=SYSTEM_ACTOR.id,
            action="boost_executed",
            run_id=run.id,
            listing=best.listing.id,
            expert=expert.id,
            rank_before=rank_before,
            cost=charged,
            score=best.score,
            quality_part=round(best.quality_part, 4),
            staleness_part=round(best.staleness_part, 4),
            performance_part=round(best.performance_part, 4),
        )
        records.append(
            BoostRecord(run.id, best.listing.id, expert.id, charged, rank_before, best.score, now)
        )

        remaining = market.balance(expert.panel_id)
        if (
            remaining < self._config.low_balance_threshold
            and expert.panel_id not in self._low_balance_warned
        ):
            self._low_balance_warned.add(expert.panel_id)
            self.audit.record(
                ts=now,
                actor_id=SYSTEM_ACTOR.id,
                action="low_balance_warning",
                run_id=run.id,
                panel=expert.panel_id,
                balance=remaining,
            )

    def _pause_for_funds(self, run: Run, expert: Expert, now: datetime) -> None:
        self._apply(run, RunStatus.PAUSED, SYSTEM_ACTOR, now, "run_paused",
                    reason="insufficient_funds", panel=expert.panel_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _run_experts(self, run: Run) -> list[Expert]:
        return [self._experts[i] for i in run.expert_ids]

    def _apply(
        self, run: Run, dst: RunStatus, actor: Actor, now: datetime, action: str, **details: object
    ) -> None:
        try:
            state_machine.transition(run, dst)
        except InvalidTransition as exc:
            if actor.role is not Role.SYSTEM:  # a person asked for something impossible
                self._reject(actor, now, action, [str(exc)], run_id=run.id)
            raise
        self.audit.record(ts=now, actor_id=actor.id, action=action, run_id=run.id, **details)

    def _authorize(
        self, actor: Actor, experts: list[Expert], now: datetime, command: str, **ctx: str
    ) -> None:
        try:
            authorize_for_experts(actor, experts)
        except AuthorizationError as exc:
            self._reject(actor, now, command, [str(exc)], **ctx)
            raise

    def _reject(
        self, actor: Actor, now: datetime, command: str, reasons: list[str], **ctx: str
    ) -> None:
        self.audit.record(
            ts=now,
            actor_id=actor.id,
            action="command_rejected",
            run_id=ctx.pop("run_id", None),
            command=command,
            reasons=reasons,
            **ctx,
        )
