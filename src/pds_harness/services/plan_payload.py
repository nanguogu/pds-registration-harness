from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pds_harness.domain.models import (
    FunctionCatalog,
    PlanRuntimeValues,
    RegistrationDefaults,
    ResolvedDictionaryValues,
)


VOLATILE_PLAN_FIELDS = {
    "auditDate",
    "statusValue",
    "updateDate",
    "updatedAt",
    "timestamp",
}


def _item_code(
    *, program_id: int, plan_id: int, item_type: int, position: int, name: str
) -> str:
    seed = f"pds-harness|{program_id}|{plan_id}|{item_type}|{position}|{name}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest().upper()


def build_function_tree(
    catalog: FunctionCatalog, *, plan_id: int
) -> list[dict[str, Any]]:
    if plan_id <= 0:
        raise ValueError("plan_id must be a positive integer")

    tree: list[dict[str, Any]] = []
    function_position = 0
    for module_position, module in enumerate(catalog.modules, start=1):
        module_code = _item_code(
            program_id=catalog.program_id,
            plan_id=plan_id,
            item_type=2,
            position=module_position,
            name=module.name,
        )
        children: list[dict[str, Any]] = []
        for function in module.functions:
            function_position += 1
            function_code = _item_code(
                program_id=catalog.program_id,
                plan_id=plan_id,
                item_type=3,
                position=function_position,
                name=function.name,
            )
            children.append(
                {
                    "__itemCode": function_code,
                    "itemCode": function_code,
                    "itemType": 3,
                    "testItem": function.name,
                    "itemName": "",
                    "testExplain": function.description,
                    "parentItemCode": module_code,
                    "children": [],
                }
            )
        tree.append(
            {
                "__itemCode": module_code,
                "itemCode": module_code,
                "itemType": 2,
                "testItem": module.name,
                "itemName": "",
                "testExplain": "",
                "parentItemCode": "0",
                "children": children,
            }
        )
    return tree


def apply_registration_defaults(
    initialized_plan: Mapping[str, Any],
    *,
    defaults: RegistrationDefaults,
    dictionaries: ResolvedDictionaryValues,
    runtime: PlanRuntimeValues,
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(initialized_plan))
    base = payload.setdefault("ycsTestPlanBase", {})
    scene = payload.setdefault("ycsTestPlanSceneTest", {})
    implementation = payload.setdefault("ycsTestPlanImplement", {})

    method_ids = dictionaries.method_ids_for(defaults.test_method_labels)
    base["testSpecification"] = defaults.test_specification
    base["testMethods"] = method_ids
    base["testMethod"] = ",".join(str(value) for value in method_ids)
    base["testMethodOther"] = defaults.test_method_other
    base["standardMethod"] = "\n".join(defaults.criteria)
    base["ycsTestPlanCriteriaBasis"] = list(defaults.criteria)
    base["ycsTestPlanCriteriaBasisChecks"] = list(defaults.criteria)
    criteria_json = json.dumps(defaults.criteria, ensure_ascii=False)
    base["criteriaStr"] = criteria_json
    base["criteriaChecksStr"] = criteria_json
    base["compileUserName"] = runtime.current_username
    base["compileDate"] = runtime.compile_date.isoformat()
    base["testMember"] = runtime.current_username

    scene["wasNeed"] = dictionaries.onsite_id_for(defaults.onsite_option_label)
    scene.setdefault("wasNeedReason", None)
    scene.setdefault("wasNeedReasons", None)
    scene.setdefault("otherReason", None)

    implementation["testUsers"] = [runtime.current_username]
    implementation["supervisors"] = list(defaults.default_supervisors)
    implementation["customers"] = list(defaults.default_customers)
    implementation["testPlaces"] = list(defaults.default_test_places)
    implementation["testDates"] = (
        [runtime.test_date.isoformat()] if runtime.test_date is not None else []
    )
    return payload


def apply_catalog_to_plan(
    remote_plan: Mapping[str, Any], catalog: FunctionCatalog, *, plan_id: int
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(remote_plan))
    remote_program_id = payload.get("programId")
    if remote_program_id is not None and int(remote_program_id) != catalog.program_id:
        raise ValueError(
            f"Remote programId={remote_program_id} does not match catalog "
            f"programId={catalog.program_id}"
        )
    payload["planId"] = plan_id
    payload["programId"] = catalog.program_id
    payload["ycsTestPlanTesItems"] = build_function_tree(catalog, plan_id=plan_id)
    return payload


def _without_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_volatile(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_PLAN_FIELDS
        }
    if isinstance(value, list):
        return [_without_volatile(item) for item in value]
    return value


def canonical_plan_hash(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _without_volatile(dict(plan)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

