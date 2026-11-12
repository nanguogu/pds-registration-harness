from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from pds_harness.domain.models import ArchivedFile


WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def safe_filename(name: str) -> str:
    candidate = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not candidate:
        candidate = "unnamed-file"
    stem = Path(candidate).stem.upper()
    if stem in WINDOWS_RESERVED:
        candidate = f"_{candidate}"
    return candidate[:180]


class ProjectArchive:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def store_bytes(
        self, *, program_code: str, original_name: str, content: bytes
    ) -> ArchivedFile:
        if not program_code.strip():
            raise ValueError("program_code is required")
        clean_program = safe_filename(program_code)
        clean_name = safe_filename(original_name)
        digest = hashlib.sha256(content).hexdigest()
        target_dir = (self.root / clean_program / "manuals" / "original").resolve()
        if self.root not in target_dir.parents:
            raise ValueError("Resolved archive target escapes the configured root")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest[:12]}-{clean_name}"

        if target.exists():
            existing_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing_digest != digest:
                raise RuntimeError("Archive hash collision or external file modification")
        else:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".download-", dir=target_dir
            )
            try:
                with os.fdopen(file_descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                Path(temporary_name).replace(target)
            finally:
                temporary = Path(temporary_name)
                if temporary.exists():
                    temporary.unlink()

        return ArchivedFile(
            path=str(target),
            sha256=digest,
            size=len(content),
            original_name=original_name,
        )

    def document_directory(
        self, *, program_code: str, role: str, stage: str
    ) -> Path:
        if stage not in {"original", "derived", "parsed"}:
            raise ValueError("Unsupported document archive stage")
        clean_program = safe_filename(program_code)
        clean_role = safe_filename(role).lower()
        target = (
            self.root / clean_program / "documents" / clean_role / stage
        ).resolve()
        if self.root not in target.parents:
            raise ValueError("Resolved archive target escapes the configured root")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def store_document_bytes(
        self,
        *,
        program_code: str,
        role: str,
        original_name: str,
        content: bytes,
    ) -> ArchivedFile:
        clean_name = safe_filename(original_name)
        digest = hashlib.sha256(content).hexdigest()
        target_dir = self.document_directory(
            program_code=program_code, role=role, stage="original"
        )
        target = target_dir / f"{digest[:12]}-{clean_name}"
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("Archive hash collision or external file modification")
        else:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".download-", dir=target_dir
            )
            try:
                with os.fdopen(file_descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                Path(temporary_name).replace(target)
            finally:
                temporary = Path(temporary_name)
                if temporary.exists():
                    temporary.unlink()
        return ArchivedFile(
            path=str(target),
            sha256=digest,
            size=len(content),
            original_name=original_name,
        )

    def store_parsed_manual(
        self,
        *,
        program_code: str,
        manual_sha256: str,
        payload: dict,
    ) -> str:
        if not re.fullmatch(r"[A-Fa-f0-9]{64}", manual_sha256):
            raise ValueError("manual_sha256 must contain exactly 64 hexadecimal characters")
        clean_program = safe_filename(program_code)
        target_dir = (self.root / clean_program / "manuals" / "parsed").resolve()
        if self.root not in target_dir.parents:
            raise ValueError("Resolved archive target escapes the configured root")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{manual_sha256.lower()}.parsed.json"
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        if target.exists():
            if target.read_bytes() != encoded:
                raise RuntimeError(
                    "Parsed manual artifact conflicts with existing content-addressed JSON"
                )
            return str(target)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".parsed-", dir=target_dir
        )
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary_name).replace(target)
        finally:
            temporary = Path(temporary_name)
            if temporary.exists():
                temporary.unlink()
        return str(target)

    def store_parsed_document(
        self,
        *,
        program_code: str,
        role: str,
        source_sha256: str,
        payload: dict,
    ) -> str:
        if not re.fullmatch(r"[A-Fa-f0-9]{64}", source_sha256):
            raise ValueError("source_sha256 must contain exactly 64 hexadecimal characters")
        target_dir = self.document_directory(
            program_code=program_code, role=role, stage="parsed"
        )
        target = target_dir / f"{source_sha256.lower()}.parsed.json"
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if target.exists():
            if target.read_bytes() != encoded:
                raise RuntimeError(
                    "Parsed document conflicts with existing content-addressed JSON"
                )
            return str(target)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".parsed-", dir=target_dir
        )
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary_name).replace(target)
        finally:
            temporary = Path(temporary_name)
            if temporary.exists():
                temporary.unlink()
        return str(target)

