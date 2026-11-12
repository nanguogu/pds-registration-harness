from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from queue import Queue
import re
from datetime import date
from threading import Lock, Thread
from typing import Any, Literal, Protocol

from pds_harness.domain.errors import (
    PDSAPIError,
    PDSConnectionError,
    PDSConnectionNotStartedError,
    PDSLoginRequiredError,
    PDSProjectNotFoundError,
    PDSProjectSafetyError,
)
from pds_harness.domain.models import (
    AttachmentMetadata,
    FunctionCatalog,
    ParsedManualArtifact,
    PlanRuntimeValues,
    RegistrationDefaults,
)
from pds_harness.services.archive import ProjectArchive
from pds_harness.services.attachment_selection import select_manual_attachment
from pds_harness.services.document_parser import parse_document
from pds_harness.services.document_strategy import (
    FunctionSourceStrategy,
    classify_document,
    merge_attachments,
    plan_project_documents,
)
from pds_harness.services.legacy_word import convert_legacy_doc_to_docx
from pds_harness.services.function_list_import import (
    DIRECT_IMPORT_VERSION,
    generate_direct_import_artifacts,
)
from pds_harness.adapters.pds_client import detect_document_suffix
from pds_harness.services.catalog_validation import CatalogValidator
from pds_harness.services.dictionary_resolver import (
    ONSITE_DICTIONARY_ID,
    TEST_METHOD_DICTIONARY_ID,
    resolve_registration_dictionaries,
)
from pds_harness.services.plan_orchestrator import PlanOrchestrator, unwrap_plan
from pds_harness.services.plan_payload import apply_catalog_to_plan, build_function_tree
from pds_harness.services.plan_resolution import (
    DraftResolutionKind,
    resolve_existing_draft,
)


ConnectionState = Literal["stopped", "starting", "login_required", "ready", "error"]


class BrowserSession(Protocol):
    def open(self) -> None: ...

    def authorization_token(self) -> str: ...

    def is_authenticated(self) -> bool: ...

    def close(self) -> None: ...


class ReadOnlyPDSClient(Protocol):
    def __enter__(self) -> "ReadOnlyPDSClient": ...

    def __exit__(self, *_args: Any) -> None: ...

    def query_projects(self, query: Mapping[str, Any]) -> dict[str, Any]: ...

    def query_plan_records(self, query: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_plan_search_conditions(self) -> dict[str, Any]: ...

    def get_application(self, program_id: int, *, status: int = 2) -> dict[str, Any]: ...

    def get_program_pdf_files(self, program_id: int) -> dict[str, Any]: ...

    def get_latest_sample(self, program_id: int) -> dict[str, Any]: ...

    def request_download_ticket(
        self, *, remote_path: str, file_name: str
    ) -> dict[str, Any]: ...

    def get_file_service_config(self) -> dict[str, Any]: ...

    def get_dictionary(self, dict_id: int) -> dict[str, Any]: ...

    def list_plans(self, program_id: int) -> dict[str, Any]: ...

    def get_plan(
        self,
        program_id: int,
        *,
        plan_id: int | None = None,
        big_or_small: int | None = None,
    ) -> dict[str, Any]: ...

    def save_plan_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def download_from_ticket(
        self,
        ticket_response: Mapping[str, Any],
        *,
        file_service_config: Mapping[str, Any],
        expected_file_name: str,
        maximum_bytes: int = 250 * 1024 * 1024,
    ) -> bytes: ...


@dataclass(frozen=True)
class PDSConnectionSnapshot:
    state: ConnectionState
    browser_open: bool
    authenticated: bool
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PDSProbeResult:
    state: ConnectionState
    browser_open: bool
    authenticated: bool
    read_only: bool
    business_code: int
    returned_count: int | None
    total_count: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PDSProjectIntakeResult:
    stage: Literal[
        "downloaded_and_parsed",
        "downloaded_parse_unsupported",
        "manual_selection_required",
        "manual_not_found",
        "manual_identity_mismatch",
        "exception_sample_downloaded_and_parsed",
        "exception_sample_downloaded_parse_unsupported",
    ]
    program_id: int
    program_code: str
    software_name: str
    software_version: str | None
    status: str
    confidentiality: str
    test_type: str
    unit_name: str | None
    receiver_phone: str | None
    receiver_phone_field: str | None
    phone_candidate_fields: tuple[str, ...]
    attachment_selection: str
    matched_priority: str | None
    attachment_candidates: tuple[dict[str, Any], ...]
    manual: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SessionFactory = Callable[[], BrowserSession]
ClientFactory = Callable[[Callable[[], str]], ReadOnlyPDSClient]
Command = tuple[str, dict[str, Any], Future[Any]]


class PDSConnectionManager:
    """Own a visible PDS browser and its token on one dedicated thread.

    Playwright's synchronous API is thread-affine. FastAPI may run sync
    endpoints on different worker threads, so every browser and API operation
    is serialized through this owner thread. The token is read on demand and
    is never returned by this class.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        client_factory: ClientFactory,
        archive_root: Path | None = None,
        plan_defaults: RegistrationDefaults | None = None,
        write_drafts_enabled: bool = False,
        command_timeout: float = 180.0,
    ) -> None:
        self._session_factory = session_factory
        self._client_factory = client_factory
        self._archive_root = archive_root
        self._plan_defaults = plan_defaults
        self._write_drafts_enabled = write_drafts_enabled
        self._command_timeout = command_timeout
        self._commands: Queue[Command] = Queue()
        self._thread: Thread | None = None
        self._thread_lock = Lock()
        self._snapshot_lock = Lock()
        self._snapshot = PDSConnectionSnapshot(
            state="stopped", browser_open=False, authenticated=False
        )

    def start(self) -> PDSConnectionSnapshot:
        return self._call("start")

    def status(self) -> PDSConnectionSnapshot:
        with self._thread_lock:
            thread_alive = self._thread is not None and self._thread.is_alive()
        if not thread_alive:
            return self._get_snapshot()
        return self._call("status")

    def probe(self) -> PDSProbeResult:
        return self._call("probe")

    def prepare_project(self, program_code: str) -> PDSProjectIntakeResult:
        return self._call("prepare_project", program_code=program_code)

    def prepare_project_documents(self, program_code: str) -> dict[str, Any]:
        return self._call("prepare_project_documents", program_code=program_code)

    def download_exception_sample(
        self, program_code: str
    ) -> PDSProjectIntakeResult:
        return self._call(
            "prepare_project",
            program_code=program_code,
            allow_identity_mismatch=True,
        )

    def inspect_plan_contract(self, program_code: str) -> dict[str, Any]:
        return self._call("inspect_plan_contract", program_code=program_code)

    def create_plan_draft(self, program_code: str) -> dict[str, Any]:
        return self._call("create_plan_draft", program_code=program_code)

    def write_function_catalog(
        self, program_code: str, catalog: FunctionCatalog
    ) -> dict[str, Any]:
        return self._call(
            "write_function_catalog",
            program_code=program_code,
            catalog=catalog.model_dump(mode="json"),
        )

    def stop(self) -> PDSConnectionSnapshot:
        with self._thread_lock:
            thread_alive = self._thread is not None and self._thread.is_alive()
        if not thread_alive:
            return self._get_snapshot()
        return self._call("stop")

    def shutdown(self) -> None:
        with self._thread_lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return
        try:
            self._call("shutdown")
        finally:
            thread.join(timeout=5)

    def _call(self, command: str, **payload: Any) -> Any:
        self._ensure_worker()
        future: Future[Any] = Future()
        self._commands.put((command, payload, future))
        try:
            return future.result(timeout=self._command_timeout)
        except FutureTimeoutError as exc:
            raise PDSConnectionError(
                "PDS browser operation timed out; check the visible browser window"
            ) from exc

    def _ensure_worker(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = Thread(
                target=self._run,
                name="pds-browser-owner",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        session: BrowserSession | None = None
        while True:
            command, payload, future = self._commands.get()
            try:
                if command == "start":
                    session = self._handle_start(session)
                    future.set_result(self._get_snapshot())
                elif command == "status":
                    self._refresh_status(session)
                    future.set_result(self._get_snapshot())
                elif command == "probe":
                    future.set_result(self._handle_probe(session))
                elif command == "prepare_project":
                    future.set_result(
                        self._handle_prepare_project(
                            session,
                            str(payload["program_code"]),
                            allow_identity_mismatch=bool(
                                payload.get("allow_identity_mismatch", False)
                            ),
                        )
                    )
                elif command == "prepare_project_documents":
                    future.set_result(
                        self._handle_prepare_project_documents(
                            session, str(payload["program_code"])
                        )
                    )
                elif command == "inspect_plan_contract":
                    future.set_result(
                        self._handle_inspect_plan_contract(
                            session, str(payload["program_code"])
                        )
                    )
                elif command == "create_plan_draft":
                    future.set_result(
                        self._handle_create_plan_draft(
                            session, str(payload["program_code"])
                        )
                    )
                elif command == "write_function_catalog":
                    future.set_result(
                        self._handle_write_function_catalog(
                            session,
                            str(payload["program_code"]),
                            FunctionCatalog.model_validate(payload["catalog"]),
                        )
                    )
                elif command == "stop":
                    session = self._close_session(session)
                    future.set_result(self._get_snapshot())
                elif command == "shutdown":
                    self._close_session(session)
                    future.set_result(None)
                    return
                else:
                    raise PDSConnectionError("Unsupported PDS browser command")
            except Exception as exc:
                if not future.done():
                    future.set_exception(self._safe_exception(exc))

    def _handle_start(
        self, session: BrowserSession | None
    ) -> BrowserSession:
        if session is None:
            self._set_snapshot(
                PDSConnectionSnapshot(
                    state="starting", browser_open=False, authenticated=False
                )
            )
            session = self._session_factory()
            try:
                session.open()
            except Exception as exc:
                try:
                    session.close()
                finally:
                    self._set_snapshot(
                        PDSConnectionSnapshot(
                            state="error",
                            browser_open=False,
                            authenticated=False,
                            last_error="Unable to open the dedicated PDS browser",
                        )
                    )
                raise PDSConnectionError(
                    "Unable to open the dedicated PDS browser"
                ) from exc
        self._refresh_status(session)
        return session

    def _refresh_status(self, session: BrowserSession | None) -> None:
        if session is None:
            if self._get_snapshot().state == "error":
                return
            self._set_snapshot(
                PDSConnectionSnapshot(
                    state="stopped", browser_open=False, authenticated=False
                )
            )
            return
        authenticated = session.is_authenticated()
        if authenticated:
            minimize = getattr(session, "minimize", None)
            if callable(minimize):
                minimize()
        self._set_snapshot(
            PDSConnectionSnapshot(
                state="ready" if authenticated else "login_required",
                browser_open=True,
                authenticated=authenticated,
            )
        )

    def _handle_probe(
        self, session: BrowserSession | None
    ) -> PDSProbeResult:
        if session is None:
            raise PDSConnectionNotStartedError(
                "Open the dedicated PDS browser before testing the connection"
            )
        if not session.is_authenticated():
            self._refresh_status(session)
            raise PDSLoginRequiredError(
                "Complete the PDS login in the dedicated browser, then try again"
            )

        with self._client_factory(session.authorization_token) as client:
            payload = client.query_projects(
                {"page_number": 1, "page_size": 1}
            )
        returned_count, total_count = _project_counts(payload)
        self._set_snapshot(
            PDSConnectionSnapshot(
                state="ready", browser_open=True, authenticated=True
            )
        )
        code = payload.get("code", payload.get("messageCode", 10000))
        return PDSProbeResult(
            state="ready",
            browser_open=True,
            authenticated=True,
            read_only=True,
            business_code=int(code),
            returned_count=returned_count,
            total_count=total_count,
        )

    def _handle_prepare_project_documents(
        self,
        session: BrowserSession | None,
        program_code: str,
    ) -> dict[str, Any]:
        code = program_code.strip().upper()
        if not re.fullmatch(r"STS\d{7}", code):
            raise PDSProjectSafetyError(
                "Project code must use the exact STS1234567 format"
            )
        if session is None or not session.is_authenticated():
            self._refresh_status(session)
            raise PDSLoginRequiredError("PDS login is required")
        if self._archive_root is None:
            raise PDSConnectionError("Project archive storage is not configured")

        with self._client_factory(session.authorization_token) as client:
            projects_payload = client.query_plan_records(
                {"programCode": code, "page_number": 1, "page_size": 40}
            )
            project_row = _find_exact_project(projects_payload, code)
            program_id = _required_int(project_row, "programId", "id")
            status = _resolve_plan_status(
                project_row, client.get_plan_search_conditions()
            )
            if status not in {"待计划编制", "待提交"}:
                raise PDSProjectSafetyError(
                    "Project documents can only be prepared before plan submission"
                )
            if _first_text(project_row, "wasKeepSecret") != "0":
                raise PDSProjectSafetyError(
                    "The selected project is not explicitly marked 不保密"
                )
            application = client.get_application(program_id)
            attachments = merge_attachments(
                _extract_attachments(client.get_program_pdf_files(program_id)),
                _extract_attachments(client.get_latest_sample(program_id)),
            )
            document_plan = plan_project_documents(attachments)
            software_name = _first_text(
                project_row, "softwareName", "productName", "programName"
            )
            software_version = _find_text(
                application,
                ("softwareVersion", "version", "versionNo", "productVersion"),
            )
            unit_name = _first_text(
                project_row, "unitName", "companyName"
            ) or _find_text(
                application,
                ("unitName", "companyName", "entrustUnitName", "applyUnitName"),
            )
            receiver_phone, phone_field, _phone_fields = _find_receiver_phone(
                application
            )
            file_service_config = (
                client.get_file_service_config()
                if document_plan.documents
                else None
            )
            downloaded: list[dict[str, Any]] = []
            direct_catalog_artifacts: dict[str, str | int | bool] | None = None
            archive = ProjectArchive(self._archive_root)
            for role, selected in document_plan.documents:
                ticket = client.request_download_ticket(
                    remote_path=selected.remote_path,
                    file_name=selected.name,
                )
                assert file_service_config is not None
                content = client.download_from_ticket(
                    ticket,
                    file_service_config=file_service_config,
                    expected_file_name=selected.name,
                )
                detected_suffix = detect_document_suffix(content)
                declared_suffix = Path(selected.name).suffix.lower()
                archive_name = (
                    selected.name
                    if detected_suffix == declared_suffix
                    else f"{Path(selected.name).stem}{detected_suffix}"
                )
                archived = archive.store_document_bytes(
                    program_code=code,
                    role=role.value,
                    original_name=archive_name,
                    content=content,
                )
                identity_match = _filename_matches_software(
                    selected.name, software_name
                )
                parse_path = Path(archived.path)
                converted_path: str | None = None
                if parse_path.suffix.lower() == ".doc":
                    parse_path = convert_legacy_doc_to_docx(
                        parse_path,
                        target_dir=archive.document_directory(
                            program_code=code,
                            role=role.value,
                            stage="derived",
                        ),
                    )
                    converted_path = str(parse_path)
                parsed_path: str | None = None
                block_count = 0
                if parse_path.suffix.lower() in {".pdf", ".docx"}:
                    parsed = parse_document(parse_path)
                    if role.value == "manual":
                        parsed_payload: dict[str, Any] = ParsedManualArtifact(
                            program_id=program_id,
                            program_code=code,
                            software_name=software_name,
                            confidentiality="不保密",
                            attachment_id=selected.attachment_id,
                            original_name=selected.name,
                            original_path=archived.path,
                            manual_sha256=archived.sha256,
                            parsed_source_path=str(parse_path),
                            parsed_source_sha256=parsed.sha256,
                            media_type=parsed.media_type,
                            identity_match=identity_match,
                            function_generation_allowed=identity_match,
                            blocks=parsed.blocks,
                            needs_ocr=parsed.needs_ocr,
                            warnings=parsed.warnings,
                        ).model_dump(mode="json")
                    else:
                        parsed_payload = {
                            "schema_version": "1.0",
                            "program_id": program_id,
                            "program_code": code,
                            "software_name": software_name,
                            "role": role.value,
                            "attachment_id": selected.attachment_id,
                            "original_name": selected.name,
                            "original_path": archived.path,
                            "source_sha256": archived.sha256,
                            "identity_match": identity_match,
                            "parsed_document": parsed.model_dump(mode="json"),
                        }
                    parsed_path = archive.store_parsed_document(
                        program_code=code,
                        role=role.value,
                        source_sha256=archived.sha256,
                        payload=parsed_payload,
                    )
                    block_count = len(parsed.blocks)
                    if role.value == "function-list" and identity_match:
                        direct_catalog_artifacts = generate_direct_import_artifacts(
                            Path(parsed_path), archive_root=self._archive_root
                        )
                downloaded.append(
                    {
                        "role": role.value,
                        "attachment_id": selected.attachment_id,
                        "original_name": selected.name,
                        "original_path": archived.path,
                        "view_path": archived.path,
                        "converted_path": converted_path,
                        "parsed_json_path": parsed_path,
                        "sha256": archived.sha256,
                        "declared_suffix": declared_suffix,
                        "detected_suffix": detected_suffix,
                        "identity_match": identity_match,
                        "block_count": block_count,
                    }
                )

        candidates = [
            {
                "role": classify_document(item.name).value,
                "attachment_id": item.attachment_id,
                "name": item.name,
                "uploaded_at": item.uploaded_at,
            }
            for item in attachments
        ]
        return {
            "stage": "documents_prepared",
            "program_id": program_id,
            "program_code": code,
            "software_name": software_name,
            "software_version": software_version,
            "status": status,
            "confidentiality": "不保密",
            "unit_name": unit_name,
            "receiver_phone": receiver_phone,
            "receiver_phone_field": phone_field,
            "function_source_strategy": document_plan.strategy.value,
            "candidates": candidates,
            "documents": downloaded,
            "function_catalog": direct_catalog_artifacts,
            "warnings": list(document_plan.warnings),
            "submitted": False,
        }

    def _handle_prepare_project(
        self,
        session: BrowserSession | None,
        program_code: str,
        *,
        allow_identity_mismatch: bool = False,
    ) -> PDSProjectIntakeResult:
        code = program_code.strip().upper()
        if not re.fullmatch(r"STS\d{7}", code):
            raise PDSProjectSafetyError(
                "Project code must use the exact STS1234567 format"
            )
        if session is None:
            raise PDSConnectionNotStartedError(
                "Open the dedicated PDS browser before reading a project"
            )
        if not session.is_authenticated():
            self._refresh_status(session)
            raise PDSLoginRequiredError(
                "Complete the PDS login in the dedicated browser, then try again"
            )

        with self._client_factory(session.authorization_token) as client:
            projects_payload = client.query_plan_records(
                {"programCode": code, "page_number": 1, "page_size": 40}
            )
            project_row = _find_exact_project(projects_payload, code)
            program_id = _required_int(project_row, "programId", "id")
            search_conditions = client.get_plan_search_conditions()
            status = _resolve_plan_status(project_row, search_conditions)
            secrecy_code = _first_text(project_row, "wasKeepSecret")
            confidentiality = (
                "不保密"
                if secrecy_code == "0"
                else "保密"
                if secrecy_code == "1"
                else "未知"
            )
            test_type = "软件产品登记测试"
            if status != "待计划编制":
                raise PDSProjectSafetyError(
                    "The selected project is no longer in 待计划编制 status"
                )
            if confidentiality != "不保密":
                raise PDSProjectSafetyError(
                    "The selected project is not explicitly marked 不保密"
                )
            application = client.get_application(program_id)
            program_files = client.get_program_pdf_files(program_id)
            sample = client.get_latest_sample(program_id)
            program_attachments = _extract_attachments(program_files)
            sample_attachments = _extract_attachments(sample)
            attachments = program_attachments or sample_attachments
            selection = select_manual_attachment(attachments)

            software_name = _first_text(
                project_row, "softwareName", "productName", "programName"
            )
            unit_name = _first_text(project_row, "unitName", "companyName") or _find_text(
                application,
                ("unitName", "companyName", "entrustUnitName", "applyUnitName"),
            )
            software_version = _find_text(
                application,
                ("softwareVersion", "version", "versionNo", "productVersion"),
            )
            receiver_phone, phone_field, phone_fields = _find_receiver_phone(
                application
            )

            visible_candidates = selection.candidates or tuple(attachments)
            candidates = tuple(
                {
                    "attachment_id": item.attachment_id,
                    "name": item.name,
                    "uploaded_at": item.uploaded_at,
                }
                for item in visible_candidates
            )
            selected = selection.selected
            if selected is None:
                stage = (
                    "manual_selection_required"
                    if selection.kind.value == "ambiguous"
                    else "manual_not_found"
                )
                return PDSProjectIntakeResult(
                    stage=stage,
                    program_id=program_id,
                    program_code=code,
                    software_name=software_name,
                    software_version=software_version,
                    status=status,
                    confidentiality=confidentiality,
                    test_type=test_type,
                    unit_name=unit_name,
                    receiver_phone=receiver_phone,
                    receiver_phone_field=phone_field,
                    phone_candidate_fields=phone_fields,
                    attachment_selection=selection.kind.value,
                    matched_priority=selection.matched_priority,
                    attachment_candidates=candidates,
                    manual=None,
                )
            identity_matches = _filename_matches_software(
                selected.name, software_name
            )
            if not identity_matches and not allow_identity_mismatch:
                return PDSProjectIntakeResult(
                    stage="manual_identity_mismatch",
                    program_id=program_id,
                    program_code=code,
                    software_name=software_name,
                    software_version=software_version,
                    status=status,
                    confidentiality=confidentiality,
                    test_type=test_type,
                    unit_name=unit_name,
                    receiver_phone=receiver_phone,
                    receiver_phone_field=phone_field,
                    phone_candidate_fields=phone_fields,
                    attachment_selection=selection.kind.value,
                    matched_priority=selection.matched_priority,
                    attachment_candidates=candidates,
                    manual=None,
                )
            if self._archive_root is None:
                raise PDSConnectionError("Project archive storage is not configured")

            ticket = client.request_download_ticket(
                remote_path=selected.remote_path,
                file_name=selected.name,
            )
            file_service_config = client.get_file_service_config()
            content = client.download_from_ticket(
                ticket,
                file_service_config=file_service_config,
                expected_file_name=selected.name,
            )

        detected_suffix = detect_document_suffix(content)
        declared_suffix = Path(selected.name).suffix.lower()
        archive_name = selected.name
        ingestion_warnings: list[str] = []
        if detected_suffix != declared_suffix:
            archive_name = f"{Path(selected.name).stem}{detected_suffix}"
            ingestion_warnings.append(
                f"附件声明扩展名 {declared_suffix or '<无>'} 与内容签名不一致，"
                f"已按 {detected_suffix} 归档"
            )

        archive = ProjectArchive(self._archive_root)
        archived = archive.store_bytes(
            program_code=code,
            original_name=archive_name,
            content=content,
        )
        manual: dict[str, Any] = {
            "attachment_id": selected.attachment_id,
            "name": selected.name,
            "uploaded_at": selected.uploaded_at,
            "path": archived.path,
            "sha256": archived.sha256,
            "size": archived.size,
            "declared_suffix": declared_suffix,
            "detected_suffix": detected_suffix,
            "ingestion_warnings": ingestion_warnings,
            "identity_match": identity_matches,
            "function_generation_allowed": identity_matches,
            "usage_restriction": (
                None if identity_matches else "exception_sample_only"
            ),
        }
        suffix = Path(archived.path).suffix.lower()
        parse_path = Path(archived.path)
        if suffix == ".doc":
            parse_path = convert_legacy_doc_to_docx(
                parse_path,
                target_dir=archive.document_directory(
                    program_code=code, role="manual", stage="derived"
                ),
            )
            manual["converted_path"] = str(parse_path)
        if parse_path.suffix.lower() in {".pdf", ".docx"}:
            parsed = parse_document(parse_path)
            artifact = ParsedManualArtifact(
                program_id=program_id,
                program_code=code,
                software_name=software_name,
                confidentiality=confidentiality,
                attachment_id=selected.attachment_id,
                original_name=selected.name,
                original_path=archived.path,
                manual_sha256=archived.sha256,
                parsed_source_path=str(parse_path),
                parsed_source_sha256=parsed.sha256,
                media_type=parsed.media_type,
                identity_match=identity_matches,
                function_generation_allowed=identity_matches,
                usage_restriction=(
                    None if identity_matches else "exception_sample_only"
                ),
                blocks=parsed.blocks,
                needs_ocr=parsed.needs_ocr,
                warnings=[*ingestion_warnings, *parsed.warnings],
            )
            parsed_json_path = archive.store_parsed_manual(
                program_code=code,
                manual_sha256=archived.sha256,
                payload=artifact.model_dump(mode="json"),
            )
            manual.update(
                {
                    "media_type": parsed.media_type,
                    "block_count": len(parsed.blocks),
                    "needs_ocr": parsed.needs_ocr,
                    "warnings": [*ingestion_warnings, *parsed.warnings],
                    "parsed_json_path": parsed_json_path,
                }
            )
            stage = (
                "downloaded_and_parsed"
                if identity_matches
                else "exception_sample_downloaded_and_parsed"
            )
        else:
            stage = (
                "downloaded_parse_unsupported"
                if identity_matches
                else "exception_sample_downloaded_parse_unsupported"
            )

        return PDSProjectIntakeResult(
            stage=stage,
            program_id=program_id,
            program_code=code,
            software_name=software_name,
            software_version=software_version,
            status=status,
            confidentiality=confidentiality,
            test_type=test_type,
            unit_name=unit_name,
            receiver_phone=receiver_phone,
            receiver_phone_field=phone_field,
            phone_candidate_fields=phone_fields,
            attachment_selection=selection.kind.value,
            matched_priority=selection.matched_priority,
            attachment_candidates=candidates,
            manual=manual,
        )

    def _handle_inspect_plan_contract(
        self,
        session: BrowserSession | None,
        program_code: str,
    ) -> dict[str, Any]:
        code = program_code.strip().upper()
        if not re.fullmatch(r"STS\d{7}", code):
            raise PDSProjectSafetyError(
                "Project code must use the exact STS1234567 format"
            )
        if session is None:
            raise PDSConnectionNotStartedError(
                "Open the dedicated PDS browser before reading a project"
            )
        if not session.is_authenticated():
            self._refresh_status(session)
            raise PDSLoginRequiredError(
                "Complete the PDS login in the dedicated browser, then try again"
            )

        with self._client_factory(session.authorization_token) as client:
            projects_payload = client.query_plan_records(
                {"programCode": code, "page_number": 1, "page_size": 40}
            )
            project_row = _find_exact_project(projects_payload, code)
            program_id = _required_int(project_row, "programId", "id")
            search_conditions = client.get_plan_search_conditions()
            status = _resolve_plan_status(project_row, search_conditions)
            confidentiality = (
                "不保密"
                if _first_text(project_row, "wasKeepSecret") == "0"
                else "保密"
                if _first_text(project_row, "wasKeepSecret") == "1"
                else "未知"
            )
            if status not in {"待计划编制", "待提交"}:
                raise PDSProjectSafetyError(
                    "The selected project is neither 待计划编制 nor 待提交"
                )
            if confidentiality != "不保密":
                raise PDSProjectSafetyError(
                    "The selected project is not explicitly marked 不保密"
                )
            plans = client.list_plans(program_id)
            resolution = resolve_existing_draft(plans, program_id=program_id)
            if resolution.kind is DraftResolutionKind.RESUME:
                assert resolution.plan is not None
                draft_plan_id = resolution.plan.plan_id
                current_plan = client.get_plan(program_id, plan_id=draft_plan_id)
            else:
                draft_plan_id = None
                current_plan = client.get_plan(program_id)
        return {
            "program_code": code,
            "program_id": program_id,
            "status": status,
            "confidentiality": confidentiality,
            "plans_response": plans,
            "current_plan_response": current_plan,
            # Kept for compatibility with the initial contract-discovery UI.
            "initialized_plan_response": current_plan,
            "draft_resolution": resolution.kind.value,
            "draft_plan_id": draft_plan_id,
            "search_conditions_response": search_conditions,
            "read_only": True,
        }

    def _handle_create_plan_draft(
        self,
        session: BrowserSession | None,
        program_code: str,
    ) -> dict[str, Any]:
        code = program_code.strip().upper()
        if not re.fullmatch(r"STS\d{7}", code):
            raise PDSProjectSafetyError(
                "Project code must use the exact STS1234567 format"
            )
        if self._plan_defaults is None:
            raise PDSConnectionError("Registration plan defaults are not configured")
        if not self._write_drafts_enabled:
            raise PDSProjectSafetyError("PDS draft writing is disabled")
        if session is None or not session.is_authenticated():
            self._refresh_status(session)
            raise PDSLoginRequiredError("PDS login is required")

        with self._client_factory(session.authorization_token) as client:
            projects_payload = client.query_plan_records(
                {"programCode": code, "page_number": 1, "page_size": 40}
            )
            project_row = _find_exact_project(projects_payload, code)
            program_id = _required_int(project_row, "programId", "id")
            search_conditions = client.get_plan_search_conditions()
            status = _resolve_plan_status(project_row, search_conditions)
            confidentiality = (
                "不保密"
                if _first_text(project_row, "wasKeepSecret") == "0"
                else "保密"
                if _first_text(project_row, "wasKeepSecret") == "1"
                else "未知"
            )
            if status != "待计划编制":
                raise PDSProjectSafetyError(
                    "The selected project is no longer in 待计划编制 status"
                )
            if confidentiality != "不保密":
                raise PDSProjectSafetyError(
                    "The selected project is not explicitly marked 不保密"
                )

            initialized = unwrap_plan(client.get_plan(program_id))
            base = initialized.get("ycsTestPlanBase")
            if not isinstance(base, Mapping):
                raise PDSAPIError("Initialized plan has no ycsTestPlanBase")
            current_username = str(
                base.get("compileUserName") or base.get("testMember") or ""
            ).strip()
            if not current_username:
                raise PDSProjectSafetyError(
                    "PDS did not return the current login username"
                )
            current_username = current_username.split(",", 1)[0].strip()
            compile_date_text = str(base.get("compileDate") or "").strip()
            try:
                compile_date = date.fromisoformat(compile_date_text)
            except ValueError as exc:
                raise PDSProjectSafetyError(
                    "PDS did not return a valid compile date"
                ) from exc

            dictionaries = resolve_registration_dictionaries(
                defaults=self._plan_defaults,
                test_method_payload=client.get_dictionary(
                    TEST_METHOD_DICTIONARY_ID
                ),
                onsite_payload=client.get_dictionary(ONSITE_DICTIONARY_ID),
            )
            result = PlanOrchestrator(
                client, write_drafts_enabled=True
            ).prepare_draft(
                program_id=program_id,
                defaults=self._plan_defaults,
                dictionaries=dictionaries,
                runtime=PlanRuntimeValues(
                    current_username=current_username,
                    compile_date=compile_date,
                    test_date=None,
                ),
            )
        persisted_base = result.payload.get("ycsTestPlanBase") or {}
        persisted_scene = result.payload.get("ycsTestPlanSceneTest") or {}
        persisted_implementation = result.payload.get("ycsTestPlanImplement") or {}
        return {
            "action": result.action,
            "persisted": result.persisted,
            "program_code": code,
            "program_id": program_id,
            "plan_id": result.plan_id,
            "current_username": current_username,
            "compile_date": compile_date.isoformat(),
            "test_date": None,
            "test_methods": persisted_base.get("testMethods"),
            "test_method": persisted_base.get("testMethod"),
            "standard_method": persisted_base.get("standardMethod"),
            "onsite_value": persisted_scene.get("wasNeed"),
            "test_users": persisted_implementation.get("testUsers"),
            "supervisors": persisted_implementation.get("supervisors"),
            "customers": persisted_implementation.get("customers"),
            "test_places": persisted_implementation.get("testPlaces"),
            "test_dates": persisted_implementation.get("testDates"),
            "submitted": False,
        }

    def _handle_write_function_catalog(
        self,
        session: BrowserSession | None,
        program_code: str,
        catalog: FunctionCatalog,
    ) -> dict[str, Any]:
        code = program_code.strip().upper()
        if not re.fullmatch(r"STS\d{7}", code):
            raise PDSProjectSafetyError(
                "Project code must use the exact STS1234567 format"
            )
        if not self._write_drafts_enabled:
            raise PDSProjectSafetyError("PDS draft writing is disabled")
        if session is None or not session.is_authenticated():
            self._refresh_status(session)
            raise PDSLoginRequiredError("PDS login is required")
        if catalog.program_code.strip().upper() != code:
            raise PDSProjectSafetyError("Catalog project code does not match the target")
        issues = CatalogValidator().validate(catalog)
        direct_source = catalog.prompt_version == DIRECT_IMPORT_VERSION
        blocking = [
            issue
            for issue in issues
            if issue.severity == "error"
            and not (direct_source and issue.code == "conclusion_language")
        ]
        if blocking:
            codes = sorted({issue.code for issue in blocking})
            raise PDSProjectSafetyError(
                "Catalog has blocking validation errors: " + ", ".join(codes)
            )

        with self._client_factory(session.authorization_token) as client:
            projects_payload = client.query_plan_records(
                {"programCode": code, "page_number": 1, "page_size": 40}
            )
            project_row = _find_exact_project(projects_payload, code)
            program_id = _required_int(project_row, "programId", "id")
            if catalog.program_id != program_id:
                raise PDSProjectSafetyError(
                    "Catalog program ID does not match the PDS project"
                )
            status = _resolve_plan_status(
                project_row, client.get_plan_search_conditions()
            )
            if status != "待提交":
                raise PDSProjectSafetyError(
                    "Function items can only be written to a 待提交 draft"
                )
            if _first_text(project_row, "wasKeepSecret") != "0":
                raise PDSProjectSafetyError(
                    "The selected project is not explicitly marked 不保密"
                )

            resolution = resolve_existing_draft(
                client.list_plans(program_id), program_id=program_id
            )
            if resolution.kind is not DraftResolutionKind.RESUME:
                raise PDSProjectSafetyError(
                    "Exactly one existing 待提交 draft is required"
                )
            assert resolution.plan is not None
            plan_id = resolution.plan.plan_id
            remote_plan = unwrap_plan(
                client.get_plan(program_id, plan_id=plan_id)
            )
            payload = apply_catalog_to_plan(
                remote_plan, catalog, plan_id=plan_id
            )
            client.save_plan_draft(payload)
            persisted = unwrap_plan(
                client.get_plan(program_id, plan_id=plan_id)
            )

        expected_tree = build_function_tree(catalog, plan_id=plan_id)
        persisted_tree = persisted.get("ycsTestPlanTesItems")
        if _function_tree_signature(persisted_tree) != _function_tree_signature(
            expected_tree
        ):
            raise PDSAPIError(
                "Saved function tree did not match the approved catalog on readback"
            )
        return {
            "action": "function_catalog_saved",
            "persisted": True,
            "program_code": code,
            "program_id": program_id,
            "plan_id": plan_id,
            "module_count": len(catalog.modules),
            "function_count": sum(
                len(module.functions) for module in catalog.modules
            ),
            "validation_warning_count": sum(
                issue.severity == "warning"
                or (direct_source and issue.code == "conclusion_language")
                for issue in issues
            ),
            "submitted": False,
        }

    def _close_session(
        self, session: BrowserSession | None
    ) -> None:
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                self._set_snapshot(
                    PDSConnectionSnapshot(
                        state="error",
                        browser_open=False,
                        authenticated=False,
                        last_error="Unable to close the dedicated PDS browser cleanly",
                    )
                )
                raise PDSConnectionError(
                    "Unable to close the dedicated PDS browser cleanly"
                ) from exc
        self._set_snapshot(
            PDSConnectionSnapshot(
                state="stopped", browser_open=False, authenticated=False
            )
        )
        return None

    @staticmethod
    def _safe_exception(exc: Exception) -> Exception:
        if isinstance(
            exc,
            (
                PDSAPIError,
                PDSConnectionError,
                PDSConnectionNotStartedError,
                PDSLoginRequiredError,
                PDSProjectNotFoundError,
                PDSProjectSafetyError,
            ),
        ):
            return exc
        return PDSConnectionError(
            f"PDS connection failed ({type(exc).__name__})"
        )

    def _get_snapshot(self) -> PDSConnectionSnapshot:
        with self._snapshot_lock:
            return self._snapshot

    def _set_snapshot(self, snapshot: PDSConnectionSnapshot) -> None:
        with self._snapshot_lock:
            self._snapshot = snapshot


def _project_counts(payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Return only aggregate metadata, never project/customer fields."""

    def visit(node: Any, depth: int = 0) -> tuple[int | None, int | None]:
        if depth > 4 or not isinstance(node, Mapping):
            return None, None
        for key in ("list", "records", "rows"):
            rows = node.get(key)
            if isinstance(rows, list):
                total = node.get("total", node.get("totalCount"))
                return len(rows), int(total) if total is not None else None
        for key in ("data", "result"):
            if key in node:
                found = visit(node[key], depth + 1)
                if found != (None, None):
                    return found
        return None, None

    return visit(payload)


def _function_tree_signature(value: Any) -> tuple[Any, ...] | None:
    """Compare the user-visible module/function tree after PDS normalization."""

    if not isinstance(value, list):
        return None
    modules: list[Any] = []
    for module in value:
        if not isinstance(module, Mapping):
            return None
        children = module.get("children")
        if not isinstance(children, list):
            return None
        functions: list[Any] = []
        for function in children:
            if not isinstance(function, Mapping):
                return None
            functions.append(
                (
                    str(function.get("itemType") or ""),
                    str(function.get("testItem") or "").strip(),
                    str(function.get("testExplain") or "").strip(),
                )
            )
        modules.append(
            (
                str(module.get("itemType") or ""),
                str(module.get("testItem") or "").strip(),
                tuple(functions),
            )
        )
    return tuple(modules)


def _project_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    def visit(node: Any, depth: int = 0) -> list[dict[str, Any]] | None:
        if depth > 5 or not isinstance(node, Mapping):
            return None
        for key in ("list", "records", "rows"):
            rows = node.get(key)
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, Mapping)]
        for key in ("data", "result"):
            if key in node:
                found = visit(node[key], depth + 1)
                if found is not None:
                    return found
        return None

    rows = visit(payload)
    if rows is None:
        raise PDSConnectionError("PDS project response has an unknown structure")
    return rows


def _find_exact_project(
    payload: Mapping[str, Any], program_code: str
) -> dict[str, Any]:
    matches = []
    for row in _project_rows(payload):
        row_code = _first_text(row, "code", "programCode", "projectCode").upper()
        if row_code == program_code:
            matches.append(row)
    if len(matches) != 1:
        raise PDSProjectNotFoundError(
            "Exact project-code lookup did not return one unique project"
        )
    return matches[0]


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _matching_scalar(
    node: Any, predicate: Callable[[str], bool]
) -> str:
    for _path, value in _scalar_entries(node):
        if predicate(value):
            return value
    return ""


def _resolve_plan_status(
    project_row: Mapping[str, Any], search_conditions: Mapping[str, Any]
) -> str:
    direct = _matching_scalar(
        project_row, lambda value: value == "待计划编制"
    )
    if direct:
        return direct
    status_code = _first_text(project_row, "planStatus")
    if not status_code:
        return ""
    for _path, mapping in _walk_mappings(search_conditions):
        code = _first_text(mapping, "code", "value", "id")
        label = _first_text(mapping, "value", "label", "name")
        if code == status_code and label and label != status_code:
            return label
    return status_code


def _required_int(mapping: Mapping[str, Any], *keys: str) -> int:
    value = _first_text(mapping, *keys)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PDSConnectionError("PDS project is missing its numeric program ID") from exc
    if result <= 0:
        raise PDSConnectionError("PDS project has an invalid program ID")
    return result


def _walk_mappings(
    node: Any, path: tuple[str, ...] = (), depth: int = 0
):
    if depth > 8:
        return
    if isinstance(node, Mapping):
        yield path, node
        for key, value in node.items():
            yield from _walk_mappings(value, (*path, str(key)), depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_mappings(value, (*path, str(index)), depth + 1)


def _scalar_entries(node: Any):
    for path, mapping in _walk_mappings(node):
        for key, value in mapping.items():
            if isinstance(value, (str, int, float)) and str(value).strip():
                yield (*path, str(key)), str(value).strip()


def _find_text(node: Any, keys: tuple[str, ...]) -> str | None:
    expected = {key.lower() for key in keys}
    for path, value in _scalar_entries(node):
        if path[-1].lower() in expected:
            return value
    return None


def _find_receiver_phone(
    application: Mapping[str, Any],
) -> tuple[str | None, str | None, tuple[str, ...]]:
    phone_tokens = ("phone", "mobile", "telephone", "tel", "电话", "手机")
    receiver_tokens = (
        "receiver",
        "recipient",
        "receive",
        "pickup",
        "pick_up",
        "draw",
        "getpeople",
        "getperson",
        "领取",
        "领用",
    )
    excluded_tokens = ("fax", "传真")
    candidates: list[tuple[int, str, str]] = []
    for path, value in _scalar_entries(application):
        field = ".".join(path)
        lowered = field.lower()
        if not any(token in lowered for token in phone_tokens):
            continue
        if any(token in lowered for token in excluded_tokens):
            continue
        digits = re.sub(r"\D", "", value)
        if len(digits) < 6:
            continue
        score = 1
        if any(token in lowered for token in receiver_tokens):
            score += 5
        if any(token in lowered for token in ("unit", "company", "单位", "公司")):
            score -= 1
        candidates.append((score, field, value))
    if not candidates:
        return None, None, ()
    candidates.sort(key=lambda item: (-item[0], item[1]))
    fields = tuple(item[1] for item in candidates)
    top_score = candidates[0][0]
    top = [item for item in candidates if item[0] == top_score]
    if len(top) == 1 and (top_score >= 6 or len(candidates) == 1):
        return top[0][2], top[0][1], fields
    return None, None, fields


def _extract_attachments(*payloads: Mapping[str, Any]) -> list[AttachmentMetadata]:
    name_keys = (
        "pdfFileName",
        "programFileName",
        "sampleFileName",
        "filename",
        "fileName",
        "originalName",
        "originalFileName",
        "uploadFileName",
        "name",
    )
    path_keys = (
        "pdfFilePath",
        "programFilePath",
        "sampleFilePath",
        "filePath",
        "remotePath",
        "uploadPath",
        "attachmentPath",
        "fileUrl",
        "url",
        "path",
    )
    id_keys = ("attachmentId", "fileId", "id", "uuid")
    uploaded_keys = ("uploadedAt", "uploadTime", "createTime", "createdAt")
    suffixes = (".pdf", ".docx", ".doc")
    found: list[AttachmentMetadata] = []
    seen: set[tuple[str, str]] = set()

    for payload in payloads:
        for mapping_path, mapping in _walk_mappings(payload):
            name = _first_text(mapping, *name_keys)
            remote_path = _first_text(mapping, *path_keys)
            if not name:
                for value in mapping.values():
                    if isinstance(value, str) and value.lower().strip().endswith(suffixes):
                        name = Path(value.strip()).name
                        break
            if not remote_path:
                for key, value in mapping.items():
                    if not isinstance(value, str):
                        continue
                    lowered_key = str(key).lower()
                    if "path" in lowered_key or "url" in lowered_key:
                        remote_path = value.strip()
                        break
            if not name or not name.lower().endswith(suffixes) or not remote_path:
                continue
            dedupe_key = (name, remote_path)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            attachment_id = _first_text(mapping, *id_keys)
            if not attachment_id:
                attachment_id = "/".join(mapping_path) or f"attachment-{len(found) + 1}"
            found.append(
                AttachmentMetadata(
                    attachment_id=attachment_id,
                    name=Path(name).name,
                    remote_path=remote_path,
                    uploaded_at=_first_text(mapping, *uploaded_keys) or None,
                )
            )
    return found


def _filename_matches_software(file_name: str, software_name: str) -> bool:
    def core(value: str) -> str:
        value = Path(value).stem.lower()
        value = re.sub(r"v?\d+(?:\.\d+)*", "", value)
        value = re.sub(
            r"用户手册|操作手册|产品说明书|软件说明书|厂商手册|设计说明书|"
            r"软件|系统|平台|应用|程序|客户端|服务端",
            "",
            value,
        )
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", value)

    file_core = core(file_name)
    software_core = core(software_name)
    if not file_core or not software_core:
        return True
    if file_core in software_core or software_core in file_core:
        return True
    return SequenceMatcher(None, file_core, software_core).ratio() >= 0.68
