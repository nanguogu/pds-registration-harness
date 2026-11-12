from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceReference(StrictModel):
    file: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    section: str | None = None


class FunctionItem(StrictModel):
    local_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=2, max_length=2000)
    source_refs: list[SourceReference] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    review_status: Literal["pending", "approved", "rejected"] = "pending"


class FunctionModule(StrictModel):
    local_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    source_refs: list[SourceReference] = Field(default_factory=list)
    functions: list[FunctionItem] = Field(min_length=1)


class CoverageDecision(StrictModel):
    section: str = Field(min_length=1)
    decision: Literal["covered", "excluded"]
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def excluded_requires_reason(cls, value: str | None, info):
        if info.data.get("decision") == "excluded" and not value:
            raise ValueError("Excluded sections require a reason")
        return value


class FunctionCatalog(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    program_id: int = Field(gt=0)
    program_code: str = Field(min_length=1)
    manual_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    modules: list[FunctionModule] = Field(min_length=1)
    coverage: list[CoverageDecision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProductSummary(StrictModel):
    overview: str = Field(min_length=20, max_length=2000)
    core_capabilities: list[str] = Field(min_length=1)
    source_block_ids: list[str] = Field(min_length=1)
    source_document_path: str | None = None


class RegistrationDefaults(StrictModel):
    test_specification: str
    test_method_labels: list[str] = Field(min_length=1)
    test_method_other: str = ""
    criteria: list[str] = Field(min_length=1)
    onsite_option_label: str
    default_supervisors: list[str] = Field(default_factory=lambda: ["/"])
    default_customers: list[str] = Field(default_factory=lambda: ["/"])
    default_test_places: list[str] = Field(default_factory=lambda: ["本实验室"])
    leave_test_dates_empty: bool = True


class ResolvedDictionaryValues(StrictModel):
    test_method_ids: dict[str, int]
    onsite_option_ids: dict[str, int]

    def method_ids_for(self, labels: list[str]) -> list[int]:
        missing = [label for label in labels if label not in self.test_method_ids]
        if missing:
            raise ValueError(f"Missing PDS test-method dictionary labels: {missing}")
        return [self.test_method_ids[label] for label in labels]

    def onsite_id_for(self, label: str) -> int:
        try:
            return self.onsite_option_ids[label]
        except KeyError as exc:
            raise ValueError(f"Missing PDS onsite dictionary label: {label}") from exc


class ReviewChecklist(StrictModel):
    all_sections_decided: bool
    low_confidence_resolved: bool
    no_duplicates: bool
    all_items_have_sources: bool
    no_conclusion_language: bool
    plan_parameters_reviewed: bool
    test_date_confirmed: bool

    def is_complete(self) -> bool:
        return all(self.model_dump().values())


class SubmissionApproval(StrictModel):
    actor: str = Field(min_length=1)
    confirmed_at: datetime
    project_summary_reviewed: bool
    plan_parameters_reviewed: bool
    function_diff_reviewed: bool
    checklist: ReviewChecklist

    def is_valid(self) -> bool:
        return (
            self.project_summary_reviewed
            and self.plan_parameters_reviewed
            and self.function_diff_reviewed
            and self.checklist.is_complete()
        )


class PlanRuntimeValues(StrictModel):
    current_username: str = Field(min_length=1)
    compile_date: date
    test_date: date | None = None


class PendingProject(StrictModel):
    program_id: int = Field(gt=0)
    program_code: str = Field(min_length=1)
    software_name: str = Field(min_length=1)
    status: str = Field(min_length=1)
    confidentiality: str | None = None
    unit_name: str | None = None
    test_type: str | None = None


class AttachmentMetadata(StrictModel):
    attachment_id: str
    name: str = Field(min_length=1)
    remote_path: str = Field(min_length=1)
    uploaded_at: str | None = None


class PlanSummary(StrictModel):
    plan_id: int = Field(gt=0)
    program_id: int = Field(gt=0)
    status_code: int | None = None
    status_text: str | None = None
    version: str | None = None


class DictionaryEntry(StrictModel):
    data_id: int
    data_name: str = Field(min_length=1)


class DocumentBlock(StrictModel):
    block_id: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    block_type: Literal["heading", "paragraph", "table", "page_text"]
    text: str = Field(min_length=1)


class ParsedDocument(StrictModel):
    source_path: str
    sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    media_type: Literal["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]
    blocks: list[DocumentBlock]
    needs_ocr: bool = False
    warnings: list[str] = Field(default_factory=list)


class ParsedManualArtifact(StrictModel):
    """The only document source accepted by summary/catalog generation."""

    schema_version: Literal["1.0"] = "1.0"
    program_id: int = Field(gt=0)
    program_code: str = Field(pattern=r"^STS\d{7}$")
    software_name: str = Field(min_length=1)
    confidentiality: str = Field(min_length=1)
    attachment_id: str
    original_name: str = Field(min_length=1)
    original_path: str = Field(min_length=1)
    manual_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    parsed_source_path: str = Field(min_length=1)
    parsed_source_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    media_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    identity_match: bool
    function_generation_allowed: bool
    usage_restriction: Literal["exception_sample_only"] | None = None
    blocks: list[DocumentBlock]
    needs_ocr: bool = False
    warnings: list[str] = Field(default_factory=list)


class ArchivedFile(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    size: int = Field(ge=0)
    original_name: str

