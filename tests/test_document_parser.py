from docx import Document
import fitz

from pds_harness.services.document_parser import parse_document


def test_parses_docx_headings_paragraphs_and_tables(tmp_path):
    path = tmp_path / "manual.docx"
    document = Document()
    document.add_heading("终端管理", level=1)
    document.add_paragraph("控制终端开机和关机。")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "功能"
    table.cell(0, 1).text = "说明"
    document.save(path)

    parsed = parse_document(path)
    assert parsed.needs_ocr is False
    assert {block.block_type for block in parsed.blocks} >= {
        "heading",
        "paragraph",
        "table",
    }
    assert [block.block_type for block in parsed.blocks] == [
        "heading",
        "paragraph",
        "table",
    ]
    assert parsed.blocks[-1].section == "终端管理"
    assert parsed.warnings == []


def test_warns_only_for_real_unicode_extraction_damage(tmp_path):
    path = tmp_path / "damaged.docx"
    document = Document()
    document.add_paragraph("正常中文不应被误报。")
    document.add_paragraph("损坏字符：\ufffd")
    document.save(path)

    parsed = parse_document(path)

    assert len(parsed.warnings) == 1
    assert "Unicode 替换字符" in parsed.warnings[0]


def test_marks_low_text_pdf_for_ocr(tmp_path):
    path = tmp_path / "scan.pdf"
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()

    parsed = parse_document(path)
    assert parsed.needs_ocr is True
    assert parsed.warnings

