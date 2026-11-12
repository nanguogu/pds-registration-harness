from pds_harness.domain.models import AttachmentMetadata
from pds_harness.services.attachment_selection import (
    AttachmentSelectionKind,
    select_manual_attachment,
)


def attachment(identifier: str, name: str) -> AttachmentMetadata:
    return AttachmentMetadata(
        attachment_id=identifier, name=name, remote_path=f"/files/{name}"
    )


def test_prefers_user_manual_over_design_specification():
    result = select_manual_attachment(
        [attachment("1", "产品设计说明书.docx"), attachment("2", "产品用户手册.pdf")]
    )
    assert result.kind is AttachmentSelectionKind.UNIQUE
    assert result.selected.attachment_id == "2"


def test_same_priority_is_ambiguous():
    result = select_manual_attachment(
        [attachment("1", "A用户手册.pdf"), attachment("2", "B用户手册.docx")]
    )
    assert result.kind is AttachmentSelectionKind.AMBIGUOUS
    assert result.selected is None


def test_unsupported_files_are_not_selected():
    result = select_manual_attachment([attachment("1", "用户手册.exe")])
    assert result.kind is AttachmentSelectionKind.NONE


def test_supports_vendor_manual_name_used_by_pds():
    result = select_manual_attachment(
        [attachment("1", "示例视觉控制软件厂商手册.doc")]
    )
    assert result.kind is AttachmentSelectionKind.UNIQUE
    assert result.matched_priority == "厂商手册"

