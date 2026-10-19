from datetime import timedelta

import pytest
from conftest import T0, push_down

from boost_scheduler import (
    AuthorizationError,
    InvalidTransition,
    Listing,
    PreconditionFailed,
    RunStatus,
    Scheduler,
)
from boost_scheduler.sample_world import EXPERTS, MANAGER_A, MANAGER_B, OWNER, build_world

H = timedelta(hours=1)


def create(scheduler, actor=MANAGER_A, **overrides):
    params = dict(machine_id="cnc-01", plan_name="standard", expert_ids=["nora", "omar"],
                  start=T0, end=T0 + 4 * H, now=T0)
    params.update(overrides)
    return scheduler.create_run(actor, **params)


# ---------------------------------------------------------------- creation
def test_create_run_happy_path(scheduler):
    run = create(scheduler)
    assert run.status is RunStatus.SCHEDULED
    assert run.id == "run-1"
    assert scheduler.audit.events(action="run_created")


def test_cross_team_command_is_denied_and_audited(scheduler):
    with pytest.raises(AuthorizationError):
        create(scheduler, actor=MANAGER_B)  # nora/omar belong to metalworking
    assert scheduler.runs == {}
    assert scheduler.audit.events(action="command_rejected")


def test_preconditions_are_collected_and_nothing_is_created(scheduler):
    with pytest.raises(PreconditionFailed) as exc:
        create(scheduler, plan_name="turbo", expert_ids=["nora", "ghost"], end=T0 - H)
    reasons = " | ".join(exc.value.reasons)
    for expected in ("unknown plan", "unknown expert", "window end"):
        assert expected in reasons
    assert scheduler.runs == {}


def test_naive_datetimes_are_rejected(scheduler):
    with pytest.raises(PreconditionFailed, match="timezone-aware"):
        create(scheduler, start=T0.replace(tzinfo=None))


def test_expert_without_listing_for_car_is_rejected(scheduler):
    with pytest.raises(PreconditionFailed, match="no listing"):
        create(scheduler, actor=OWNER, expert_ids=["lena"])  # lena only advertises comp-02


def test_empty_wallet_blocks_creation(world):
    world.market.set_balance("panel-1", 0)
    scheduler = Scheduler(market=world.market, experts=EXPERTS)
    with pytest.raises(PreconditionFailed, match="panel-1"):
        create(scheduler)


def test_second_run_for_same_car_needs_replace(scheduler):
    create(scheduler)
    with pytest.raises(PreconditionFailed, match="already has a live run"):
        create(scheduler)
    new = create(scheduler, replace=True)
    assert scheduler.get_run("run-1").status is RunStatus.CANCELLED
    assert new.status is RunStatus.SCHEDULED


def test_replace_requires_authority_over_the_old_run(scheduler, world):
    world.market.add_listing(Listing(
        id="cnc-01-lena", machine_id="cnc-01", owner_id="lena", is_ours=True, quality=70.0,
        last_boost_at=T0 - 5 * H, panel_id="panel-1"))
    create(scheduler, actor=OWNER, expert_ids=["nora", "lena"])  # mixed-team run
    with pytest.raises(AuthorizationError):
        create(scheduler, actor=MANAGER_A, replace=True)
    assert scheduler.get_run("run-1").status is RunStatus.SCHEDULED


# ------------------------------------------------------------------ ticking
def test_run_activates_at_start_and_boosts_when_rivals_are_ahead(scheduler, world):
    create(scheduler, start=T0 + H)
    assert scheduler.tick(T0) == []  # window not open yet
    records = scheduler.tick(T0 + H)
    assert scheduler.get_run("run-1").status is RunStatus.ACTIVE
    assert len(records) == 1  # all 8 rivals rank above our listings
    assert world.market.rank_of(records[0].listing_id) == 0


def test_no_boost_until_enough_rivals_pass_us(scheduler, world):
    create(scheduler)                       # "standard": 3 passes required
    first = scheduler.tick(T0)
    assert len(first) == 1                  # initial position is far behind
    later = T0 + timedelta(minutes=30)
    push_down(world.market, "cnc-01", later, times=2)
    assert scheduler.tick(later) == []      # only 2 rivals ahead -> wait
    push_down(world.market, "cnc-01", later + timedelta(minutes=1), times=3)
    assert len(scheduler.tick(later + timedelta(minutes=2))) == 1


def test_cooldown_prevents_back_to_back_boosts_of_one_listing(world):
    # A single expert has a single listing, so the cooldown is the only gate.
    scheduler = Scheduler(market=world.market, experts=EXPERTS)
    create(scheduler, expert_ids=["nora"], plan_name="aggressive")
    assert len(scheduler.tick(T0)) == 1
    push_down(world.market, "cnc-01", T0 + timedelta(minutes=1), times=2)
    assert scheduler.tick(T0 + timedelta(minutes=2)) == []   # inside 5-min cooldown
    assert len(scheduler.tick(T0 + timedelta(minutes=6))) == 1


def test_best_listing_is_chosen_among_candidates(scheduler):
    create(scheduler)
    record = scheduler.tick(T0)[0]
    # omar's listing has higher quality (72 vs 60) and has waited longer -> omar wins
    assert record.expert_id == "omar"


def test_wallet_exhaustion_pauses_the_run_via_system_actor(world):
    scheduler = Scheduler(market=world.market, experts=EXPERTS)
    world.market.set_balance("panel-1", 10)       # exactly one boost for nora
    create(scheduler, expert_ids=["nora"], plan_name="aggressive")
    assert len(scheduler.tick(T0)) == 1
    later = T0 + timedelta(minutes=10)
    push_down(world.market, "cnc-01", later, times=2)
    assert scheduler.tick(later + timedelta(minutes=1)) == []
    assert scheduler.get_run("run-1").status is RunStatus.PAUSED
    pause = scheduler.audit.events(action="run_paused")[-1]
    assert pause.actor_id == "system"
    assert pause.details["reason"] == "insufficient_funds"


def test_low_balance_warning_is_emitted_once(world):
    scheduler = Scheduler(market=world.market, experts=EXPERTS)
    world.market.set_balance("panel-2", 35)       # omar is paid by panel-2
    create(scheduler, plan_name="aggressive")
    scheduler.tick(T0)
    assert len(scheduler.audit.events(action="low_balance_warning")) == 1
    push_down(world.market, "cnc-01", T0 + timedelta(minutes=10), times=3)
    scheduler.tick(T0 + timedelta(minutes=20))
    assert len(scheduler.audit.events(action="low_balance_warning")) == 1


def test_run_completes_when_window_elapses(scheduler):
    create(scheduler, end=T0 + H)
    scheduler.tick(T0)
    assert scheduler.tick(T0 + H) == []
    assert scheduler.get_run("run-1").status is RunStatus.COMPLETED


# ------------------------------------------------------------ pause / resume
def test_pause_and_resume_round_trip(scheduler):
    create(scheduler)
    scheduler.tick(T0)
    scheduler.pause_run(MANAGER_A, "run-1", T0 + H / 4)
    assert scheduler.tick(T0 + H / 2) == []
    scheduler.resume_run(MANAGER_A, "run-1", T0 + H / 2)
    assert scheduler.get_run("run-1").status is RunStatus.ACTIVE


def test_resume_before_window_goes_back_to_scheduled(scheduler):
    create(scheduler, start=T0 + H)
    scheduler.pause_run(MANAGER_A, "run-1", T0)
    scheduler.resume_run(MANAGER_A, "run-1", T0 + H / 2)
    assert scheduler.get_run("run-1").status is RunStatus.SCHEDULED


def test_resume_of_a_non_paused_run_is_a_precondition_failure(scheduler):
    create(scheduler)
    with pytest.raises(PreconditionFailed):
        scheduler.resume_run(MANAGER_A, "run-1", T0)


def test_double_pause_is_an_invalid_transition_and_audited(scheduler):
    create(scheduler)
    scheduler.pause_run(MANAGER_A, "run-1", T0)
    with pytest.raises(InvalidTransition):
        scheduler.pause_run(MANAGER_A, "run-1", T0)
    assert scheduler.audit.events(action="command_rejected")


def test_foreign_manager_cannot_touch_a_run(scheduler):
    create(scheduler)
    for command in (scheduler.pause_run, scheduler.cancel_run):
        with pytest.raises(AuthorizationError):
            command(MANAGER_B, "run-1", T0)


def test_cancelled_run_is_final(scheduler):
    create(scheduler)
    scheduler.cancel_run(MANAGER_A, "run-1", T0)
    with pytest.raises(InvalidTransition):
        scheduler.pause_run(MANAGER_A, "run-1", T0)
    with pytest.raises(PreconditionFailed):
        scheduler.resume_run(MANAGER_A, "run-1", T0)


# ------------------------------------------------------------------ stop_all
def test_owner_stop_all_pauses_every_team(scheduler):
    create(scheduler)
    create(scheduler, actor=MANAGER_B, machine_id="comp-02", expert_ids=["lena", "ravi"])
    assert scheduler.stop_all(OWNER, T0) == ["run-1", "run-2"]


def test_manager_stop_all_is_limited_to_their_team(scheduler):
    create(scheduler)
    create(scheduler, actor=MANAGER_B, machine_id="comp-02", expert_ids=["lena", "ravi"])
    assert scheduler.stop_all(MANAGER_A, T0) == ["run-1"]
    assert scheduler.get_run("run-2").status is RunStatus.SCHEDULED


def test_manager_cannot_stop_another_team(scheduler):
    with pytest.raises(AuthorizationError):
        scheduler.stop_all(MANAGER_A, T0, team="utilities")


# --------------------------------------------------------------- determinism
def _simulate(seed: int):
    start = T0
    w = build_world(start, seed=seed)
    s = Scheduler(market=w.market, experts=EXPERTS)
    s.create_run(MANAGER_A, machine_id="cnc-01", plan_name="standard", expert_ids=["nora", "omar"],
                 start=start, end=start + 4 * H, now=start)
    out, now = [], start
    for _ in range(48):
        now += timedelta(minutes=5)
        w.market.simulate_step(now)
        out.extend((r.listing_id, r.at) for r in s.tick(now))
    return out, s.audit.head_hash, s.audit.verify()


def test_same_seed_gives_identical_runs_and_audit_hash():
    a, b = _simulate(3), _simulate(3)
    assert a == b and a[2] is True and a[0]


def test_different_seed_changes_the_outcome():
    assert _simulate(3)[1] != _simulate(4)[1]
