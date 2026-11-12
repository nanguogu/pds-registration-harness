from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from pds_harness.domain.errors import CloudModelError


@dataclass(frozen=True)
class CloudModelConfig:
    api_key: str = field(repr=False)
    base_url: str
    model_id: str

    @classmethod
    def from_env_file(cls, path: Path) -> "CloudModelConfig":
        values: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise CloudModelError("Cloud model .env file is unavailable") from exc
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip().lower()] = value.strip().strip('"').strip("'")

        missing = [
            name
            for name in ("api_key", "base_url", "model_id")
            if not values.get(name)
        ]
        if missing:
            raise CloudModelError(
                "Cloud model .env is missing required settings: "
                + ", ".join(missing)
            )
        return cls(
            api_key=values["api_key"],
            base_url=values["base_url"],
            model_id=values["model_id"],
        )


class OpenAICompatibleModelClient:
    """Minimal JSON-only client for the configured third-party relay."""

    def __init__(
        self,
        config: CloudModelConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 600.0,
    ) -> None:
        base_url = config.base_url.strip().rstrip("/")
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise CloudModelError("Cloud model base URL must be a valid HTTPS URL")
        endpoint = (
            base_url
            if base_url.endswith("/chat/completions")
            else f"{base_url}/chat/completions"
        )
        self.model_id = config.model_id
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self._endpoint = endpoint

    def close(self) -> None:
        self._client.close()

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        if not system_prompt.strip() or not user_prompt.strip():
            raise ValueError("Both model prompts are required")
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(self._endpoint, json=payload)
        except httpx.HTTPError as exc:
            raise CloudModelError(
                f"Cloud model request failed ({type(exc).__name__})"
            ) from exc
        if response.is_error:
            raise CloudModelError(
                f"Cloud model returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
            choices = body.get("choices") or []
            content = (choices[0].get("message") or {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("missing content")
            result = json.loads(content)
        except (ValueError, TypeError, AttributeError, IndexError) as exc:
            raise CloudModelError(
                "Cloud model did not return a valid JSON object"
            ) from exc
        if not isinstance(result, dict):
            raise CloudModelError("Cloud model JSON response must be an object")
        return result

    def probe(self) -> dict[str, Any]:
        result = self.complete_json(
            system_prompt="你是 API 连通性检测器，只输出合法 JSON。",
            user_prompt='仅返回 {"status":"ok"}。',
            max_tokens=64,
        )
        if result.get("status") != "ok":
            raise CloudModelError("Cloud model probe returned an unexpected result")
        return {"status": "ok", "model": self.model_id, "json_mode": True}

