from pds_harness.domain.models import AttachmentMetadata
from pds_harness.services.document_strategy import (
    DocumentRole,
    FunctionSourceStrategy,
    classify_document,
    merge_attachments,
    plan_project_documents,
)


def item(identifier: str, name: str) -> AttachmentMetadata:
    return AttachmentMetadata(
        attachment_id=identifier,
        name=name,
        remote_path=f"/files/{identifier}/{name}",
    )


def test_function_list_takes_precedence_over_manual_for_function_source():
    plan = plan_project_documents(
        [item("1", "产品操作手册.docx"), item("2", "产品功能列表.doc")]
    )
    assert plan.strategy is FunctionSourceStrategy.DIRECT_FUNCTION_LIST
    assert plan.function_list is not None
    assert plan.function_list.attachment_id == "2"
    assert plan.manual is not None
    assert [role for role, _ in plan.documents] == [
        DocumentRole.FUNCTION_LIST,
        DocumentRole.MANUAL,
    ]


def test_manual_is_fallback_only_when_function_list_is_missing():
    plan = plan_project_documents([item("1", "产品用户手册.pdf")])
    assert plan.strategy is FunctionSourceStrategy.MANUAL_AI_FALLBACK
    assert plan.function_list is None
    assert plan.manual is not None


def test_multiple_function_lists_require_selection():
    plan = plan_project_documents(
        [item("1", "功能清单A.docx"), item("2", "功能列表B.docx")]
    )
    assert plan.strategy is FunctionSourceStrategy.HUMAN_SELECTION_REQUIRED


def test_merge_uses_union_instead_of_or_fallback():
    first = item("1", "产品操作手册.docx")
    second = item("2", "产品功能列表.docx")
    assert merge_attachments([first], [second]) == [first, second]


def test_classifies_unrelated_document_as_supporting():
    assert classify_document("测试环境说明.pdf") is DocumentRole.SUPPORTING
