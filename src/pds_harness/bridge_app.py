from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException

from pds_harness.adapters.browser_session import (
    BrowserSessionConfig,
    PDSBrowserSession,
)
from pds_harness.adapters.pds_client import PDSClient
from pds_harness.config import load_settings
from pds_harness.domain.errors import (
    PDSAPIError,
    PDSConnectionError,
    PDSConnectionNotStartedError,
    PDSLoginRequiredError,
    PDSProjectNotFoundError,
    PDSProjectSafetyError,
)
from pds_harness.domain.models import FunctionCatalog
from pds_harness.services.pds_connection import PDSConnectionManager


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "config" / "registration_defaults.yaml"
ENV_PATH = PROJECT_ROOT / ".env"


def _build_manager() -> PDSConnectionManager:
    settings = load_settings(SETTINGS_PATH)
    profile_dir = settings.storage.requested_archive_root / "pds-browser-profile"
    login_url = f"{settings.pds.base_url.rstrip('/')}/ycs-v2-back/"
    return PDSConnectionManager(
        session_factory=lambda: PDSBrowserSession(
            BrowserSessionConfig(
                profile_dir=profile_dir,
                login_url=login_url,
                channel="chrome",
                headless=False,
                credentials_env_path=ENV_PATH,
            )
        ),
        client_factory=lambda token_provider: PDSClient(
            token_provider,
            base_url=settings.pds.base_url,
            submit_enabled=False,
        ),
        archive_root=settings.storage.requested_archive_root,
        plan_defaults=settings.registration_plan,
        write_drafts_enabled=settings.pds.write_drafts_enabled,
    )


PDS_CONNECTION = _build_manager()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        yield
    finally:
        PDS_CONNECTION.shutdown()


app = FastAPI(
    title="PDS Session Bridge",
    version="0.1.0",
    lifespan=_lifespan,
)


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, PDSProjectNotFoundError):
        status_code = 404
    elif isinstance(
        exc,
        (
            PDSConnectionNotStartedError,
            PDSLoginRequiredError,
            PDSProjectSafetyError,
        ),
    ):
        status_code = 409
    else:
        status_code = 503
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "pds-session-bridge"}


@app.get("/api/pds-connection/status")
def status() -> dict[str, Any]:
    try:
        return PDS_CONNECTION.status().to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/pds-connection/start")
def start() -> dict[str, Any]:
    try:
        return PDS_CONNECTION.start().to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/pds-connection/probe")
def probe() -> dict[str, Any]:
    try:
        return PDS_CONNECTION.probe().to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/pds-connection/stop")
def stop() -> dict[str, Any]:
    try:
        return PDS_CONNECTION.stop().to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/projects/{program_code}/prepare")
def prepare_project(program_code: str) -> dict[str, Any]:
    try:
        return PDS_CONNECTION.prepare_project(program_code).to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/projects/{program_code}/prepare-documents")
def prepare_project_documents(program_code: str) -> dict[str, Any]:
    try:
        return PDS_CONNECTION.prepare_project_documents(program_code)
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/projects/{program_code}/inspect-plan-contract")
def inspect_plan_contract(program_code: str) -> dict[str, Any]:
    try:
        return PDS_CONNECTION.inspect_plan_contract(program_code)
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/projects/{program_code}/create-plan-draft")
def create_plan_draft(program_code: str) -> dict[str, Any]:
    try:
        return PDS_CONNECTION.create_plan_draft(program_code)
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/projects/{program_code}/write-function-catalog")
def write_function_catalog(
    program_code: str, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    try:
        catalog = FunctionCatalog.model_validate(payload)
        return PDS_CONNECTION.write_function_catalog(program_code, catalog)
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)


@app.post("/api/projects/{program_code}/download-exception-sample")
def download_exception_sample(
    program_code: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    if payload.get("acknowledge_identity_mismatch") is not True:
        raise HTTPException(
            status_code=422,
            detail="Explicit identity-mismatch acknowledgement is required",
        )
    try:
        return PDS_CONNECTION.download_exception_sample(program_code).to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_http_error(exc)

