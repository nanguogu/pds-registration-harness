from pathlib import Path

from pds_harness.config import load_settings


def test_loads_confirmed_defaults():
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config" / "registration_defaults.yaml")
    assert settings.pds.write_drafts_enabled is True
    assert settings.pds.submit_enabled is False
    assert settings.registration_plan.test_method_labels == ["黑盒测试", "文档检查"]
    assert settings.registration_plan.leave_test_dates_empty is True
    assert settings.cloud_policy.enabled is True

