from __future__ import annotations

from collections.abc import Callable, Mapping
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from typing import Any
from zipfile import BadZipFile, ZipFile

import httpx

from pds_harness.domain.errors import PDSAPIError, SubmissionDisabledError
from pds_harness.domain.models import SubmissionApproval


class PDSClient:
    QUERY_PROJECTS_PATH = "/management/queryStatistics/queryStatisticsList"
    QUERY_PLAN_RECORDS_PATH = (
        "/registration/ycsTestPlanRecord/listYcsTestPlanRecord"
    )
    APPLICATION_PATH = "/management/ycsApply/getYcsApplyInfo"
    PROGRAM_PDF_FILES_PATH = "/management/ycsApply/getYcsProgramPdfFiles"
    SAMPLE_PATH = "/management/sampleManage/getLatestYcsProgramSample"
    LIST_PLANS_PATH = "/registration/ycsTestPlanRecord/listYcsRegTestPlan"
    GET_PLAN_PATH = "/registration/ycsTestPlanRecord/getYcsRegPlanInfo"
    SAVE_PLAN_PATH = "/registration/ycsTestPlanRecord/saveYcsRegPlanInfo"
    CANDIDATES_PATH = "/registration/ycsTestPlan/getPlanInfoCandidate"
    PLAN_SEARCH_CONDITIONS_PATH = "/registration/ycsTestPlan/initSearchCondition"
    SUBMIT_PLAN_PATH = "/registration/ycsTestPlanRecord/submitYcsRegPlanInfo"
    DICTIONARY_PATH = "/system/dictionary/getDictionaryDataInfoList"
    DOWNLOAD_TICKET_PATH = "/system/file/download"
    FILE_SERVICE_CONFIG_PATH = "/system/file/getKkfileViewUrl"

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        base_url: str = "https://pds.pd-sts.com",
        timeout: float = 30.0,
        submit_enabled: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._submit_enabled = submit_enabled
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "Accept-Language": "zh-CN"},
        )

    def __enter__(self) -> "PDSClient":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _authorization_header(self) -> dict[str, str]:
        token = self._token_provider().strip()
        if not token:
            raise PDSAPIError("PDS session is unavailable; login is required")
        if any(character in token for character in "\r\n"):
            raise PDSAPIError("PDS session token has an invalid format")
        return {"authorization": token}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=dict(json_body) if json_body is not None else None,
                headers=self._authorization_header(),
            )
        except httpx.HTTPError as exc:
            raise PDSAPIError(f"PDS request failed: {type(exc).__name__}") from exc

        if response.status_code == 401:
            raise PDSAPIError("PDS session expired (HTTP 401)")
        if response.is_error:
            raise PDSAPIError(f"PDS returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PDSAPIError("PDS returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise PDSAPIError("PDS returned an unexpected JSON shape")

        code = payload.get("code", payload.get("messageCode"))
        if code is not None and int(code) != 10000:
            message = payload.get("msg", payload.get("messageContent", "unknown error"))
            raise PDSAPIError(f"PDS business error {code}: {message}")
        return payload

    def get_application(self, program_id: int, *, status: int = 2) -> dict[str, Any]:
        return self._request(
            "GET",
            self.APPLICATION_PATH,
            params={"programId": program_id, "status": status},
        )

    def get_program_pdf_files(self, program_id: int) -> dict[str, Any]:
        return self._request(
            "GET", self.PROGRAM_PDF_FILES_PATH, params={"programId": program_id}
        )

    def query_projects(self, query: Mapping[str, Any]) -> dict[str, Any]:
        """Query the broad project-statistics list used by the PDS web UI.

        The exact status code for “待计划编制” remains a contract-discovery item,
        so callers must filter and validate both the returned status text and the
        registration-test type before enabling write operations.
        """
        body = {"page_number": 1, "page_size": 999999, **dict(query)}
        return self._request("POST", self.QUERY_PROJECTS_PATH, json_body=body)

    def query_plan_records(self, query: Mapping[str, Any]) -> dict[str, Any]:
        body = {"page_number": 1, "page_size": 40, **dict(query)}
        return self._request(
            "POST", self.QUERY_PLAN_RECORDS_PATH, json_body=body
        )

    def get_plan_search_conditions(self) -> dict[str, Any]:
        return self._request("GET", self.PLAN_SEARCH_CONDITIONS_PATH)

    def get_latest_sample(self, program_id: int) -> dict[str, Any]:
        return self._request(
            "GET", self.SAMPLE_PATH, params={"programId": program_id}
        )

    def get_dictionary(self, dict_id: int) -> dict[str, Any]:
        return self._request(
            "GET", self.DICTIONARY_PATH, params={"dictId": dict_id}
        )

    def request_download_ticket(
        self, *, remote_path: str, file_name: str
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            self.DOWNLOAD_TICKET_PATH,
            params={"path": remote_path, "fileName": file_name},
        )

    def get_file_service_config(self) -> dict[str, Any]:
        return self._request("GET", self.FILE_SERVICE_CONFIG_PATH)

    def download_from_ticket(
        self,
        ticket_response: Mapping[str, Any],
        *,
        file_service_config: Mapping[str, Any],
        expected_file_name: str,
        maximum_bytes: int = 250 * 1024 * 1024,
    ) -> bytes:
        data: Any = ticket_response.get("data")
        if isinstance(data, Mapping) and "data" in data:
            data = data["data"]
        if isinstance(data, Mapping):
            candidate = data.get("url") or data.get("path") or data.get("filePath")
        else:
            candidate = data
        if not isinstance(candidate, str) or not candidate.strip():
            raise PDSAPIError("PDS download ticket did not contain a URL or path")

        candidate = candidate.strip()
        parsed_candidate = urlparse(candidate)
        if parsed_candidate.scheme or parsed_candidate.netloc:
            raise PDSAPIError("PDS download ticket unexpectedly contained an absolute URL")

        config_data: Any = file_service_config.get("data")
        if isinstance(config_data, Mapping) and "data" in config_data:
            config_data = config_data["data"]
        if not isinstance(config_data, Mapping):
            raise PDSAPIError("PDS file-service configuration has an invalid shape")
        download_base = config_data.get("downloadBaseUrl")
        if not isinstance(download_base, str) or not download_base.strip():
            raise PDSAPIError("PDS file-service configuration has no download base URL")
        download_base = download_base.strip().rstrip("/")
        parsed_base = urlparse(download_base)
        if (
            parsed_base.scheme not in {"http", "https"}
            or not parsed_base.netloc
            or parsed_base.username
            or parsed_base.password
            or parsed_base.fragment
        ):
            raise PDSAPIError("PDS file-service base URL is not permitted")
        target = f"{download_base}{candidate}"
        parsed_target = urlparse(target)
        if (
            parsed_target.scheme != parsed_base.scheme
            or parsed_target.netloc != parsed_base.netloc
        ):
            raise PDSAPIError("PDS download ticket points to an untrusted origin")

        try:
            with self._client.stream(
                "GET", target, headers={"Accept": "application/octet-stream"}
            ) as response:
                if response.is_error:
                    raise PDSAPIError(
                        f"PDS download returned HTTP {response.status_code}"
                    )
                content_type = response.headers.get("content-type", "").lower()
                if any(
                    marker in content_type
                    for marker in ("text/html", "application/json", "text/xml")
                ):
                    raise PDSAPIError(
                        "PDS file service returned a non-document response"
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise PDSAPIError(
                            f"PDS download exceeds the {maximum_bytes}-byte limit"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                _validate_document_content(content, expected_file_name)
                return content
        except httpx.HTTPError as exc:
            raise PDSAPIError(
                f"PDS download failed: {type(exc).__name__}"
            ) from exc
    def list_plans(self, program_id: int) -> dict[str, Any]:
        return self._request(
            "POST",
            self.LIST_PLANS_PATH,
            json_body={
                "programId": program_id,
                "wasQuery": "0",
                "page_number": 1,
                "page_size": 999999,
            },
        )

    def get_plan(
        self,
        program_id: int,
        *,
        plan_id: int | None = None,
        big_or_small: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"programId": program_id}
        if plan_id is not None:
            params["planId"] = plan_id
            params["sourcePlanId"] = plan_id
        if big_or_small is not None:
            params["bigOrSmall"] = big_or_small
        return self._request("GET", self.GET_PLAN_PATH, params=params)

    def save_plan_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", self.SAVE_PLAN_PATH, json_body=payload)

    def get_submit_candidates(self, plan_id: int) -> dict[str, Any]:
        return self._request(
            "GET", self.CANDIDATES_PATH, params={"planId": plan_id}
        )

    def submit_plan(
        self,
        *,
        plan_id: int,
        program_id: int,
        process_choices: list[dict[str, Any]],
        approval: SubmissionApproval,
    ) -> dict[str, Any]:
        if not self._submit_enabled:
            raise SubmissionDisabledError(
                "PDS submission is disabled by the runtime kill switch"
            )
        if not approval.is_valid():
            raise SubmissionDisabledError(
                "PDS submission requires a complete human review approval"
            )
        return self._request(
            "POST",
            self.SUBMIT_PLAN_PATH,
            json_body={
                "planId": plan_id,
                "programId": program_id,
                "ycsProcessNodeChooses": process_choices,
            },
        )


def detect_document_suffix(content: bytes) -> str:
    """Return the document type proven by content, independent of its name."""

    if not content:
        raise PDSAPIError("PDS file service returned an empty document")
    stripped = content.lstrip()
    if stripped.lower().startswith((b"<!doctype html", b"<html")):
        raise PDSAPIError("PDS file service returned HTML instead of a document")
    if content.startswith(b"%PDF-"):
        return ".pdf"
    if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") or stripped.startswith(
        b"{\\rtf"
    ):
        return ".doc"
    try:
        with ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
    except BadZipFile:
        names = set()
    if "[Content_Types].xml" in names and "word/document.xml" in names:
        return ".docx"
    raise PDSAPIError("Downloaded file has no supported document signature")


def _validate_document_content(content: bytes, file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix not in {".pdf", ".doc", ".docx"}:
        raise PDSAPIError("Downloaded file type is not supported")
    return detect_document_suffix(content)

