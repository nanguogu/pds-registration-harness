from datetime import date

from pds_harness.domain.models import (
    PlanRuntimeValues,
    RegistrationDefaults,
    ResolvedDictionaryValues,
)
from pds_harness.services.plan_payload import (
    apply_catalog_to_plan,
    apply_registration_defaults,
    build_function_tree,
    canonical_plan_hash,
)


def test_builds_module_function_tree(valid_catalog):
    tree = build_function_tree(valid_catalog, plan_id=15191)
    assert len(tree) == 1
    assert tree[0]["itemType"] == 2
    assert tree[0]["parentItemCode"] == "0"
    assert tree[0]["children"][0]["itemType"] == 3
    assert tree[0]["children"][0]["parentItemCode"] == tree[0]["itemCode"]


def test_applies_confirmed_registration_defaults():
    defaults = RegistrationDefaults(
        test_specification="STS-QI-41-V2.0《软件产品登记测试规范》",
        test_method_labels=["黑盒测试", "文档检查"],
        criteria=["GB/T 25000.51-2016"],
        onsite_option_label="本实验室测试",
    )
    dictionaries = ResolvedDictionaryValues(
        test_method_ids={"黑盒测试": 140001, "文档检查": 140002},
        onsite_option_ids={"本实验室测试": 230001},
    )
    runtime = PlanRuntimeValues(
        current_username="陈圆", compile_date=date(2026, 11, 10)
    )
    payload = apply_registration_defaults(
        {"programId": 81202},
        defaults=defaults,
        dictionaries=dictionaries,
        runtime=runtime,
    )

    assert payload["ycsTestPlanBase"]["testMethods"] == [140001, 140002]
    assert payload["ycsTestPlanBase"]["compileUserName"] == "陈圆"
    assert payload["ycsTestPlanSceneTest"]["wasNeed"] == 230001
    assert payload["ycsTestPlanImplement"]["testUsers"] == ["陈圆"]
    assert payload["ycsTestPlanImplement"]["testDates"] == []


def test_applies_catalog_without_mutating_remote_plan(valid_catalog):
    remote = {"programId": 81202, "planId": 15191, "ycsTestPlanTesItems": []}
    payload = apply_catalog_to_plan(remote, valid_catalog, plan_id=15191)
    assert remote["ycsTestPlanTesItems"] == []
    assert payload["ycsTestPlanTesItems"][0]["itemType"] == 2


def test_plan_hash_ignores_volatile_fields():
    left = {"planId": 1, "statusValue": "待提交", "value": 2}
    right = {"planId": 1, "statusValue": "新文本", "value": 2}
    assert canonical_plan_hash(left) == canonical_plan_hash(right)

