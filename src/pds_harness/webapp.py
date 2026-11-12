from __future__ import annotations

import html
import json
import os
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from pds_harness.adapters.browser_session import (
    BrowserSessionConfig,
    PDSBrowserSession,
)
from pds_harness.adapters.cloud_model import (
    CloudModelConfig,
    OpenAICompatibleModelClient,
)
from pds_harness.adapters.pds_client import PDSClient
from pds_harness.adapters.session_bridge_client import PDSBridgeClient
from pds_harness.config import HarnessSettings, load_settings
from pds_harness.domain.errors import (
    CloudModelError,
    PDSAPIError,
    PDSConnectionError,
    PDSConnectionNotStartedError,
    PDSLoginRequiredError,
    PDSProjectNotFoundError,
    PDSProjectSafetyError,
)
from pds_harness.domain.models import FunctionCatalog
from pds_harness.services.catalog_validation import CatalogValidator
from pds_harness.services.document_parser import parse_document
from pds_harness.services.pds_connection import PDSConnectionManager


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "config" / "registration_defaults.yaml"
RISK_PATH = PROJECT_ROOT / "config" / "risk_register.yaml"
ENV_PATH = PROJECT_ROOT / ".env"


def _settings() -> HarnessSettings:
    return load_settings(SETTINGS_PATH)


def _risks() -> list[dict[str, Any]]:
    payload = yaml.safe_load(RISK_PATH.read_text(encoding="utf-8"))
    return payload["risks"]


def _build_pds_connection() -> PDSConnectionManager | PDSBridgeClient:
    bridge_url = os.environ.get("PDS_SESSION_BRIDGE_URL", "").strip()
    if bridge_url:
        return PDSBridgeClient(bridge_url)
    settings = _settings()
    profile_dir = settings.storage.requested_archive_root / "pds-browser-profile"
    login_url = f"{settings.pds.base_url.rstrip('/')}/ycs-v2-back/"
    return PDSConnectionManager(
        session_factory=lambda: PDSBrowserSession(
            BrowserSessionConfig(
                profile_dir=profile_dir,
                login_url=login_url,
                channel="chrome",
                headless=False,
                credentials_env_path=ENV_PATH,
            )
        ),
        client_factory=lambda token_provider: PDSClient(
            token_provider,
            base_url=settings.pds.base_url,
            submit_enabled=False,
        ),
        archive_root=settings.storage.requested_archive_root,
        plan_defaults=settings.registration_plan,
        write_drafts_enabled=settings.pds.write_drafts_enabled,
    )


PDS_CONNECTION = _build_pds_connection()


def _build_model_client() -> OpenAICompatibleModelClient | None:
    if not _settings().cloud_policy.enabled:
        return None
    return OpenAICompatibleModelClient(CloudModelConfig.from_env_file(ENV_PATH))


MODEL_CLIENT = _build_model_client()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        yield
    finally:
        PDS_CONNECTION.shutdown()
        if MODEL_CLIENT is not None:
            MODEL_CLIENT.close()


def _home_html() -> str:
    settings = _settings()
    risks = _risks()
    risk_rows = "".join(
        "<tr>"
        f"<td>{html.escape(risk['id'])}</td>"
        f"<td>{html.escape(risk['title'])}</td>"
        f"<td>{html.escape(risk['status'])}</td>"
        f"<td>{'是' if risk.get('production_blocker') else '否'}</td>"
        "</tr>"
        for risk in risks
    )
    defaults = settings.registration_plan
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>PDS 登记测试 Harness</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; background: #f5f7fa; color: #1f2937; }}
    main {{ max-width: 1100px; margin: 32px auto; padding: 0 20px; }}
    .card {{ background: white; border-radius: 12px; padding: 20px; margin-bottom: 18px; box-shadow: 0 2px 10px #0000000d; }}
    .switches {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; }}
    .metric {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; }}
    .off {{ color: #b91c1c; font-weight: 700; }}
    .ready {{ color: #047857; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; text-align: left; }}
    code {{ background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }}
    button {{ background: #1677ff; color: white; border: 0; border-radius: 6px; padding: 9px 14px; cursor: pointer; }}
    button.secondary {{ background: #475569; }}
    button:disabled {{ opacity: .55; cursor: wait; }}
    input[type=text] {{ min-width: 250px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 9px 10px; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; }}
    pre {{ white-space: pre-wrap; max-height: 380px; overflow: auto; background:#111827; color:#e5e7eb; padding:14px; border-radius:8px; }}
  </style>
</head>
<body><main>
  <h1>PDS 登记测试自动化 Harness</h1>
  <p>开发模式：真实草稿写入、提交和云模型均默认关闭。</p>
  <section class="card switches">
    <div class="metric">草稿写入<br><span class="off">{'已启用' if settings.pds.write_drafts_enabled else '已关闭'}</span></div>
    <div class="metric">计划提交<br><span class="off">{'已启用' if settings.pds.submit_enabled else '已关闭'}</span></div>
    <div class="metric">云模型<br><span class="off">{'已启用' if settings.cloud_policy.enabled else '已关闭'}</span></div>
  </section>
  <section class="card">
    <h2>PDS 专用浏览器连接</h2>
    <p>状态：<span id="pds-state" class="off">正在读取…</span></p>
    <p>令牌只在专用浏览器和 Session Bridge 进程内存中使用，不写入日志、数据库或归档文件。工作台重启不会关闭 Bridge。连接测试只查询一条记录，并且只向界面返回数量信息。</p>
    <div class="actions">
      <button data-pds-action onclick="pdsAction('start')">打开 PDS 登录窗口</button>
      <button data-pds-action class="secondary" onclick="refreshPdsStatus()">检测登录状态</button>
      <button data-pds-action onclick="pdsAction('probe')">只读连接测试</button>
      <button data-pds-action class="secondary" onclick="pdsAction('stop')">关闭专用会话</button>
    </div>
    <pre id="pds-result">尚未连接。</pre>
  </section>
  <section class="card">
    <h2>云模型连接</h2>
    <p>仅保密状态明确为“不保密”的项目允许发送；API Key 只从本地 <code>.env</code> 读取，不展示、不写入归档。</p>
    <div class="actions">
      <button data-model-action onclick="probeModel()">模型连接测试</button>
    </div>
    <pre id="model-result">尚未测试。</pre>
  </section>
  <section class="card">
    <h2>项目材料只读测试</h2>
    <p>精确匹配项目编号并读取完整样品清单。优先下载功能清单作为功能项来源，同时下载操作手册用于产品摘要和人工核查；只有缺少功能清单时才由操作手册生成待复核功能项。</p>
    <div class="actions">
      <input id="project-code" type="text" value="STS1260215" maxlength="10" aria-label="项目编号">
      <button data-project-action onclick="prepareProject()">读取并归档项目文档</button>
    </div>
    <pre id="project-result">尚未执行。</pre>
    <div id="product-summary"></div>
    <div id="project-document-links"></div>
  </section>
  <section class="card">
    <h2>登记测试默认值</h2>
    <p><b>测试规范：</b>{html.escape(defaults.test_specification)}</p>
    <p><b>测试方法：</b>{html.escape('、'.join(defaults.test_method_labels))}</p>
    <p><b>依据标准：</b>{html.escape('；'.join(defaults.criteria))}</p>
    <p><b>现场测试：</b>{html.escape(defaults.onsite_option_label)}</p>
    <p><b>地点：</b>{html.escape('、'.join(defaults.default_test_places))}</p>
    <p><b>日期：</b>保持空白，联系客户后由测试人员填写。</p>
  </section>
  <section class="card">
    <h2>未解决风险</h2>
    <table><thead><tr><th>ID</th><th>风险</th><th>状态</th><th>生产阻断</th></tr></thead><tbody>{risk_rows}</tbody></table>
  </section>
  <section class="card">
    <h2>功能项 JSON 校验</h2>
    <input id="catalog" type="file" accept="application/json,.json">
    <button onclick="validateCatalog()">校验</button>
    <pre id="result">请选择 JSON 文件。</pre>
  </section>
  <script>
    function setPdsBusy(busy) {{
      document.querySelectorAll('[data-pds-action]').forEach(button => button.disabled = busy);
    }}

    function renderPdsStatus(payload) {{
      const labels = {{
        stopped: '未启动',
        starting: '正在启动',
        login_required: '等待手工登录',
        ready: '已连接',
        error: '连接错误'
      }};
      const state = payload.state || 'error';
      const element = document.getElementById('pds-state');
      element.textContent = labels[state] || state;
      element.className = state === 'ready' ? 'ready' : 'off';
      document.getElementById('pds-result').textContent = JSON.stringify(payload, null, 2);
    }}

    async function readJson(response) {{
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${{response.status}}`);
      return payload;
    }}

    async function refreshPdsStatus() {{
      setPdsBusy(true);
      try {{
        renderPdsStatus(await readJson(await fetch('/api/pds-connection/status')));
      }} catch (error) {{
        renderPdsStatus({{state:'error', error:error.message}});
      }} finally {{
        setPdsBusy(false);
      }}
    }}

    async function pdsAction(action) {{
      setPdsBusy(true);
      try {{
        const payload = await readJson(await fetch(`/api/pds-connection/${{action}}`, {{method:'POST'}}));
        renderPdsStatus(payload);
      }} catch (error) {{
        const state = action === 'probe' ? 'login_required' : 'error';
        renderPdsStatus({{state, error:error.message}});
      }} finally {{
        setPdsBusy(false);
      }}
    }}

    async function prepareProject() {{
      const button = document.querySelector('[data-project-action]');
      const code = document.getElementById('project-code').value.trim().toUpperCase();
      button.disabled = true;
      document.getElementById('project-result').textContent = '正在执行只读检查与下载…';
      try {{
        const response = await fetch(`/api/projects/${{encodeURIComponent(code)}}/prepare-documents`, {{method:'POST'}});
        const payload = await readJson(response);
        document.getElementById('project-result').textContent = JSON.stringify(payload, null, 2);
        await renderProductSummary(code);
        renderProjectDocumentLinks(payload);
      }} catch (error) {{
        document.getElementById('project-result').textContent = JSON.stringify({{error:error.message}}, null, 2);
      }} finally {{
        button.disabled = false;
      }}
    }}

    async function renderProductSummary(code) {{
      const container = document.getElementById('product-summary');
      container.replaceChildren();
      const response = await fetch(`/api/projects/${{encodeURIComponent(code)}}/review-package`);
      if (response.status === 404) return;
      const payload = await readJson(response);
      const title = document.createElement('h3');
      title.textContent = '产品摘要';
      const overview = document.createElement('p');
      overview.textContent = payload.summary.overview;
      const source = document.createElement('p');
      source.append('原始文档：');
      const link = document.createElement('a');
      link.textContent = payload.source_document_path;
      link.href = `/api/archive-file?path=${{encodeURIComponent(payload.source_document_path)}}`;
      link.target = '_blank';
      link.rel = 'noopener';
      source.appendChild(link);
      container.append(title, overview, source);
    }}

    function renderProjectDocumentLinks(payload) {{
      const container = document.getElementById('project-document-links');
      container.replaceChildren();
      const documents = Array.isArray(payload.documents) ? payload.documents : [];
      if (!documents.length) return;
      const title = document.createElement('h3');
      title.textContent = '产品摘要与功能项核查原文';
      container.appendChild(title);
      for (const documentInfo of documents) {{
        const row = document.createElement('p');
        const label = documentInfo.role === 'function-list' ? '功能清单' : '操作手册';
        row.append(`${{label}}：`);
        const link = document.createElement('a');
        link.textContent = documentInfo.original_name;
        link.href = `/api/archive-file?path=${{encodeURIComponent(documentInfo.view_path)}}`;
        link.target = '_blank';
        link.rel = 'noopener';
        row.appendChild(link);
        container.appendChild(row);
      }}
    }}

    async function probeModel() {{
      const button = document.querySelector('[data-model-action]');
      button.disabled = true;
      document.getElementById('model-result').textContent = '正在测试 JSON 模式…';
      try {{
        const payload = await readJson(await fetch('/api/model/probe', {{method:'POST'}}));
        document.getElementById('model-result').textContent = JSON.stringify(payload, null, 2);
      }} catch (error) {{
        document.getElementById('model-result').textContent = JSON.stringify({{error:error.message}}, null, 2);
      }} finally {{
        button.disabled = false;
      }}
    }}

    async function validateCatalog() {{
      const file = document.getElementById('catalog').files[0];
      if (!file) return;
      const body = JSON.parse(await file.text());
      const response = await fetch('/api/validate-catalog', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
      document.getElementById('result').textContent = JSON.stringify(await response.json(), null, 2);
    }}
    refreshPdsStatus();
  </script>
</main></body></html>"""


app = FastAPI(
    title="PDS Registration Harness", version="0.3.0", lifespan=_lifespan
)


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return _home_html()


@app.get("/health")
def health() -> dict[str, Any]:
    settings = _settings()
    return {
        "status": "ok",
        "write_drafts_enabled": settings.pds.write_drafts_enabled,
        "submit_enabled": settings.pds.submit_enabled,
        "cloud_enabled": settings.cloud_policy.enabled,
    }


@app.get("/api/archive-file")
def archive_file(path: str) -> FileResponse:
    root = _settings().storage.requested_archive_root.resolve()
    target = Path(path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(status_code=403, detail="Path is outside the archive root")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Archived file was not found")
    return FileResponse(target, filename=target.name)


@app.get("/api/projects/{program_code}/review-package")
def review_package(program_code: str) -> dict[str, Any]:
    code = program_code.strip().upper()
    if not re.fullmatch(r"STS\d{7}", code):
        raise HTTPException(status_code=422, detail="Invalid project code")
    root = _settings().storage.requested_archive_root.resolve()
    generation_root = (root / code / "generation").resolve()
    if root not in generation_root.parents or not generation_root.is_dir():
        raise HTTPException(status_code=404, detail="Review package was not found")
    candidates = list(generation_root.rglob("product_summary.json"))
    if not candidates:
        raise HTTPException(status_code=404, detail="Product summary was not found")
    summary_path = max(candidates, key=lambda item: item.stat().st_mtime_ns)
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Product summary is invalid") from exc
    source_path = Path(str(summary.get("source_document_path") or "")).resolve()
    if root not in source_path.parents or not source_path.is_file():
        raise HTTPException(status_code=409, detail="Summary source document is unavailable")
    return {
        "program_code": code,
        "summary": summary,
        "source_document_path": str(source_path),
    }


@app.post("/api/model/probe")
def probe_model() -> dict[str, Any]:
    if MODEL_CLIENT is None:
        raise HTTPException(status_code=409, detail="Cloud model integration is disabled")
    try:
        return MODEL_CLIENT.probe()
    except CloudModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _raise_connection_http_error(exc: Exception) -> None:
    if isinstance(exc, PDSProjectNotFoundError):
        status_code = 404
    elif isinstance(
        exc,
        (
            PDSConnectionNotStartedError,
            PDSLoginRequiredError,
            PDSProjectSafetyError,
        ),
    ):
        status_code = 409
    else:
        status_code = 503
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/api/pds-connection/status")
def pds_connection_status() -> dict[str, Any]:
    try:
        return PDS_CONNECTION.status().to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/pds-connection/start")
def start_pds_connection() -> dict[str, Any]:
    try:
        return PDS_CONNECTION.start().to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/pds-connection/probe")
def probe_pds_connection() -> dict[str, Any]:
    try:
        return PDS_CONNECTION.probe().to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/pds-connection/stop")
def stop_pds_connection() -> dict[str, Any]:
    try:
        return PDS_CONNECTION.stop().to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/projects/{program_code}/prepare")
def prepare_project(program_code: str) -> dict[str, Any]:
    try:
        return PDS_CONNECTION.prepare_project(program_code).to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/projects/{program_code}/prepare-documents")
def prepare_project_documents(program_code: str) -> dict[str, Any]:
    try:
        result = PDS_CONNECTION.prepare_project_documents(program_code)
        return result if isinstance(result, dict) else result.to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/projects/{program_code}/inspect-plan-contract")
def inspect_plan_contract(program_code: str) -> dict[str, Any]:
    try:
        result = PDS_CONNECTION.inspect_plan_contract(program_code)
        return result if isinstance(result, dict) else result.to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/projects/{program_code}/create-plan-draft")
def create_plan_draft(program_code: str) -> dict[str, Any]:
    try:
        result = PDS_CONNECTION.create_plan_draft(program_code)
        return result if isinstance(result, dict) else result.to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/projects/{program_code}/write-function-catalog")
def write_function_catalog(
    program_code: str, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    try:
        catalog = FunctionCatalog.model_validate(payload)
        result = PDS_CONNECTION.write_function_catalog(program_code, catalog)
        return result if isinstance(result, dict) else result.to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/projects/{program_code}/download-exception-sample")
def download_exception_sample(
    program_code: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    if payload.get("acknowledge_identity_mismatch") is not True:
        raise HTTPException(
            status_code=422,
            detail="Explicit identity-mismatch acknowledgement is required",
        )
    try:
        return PDS_CONNECTION.download_exception_sample(program_code).to_dict()
    except (PDSConnectionError, PDSAPIError) as exc:
        _raise_connection_http_error(exc)


@app.post("/api/validate-catalog")
def validate_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        catalog = FunctionCatalog.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = _settings()
    issues = CatalogValidator(
        prohibited_phrases=settings.validation.prohibited_phrases,
        minimum_confidence=settings.validation.minimum_confidence,
    ).validate(catalog)
    return {
        "valid": not any(issue.severity == "error" for issue in issues),
        "module_count": len(catalog.modules),
        "function_count": sum(len(module.functions) for module in catalog.modules),
        "issues": [issue.__dict__ for issue in issues],
    }


@app.post("/api/inspect-document")
def inspect_document(file: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(status_code=415, detail="Only PDF and DOCX are supported")
    content = file.file.read(250 * 1024 * 1024 + 1)
    if len(content) > 250 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Document exceeds 250 MiB")
    with tempfile.TemporaryDirectory(prefix="pds-harness-") as directory:
        path = Path(directory) / f"document{suffix}"
        path.write_bytes(content)
        try:
            parsed = parse_document(path)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "sha256": parsed.sha256,
        "media_type": parsed.media_type,
        "block_count": len(parsed.blocks),
        "needs_ocr": parsed.needs_ocr,
        "warnings": parsed.warnings,
        "preview": [block.model_dump() for block in parsed.blocks[:20]],
    }

