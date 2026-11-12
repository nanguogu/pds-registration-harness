from __future__ import annotations

from dataclasses import dataclass

from pds_harness.domain.errors import CloudPolicyError


@dataclass(frozen=True)
class CloudDecision:
    allowed: bool
    normalized_value: str
    reason: str


def normalize_confidentiality(value: str | None) -> str:
    if value is None:
        return ""
    return "".join(value.replace("\u3000", " ").split())


def decide_cloud_access(value: str | None) -> CloudDecision:
    normalized = normalize_confidentiality(value)
    if normalized == "不保密":
        return CloudDecision(True, normalized, "项目保密状态明确为不保密")
    if not normalized:
        return CloudDecision(False, normalized, "保密状态缺失，默认禁止云调用")
    return CloudDecision(False, normalized, f"保密状态为“{normalized}”，禁止云调用")


def require_cloud_access(value: str | None) -> None:
    decision = decide_cloud_access(value)
    if not decision.allowed:
        raise CloudPolicyError(decision.reason)

