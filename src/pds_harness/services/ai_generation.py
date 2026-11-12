from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from pds_harness.domain.errors import GenerationSourceBlockedError
from pds_harness.domain.models import (
    CoverageDecision,
    FunctionCatalog,
    FunctionItem,
    FunctionModule,
    ParsedManualArtifact,
    ProductSummary,
    SourceReference,
)
from pds_harness.services.catalog_validation import CatalogValidator, ValidationIssue
from pds_harness.services.generation_gate import load_cloud_generation_source


PROMPT_VERSION = "registration_function_items_v1"
SUMMARY_PROMPT_VERSION = "product_summary_v1"


class ModelGateway(Protocol):
    model_id: str

    def complete_json(
        self, *, system_prompt: str, user_prompt: str, max_tokens: int = 4096
    ) -> dict[str, Any]: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _DraftFunction(_StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=2, max_length=2000)
    source_block_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class _DraftModule(_StrictModel):
    name: str = Field(min_length=1, max_length=200)
    source_block_ids: list[str] = Field(default_factory=list)
    functions: list[_DraftFunction] = Field(min_length=1)


class _DraftModulePlan(_StrictModel):
    name: str = Field(min_length=1, max_length=200)
    source_block_ids: list[str] = Field(min_length=1)


class _DraftPlan(_StrictModel):
    product_summary: ProductSummary
    modules: list[_DraftModulePlan] = Field(min_length=1)
    excluded_block_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class _DraftModuleFunctions(_StrictModel):
    functions: list[_DraftFunction] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class _DraftCoverage(_StrictModel):
    source_block_id: str = Field(min_length=1)
    decision: str
    reason: str | None = None


class _DraftGeneration(_StrictModel):
    product_summary: ProductSummary
    modules: list[_DraftModule] = Field(min_length=1)
    coverage: list[_DraftCoverage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class GenerationArtifacts:
    summary_path: str
    catalog_path: str
    manifest_path: str
    catalog: FunctionCatalog
    summary: ProductSummary
    issues: tuple[ValidationIssue, ...]


def generate_product_summary_artifact(
    artifact_path: Path,
    *,
    model: ModelGateway,
    archive_root: Path,
) -> dict[str, str | int]:
    """Generate only the product introduction; never generate function items."""

    artifact = load_cloud_generation_source(artifact_path, cloud_enabled=True)
    block_text = "\n".join(
        f"[{block.block_id}] {block.text}" for block in artifact.blocks
    )
    raw = model.complete_json(
        system_prompt=(
            "你是一名软件登记测试工程师。只根据操作手册概括产品功能，"
            "不得生成或改写测试功能项，只输出合法JSON。每项来源必须引用输入中的block_id。"
        ),
        user_prompt=f"""项目：{artifact.program_code}
软件：{artifact.software_name}
请返回：
{{"overview":"150至500字产品功能介绍","core_capabilities":["能力"],"source_block_ids":["paragraph-00001"]}}
操作手册：
{block_text}""",
        max_tokens=1400,
    )
    summary = ProductSummary.model_validate(raw).model_copy(
        update={"source_document_path": artifact.original_path}
    )
    valid_ids = {block.block_id for block in artifact.blocks}
    unknown = sorted(set(summary.source_block_ids) - valid_ids)
    if unknown:
        raise GenerationSourceBlockedError(
            "Summary referenced unknown document blocks: " + ", ".join(unknown[:10])
        )
    output_dir = (
        archive_root
        / artifact.program_code
        / "generation"
        / artifact.manual_sha256
        / SUMMARY_PROMPT_VERSION
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "product_summary.json"
    summary_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return {
        "summary_path": str(summary_path),
        "source_document_path": artifact.original_path,
        "source_block_count": len(summary.source_block_ids),
    }


def _source_ref(artifact: ParsedManualArtifact, block_id: str) -> SourceReference:
    return SourceReference(file=artifact.original_name, section=block_id)


def _check_source_ids(draft: _DraftGeneration, valid_ids: set[str]) -> None:
    referenced = set(draft.product_summary.source_block_ids)
    for module in draft.modules:
        referenced.update(module.source_block_ids)
        for function in module.functions:
            referenced.update(function.source_block_ids)
    referenced.update(entry.source_block_id for entry in draft.coverage)
    unknown = sorted(referenced - valid_ids)
    if unknown:
        raise GenerationSourceBlockedError(
            "Model referenced unknown document blocks: " + ", ".join(unknown[:10])
        )


def _build_catalog(
    artifact: ParsedManualArtifact,
    draft: _DraftGeneration,
    *,
    model_id: str,
) -> FunctionCatalog:
    modules: list[FunctionModule] = []
    function_number = 0
    for module_number, module in enumerate(draft.modules, start=1):
        functions: list[FunctionItem] = []
        for function in module.functions:
            function_number += 1
            functions.append(
                FunctionItem(
                    local_id=f"F{function_number:03d}",
                    name=function.name,
                    description=function.description,
                    source_refs=[
                        _source_ref(artifact, block_id)
                        for block_id in function.source_block_ids
                    ],
                    confidence=function.confidence,
                    review_status="pending",
                )
            )
        modules.append(
            FunctionModule(
                local_id=f"M{module_number:03d}",
                name=module.name,
                source_refs=[
                    _source_ref(artifact, block_id)
                    for block_id in module.source_block_ids
                ],
                functions=functions,
            )
        )

    coverage: list[CoverageDecision] = []
    for entry in draft.coverage:
        decision = entry.decision.lower()
        if decision not in {"covered", "excluded"}:
            raise GenerationSourceBlockedError(
                f"Model returned invalid coverage decision: {entry.decision}"
            )
        coverage.append(
            CoverageDecision(
                section=entry.source_block_id,
                decision=decision,
                reason=entry.reason,
            )
        )
    return FunctionCatalog(
        program_id=artifact.program_id,
        program_code=artifact.program_code,
        manual_sha256=artifact.manual_sha256,
        prompt_version=PROMPT_VERSION,
        model=model_id,
        modules=modules,
        coverage=coverage,
        warnings=[*artifact.warnings, *draft.warnings],
    )


def generate_review_artifacts(
    artifact_path: Path,
    *,
    model: ModelGateway,
    archive_root: Path,
    validator: CatalogValidator,
) -> GenerationArtifacts:
    artifact = load_cloud_generation_source(artifact_path, cloud_enabled=True)
    block_text = "\n".join(
        f"[{block.block_id}] {block.text}" for block in artifact.blocks
    )
    system_prompt = """你是一名经验丰富的软件登记测试工程师。只根据给定产品说明书编写待人工复核的产品摘要和测试功能项，并且只输出合法 JSON 对象。

硬性规则：
1. 禁止虚构说明书未描述的功能。
2. 模块是稳定的功能域，不照抄“操作步骤、界面说明”等文档章节名。
3. 功能名称简洁；功能说明必须以动词开头并采用动宾结构。
4. 禁止使用“正常、正确、成功、通过、符合要求”等结论性语句。
5. 合并重复操作，避免把每个按钮、菜单或点击步骤拆成独立功能。
6. 排除公司信息、联系方式、纯环境要求和无业务含义的安装步骤；确有用户可操作功能时才保留启动、导入、配置等功能。
7. 每个摘要、模块和功能必须引用真实 block_id；低把握内容降低 confidence 并写入 warnings。
8. 所有功能项 review_status 由系统统一设为 pending，不在输出中给出。
"""
    schema_instruction = """
返回结构必须严格为：
{
  "product_summary": {
    "overview": "150至500字产品功能介绍",
    "core_capabilities": ["能力1"],
    "source_block_ids": ["paragraph-00001"]
  },
  "modules": [
    {
      "name": "模块名",
      "source_block_ids": ["paragraph-00001"],
      "functions": [
        {
          "name": "功能名",
          "description": "以动词开头的功能说明。",
          "source_block_ids": ["paragraph-00001"],
          "confidence": 0.90
        }
      ]
    }
  ],
  "coverage": [
    {"source_block_id":"paragraph-00001","decision":"covered","reason":null}
  ],
  "warnings": []
}
coverage.decision 只能是 covered 或 excluded；excluded 必须填写 reason。
"""
    raw = model.complete_json(
        system_prompt=system_prompt,
        user_prompt=(
            f"项目：{artifact.program_code}\n软件：{artifact.software_name}\n"
            f"说明书内容如下：\n{block_text}\n{schema_instruction}"
        ),
        max_tokens=8000,
    )
    draft = _DraftGeneration.model_validate(raw)
    return _finalize_generation(
        artifact,
        draft,
        model_id=model.model_id,
        archive_root=archive_root,
        validator=validator,
        raw_seed=raw,
    )


def _finalize_generation(
    artifact: ParsedManualArtifact,
    draft: _DraftGeneration,
    *,
    model_id: str,
    archive_root: Path,
    validator: CatalogValidator,
    raw_seed: Any,
) -> GenerationArtifacts:
    valid_ids = {block.block_id for block in artifact.blocks}
    _check_source_ids(draft, valid_ids)
    catalog = _build_catalog(artifact, draft, model_id=model_id)
    issues = tuple(validator.validate(catalog))
    summary = draft.product_summary.model_copy(
        update={"source_document_path": artifact.original_path}
    )

    encoded = json.dumps(raw_seed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    run_id = hashlib.sha256(encoded).hexdigest()[:12]
    output_dir = (
        archive_root
        / artifact.program_code
        / "generation"
        / artifact.manual_sha256
        / run_id
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    summary_path = output_dir / "product_summary.json"
    catalog_path = output_dir / "function_catalog.json"
    manifest_path = output_dir / "review_manifest.json"
    summary_path.write_text(
        summary.model_dump_json(indent=2), encoding="utf-8"
    )
    catalog_path.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "status": "review_pending",
        "program_code": artifact.program_code,
        "manual_sha256": artifact.manual_sha256,
        "prompt_version": PROMPT_VERSION,
        "model": model_id,
        "module_count": len(catalog.modules),
        "function_count": sum(len(module.functions) for module in catalog.modules),
        "validation_issues": [issue.__dict__ for issue in issues],
        "pds_write_allowed": False,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return GenerationArtifacts(
        summary_path=str(summary_path),
        catalog_path=str(catalog_path),
        manifest_path=str(manifest_path),
        catalog=catalog,
        summary=summary,
        issues=issues,
    )


def generate_review_artifacts_segmented(
    artifact_path: Path,
    *,
    model: ModelGateway,
    archive_root: Path,
    validator: CatalogValidator,
) -> GenerationArtifacts:
    """Generate a review package through short, bounded model requests."""

    artifact = load_cloud_generation_source(artifact_path, cloud_enabled=True)
    valid_ids = {block.block_id for block in artifact.blocks}
    text_by_id = {block.block_id: block.text for block in artifact.blocks}
    block_text = "\n".join(
        f"[{block.block_id}] {block.text}" for block in artifact.blocks
    )
    shared_rules = """只根据说明书生成软件登记测试功能项。禁止虚构；功能说明必须以动词开头并采用动宾结构；禁止“正常、正确、成功、通过、符合要求”等结论性词语；合并重复操作；不要把按钮、点击步骤、联系方式、公司信息或纯说明文字当成独立功能；所有 source_block_ids 必须来自输入。只输出合法 JSON。"""
    plan_raw = model.complete_json(
        system_prompt=shared_rules,
        user_prompt=f"""项目：{artifact.program_code}
软件：{artifact.software_name}
请先只规划摘要和功能域，不生成具体功能。返回：
{{
  "product_summary": {{"overview":"150至500字", "core_capabilities":["能力"], "source_block_ids":["paragraph-00001"]}},
  "modules": [{{"name":"稳定功能域", "source_block_ids":["paragraph-00001"]}}],
  "excluded_block_ids": ["不作为功能依据的block_id"],
  "warnings": []
}}
说明书：
{block_text}""",
        max_tokens=2200,
    )
    plan = _DraftPlan.model_validate(plan_raw)
    plan_ids = set(plan.product_summary.source_block_ids)
    plan_ids.update(plan.excluded_block_ids)
    for module in plan.modules:
        plan_ids.update(module.source_block_ids)
    unknown = sorted(plan_ids - valid_ids)
    if unknown:
        raise GenerationSourceBlockedError(
            "Model plan referenced unknown document blocks: " + ", ".join(unknown[:10])
        )

    modules: list[_DraftModule] = []
    warnings = list(plan.warnings)
    function_raw_results: list[dict[str, Any]] = []
    for module in plan.modules:
        source_text = "\n".join(
            f"[{block_id}] {text_by_id[block_id]}"
            for block_id in module.source_block_ids
        )
        module_raw = model.complete_json(
            system_prompt=shared_rules,
            user_prompt=f"""功能域：{module.name}
请根据下列来源生成适度精简、可独立测试的功能。返回：
{{
  "functions": [
    {{"name":"功能名", "description":"以动词开头的功能说明。", "source_block_ids":["paragraph-00001"], "confidence":0.90}}
  ],
  "warnings": []
}}
来源：
{source_text}""",
            max_tokens=2600,
        )
        module_result = _DraftModuleFunctions.model_validate(module_raw)
        allowed = set(module.source_block_ids)
        referenced = {
            block_id
            for function in module_result.functions
            for block_id in function.source_block_ids
        }
        if not referenced <= allowed:
            raise GenerationSourceBlockedError(
                f"Module {module.name} referenced blocks outside its plan"
            )
        modules.append(
            _DraftModule(
                name=module.name,
                source_block_ids=module.source_block_ids,
                functions=module_result.functions,
            )
        )
        warnings.extend(module_result.warnings)
        function_raw_results.append(module_raw)

    covered_ids = sorted(
        {
            block_id
            for module in modules
            for function in module.functions
            for block_id in function.source_block_ids
        }
    )
    coverage = [
        _DraftCoverage(
            source_block_id=block_id,
            decision="covered",
            reason=None,
        )
        for block_id in covered_ids
    ]
    draft = _DraftGeneration(
        product_summary=plan.product_summary,
        modules=modules,
        coverage=coverage,
        warnings=warnings,
    )
    return _finalize_generation(
        artifact,
        draft,
        model_id=model.model_id,
        archive_root=archive_root,
        validator=validator,
        raw_seed={"plan": plan_raw, "modules": function_raw_results},
    )

