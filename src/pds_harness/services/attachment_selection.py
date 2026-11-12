from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pds_harness.domain.models import AttachmentMetadata


class AttachmentSelectionKind(StrEnum):
    NONE = "none"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class AttachmentSelection:
    kind: AttachmentSelectionKind
    candidates: tuple[AttachmentMetadata, ...]
    matched_priority: str | None

    @property
    def selected(self) -> AttachmentMetadata | None:
        if self.kind is AttachmentSelectionKind.UNIQUE:
            return self.candidates[0]
        return None


MANUAL_PRIORITIES = (
    "用户手册",
    "操作手册",
    "产品说明书",
    "软件说明书",
    "厂商手册",
    "设计说明书",
)
SUPPORTED_SUFFIXES = (".pdf", ".docx", ".doc")


def select_manual_attachment(
    attachments: list[AttachmentMetadata],
) -> AttachmentSelection:
    supported = [
        attachment
        for attachment in attachments
        if attachment.name.lower().endswith(SUPPORTED_SUFFIXES)
    ]
    for priority in MANUAL_PRIORITIES:
        matches = [item for item in supported if priority in item.name]
        if len(matches) == 1:
            return AttachmentSelection(
                AttachmentSelectionKind.UNIQUE, tuple(matches), priority
            )
        if len(matches) > 1:
            return AttachmentSelection(
                AttachmentSelectionKind.AMBIGUOUS, tuple(matches), priority
            )
    return AttachmentSelection(AttachmentSelectionKind.NONE, (), None)

