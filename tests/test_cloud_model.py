from __future__ import annotations

import json

import httpx
import pytest

from pds_harness.adapters.cloud_model import (
    CloudModelConfig,
    OpenAICompatibleModelClient,
)
from pds_harness.domain.errors import CloudModelError


def test_loads_case_insensitive_env_without_exposing_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "API_Key=secret-value\nbase_url=https://relay.example/v1\nmodel_id=test-model\n",
        encoding="utf-8",
    )
    config = CloudModelConfig.from_env_file(path)
    assert config.model_id == "test-model"
    assert "secret-value" not in repr(config)


def test_json_completion_uses_configured_model_and_json_mode():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://relay.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret-value"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"status":"ok"}'}}],
            },
        )

    client = OpenAICompatibleModelClient(
        CloudModelConfig(
            api_key="secret-value",
            base_url="https://relay.example/v1",
            model_id="test-model",
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.probe() == {
            "status": "ok",
            "model": "test-model",
            "json_mode": True,
        }
    finally:
        client.close()


def test_model_errors_do_not_echo_api_key():
    client = OpenAICompatibleModelClient(
        CloudModelConfig(
            api_key="secret-value",
            base_url="https://relay.example/v1",
            model_id="test-model",
        ),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(401, json={"error": "bad key"})
        ),
    )
    try:
        with pytest.raises(CloudModelError) as exc_info:
            client.probe()
        assert "secret-value" not in str(exc_info.value)
    finally:
        client.close()


def test_rejects_non_https_model_endpoint():
    with pytest.raises(CloudModelError, match="HTTPS"):
        OpenAICompatibleModelClient(
            CloudModelConfig(
                api_key="secret-value",
                base_url="http://relay.example/v1",
                model_id="test-model",
            )
        )
