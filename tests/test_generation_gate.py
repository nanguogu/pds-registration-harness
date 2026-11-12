from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pds_harness.domain.errors import GenerationSourceBlockedError
from pds_harness.domain.models import ParsedManualArtifact
from pds_harness.services.generation_gate import (
    load_cloud_generation_source,
    load_generation_source,
)


def _write_artifact(
    tmp_path: Path,
    *,
    identity_match: bool = True,
    generation_allowed: bool = True,
    restriction: str | None = None,
) -> Path:
    manual = tmp_path / "manual.docx"
    manual.write_bytes(b"valid manual bytes")
    digest = hashlib.sha256(manual.read_bytes()).hexdigest()
    artifact = ParsedManualArtifact(
        program_id=80995,
        program_code="STS1260215",
        software_name="示例软件",
        confidentiality="不保密",
        attachment_id="1",
        original_name="示例软件手册.docx",
        original_path=str(manual),
        manual_sha256=digest,
        parsed_source_path=str(manual),
        parsed_source_sha256=digest,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        identity_match=identity_match,
        function_generation_allowed=generation_allowed,
        usage_restriction=restriction,
        blocks=[],
    )
    path = tmp_path / "manual.parsed.json"
    path.write_text(artifact.model_dump_json(), encoding="utf-8")
    return path


def test_generation_uses_verified_structured_json_as_only_source(tmp_path):
    path = _write_artifact(tmp_path)
    artifact = load_generation_source(path)
    assert artifact.program_code == "STS1260215"


def test_exception_sample_is_permanently_blocked(tmp_path):
    path = _write_artifact(
        tmp_path,
        identity_match=False,
        generation_allowed=False,
        restriction="exception_sample_only",
    )
    with pytest.raises(GenerationSourceBlockedError, match="identity"):
        load_generation_source(path)


def test_manual_hash_tampering_is_blocked(tmp_path):
    path = _write_artifact(tmp_path)
    artifact = ParsedManualArtifact.model_validate_json(path.read_text("utf-8"))
    Path(artifact.original_path).write_bytes(b"tampered")
    with pytest.raises(GenerationSourceBlockedError, match="hash"):
        load_generation_source(path)


def test_cloud_generation_remains_disabled(tmp_path):
    path = _write_artifact(tmp_path)
    with pytest.raises(GenerationSourceBlockedError, match="disabled"):
        load_cloud_generation_source(path, cloud_enabled=False)
