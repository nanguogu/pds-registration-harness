from __future__ import annotations

import hashlib
from pathlib import Path

from pds_harness.domain.errors import GenerationSourceBlockedError
from pds_harness.domain.models import ParsedManualArtifact
from pds_harness.services.confidentiality import require_cloud_access


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_generation_source(path: Path) -> ParsedManualArtifact:
    """Load and verify the only JSON source permitted for generation."""

    try:
        artifact = ParsedManualArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise GenerationSourceBlockedError(
            f"Parsed manual JSON is invalid ({type(exc).__name__})"
        ) from exc

    if not artifact.identity_match:
        raise GenerationSourceBlockedError(
            "Attachment identity does not match the selected project"
        )
    if not artifact.function_generation_allowed:
        raise GenerationSourceBlockedError(
            "Parsed manual is explicitly blocked from function generation"
        )
    if artifact.usage_restriction is not None:
        raise GenerationSourceBlockedError(
            f"Parsed manual usage is restricted to {artifact.usage_restriction}"
        )

    original = Path(artifact.original_path).resolve()
    if not original.is_file():
        raise GenerationSourceBlockedError("Original manual is missing from the archive")
    if _sha256(original) != artifact.manual_sha256.lower():
        raise GenerationSourceBlockedError(
            "Original manual hash no longer matches the parsed JSON"
        )
    return artifact


def load_cloud_generation_source(
    path: Path, *, cloud_enabled: bool
) -> ParsedManualArtifact:
    artifact = load_generation_source(path)
    if not cloud_enabled:
        raise GenerationSourceBlockedError("Cloud model integration is disabled")
    try:
        require_cloud_access(artifact.confidentiality)
    except Exception as exc:
        raise GenerationSourceBlockedError(str(exc)) from exc
    return artifact
