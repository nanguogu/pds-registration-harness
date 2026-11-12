from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pds_harness.domain.models import PendingProject


PENDING_PLAN_STATUS = "待计划编制"
REGISTRATION_TEST_TYPE = "软件产品登记测试"


def _extract_project_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    current: Any = payload
    for key in ("data", "data"):
        if isinstance(current, Mapping) and key in current:
            current = current[key]
    if isinstance(current, Mapping) and isinstance(current.get("list"), list):
        return [dict(row) for row in current["list"] if isinstance(row, Mapping)]

    first_data = payload.get("data")
    if isinstance(first_data, Mapping) and isinstance(first_data.get("list"), list):
        return [
            dict(row) for row in first_data["list"] if isinstance(row, Mapping)
        ]
    raise ValueError("Unable to find the project list in the PDS response")


def discover_pending_registration_projects(
    payload: Mapping[str, Any],
) -> list[PendingProject]:
    projects: list[PendingProject] = []
    for row in _extract_project_rows(payload):
        status = str(row.get("statusValue") or row.get("status") or "").strip()
        test_type = str(row.get("testTypeValue") or "").strip()
        if status != PENDING_PLAN_STATUS or test_type != REGISTRATION_TEST_TYPE:
            continue
        projects.append(
            PendingProject(
                program_id=int(row["programId"]),
                program_code=str(row.get("code") or row.get("programCode") or ""),
                software_name=str(row.get("softwareName") or ""),
                status=status,
                confidentiality=row.get("wasKeepSecret"),
                unit_name=row.get("unitName"),
                test_type=test_type,
            )
        )
    return projects

