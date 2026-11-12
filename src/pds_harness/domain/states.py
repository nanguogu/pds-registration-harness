from __future__ import annotations

from enum import StrEnum

from .errors import InvalidTransitionError


class WorkflowState(StrEnum):
    DISCOVERED = "discovered"
    AUTH_REQUIRED = "auth_required"
    PLAN_DRAFT_CREATING = "plan_draft_creating"
    PLAN_DRAFT_READY = "plan_draft_ready"
    MATERIAL_DOWNLOADING = "material_downloading"
    MATERIAL_READY = "material_ready"
    CLOUD_BLOCKED = "cloud_blocked"
    GENERATING = "generating"
    REVIEW_PENDING = "review_pending"
    DRAFT_SAVING = "draft_saving"
    SUBMIT_READY = "submit_ready"
    SUBMIT_CONFIRMING = "submit_confirming"
    SUBMITTED = "submitted"
    NEEDS_ATTENTION = "needs_attention"


ALLOWED_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.DISCOVERED: frozenset(
        {WorkflowState.AUTH_REQUIRED, WorkflowState.PLAN_DRAFT_CREATING}
    ),
    WorkflowState.AUTH_REQUIRED: frozenset({WorkflowState.DISCOVERED}),
    WorkflowState.PLAN_DRAFT_CREATING: frozenset(
        {WorkflowState.PLAN_DRAFT_READY, WorkflowState.NEEDS_ATTENTION}
    ),
    WorkflowState.PLAN_DRAFT_READY: frozenset(
        {WorkflowState.MATERIAL_DOWNLOADING, WorkflowState.NEEDS_ATTENTION}
    ),
    WorkflowState.MATERIAL_DOWNLOADING: frozenset(
        {WorkflowState.MATERIAL_READY, WorkflowState.NEEDS_ATTENTION}
    ),
    WorkflowState.MATERIAL_READY: frozenset(
        {
            WorkflowState.CLOUD_BLOCKED,
            WorkflowState.GENERATING,
            WorkflowState.NEEDS_ATTENTION,
        }
    ),
    WorkflowState.CLOUD_BLOCKED: frozenset({WorkflowState.NEEDS_ATTENTION}),
    WorkflowState.GENERATING: frozenset(
        {WorkflowState.REVIEW_PENDING, WorkflowState.NEEDS_ATTENTION}
    ),
    WorkflowState.REVIEW_PENDING: frozenset(
        {WorkflowState.DRAFT_SAVING, WorkflowState.SUBMIT_READY}
    ),
    WorkflowState.DRAFT_SAVING: frozenset(
        {WorkflowState.REVIEW_PENDING, WorkflowState.NEEDS_ATTENTION}
    ),
    WorkflowState.SUBMIT_READY: frozenset(
        {WorkflowState.SUBMIT_CONFIRMING, WorkflowState.REVIEW_PENDING}
    ),
    WorkflowState.SUBMIT_CONFIRMING: frozenset(
        {WorkflowState.SUBMITTED, WorkflowState.NEEDS_ATTENTION}
    ),
    WorkflowState.SUBMITTED: frozenset(),
    WorkflowState.NEEDS_ATTENTION: frozenset(
        {
            WorkflowState.DISCOVERED,
            WorkflowState.PLAN_DRAFT_READY,
            WorkflowState.MATERIAL_READY,
            WorkflowState.REVIEW_PENDING,
        }
    ),
}


def ensure_transition(current: WorkflowState, target: WorkflowState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"Illegal workflow transition: {current.value} -> {target.value}"
        )

