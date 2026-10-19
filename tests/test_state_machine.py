import pytest
from conftest import T0

from boost_scheduler import InvalidTransition, Plan, Run, RunStatus
from boost_scheduler.state_machine import ALLOWED, can_transition, transition


def make_run(status: RunStatus) -> Run:
    return Run("r", "machine", Plan("p", 1), ("e",), T0, T0, "m", status=status)


@pytest.mark.parametrize("terminal", [RunStatus.CANCELLED, RunStatus.COMPLETED])
def test_terminal_states_have_no_exits(terminal):
    assert ALLOWED[terminal] == frozenset()
    for dst in RunStatus:
        assert not can_transition(terminal, dst)


def test_valid_transition_updates_status_and_returns_previous():
    run = make_run(RunStatus.ACTIVE)
    assert transition(run, RunStatus.PAUSED) is RunStatus.ACTIVE
    assert run.status is RunStatus.PAUSED


def test_invalid_transition_raises_and_leaves_run_untouched():
    run = make_run(RunStatus.CANCELLED)
    with pytest.raises(InvalidTransition):
        transition(run, RunStatus.ACTIVE)
    assert run.status is RunStatus.CANCELLED


def test_cannot_pause_twice():
    run = make_run(RunStatus.PAUSED)
    with pytest.raises(InvalidTransition):
        transition(run, RunStatus.PAUSED)
