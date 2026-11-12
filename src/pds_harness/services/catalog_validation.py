from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from pds_harness.domain.errors import CatalogValidationError
from pds_harness.domain.models import FunctionCatalog


DEFAULT_PROHIBITED_PHRASES = ("正常", "正确", "成功", "通过", "符合要求")
DEFAULT_ACTION_VERBS = (
    "安装",
    "启动",
    "登录",
    "退出",
    "创建",
    "新增",
    "删除",
    "修改",
    "编辑",
    "查看",
    "查询",
    "搜索",
    "筛选",
    "配置",
    "设置",
    "管理",
    "维护",
    "导入",
    "导出",
    "上传",
    "下载",
    "生成",
    "运行",
    "执行",
    "控制",
    "选择",
    "切换",
    "展示",
    "显示",
    "统计",
    "分析",
    "验证",
    "保存",
    "提交",
    "发送",
    "接收",
    "关联",
    "分配",
    "监控",
    "监看",
    "播放",
    "广播",
    "组织",
    "限制",
    "支持",
    "获取",
    "处理",
    "同步",
    "调用",
    "识别",
    "记录",
)


@dataclass(frozen=True)
class ValidationIssue:
    severity: Literal["error", "warning"]
    code: str
    message: str
    path: str


class CatalogValidator:
    def __init__(
        self,
        *,
        prohibited_phrases: Iterable[str] = DEFAULT_PROHIBITED_PHRASES,
        action_verbs: Iterable[str] = DEFAULT_ACTION_VERBS,
        minimum_confidence: float = 0.60,
    ) -> None:
        self.prohibited_phrases = tuple(prohibited_phrases)
        self.action_verbs = tuple(action_verbs)
        self.minimum_confidence = minimum_confidence

    def validate(
        self,
        catalog: FunctionCatalog,
        *,
        expected_sections: Iterable[str] | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen_modules: set[str] = set()

        for module_index, module in enumerate(catalog.modules):
            module_key = module.name.casefold()
            module_path = f"modules[{module_index}]"
            if module_key in seen_modules:
                issues.append(
                    ValidationIssue(
                        "error",
                        "duplicate_module",
                        f"模块名称重复：{module.name}",
                        f"{module_path}.name",
                    )
                )
            seen_modules.add(module_key)

            seen_functions: set[str] = set()
            for function_index, function in enumerate(module.functions):
                path = f"{module_path}.functions[{function_index}]"
                function_key = function.name.casefold()
                if function_key in seen_functions:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "duplicate_function",
                            f"模块“{module.name}”中功能名称重复：{function.name}",
                            f"{path}.name",
                        )
                    )
                seen_functions.add(function_key)

                for phrase in self.prohibited_phrases:
                    if phrase in function.description:
                        issues.append(
                            ValidationIssue(
                                "error",
                                "conclusion_language",
                                f"功能说明包含结论性词语“{phrase}”",
                                f"{path}.description",
                            )
                        )

                if not function.description.startswith(self.action_verbs):
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "verb_object_uncertain",
                            "功能说明可能不是动宾结构，需要人工复核",
                            f"{path}.description",
                        )
                    )

                if not function.source_refs:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "missing_source",
                            "功能项缺少来源依据",
                            f"{path}.source_refs",
                        )
                    )

                if function.confidence < self.minimum_confidence:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "low_confidence",
                            f"置信度 {function.confidence:.2f} 低于阈值",
                            f"{path}.confidence",
                        )
                    )

        if expected_sections is not None:
            decided = {entry.section for entry in catalog.coverage}
            for section in expected_sections:
                if section not in decided:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "coverage_gap",
                            f"章节尚未标记为已覆盖或已排除：{section}",
                            "coverage",
                        )
                    )
        return issues

    def assert_submittable(
        self,
        catalog: FunctionCatalog,
        *,
        expected_sections: Iterable[str] | None = None,
    ) -> list[ValidationIssue]:
        issues = self.validate(catalog, expected_sections=expected_sections)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            joined = "; ".join(f"{issue.code}: {issue.message}" for issue in errors)
            raise CatalogValidationError(joined)
        return issues

