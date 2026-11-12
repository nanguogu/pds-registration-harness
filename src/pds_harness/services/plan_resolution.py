from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pds_harness.domain.models import PlanSummary


class DraftResolutionKind(StrEnum):
    CREATE = "create"
    RESUME = "resume"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class DraftResolution:
    kind: DraftResolutionKind
    candidates: tuple[PlanSummary, ...]

    @property
    def plan(self) -> PlanSummary | None:
        if self.kind is DraftResolutionKind.RESUME:
            return self.candidates[0]
        return None


def _find_plan_list(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, Mapping) and isinstance(data.get("data"), Mapping):
        data = data["data"]
    if isinstance(data, Mapping) and isinstance(data.get("ycsRegTestPlanList"), Mapping):
        data = data["ycsRegTestPlanList"]
    if isinstance(data, Mapping) and isinstance(data.get("list"), list):
        return [dict(item) for item in data["list"] if isinstance(item, Mapping)]
    raise ValueError("Unable to find ycsRegTestPlanList.list in the PDS response")


def resolve_existing_draft(
    payload: Mapping[str, Any], *, program_id: int
) -> DraftResolution:
    drafts: list[PlanSummary] = []
    for item in _find_plan_list(payload):
        item_program_id = int(item.get("programId") or program_id)
        if item_program_id != program_id:
            continue
        status_code = item.get("status")
        status_text = str(item.get("statusValue") or "").strip() or None
        is_draft = status_code == 0 or status_text in {"待提交", "草稿"}
        if not is_draft:
            continue
        drafts.append(
            PlanSummary(
                plan_id=int(item["planId"]),
                program_id=item_program_id,
                status_code=int(status_code) if status_code is not None else None,
                status_text=status_text,
                version=item.get("version"),
            )
        )

    if not drafts:
        return DraftResolution(DraftResolutionKind.CREATE, ())
    if len(drafts) == 1:
        return DraftResolution(DraftResolutionKind.RESUME, (drafts[0],))
    return DraftResolution(DraftResolutionKind.AMBIGUOUS, tuple(drafts))

