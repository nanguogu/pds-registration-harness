from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from pds_harness.domain.models import RegistrationDefaults


class PDSSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    write_drafts_enabled: bool = False
    submit_enabled: bool = False


class CloudPolicySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    allowed_confidentiality_value: str = "不保密"
    unresolved_reason: str


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_archive_root: Path
    retention: str
    backup_policy_resolved: bool = False


class ValidationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_confidence: float = Field(ge=0, le=1)
    prohibited_phrases: list[str]


class HarnessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str
    pds: PDSSettings
    registration_plan: RegistrationDefaults
    cloud_policy: CloudPolicySettings
    storage: StorageSettings
    validation: ValidationSettings


def load_settings(path: Path) -> HarnessSettings:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return HarnessSettings.model_validate(raw)

