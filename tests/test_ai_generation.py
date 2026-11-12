from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pds_harness.domain.models import ParsedManualArtifact
from pds_harness.services.ai_generation import (
    generate_product_summary_artifact,
    generate_review_artifacts,
)
from pds_harness.services.catalog_validation import CatalogValidator


class FakeModel:
    model_id = "fake-model"

    def complete_json(self, **_kwargs):
        return {
            "product_summary": {
                "overview": "本软件用于导入设计数据并执行数据比较和结果查看。",
                "core_capabilities": ["导入设计数据", "比较设计数据"],
                "source_block_ids": ["paragraph-00001"],
            },
            "modules": [
                {
                    "name": "数据处理",
                    "source_block_ids": ["paragraph-00001"],
                    "functions": [
                        {
                            "name": "导入设计数据",
                            "description": "导入设计文件并展示数据内容。",
                            "source_block_ids": ["paragraph-00001"],
                            "confidence": 0.9,
                        }
                    ],
                }
            ],
            "coverage": [
                {
                    "source_block_id": "paragraph-00001",
                    "decision": "covered",
                    "reason": None,
                }
            ],
            "warnings": [],
        }


def test_generates_review_pending_artifacts(tmp_path):
    manual = tmp_path / "manual.docx"
    manual.write_bytes(b"manual")
    digest = hashlib.sha256(b"manual").hexdigest()
    artifact = ParsedManualArtifact.model_validate(
        {
            "program_id": 81220,
            "program_code": "STS1260336",
            "software_name": "示例软件",
            "confidentiality": "不保密",
            "attachment_id": "1",
            "original_name": "manual.docx",
            "original_path": str(manual),
            "manual_sha256": digest,
            "parsed_source_path": str(manual),
            "parsed_source_sha256": digest,
            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "identity_match": True,
            "function_generation_allowed": True,
            "blocks": [
                {
                    "block_id": "paragraph-00001",
                    "block_type": "paragraph",
                    "text": "导入设计数据并进行比较。",
                }
            ],
        }
    )
    artifact_path = tmp_path / "parsed.json"
    artifact_path.write_text(artifact.model_dump_json(), encoding="utf-8")
    result = generate_review_artifacts(
        artifact_path,
        model=FakeModel(),
        archive_root=tmp_path / "archive",
        validator=CatalogValidator(),
    )
    assert result.catalog.program_code == "STS1260336"
    assert result.catalog.modules[0].functions[0].review_status == "pending"
    assert result.summary.source_document_path == str(manual)
    assert Path(result.manifest_path).read_text(encoding="utf-8").find(
        '"pds_write_allowed": false'
    ) > 0


def test_generates_summary_only_with_original_document_path(tmp_path):
    manual = tmp_path / "manual.docx"
    manual.write_bytes(b"manual")
    digest = hashlib.sha256(b"manual").hexdigest()
    artifact = ParsedManualArtifact.model_validate(
        {
            "program_id": 81220,
            "program_code": "STS1260336",
            "software_name": "示例软件",
            "confidentiality": "不保密",
            "attachment_id": "1",
            "original_name": "manual.docx",
            "original_path": str(manual),
            "manual_sha256": digest,
            "parsed_source_path": str(manual),
            "parsed_source_sha256": digest,
            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "identity_match": True,
            "function_generation_allowed": True,
            "blocks": [
                {
                    "block_id": "paragraph-00001",
                    "block_type": "paragraph",
                    "text": "导入设计数据并进行比较。",
                }
            ],
        }
    )
    artifact_path = tmp_path / "parsed.json"
    artifact_path.write_text(artifact.model_dump_json(), encoding="utf-8")

    class SummaryModel:
        model_id = "summary-model"

        def complete_json(self, **_kwargs):
            return {
                "overview": "本软件用于导入设计数据，并对相关设计内容进行比较和结果查看。",
                "core_capabilities": ["导入设计数据", "比较设计数据"],
                "source_block_ids": ["paragraph-00001"],
            }

    result = generate_product_summary_artifact(
        artifact_path,
        model=SummaryModel(),
        archive_root=tmp_path / "archive",
    )
    summary = json.loads(
        Path(str(result["summary_path"])).read_text(encoding="utf-8")
    )
    assert summary["source_document_path"] == str(manual)
