from pds_harness.services.plan_resolution import (
    DraftResolutionKind,
    resolve_existing_draft,
)


def payload(plans):
    return {
        "code": 10000,
        "data": {"data": {"ycsRegTestPlanList": {"list": plans, "total": len(plans)}}},
    }


def test_creates_when_no_draft_exists():
    result = resolve_existing_draft(payload([]), program_id=81202)
    assert result.kind is DraftResolutionKind.CREATE


def test_resumes_unique_draft():
    result = resolve_existing_draft(
        payload(
            [
                {
                    "planId": 15191,
                    "programId": 81202,
                    "status": 0,
                    "statusValue": "待提交",
                }
            ]
        ),
        program_id=81202,
    )
    assert result.kind is DraftResolutionKind.RESUME
    assert result.plan.plan_id == 15191


def test_multiple_drafts_require_human_selection():
    plans = [
        {"planId": 1, "programId": 81202, "status": 0},
        {"planId": 2, "programId": 81202, "statusValue": "待提交"},
    ]
    result = resolve_existing_draft(payload(plans), program_id=81202)
    assert result.kind is DraftResolutionKind.AMBIGUOUS
    assert len(result.candidates) == 2
