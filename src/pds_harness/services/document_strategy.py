from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pds_harness.domain.models import AttachmentMetadata
from pds_harness.services.attachment_selection import (
    MANUAL_PRIORITIES,
    SUPPORTED_SUFFIXES,
)


class DocumentRole(StrEnum):
    FUNCTION_LIST = "function-list"
    MANUAL = "manual"
    SUPPORTING = "supporting"


class FunctionSourceStrategy(StrEnum):
    DIRECT_FUNCTION_LIST = "direct_function_list"
    MANUAL_AI_FALLBACK = "manual_ai_fallback"
    HUMAN_SELECTION_REQUIRED = "human_selection_required"


FUNCTION_LIST_LABELS = ("功能清单", "功能列表", "功能项清单", "功能项细则")


@dataclass(frozen=True)
class ProjectDocumentPlan:
    strategy: FunctionSourceStrategy
    function_list: AttachmentMetadata | None
    manual: AttachmentMetadata | None
    documents: tuple[tuple[DocumentRole, AttachmentMetadata], ...]
    warnings: tuple[str, ...] = ()


def classify_document(name: str) -> DocumentRole:
    normalized = name.strip().lower()
    if any(label in normalized for label in FUNCTION_LIST_LABELS):
        return DocumentRole.FUNCTION_LIST
    if any(label in normalized for label in MANUAL_PRIORITIES):
        return DocumentRole.MANUAL
    return DocumentRole.SUPPORTING


def merge_attachments(
    *groups: list[AttachmentMetadata],
) -> list[AttachmentMetadata]:
    merged: list[AttachmentMetadata] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for item in group:
            key = (item.name.casefold(), item.remote_path)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def plan_project_documents(
    attachments: list[AttachmentMetadata],
) -> ProjectDocumentPlan:
    supported = [
        item
        for item in attachments
        if item.name.lower().endswith(SUPPORTED_SUFFIXES)
    ]
    function_lists = [
        item
        for item in supported
        if classify_document(item.name) is DocumentRole.FUNCTION_LIST
    ]
    manuals = [
        item
        for item in supported
        if classify_document(item.name) is DocumentRole.MANUAL
    ]

    selected_manual: AttachmentMetadata | None = None
    for priority in MANUAL_PRIORITIES:
        matches = [item for item in manuals if priority in item.name]
        if len(matches) == 1:
            selected_manual = matches[0]
            break
        if len(matches) > 1:
            return ProjectDocumentPlan(
                strategy=FunctionSourceStrategy.HUMAN_SELECTION_REQUIRED,
                function_list=function_lists[0] if len(function_lists) == 1 else None,
                manual=None,
                documents=tuple(
                    (classify_document(item.name), item) for item in supported
                ),
                warnings=(f"存在 {len(matches)} 份同优先级说明书，需要人工选择",),
            )

    if len(function_lists) > 1:
        return ProjectDocumentPlan(
            strategy=FunctionSourceStrategy.HUMAN_SELECTION_REQUIRED,
            function_list=None,
            manual=selected_manual,
            documents=tuple(
                (classify_document(item.name), item) for item in supported
            ),
            warnings=(f"存在 {len(function_lists)} 份功能清单，需要人工选择",),
        )

    selected_function_list = function_lists[0] if function_lists else None
    strategy = (
        FunctionSourceStrategy.DIRECT_FUNCTION_LIST
        if selected_function_list is not None
        else FunctionSourceStrategy.MANUAL_AI_FALLBACK
    )
    chosen: list[tuple[DocumentRole, AttachmentMetadata]] = []
    if selected_function_list is not None:
        chosen.append((DocumentRole.FUNCTION_LIST, selected_function_list))
    if selected_manual is not None:
        chosen.append((DocumentRole.MANUAL, selected_manual))
    warnings: list[str] = []
    if selected_function_list is None:
        warnings.append("未找到功能清单，将使用操作手册生成待复核功能项")
    if selected_manual is None:
        warnings.append("未找到操作手册，无法生成完整产品摘要")
    return ProjectDocumentPlan(
        strategy=strategy,
        function_list=selected_function_list,
        manual=selected_manual,
        documents=tuple(chosen),
        warnings=tuple(warnings),
    )
