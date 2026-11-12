from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pds_harness.domain.models import DocumentBlock, ParsedDocument


PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value.replace("\u00a0", " ")).strip()


def _text_quality_warnings(blocks: list[DocumentBlock]) -> list[str]:
    """Report characters that indicate real extraction damage.

    This intentionally does not use terminal rendering as evidence. A valid
    Chinese string can look garbled when a console decodes UTF-8 output with
    a different code page even though the parsed Unicode text is intact.
    """

    text = "\n".join(block.text for block in blocks)
    replacement_count = text.count("\ufffd")
    private_use_count = sum(
        0xE000 <= ord(character) <= 0xF8FF for character in text
    )
    control_count = sum(
        ord(character) < 32 and character not in "\n\r\t" for character in text
    )

    warnings: list[str] = []
    if replacement_count:
        warnings.append(
            f"检测到 {replacement_count} 个 Unicode 替换字符，文本可能存在编码损坏"
        )
    if private_use_count:
        warnings.append(
            f"检测到 {private_use_count} 个私用区字符，文本可能依赖原文档字体映射"
        )
    if control_count:
        warnings.append(
            f"检测到 {control_count} 个异常控制字符，文本可能存在提取损坏"
        )
    return warnings


def parse_pdf(path: Path, *, minimum_chars_per_page: int = 40) -> ParsedDocument:
    import fitz

    document = fitz.open(path)
    blocks: list[DocumentBlock] = []
    low_text_pages: list[int] = []
    try:
        for page_index, page in enumerate(document, start=1):
            text = _normalize_text(page.get_text("text"))
            if len(text) < minimum_chars_per_page:
                low_text_pages.append(page_index)
            if text:
                blocks.append(
                    DocumentBlock(
                        block_id=f"page-{page_index:04d}",
                        page=page_index,
                        block_type="page_text",
                        text=text,
                    )
                )
    finally:
        document.close()

    warnings = _text_quality_warnings(blocks)
    if low_text_pages:
        warnings.append(
            f"{len(low_text_pages)} 个页面文本过少，可能需要 OCR："
            + ", ".join(map(str, low_text_pages[:20]))
        )
    return ParsedDocument(
        source_path=str(path.resolve()),
        sha256=_hash_file(path),
        media_type=PDF_MEDIA_TYPE,
        blocks=blocks,
        needs_ocr=bool(low_text_pages),
        warnings=warnings,
    )


def parse_docx(path: Path) -> ParsedDocument:
    from docx import Document
    from docx.document import Document as DocumentObject
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(path)
    blocks: list[DocumentBlock] = []
    current_section: str | None = None
    sequence = 0

    def iter_blocks(parent: DocumentObject):
        for child in parent.element.body.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    table_index = 0
    for block in iter_blocks(document):
        if isinstance(block, Paragraph):
            text = _normalize_text(block.text)
            if not text:
                continue
            sequence += 1
            style_name = block.style.name if block.style is not None else ""
            is_heading = style_name.lower().startswith("heading") or style_name.startswith("标题")
            if is_heading:
                current_section = text
            blocks.append(
                DocumentBlock(
                    block_id=f"paragraph-{sequence:05d}",
                    section=current_section,
                    block_type="heading" if is_heading else "paragraph",
                    text=text,
                )
            )
            continue

        table_index += 1
        rows: list[str] = []
        for row in block.rows:
            values = [_normalize_text(cell.text) for cell in row.cells]
            if any(values):
                rows.append(" | ".join(values))
        if rows:
            blocks.append(
                DocumentBlock(
                    block_id=f"table-{table_index:05d}",
                    section=current_section,
                    block_type="table",
                    text="\n".join(rows),
                )
            )

    return ParsedDocument(
        source_path=str(path.resolve()),
        sha256=_hash_file(path),
        media_type=DOCX_MEDIA_TYPE,
        blocks=blocks,
        needs_ocr=False,
        warnings=_text_quality_warnings(blocks),
    )


def parse_document(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".docx":
        return parse_docx(path)
    raise ValueError(f"Unsupported document type: {suffix or '<none>'}")

