import pytest

from boost_scheduler import Actor, AuthorizationError, Role
from boost_scheduler.authz import authorize_for_experts
from boost_scheduler.sample_world import EXPERTS, MANAGER_A, OWNER

BY_ID = {e.id: e for e in EXPERTS}


def test_owner_may_act_on_everyone():
    authorize_for_experts(OWNER, EXPERTS)


def test_manager_may_act_on_own_team():
    authorize_for_experts(MANAGER_A, [BY_ID["nora"], BY_ID["omar"]])


def test_manager_is_rejected_if_any_expert_is_foreign():
    with pytest.raises(AuthorizationError, match="lena"):
        authorize_for_experts(MANAGER_A, [BY_ID["nora"], BY_ID["lena"]])


def test_manager_without_team_has_no_access():
    with pytest.raises(AuthorizationError):
        authorize_for_experts(Actor("x", Role.MANAGER, team=None), [BY_ID["nora"]])
