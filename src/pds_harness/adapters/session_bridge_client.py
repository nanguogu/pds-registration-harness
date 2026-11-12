from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from pds_harness.domain.errors import (
    PDSConnectionError,
    PDSProjectNotFoundError,
    PDSProjectSafetyError,
)
from pds_harness.domain.models import FunctionCatalog


@dataclass(frozen=True)
class BridgeResult:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload


class PDSBridgeClient:
    """Call the local PDS Session Bridge without receiving its token."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 190.0,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("PDS Session Bridge must use a plain loopback HTTP URL")
        self._client = httpx.Client(
            base_url=normalized,
            transport=transport,
            timeout=timeout,
            trust_env=False,
        )

    def status(self) -> BridgeResult:
        return self._request("GET", "/api/pds-connection/status")

    def start(self) -> BridgeResult:
        return self._request("POST", "/api/pds-connection/start")

    def probe(self) -> BridgeResult:
        return self._request("POST", "/api/pds-connection/probe")

    def stop(self) -> BridgeResult:
        return self._request("POST", "/api/pds-connection/stop")

    def prepare_project(self, program_code: str) -> BridgeResult:
        return self._request("POST", f"/api/projects/{program_code}/prepare")

    def prepare_project_documents(self, program_code: str) -> BridgeResult:
        return self._request(
            "POST", f"/api/projects/{program_code}/prepare-documents"
        )

    def inspect_plan_contract(self, program_code: str) -> BridgeResult:
        return self._request(
            "POST", f"/api/projects/{program_code}/inspect-plan-contract"
        )

    def create_plan_draft(self, program_code: str) -> BridgeResult:
        return self._request(
            "POST", f"/api/projects/{program_code}/create-plan-draft"
        )

    def write_function_catalog(
        self, program_code: str, catalog: FunctionCatalog
    ) -> BridgeResult:
        return self._request(
            "POST",
            f"/api/projects/{program_code}/write-function-catalog",
            json=catalog.model_dump(mode="json"),
        )

    def download_exception_sample(self, program_code: str) -> BridgeResult:
        return self._request(
            "POST",
            f"/api/projects/{program_code}/download-exception-sample",
            json={"acknowledge_identity_mismatch": True},
        )

    def shutdown(self) -> None:
        """Close only this proxy; the independent bridge keeps running."""

        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> BridgeResult:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PDSConnectionError(
                f"PDS Session Bridge is unavailable ({type(exc).__name__})"
            ) from exc

        if response.is_error:
            detail = "PDS Session Bridge request failed"
            try:
                payload = response.json()
                if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                    detail = payload["detail"]
            except ValueError:
                pass
            if response.status_code == 404:
                raise PDSProjectNotFoundError(detail)
            if response.status_code in {409, 422}:
                raise PDSProjectSafetyError(detail)
            raise PDSConnectionError(detail)

        try:
            payload = response.json()
        except ValueError as exc:
            raise PDSConnectionError(
                "PDS Session Bridge returned an invalid response"
            ) from exc
        if not isinstance(payload, dict):
            raise PDSConnectionError(
                "PDS Session Bridge returned an invalid response shape"
            )
        return BridgeResult(payload)

