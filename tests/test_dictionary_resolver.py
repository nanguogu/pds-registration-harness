import pytest

from pds_harness.domain.models import RegistrationDefaults
from pds_harness.services.dictionary_resolver import (
    extract_dictionary_entries,
    resolve_registration_dictionaries,
)


def defaults():
    return RegistrationDefaults(
        test_specification="spec",
        test_method_labels=["黑盒测试", "文档检查"],
        criteria=["criterion"],
        onsite_option_label="本实验室测试",
    )


def test_resolves_unique_dictionary_labels():
    methods = {
        "code": 10000,
        "data": {"data": [{"dataId": 1, "dataName": "黑盒测试"}, {"dataId": 2, "dataName": "文档检查"}]},
    }
    onsite = {"data": [{"dataId": 3, "dataName": "本实验室测试"}]}
    resolved = resolve_registration_dictionaries(
        defaults=defaults(),
        test_method_payload=methods,
        onsite_payload=onsite,
    )
    assert resolved.method_ids_for(["黑盒测试", "文档检查"]) == [1, 2]
    assert resolved.onsite_id_for("本实验室测试") == 3


def test_duplicate_dictionary_label_is_rejected():
    methods = {
        "data": [
            {"dataId": 1, "dataName": "黑盒测试"},
            {"dataId": 2, "dataName": "黑盒测试"},
            {"dataId": 3, "dataName": "文档检查"},
        ]
    }
    onsite = {"data": [{"dataId": 4, "dataName": "本实验室测试"}]}
    with pytest.raises(ValueError, match="duplicate"):
        resolve_registration_dictionaries(
            defaults=defaults(),
            test_method_payload=methods,
            onsite_payload=onsite,
        )


def test_empty_dictionary_is_rejected():
    with pytest.raises(ValueError):
        extract_dictionary_entries({"data": []})

