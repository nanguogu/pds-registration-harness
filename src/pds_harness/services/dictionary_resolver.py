from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pds_harness.domain.models import (
    DictionaryEntry,
    RegistrationDefaults,
    ResolvedDictionaryValues,
)


TEST_METHOD_DICTIONARY_ID = 140000
ONSITE_DICTIONARY_ID = 230000


def extract_dictionary_entries(payload: Mapping[str, Any]) -> list[DictionaryEntry]:
    data: Any = payload.get("data", payload)
    while isinstance(data, Mapping) and "data" in data:
        data = data["data"]
    if not isinstance(data, list):
        raise ValueError("PDS dictionary response does not contain a list")
    entries: list[DictionaryEntry] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        identifier = item.get("dataId")
        name = item.get("dataName")
        if identifier is None or not isinstance(name, str) or not name.strip():
            continue
        entries.append(DictionaryEntry(data_id=int(identifier), data_name=name.strip()))
    if not entries:
        raise ValueError("PDS dictionary response contains no valid entries")
    return entries


def _unique_mapping(entries: list[DictionaryEntry]) -> dict[str, int]:
    result: dict[str, int] = {}
    duplicates: set[str] = set()
    for entry in entries:
        if entry.data_name in result:
            duplicates.add(entry.data_name)
        result[entry.data_name] = entry.data_id
    if duplicates:
        raise ValueError(f"PDS dictionary contains duplicate labels: {sorted(duplicates)}")
    return result


def resolve_registration_dictionaries(
    *,
    defaults: RegistrationDefaults,
    test_method_payload: Mapping[str, Any],
    onsite_payload: Mapping[str, Any],
) -> ResolvedDictionaryValues:
    methods = _unique_mapping(extract_dictionary_entries(test_method_payload))
    onsite = _unique_mapping(extract_dictionary_entries(onsite_payload))
    resolved = ResolvedDictionaryValues(
        test_method_ids=methods,
        onsite_option_ids=onsite,
    )
    resolved.method_ids_for(defaults.test_method_labels)
    resolved.onsite_id_for(defaults.onsite_option_label)
    return resolved

