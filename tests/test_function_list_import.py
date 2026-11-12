from docx import Document

from pds_harness.services.function_list_import import import_function_list_docx


def test_imports_four_column_function_list_without_model(tmp_path):
    path = tmp_path / "功能列表.docx"
    document = Document()
    table = document.add_table(rows=5, cols=4)
    rows = [
        ["产品名称", "产品名称", "产品名称", "示例软件V1"],
        ["软件功能项目", "软件功能项目", "软件功能项目", "功能说明"],
        ["文件", "导入", "导入Gerber", "导入Gerber数据"],
        ["文件", "导出", "导出Gerber", "导出Gerber数据"],
        ["实用\n工具", "层管理", "层管理", "管理图层"],
    ]
    for row, values in zip(table.rows, rows):
        for cell, value in zip(row.cells, values):
            cell.text = value
    document.save(path)

    catalog = import_function_list_docx(
        path,
        program_id=1,
        program_code="STS1260001",
        source_sha256="a" * 64,
        original_name="功能列表.doc",
    )
    assert catalog.model == "none-direct-function-list"
    assert [module.name for module in catalog.modules] == ["文件", "实用工具"]
    assert [item.name for item in catalog.modules[0].functions] == [
        "导入Gerber",
        "导出Gerber",
    ]
    assert catalog.modules[0].functions[0].description == "导入Gerber数据"


def test_imports_three_column_function_list_template(tmp_path):
    path = tmp_path / "三列功能列表.docx"
    document = Document()
    table = document.add_table(rows=4, cols=3)
    rows = [
        ["产品名称", "产品名称", "示例软件V1"],
        ["软件功能项目", "软件功能项目", "功能说明"],
        ["文件", "打开工程", "打开已有工程"],
        ["工具", "层管理", "管理图层"],
    ]
    for row, values in zip(table.rows, rows):
        for cell, value in zip(row.cells, values):
            cell.text = value
    document.save(path)

    catalog = import_function_list_docx(
        path,
        program_id=2,
        program_code="STS1260002",
        source_sha256="b" * 64,
        original_name="三列功能列表.docx",
    )
    assert [module.name for module in catalog.modules] == ["文件", "工具"]
    assert catalog.modules[0].functions[0].name == "打开工程"


def test_disambiguates_repeated_three_column_function_names(tmp_path):
    path = tmp_path / "重复功能列表.docx"
    document = Document()
    table = document.add_table(rows=4, cols=3)
    rows = [
        ["产品名称", "产品名称", "示例软件V1"],
        ["软件功能项目", "软件功能项目", "功能说明"],
        ["文件", "导入", "DXF数据"],
        ["文件", "导入", "Gerber数据"],
    ]
    for row, values in zip(table.rows, rows):
        for cell, value in zip(row.cells, values):
            cell.text = value
    document.save(path)
    catalog = import_function_list_docx(
        path,
        program_id=3,
        program_code="STS1260003",
        source_sha256="c" * 64,
        original_name="重复功能列表.docx",
    )
    assert [item.name for item in catalog.modules[0].functions] == [
        "导入DXF数据",
        "导入Gerber数据",
    ]
