from datetime import UTC, datetime

import httpx
import pytest

from pds_harness.adapters.pds_client import PDSClient
from pds_harness.domain.errors import PDSAPIError, SubmissionDisabledError
from pds_harness.domain.models import ReviewChecklist, SubmissionApproval


def approval() -> SubmissionApproval:
    return SubmissionApproval(
        actor="陈圆",
        confirmed_at=datetime.now(UTC),
        project_summary_reviewed=True,
        plan_parameters_reviewed=True,
        function_diff_reviewed=True,
        checklist=ReviewChecklist(
            all_sections_decided=True,
            low_confidence_resolved=True,
            no_duplicates=True,
            all_items_have_sources=True,
            no_conclusion_language=True,
            plan_parameters_reviewed=True,
            test_date_confirmed=True,
        ),
    )


def test_reads_application_without_exposing_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "secret-token"
        assert request.url.params["programId"] == "81202"
        return httpx.Response(200, json={"code": 10000, "data": {"id": 1}})

    client = PDSClient(
        lambda: "secret-token", transport=httpx.MockTransport(handler)
    )
    try:
        assert client.get_application(81202)["data"]["id"] == 1
    finally:
        client.close()


def test_rejects_expired_session_with_sanitized_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "expired"})

    client = PDSClient(
        lambda: "secret-token", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(PDSAPIError) as exc_info:
            client.get_application(81202)
        assert "secret-token" not in str(exc_info.value)
    finally:
        client.close()


def test_submission_is_disabled_by_default():
    client = PDSClient(
        lambda: "secret-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"code": 10000})
        ),
    )
    try:
        with pytest.raises(SubmissionDisabledError):
            client.submit_plan(
                plan_id=15191,
                program_id=81202,
                process_choices=[],
                approval=approval(),
            )
    finally:
        client.close()


def test_downloads_same_origin_ticket():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/system/file/authFileToken"
        assert request.url.query == b"token=temporary"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"%PDF-content")

    client = PDSClient(
        lambda: "secret-token", transport=httpx.MockTransport(handler)
    )
    try:
        content = client.download_from_ticket(
            {"code": 10000, "data": "?token=temporary"},
            file_service_config={
                "code": 10000,
                "data": {
                    "downloadBaseUrl": "https://files.pd-sts.com/system/file/authFileToken"
                },
            },
            expected_file_name="manual.pdf",
        )
        assert content == b"%PDF-content"
    finally:
        client.close()


def test_rejects_cross_origin_download_ticket():
    client = PDSClient(
        lambda: "secret-token",
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("untrusted URL must not be requested")
        ),
    )
    try:
        with pytest.raises(PDSAPIError, match="absolute URL"):
            client.download_from_ticket(
                {"data": "https://attacker.invalid/manual.pdf"},
                file_service_config={
                    "data": {"downloadBaseUrl": "https://files.pd-sts.com"}
                },
                expected_file_name="manual.pdf",
            )
    finally:
        client.close()


def test_rejects_html_disguised_as_legacy_word_document():
    client = PDSClient(
        lambda: "secret-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, content=b"<!doctype html><html>not a document</html>"
            )
        ),
    )
    try:
        with pytest.raises(PDSAPIError, match="HTML"):
            client.download_from_ticket(
                {"data": "/download/temporary"},
                file_service_config={
                    "data": {"downloadBaseUrl": "https://files.pd-sts.com"}
                },
                expected_file_name="manual.doc",
            )
    finally:
        client.close()


def test_accepts_legacy_doc_signature_when_server_mislabels_docx():
    ole_content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"legacy-word"
    client = PDSClient(
        lambda: "secret-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=ole_content)
        ),
    )
    try:
        content = client.download_from_ticket(
            {"data": "/download/temporary"},
            file_service_config={
                "data": {"downloadBaseUrl": "https://files.pd-sts.com"}
            },
            expected_file_name="manual.docx",
        )
        assert content == ole_content
    finally:
        client.close()


def test_reads_registration_plan_records_and_dynamic_status_dictionary():
    observed_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_paths.append(request.url.path)
        assert request.headers["authorization"] == "secret-token"
        return httpx.Response(200, json={"code": 10000, "data": {}})

    client = PDSClient(
        lambda: "secret-token", transport=httpx.MockTransport(handler)
    )
    try:
        client.query_plan_records({"programCode": "STS1260215"})
        client.get_plan_search_conditions()
        client.get_program_pdf_files(80995)
        client.get_file_service_config()
        assert observed_paths == [
            "/registration/ycsTestPlanRecord/listYcsTestPlanRecord",
            "/registration/ycsTestPlan/initSearchCondition",
            "/management/ycsApply/getYcsProgramPdfFiles",
            "/system/file/getKkfileViewUrl",
        ]
    finally:
        client.close()

