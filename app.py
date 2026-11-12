from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
import yaml

from pds_harness.config import load_settings
from pds_harness.domain.models import FunctionCatalog
from pds_harness.services.catalog_validation import CatalogValidator


ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / "config" / "registration_defaults.yaml"
RISK_PATH = ROOT / "config" / "risk_register.yaml"


st.set_page_config(page_title="PDS 登记测试 Harness", layout="wide")
st.title("PDS 登记测试自动化 Harness")
st.caption("开发基线：真实草稿写入和提交默认关闭")

settings = load_settings(SETTINGS_PATH)
risks = yaml.safe_load(RISK_PATH.read_text(encoding="utf-8"))["risks"]

tab_status, tab_defaults, tab_catalog = st.tabs(
    ["开发状态", "登记测试默认值", "功能项 JSON 校验"]
)

with tab_status:
    left, right = st.columns(2)
    with left:
        st.subheader("运行开关")
        st.metric("草稿写入", "已启用" if settings.pds.write_drafts_enabled else "已关闭")
        st.metric("计划提交", "已启用" if settings.pds.submit_enabled else "已关闭")
        st.metric("云模型", "已启用" if settings.cloud_policy.enabled else "已关闭")
    with right:
        st.subheader("未解决风险")
        for risk in risks:
            icon = "🔴" if risk.get("production_blocker") else "🟠"
            st.write(f"{icon} **{risk['id']}** — {risk['title']} ({risk['status']})")

with tab_defaults:
    plan = settings.registration_plan
    st.write("**测试规范**", plan.test_specification)
    st.write("**测试方法**", "、".join(plan.test_method_labels))
    st.write("**测试依据**", "\n".join(plan.criteria))
    st.write("**现场测试**", plan.onsite_option_label)
    st.write("**监督人**", "、".join(plan.default_supervisors))
    st.write("**客户陪同**", "、".join(plan.default_customers))
    st.write("**地点**", "、".join(plan.default_test_places))
    st.info("测试者取当前 PDS 登录用户；日期必须与客户确认后填写。")

with tab_catalog:
    uploaded = st.file_uploader("上传结构化功能项 JSON", type=["json"])
    if uploaded is not None:
        try:
            raw = json.loads(uploaded.getvalue().decode("utf-8"))
            catalog = FunctionCatalog.model_validate(raw)
            issues = CatalogValidator(
                prohibited_phrases=settings.validation.prohibited_phrases,
                minimum_confidence=settings.validation.minimum_confidence,
            ).validate(catalog)
            st.success(
                f"结构有效：{len(catalog.modules)} 个模块，"
                f"{sum(len(module.functions) for module in catalog.modules)} 个功能"
            )
            if issues:
                st.dataframe([issue.__dict__ for issue in issues], use_container_width=True)
            else:
                st.success("未发现确定性校验问题")
        except Exception as exc:
            st.error(f"校验失败：{exc}")

