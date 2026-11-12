import pytest

from pds_harness.domain.errors import InvalidTransitionError
from pds_harness.domain.states import WorkflowState, ensure_transition


def test_allows_expected_transition():
    ensure_transition(WorkflowState.DISCOVERED, WorkflowState.PLAN_DRAFT_CREATING)


def test_rejects_skipping_human_review():
    with pytest.raises(InvalidTransitionError):
        ensure_transition(WorkflowState.GENERATING, WorkflowState.SUBMITTED)


def test_submitted_is_terminal():
    with pytest.raises(InvalidTransitionError):
        ensure_transition(WorkflowState.SUBMITTED, WorkflowState.REVIEW_PENDING)

