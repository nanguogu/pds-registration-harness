from __future__ import annotations

import re
import json
from collections import Counter, OrderedDict
from pathlib import Path

from pds_harness.domain.models import (
    FunctionCatalog,
    FunctionItem,
    FunctionModule,
    SourceReference,
)


DIRECT_IMPORT_VERSION = "direct_function_list_v1"


def _cell_text(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("\u00a0", " ")).strip()


def import_function_list_docx(
    path: Path,
    *,
    program_id: int,
    program_code: str,
    source_sha256: str,
    original_name: str,
) -> FunctionCatalog:
    """Import the authoritative four-column PDS function-list template.

    The first hierarchy column becomes the PDS module and the deepest non-empty
    hierarchy column becomes the function. Descriptions are preserved verbatim
    apart from whitespace normalization; no cloud model is involved.
    """

    from docx import Document

    document = Document(path)
    source_table = None
    for table in document.tables:
        joined = "\n".join(
            " | ".join(_cell_text(cell.text) for cell in row.cells)
            for row in table.rows[:3]
        )
        if "软件功能项目" in joined and "功能说明" in joined:
            source_table = table
            break
    if source_table is None:
        raise ValueError("No PDS software-function table was found")

    raw_rows: list[tuple[int, str, str, str]] = []
    header_values: list[str] | None = None
    for row in source_table.rows[:5]:
        values = [_cell_text(cell.text) for cell in row.cells]
        if any("功能说明" in value for value in values):
            header_values = values
            break
    if header_values is None:
        raise ValueError("The function-list table has no 功能说明 column")
    description_index = next(
        index for index, value in enumerate(header_values) if "功能说明" in value
    )
    if description_index < 1:
        raise ValueError("The function-list table has no hierarchy columns")

    for row_index, row in enumerate(source_table.rows, start=1):
        values = [_cell_text(cell.text) for cell in row.cells]
        if len(values) <= description_index:
            continue
        hierarchy = values[:description_index]
        description = values[description_index]
        combined = "".join(values)
        if not combined or "产品名称" in combined or "软件功能项目" in combined:
            continue
        non_empty = [value for value in hierarchy if value]
        if not non_empty or not description:
            continue
        module_name = non_empty[0]
        function_name = non_empty[-1]
        raw_rows.append((row_index, module_name, function_name, description))

    duplicate_counts = Counter(
        (module_name.casefold(), function_name.casefold())
        for _row, module_name, function_name, _description in raw_rows
    )
    grouped: OrderedDict[str, list[FunctionItem]] = OrderedDict()
    for function_index, (row_index, module_name, function_name, description) in enumerate(
        raw_rows, start=1
    ):
        if duplicate_counts[(module_name.casefold(), function_name.casefold())] > 1:
            function_name = (
                description
                if description.startswith(function_name)
                else f"{function_name}{description}"
            )
        grouped.setdefault(module_name, []).append(
            FunctionItem(
                local_id=f"function-{function_index:04d}",
                name=function_name,
                description=description,
                source_refs=[
                    SourceReference(
                        file=original_name,
                        section=f"table-00001-row-{row_index:04d}",
                    )
                ],
                confidence=1.0,
                review_status="pending",
            )
        )

    if not grouped:
        raise ValueError("The PDS function-list table contains no function rows")
    modules = [
        FunctionModule(
            local_id=f"module-{index:03d}",
            name=name,
            source_refs=[
                SourceReference(file=original_name, section="table-00001")
            ],
            functions=functions,
        )
        for index, (name, functions) in enumerate(grouped.items(), start=1)
    ]
    return FunctionCatalog(
        program_id=program_id,
        program_code=program_code,
        manual_sha256=source_sha256,
        prompt_version=DIRECT_IMPORT_VERSION,
        model="none-direct-function-list",
        modules=modules,
        warnings=["功能项由PDS功能列表原文直接导入，未调用云模型改写"],
    )


def generate_direct_import_artifacts(
    parsed_artifact_path: Path, *, archive_root: Path
) -> dict[str, str | int | bool]:
    payload = json.loads(parsed_artifact_path.read_text(encoding="utf-8"))
    if payload.get("role") != "function-list":
        raise ValueError("Parsed artifact is not a function-list document")
    parsed_document = payload.get("parsed_document") or {}
    source_path = Path(str(parsed_document.get("source_path") or ""))
    catalog = import_function_list_docx(
        source_path,
        program_id=int(payload["program_id"]),
        program_code=str(payload["program_code"]),
        source_sha256=str(payload["source_sha256"]),
        original_name=str(payload["original_name"]),
    )
    output_dir = (
        archive_root
        / catalog.program_code
        / "generation"
        / catalog.manual_sha256
        / DIRECT_IMPORT_VERSION
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / "function_catalog.json"
    manifest_path = output_dir / "review_manifest.json"
    catalog_path.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    module_count = len(catalog.modules)
    function_count = sum(len(module.functions) for module in catalog.modules)
    manifest = {
        "schema_version": "1.0",
        "status": "review_pending",
        "program_code": catalog.program_code,
        "source_strategy": "direct_function_list",
        "source_document_path": payload["original_path"],
        "source_sha256": catalog.manual_sha256,
        "cloud_model_used": False,
        "module_count": module_count,
        "function_count": function_count,
        "pds_write_allowed": False,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "catalog_path": str(catalog_path),
        "manifest_path": str(manifest_path),
        "module_count": module_count,
        "function_count": function_count,
        "cloud_model_used": False,
    }
