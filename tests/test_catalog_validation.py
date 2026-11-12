from pds_harness.domain.models import FunctionCatalog
from pds_harness.services.catalog_validation import CatalogValidator


def test_valid_catalog_has_no_errors(valid_catalog):
    issues = CatalogValidator().validate(
        valid_catalog, expected_sections=["终端管理"]
    )
    assert [issue for issue in issues if issue.severity == "error"] == []


def test_detects_duplicate_conclusion_language_and_coverage_gap(valid_catalog):
    raw = valid_catalog.model_dump()
    duplicate = dict(raw["modules"][0]["functions"][0])
    duplicate["local_id"] = "function-2"
    duplicate["description"] = "验证终端功能正常。"
    raw["modules"][0]["functions"].append(duplicate)
    catalog = FunctionCatalog.model_validate(raw)

    issues = CatalogValidator().validate(
        catalog, expected_sections=["终端管理", "权限管理"]
    )
    codes = {issue.code for issue in issues}
    assert "duplicate_function" in codes
    assert "conclusion_language" in codes
    assert "coverage_gap" in codes

