from __future__ import annotations

import pytest

from pds_harness.domain.models import FunctionCatalog


@pytest.fixture
def valid_catalog() -> FunctionCatalog:
    return FunctionCatalog.model_validate(
        {
            "schema_version": "1.0",
            "program_id": 81202,
            "program_code": "STS1260318",
            "manual_sha256": "a" * 64,
            "prompt_version": "function_items_v1",
            "model": "mock/local",
            "modules": [
                {
                    "local_id": "module-1",
                    "name": "终端管理",
                    "source_refs": [
                        {"file": "manual.pdf", "page": 10, "section": "终端管理"}
                    ],
                    "functions": [
                        {
                            "local_id": "function-1",
                            "name": "控制终端电源",
                            "description": "控制终端开机、关机和重启。",
                            "source_refs": [
                                {
                                    "file": "manual.pdf",
                                    "page": 12,
                                    "section": "电源控制",
                                }
                            ],
                            "confidence": 0.95,
                            "review_status": "approved",
                        }
                    ],
                }
            ],
            "coverage": [
                {"section": "终端管理", "decision": "covered", "reason": None}
            ],
            "warnings": [],
        }
    )

