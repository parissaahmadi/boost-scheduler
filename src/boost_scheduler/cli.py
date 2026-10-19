"""Scripted simulation: ``uv run boost-demo``.

Plays a six-hour morning on a fictional used-industrial-equipment marketplace and prints what the
scheduler decides, including the commands it *refuses*.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from .errors import AuthorizationError, InvalidTransition, PreconditionFailed
from .sample_world import MACHINE_TITLES, MANAGER_A, MANAGER_B, OWNER, build_world
from .scheduler import Scheduler

START = datetime(2026, 1, 12, 9, 0, tzinfo=UTC)
TICK = timedelta(minutes=5)
SYSTEM_EVENTS = {"run_started", "run_completed", "run_paused", "low_balance_warning"}


def _t(now: datetime) -> str:
    return now.strftime("%H:%M")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boost-demo", description=__doc__)
    parser.add_argument("--seed", type=int, default=7, help="RNG seed for the fake marketplace")
    parser.add_argument("--hours", type=int, default=6, help="simulated duration")
    parser.add_argument("--audit-out", help="write the audit log to this JSONL file")
    args = parser.parse_args(argv)

    world = build_world(START, seed=args.seed)
    scheduler = Scheduler(market=world.market, experts=world.experts)

    def attempt(label: str, now: datetime, fn) -> None:
        try:
            result = fn()
        except AuthorizationError as exc:
            print(f"{_t(now)}  DENIED     {label}\n           -> {exc}")
        except InvalidTransition as exc:
            print(f"{_t(now)}  INVALID    {label}\n           -> {exc}")
        except PreconditionFailed as exc:
            print(f"{_t(now)}  REJECTED   {label}")
            for reason in exc.reasons:
                print(f"           -> {reason}")
        else:
            suffix = f"  -> paused {result}" if isinstance(result, list) else ""
            print(f"{_t(now)}  OK         {label}{suffix}")
            return result

    print("Machines on sale:")
    for machine_id, title in MACHINE_TITLES.items():
        print(f"  {machine_id:<8} {title}")
    print()

    now = START
    window_end = START + timedelta(hours=args.hours)

    attempt(
        "manager-metal creates run for the CNC lathe (standard)",
        now,
        lambda: scheduler.create_run(
            MANAGER_A,
            machine_id="cnc-01",
            plan_name="standard",
            expert_ids=["nora", "omar"],
            start=START,
            end=START + timedelta(hours=5),
            now=now,
        ),
    )
    attempt(
        "manager-utilities tries to use a metalworking engineer",
        now,
        lambda: scheduler.create_run(
            MANAGER_B,
            machine_id="cnc-01",
            plan_name="standard",
            expert_ids=["nora"],
            start=START,
            end=window_end,
            now=now,
        ),
    )
    attempt(
        "manager-utilities submits an invalid command",
        now,
        lambda: scheduler.create_run(
            MANAGER_B,
            machine_id="comp-02",
            plan_name="turbo",
            expert_ids=["lena", "ghost"],
            start=START,
            end=START - timedelta(hours=1),
            now=now,
        ),
    )
    attempt(
        "manager-utilities creates run for the compressor (aggressive)",
        now,
        lambda: scheduler.create_run(
            MANAGER_B,
            machine_id="comp-02",
            plan_name="aggressive",
            expert_ids=["lena", "ravi"],
            start=START + timedelta(minutes=30),
            end=window_end,
            now=now,
        ),
    )

    pause_at = START + timedelta(hours=2)
    resume_at = START + timedelta(hours=2, minutes=30)
    replace_at = START + timedelta(hours=3, minutes=30)
    stop_at = START + timedelta(hours=4, minutes=30)

    total = int(timedelta(hours=args.hours) / TICK)
    for _ in range(total):
        now += TICK
        world.market.simulate_step(now)

        if now == pause_at:
            attempt(
                "manager-metal pauses run-1",
                now,
                lambda now=now: scheduler.pause_run(MANAGER_A, "run-1", now),
            )
        if now == resume_at:
            attempt(
                "manager-metal resumes run-1",
                now,
                lambda now=now: scheduler.resume_run(MANAGER_A, "run-1", now),
            )
        if now == replace_at:
            attempt(
                "manager-utilities replaces run-2 with a relaxed plan",
                now,
                lambda now=now, window_end=window_end: scheduler.create_run(
                    MANAGER_B,
                    machine_id="comp-02",
                    plan_name="relaxed",
                    expert_ids=["ravi", "lena"],
                    start=now,
                    end=window_end,
                    now=now,
                    replace=True,
                ),
            )
        if now == stop_at:
            attempt(
                "owner issues a global stop", now, lambda now=now: scheduler.stop_all(OWNER, now)
            )

        seen = len(scheduler.audit)
        records = scheduler.tick(now)
        for event in scheduler.audit.events()[seen:]:
            if event.action in SYSTEM_EVENTS:
                extra = event.details.get("reason") or event.details.get("balance", "")
                print(f"{_t(now)}  SYSTEM     {event.action} {event.run_id or ''} {extra}".rstrip())
        for rec in records:
            print(
                f"{_t(now)}  BOOST      {rec.run_id}  {rec.listing_id:<15} "
                f"rank_before={rec.rank_before}  score={rec.score:.3f}  cost={rec.cost}"
            )

    print("\n=== Summary ===")
    for run in scheduler.runs.values():
        print(
            f"{run.id}: machine={run.machine_id} plan={run.plan.name:<10} "
            f"status={run.status.value:<9} boosts={run.boosts_done} spent={run.spent}"
        )
    for panel in ("panel-1", "panel-2"):
        print(f"{panel} balance: {world.market.balance(panel)}")
    print(f"audit events: {len(scheduler.audit)}  chain valid: {scheduler.audit.verify()}")

    if args.audit_out:
        scheduler.audit.to_jsonl(args.audit_out)
        print(f"audit log written to {args.audit_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
