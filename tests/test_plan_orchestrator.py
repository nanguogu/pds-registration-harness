from datetime import date

import pytest

from pds_harness.domain.errors import AmbiguousDraftError
from pds_harness.domain.models import (
    PlanRuntimeValues,
    RegistrationDefaults,
    ResolvedDictionaryValues,
)
from pds_harness.services.plan_orchestrator import PlanOrchestrator


class FakeGateway:
    def __init__(self, plans, initialized=None, saved_plan_id=99):
        self.plans = plans
        self.initialized = initialized or {"programId": 81202}
        self.saved_plan_id = saved_plan_id
        self.saved = []

    def list_plans(self, _program_id):
        return {
            "data": {
                "data": {
                    "ycsRegTestPlanList": {
                        "list": self.plans,
                        "total": len(self.plans),
                    }
                }
            }
        }

    def get_plan(self, program_id, *, plan_id=None, big_or_small=None):
        if plan_id is None:
            return {"data": {"data": dict(self.initialized)}}
        return {
            "data": {
                "data": {
                    "programId": program_id,
                    "planId": plan_id,
                    "ycsTestPlanTesItems": [],
                }
            }
        }

    def save_plan_draft(self, payload):
        self.saved.append(payload)
        return {"code": 10000, "data": self.saved_plan_id}


@pytest.fixture
def plan_inputs():
    return {
        "defaults": RegistrationDefaults(
            test_specification="spec",
            test_method_labels=["黑盒测试", "文档检查"],
            criteria=["criterion"],
            onsite_option_label="本实验室测试",
        ),
        "dictionaries": ResolvedDictionaryValues(
            test_method_ids={"黑盒测试": 1, "文档检查": 2},
            onsite_option_ids={"本实验室测试": 3},
        ),
        "runtime": PlanRuntimeValues(
            current_username="陈圆", compile_date=date(2026, 11, 10)
        ),
    }


def test_previews_new_draft_when_write_switch_is_off(plan_inputs):
    gateway = FakeGateway([])
    result = PlanOrchestrator(
        gateway, write_drafts_enabled=False
    ).prepare_draft(program_id=81202, **plan_inputs)
    assert result.action == "preview_create"
    assert result.persisted is False
    assert gateway.saved == []


def test_creates_and_verifies_draft_when_write_switch_is_on(plan_inputs):
    gateway = FakeGateway([])
    result = PlanOrchestrator(
        gateway, write_drafts_enabled=True
    ).prepare_draft(program_id=81202, **plan_inputs)
    assert result.action == "created"
    assert result.plan_id == 99
    assert len(gateway.saved) == 1


def test_resumes_unique_existing_draft(plan_inputs):
    gateway = FakeGateway(
        [{"programId": 81202, "planId": 10, "status": 0}]
    )
    result = PlanOrchestrator(
        gateway, write_drafts_enabled=False
    ).prepare_draft(program_id=81202, **plan_inputs)
    assert result.action == "resume"
    assert result.plan_id == 10


def test_multiple_drafts_stop_automation(plan_inputs):
    gateway = FakeGateway(
        [
            {"programId": 81202, "planId": 10, "status": 0},
            {"programId": 81202, "planId": 11, "status": 0},
        ]
    )
    with pytest.raises(AmbiguousDraftError):
        PlanOrchestrator(gateway, write_drafts_enabled=False).prepare_draft(
            program_id=81202, **plan_inputs
        )
