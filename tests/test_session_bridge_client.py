from __future__ import annotations

import httpx
import pytest

from pds_harness.adapters.session_bridge_client import PDSBridgeClient
from pds_harness.domain.errors import PDSProjectSafetyError


def test_bridge_client_stays_on_loopback_and_returns_no_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:8766/api/pds-connection/status"
        return httpx.Response(
            200,
            json={
                "state": "ready",
                "browser_open": True,
                "authenticated": True,
                "last_error": None,
            },
        )

    client = PDSBridgeClient(
        "http://127.0.0.1:8766",
        transport=httpx.MockTransport(handler),
    )
    try:
        payload = client.status().to_dict()
        assert payload["state"] == "ready"
        assert "token" not in str(payload).lower()
    finally:
        client.shutdown()


def test_bridge_client_rejects_non_loopback_url():
    with pytest.raises(ValueError, match="loopback"):
        PDSBridgeClient("https://bridge.example.test")


def test_bridge_client_preserves_project_safety_failure():
    client = PDSBridgeClient(
        "http://127.0.0.1:8766",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409, json={"detail": "Project is not explicitly marked 不保密"}
            )
        ),
    )
    try:
        with pytest.raises(PDSProjectSafetyError, match="不保密"):
            client.prepare_project("STS1260215")
    finally:
        client.shutdown()

