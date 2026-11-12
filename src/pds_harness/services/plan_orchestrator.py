from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pds_harness.domain.errors import AmbiguousDraftError, PDSAPIError
from pds_harness.domain.models import (
    PlanRuntimeValues,
    RegistrationDefaults,
    ResolvedDictionaryValues,
)
from pds_harness.services.plan_payload import apply_registration_defaults
from pds_harness.services.plan_resolution import (
    DraftResolutionKind,
    resolve_existing_draft,
)


class PlanGateway(Protocol):
    def list_plans(self, program_id: int) -> dict[str, Any]: ...

    def get_plan(
        self,
        program_id: int,
        *,
        plan_id: int | None = None,
        big_or_small: int | None = None,
    ) -> dict[str, Any]: ...

    def save_plan_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PreparedDraft:
    action: str
    program_id: int
    plan_id: int | None
    payload: dict[str, Any]
    persisted: bool


def unwrap_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    current: Any = payload.get("data", payload)
    while isinstance(current, Mapping) and isinstance(current.get("data"), Mapping):
        current = current["data"]
    if not isinstance(current, Mapping):
        raise PDSAPIError("PDS plan response does not contain an object")
    return dict(current)


def extract_saved_plan_id(payload: Mapping[str, Any]) -> int:
    current: Any = payload.get("data", payload)
    for _ in range(4):
        if isinstance(current, bool):
            break
        if isinstance(current, int) or (
            isinstance(current, str) and current.isdigit()
        ):
            return int(current)
        if isinstance(current, Mapping):
            if current.get("planId") is not None:
                return int(current["planId"])
            if "data" in current:
                current = current["data"]
                continue
        break
    raise PDSAPIError("PDS save response does not contain the new planId")


class PlanOrchestrator:
    def __init__(self, gateway: PlanGateway, *, write_drafts_enabled: bool) -> None:
        self.gateway = gateway
        self.write_drafts_enabled = write_drafts_enabled

    def prepare_draft(
        self,
        *,
        program_id: int,
        defaults: RegistrationDefaults,
        dictionaries: ResolvedDictionaryValues,
        runtime: PlanRuntimeValues,
        big_or_small: int | None = None,
    ) -> PreparedDraft:
        resolution = resolve_existing_draft(
            self.gateway.list_plans(program_id), program_id=program_id
        )
        if resolution.kind is DraftResolutionKind.AMBIGUOUS:
            identifiers = [plan.plan_id for plan in resolution.candidates]
            raise AmbiguousDraftError(
                f"Project {program_id} has multiple draft plans: {identifiers}"
            )
        if resolution.kind is DraftResolutionKind.RESUME:
            existing = resolution.plan
            assert existing is not None
            plan = unwrap_plan(
                self.gateway.get_plan(program_id, plan_id=existing.plan_id)
            )
            return PreparedDraft(
                action="resume",
                program_id=program_id,
                plan_id=existing.plan_id,
                payload=plan,
                persisted=True,
            )

        initialized = unwrap_plan(
            self.gateway.get_plan(
                program_id, plan_id=None, big_or_small=big_or_small
            )
        )
        initialized_program_id = initialized.get("programId")
        if initialized_program_id is not None and int(initialized_program_id) != program_id:
            raise PDSAPIError(
                "PDS initialized a plan for a different project; draft creation stopped"
            )
        preview = apply_registration_defaults(
            initialized,
            defaults=defaults,
            dictionaries=dictionaries,
            runtime=runtime,
        )
        if not self.write_drafts_enabled:
            return PreparedDraft(
                action="preview_create",
                program_id=program_id,
                plan_id=None,
                payload=preview,
                persisted=False,
            )

        save_response = self.gateway.save_plan_draft(preview)
        plan_id = extract_saved_plan_id(save_response)
        persisted = unwrap_plan(self.gateway.get_plan(program_id, plan_id=plan_id))
        if int(persisted.get("programId", program_id)) != program_id:
            raise PDSAPIError("Created plan failed the program identity check")
        if int(persisted.get("planId", plan_id)) != plan_id:
            raise PDSAPIError("Created plan failed the plan identity check")
        return PreparedDraft(
            action="created",
            program_id=program_id,
            plan_id=plan_id,
            payload=persisted,
            persisted=True,
        )

