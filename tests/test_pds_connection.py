from __future__ import annotations

from collections.abc import Callable, Mapping
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from docx import Document

from pds_harness.domain.errors import (
    PDSConnectionError,
    PDSConnectionNotStartedError,
)
from pds_harness.services.pds_connection import PDSConnectionManager


def _docx_bytes() -> bytes:
    stream = BytesIO()
    document = Document()
    document.add_heading("示例功能", level=1)
    document.add_paragraph("提供示例功能说明。")
    document.save(stream)
    return stream.getvalue()


TEST_DOCX_BYTES = _docx_bytes()


class FakeSession:
    def __init__(self, *, authenticated: bool = False) -> None:
        self.authenticated = authenticated
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def authorization_token(self) -> str:
        if not self.authenticated:
            raise RuntimeError("not authenticated")
        return "secret-token"

    def is_authenticated(self) -> bool:
        return self.authenticated

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(
        self,
        token_provider: Callable[[], str],
        observed_queries: list[Mapping[str, Any]],
    ) -> None:
        self._token_provider = token_provider
        self._observed_queries = observed_queries

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def query_projects(self, query: Mapping[str, Any]) -> dict[str, Any]:
        assert self._token_provider() == "secret-token"
        self._observed_queries.append(query)
        return {
            "code": 10000,
            "data": {"data": {"list": [{"sensitive": "not returned"}], "total": 108}},
        }


class IntakeFakeClient:
    def __init__(self, token_provider: Callable[[], str]) -> None:
        self._token_provider = token_provider

    def __enter__(self) -> "IntakeFakeClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def query_plan_records(self, _query: Mapping[str, Any]) -> dict[str, Any]:
        assert self._token_provider() == "secret-token"
        return {
            "code": 10000,
            "data": {
                "list": [
                    {
                        "programId": 81202,
                        "code": "STS1260215",
                        "softwareName": "示例软件",
                        "unitName": "示例单位",
                        "planStatus": "10",
                        "wasKeepSecret": "0",
                    }
                ]
            },
        }

    def get_plan_search_conditions(self) -> dict[str, Any]:
        return {
            "code": 10000,
            "data": {
                "planStatuses": [
                    {"code": "10", "value": "待计划编制"}
                ]
            },
        }

    def get_application(self, program_id: int, *, status: int = 2) -> dict[str, Any]:
        assert program_id == 81202
        assert status == 2
        return {
            "code": 10000,
            "data": {
                "softwareVersion": "V1.0",
                "receiverPhone": "13800000000",
            },
        }

    def get_program_pdf_files(self, program_id: int) -> dict[str, Any]:
        assert program_id == 81202
        return {
            "code": 10000,
            "data": [
                {
                    "fileId": "program-file-1",
                    "pdfFileName": "示例软件厂商手册.docx",
                    "pdfFilePath": "/uploads/program-manual.docx",
                    "uploadTime": "2026-09-03",
                }
            ],
        }

    def get_latest_sample(self, program_id: int) -> dict[str, Any]:
        assert program_id == 81202
        return {
            "code": 10000,
            "data": {
                "files": [
                    {
                        "fileId": "file-1",
                        "sampleFileName": "示例软件用户手册.doc",
                        "sampleFilePath": "/uploads/manual.doc",
                        "uploadTime": "2026-09-03",
                    }
                ]
            },
        }

    def request_download_ticket(
        self, *, remote_path: str, file_name: str
    ) -> dict[str, Any]:
        assert remote_path == "/uploads/program-manual.docx"
        assert file_name == "示例软件厂商手册.docx"
        return {"code": 10000, "data": "/system/file/authFileToken?token=temporary"}

    def get_file_service_config(self) -> dict[str, Any]:
        return {
            "code": 10000,
            "data": {"downloadBaseUrl": "https://files.example.test"},
        }

    def download_from_ticket(
        self,
        ticket_response: Mapping[str, Any],
        *,
        file_service_config: Mapping[str, Any],
        expected_file_name: str,
        maximum_bytes: int = 250 * 1024 * 1024,
    ) -> bytes:
        assert ticket_response["code"] == 10000
        assert file_service_config["data"]["downloadBaseUrl"] == "https://files.example.test"
        assert expected_file_name.endswith(".docx")
        assert maximum_bytes > 0
        return TEST_DOCX_BYTES


class MismatchIntakeFakeClient(IntakeFakeClient):
    def get_program_pdf_files(self, program_id: int) -> dict[str, Any]:
        assert program_id == 81202
        return {
            "code": 10000,
            "data": [
                {
                    "fileId": "wrong-file-1",
                    "pdfFileName": "其他控制软件厂商手册.docx",
                    "pdfFilePath": "/uploads/other-manual.docx",
                }
            ],
        }

    def request_download_ticket(
        self, *, remote_path: str, file_name: str
    ) -> dict[str, Any]:
        assert remote_path == "/uploads/other-manual.docx"
        assert file_name == "其他控制软件厂商手册.docx"
        return {"code": 10000, "data": "/system/file/authFileToken?token=temporary"}


class DraftInspectionFakeClient(IntakeFakeClient):
    def query_plan_records(self, _query: Mapping[str, Any]) -> dict[str, Any]:
        assert self._token_provider() == "secret-token"
        return {
            "code": 10000,
            "data": {
                "list": [
                    {
                        "programId": 81202,
                        "code": "STS1260215",
                        "planStatus": 0,
                        "wasKeepSecret": "0",
                    }
                ]
            },
        }

    def get_plan_search_conditions(self) -> dict[str, Any]:
        return {
            "code": 10000,
            "data": {"planStatuses": [{"code": 0, "value": "待提交"}]},
        }

    def list_plans(self, program_id: int) -> dict[str, Any]:
        assert program_id == 81202
        return {
            "code": 10000,
            "data": {
                "ycsRegTestPlanList": {
                    "total": 1,
                    "list": [
                        {
                            "planId": 15200,
                            "programId": 81202,
                            "status": 0,
                            "statusValue": "待提交",
                        }
                    ],
                }
            },
        }

    def get_plan(
        self,
        program_id: int,
        *,
        plan_id: int | None = None,
        big_or_small: int | None = None,
    ) -> dict[str, Any]:
        assert program_id == 81202
        assert plan_id == 15200
        assert big_or_small is None
        return {
            "code": 10000,
            "data": {
                "planId": 15200,
                "programId": 81202,
                "status": 0,
                "statusValue": "待提交",
            },
        }


def test_connection_requires_manual_login_then_returns_only_counts():
    session = FakeSession()
    queries: list[Mapping[str, Any]] = []
    manager = PDSConnectionManager(
        session_factory=lambda: session,
        client_factory=lambda token_provider: FakeClient(token_provider, queries),
    )
    try:
        opened = manager.start()
        assert opened.state == "login_required"
        assert opened.browser_open is True
        assert opened.authenticated is False

        session.authenticated = True
        assert manager.status().state == "ready"

        result = manager.probe()
        assert result.to_dict() == {
            "state": "ready",
            "browser_open": True,
            "authenticated": True,
            "read_only": True,
            "business_code": 10000,
            "returned_count": 1,
            "total_count": 108,
        }
        assert queries == [{"page_number": 1, "page_size": 1}]
        assert "secret-token" not in str(result.to_dict())

        stopped = manager.stop()
        assert stopped.state == "stopped"
        assert session.closed is True
    finally:
        manager.shutdown()


def test_probe_before_open_is_blocked():
    manager = PDSConnectionManager(
        session_factory=FakeSession,
        client_factory=lambda _token_provider: pytest.fail(
            "client must not be created without a browser session"
        ),
    )
    try:
        with pytest.raises(PDSConnectionNotStartedError):
            manager.probe()
    finally:
        manager.shutdown()


def test_browser_start_error_does_not_echo_exception_details():
    class FailingSession(FakeSession):
        def open(self) -> None:
            raise RuntimeError("secret-token")

    manager = PDSConnectionManager(
        session_factory=FailingSession,
        client_factory=lambda _token_provider: pytest.fail(
            "client must not be created when browser startup fails"
        ),
    )
    try:
        with pytest.raises(PDSConnectionError) as exc_info:
            manager.start()
        assert "secret-token" not in str(exc_info.value)
        assert manager.status().state == "error"
    finally:
        manager.shutdown()


def test_prepare_project_validates_and_archives_unique_manual(tmp_path):
    session = FakeSession(authenticated=True)
    manager = PDSConnectionManager(
        session_factory=lambda: session,
        client_factory=IntakeFakeClient,
        archive_root=tmp_path,
    )
    try:
        assert manager.start().state == "ready"
        result = manager.prepare_project("sts1260215")
        assert result.stage == "downloaded_and_parsed"
        assert result.program_code == "STS1260215"
        assert result.unit_name == "示例单位"
        assert result.receiver_phone == "13800000000"
        assert result.receiver_phone_field == "data.receiverPhone"
        assert result.attachment_selection == "unique"
        assert result.manual is not None
        assert result.manual["name"] == "示例软件厂商手册.docx"
        assert result.manual["size"] == len(TEST_DOCX_BYTES)
        assert Path(result.manual["path"]).read_bytes() == TEST_DOCX_BYTES
        assert result.manual["identity_match"] is True
        assert result.manual["function_generation_allowed"] is True
        assert Path(result.manual["parsed_json_path"]).is_file()
    finally:
        manager.shutdown()


def test_exception_sample_requires_separate_explicit_path(tmp_path):
    session = FakeSession(authenticated=True)
    manager = PDSConnectionManager(
        session_factory=lambda: session,
        client_factory=MismatchIntakeFakeClient,
        archive_root=tmp_path,
    )
    try:
        manager.start()
        blocked = manager.prepare_project("STS1260215")
        assert blocked.stage == "manual_identity_mismatch"
        assert blocked.manual is None

        downloaded = manager.download_exception_sample("STS1260215")
        assert downloaded.stage == "exception_sample_downloaded_and_parsed"
        assert downloaded.manual is not None
        assert downloaded.manual["identity_match"] is False
        assert downloaded.manual["function_generation_allowed"] is False
        assert downloaded.manual["usage_restriction"] == "exception_sample_only"
        parsed_json = Path(downloaded.manual["parsed_json_path"])
        assert parsed_json.is_file()
        assert '"function_generation_allowed":false' in parsed_json.read_text(
            encoding="utf-8"
        )
    finally:
        manager.shutdown()


def test_inspect_plan_contract_reads_back_existing_pending_submission_draft():
    session = FakeSession(authenticated=True)
    manager = PDSConnectionManager(
        session_factory=lambda: session,
        client_factory=DraftInspectionFakeClient,
    )
    try:
        manager.start()
        result = manager.inspect_plan_contract("STS1260215")
        assert result["status"] == "待提交"
        assert result["draft_resolution"] == "resume"
        assert result["draft_plan_id"] == 15200
        assert result["current_plan_response"]["data"]["planId"] == 15200
        assert result["read_only"] is True
    finally:
        manager.shutdown()
