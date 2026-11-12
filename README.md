# PDS Registration Harness

这是 PDS 登记测试自动化 Harness 的安全基线实现。

当前版本包含：

- 可恢复的工作流状态机；
- 保密状态云调用闸门；
- 结构化功能项 JSON 模型；
- 功能项确定性校验；
- 登记测试默认参数合成；
- 模块 `itemType=2` → 功能 `itemType=3` 的 PDS Payload 映射；
- PDS API 适配层；
- 专用 Chrome 登录会话与只读 PDS 连通性探测；
- SQLite 本地任务账本；
- 提交默认关闭的紧急停用开关。

当前版本只从 Harness 自己启动的专用 Chrome 会话中读取 Token，Token
仅在进程内存中使用，不进入日志、数据库或归档文件。第三方模型参数只从
本地 `.env` 读取，并且只有保密状态明确为“不保密”的项目能通过云调用
闸门。草稿写入开关已启用，提交仍保持关闭。

风险及用户接受的边界记录在 `config/risk_register.yaml`。PDS 内部接口和
第三方模型已由用户明确授权使用，但提交仍需完整人工复核。`STS1260215`
返回的说明书身份不匹配，只能作为异常样本解析，禁止用于该项目的摘要、
功能项生成或写入。

已有计划的功能树草稿保存已在多个真实项目上验证：读取完整计划，只替换
`ycsTestPlanTesItems`，调用 `saveYcsRegPlanInfo`，再回读核对模块、功能和说明。
新“待计划编制”项目仍需完成首次计划创建、真实字典值和当前登录用户字段的
端到端验收。

## 项目文档采集与功能项来源

项目材料不再按“只选一份说明书”处理。Harness 会合并申请表附件与样品清单，
按用途分类并采用以下固定优先级：

1. 存在唯一“功能清单/功能列表”时，直接解析其四列表格生成结构化功能项
   JSON，不调用云模型，也不使用操作手册改写功能项；
2. 同时下载操作手册，仅用于产品摘要、功能背景理解和人工核查；
3. 只有缺少功能清单时，才允许使用操作手册调用模型生成待人工复核功能项；
4. 多份同类主文档会停止自动选择，要求人工明确目标。

归档按项目、文档角色和处理阶段分层：

```text
<archive>/<项目编号>/documents/
  function-list/
    original/   # PDS 下载原件
    derived/    # .doc 转换得到的可解析 .docx
    parsed/     # 结构化解析结果
  manual/
    original/
    derived/
    parsed/
```

`generation/<来源SHA-256>/direct_function_list_v1/` 保存由功能清单直接生成的
唯一功能项 JSON 和复核清单。产品摘要包含 `source_document_path`，工作台通过
仅限归档根目录的本地文件接口提供“查看原文件”链接。

## 运行测试

```powershell
cd C:\Users\宇宙\OneDrive\文档\ChatGPT\test_project\pds-registration-harness
python -m pytest
```

## 本地校验功能项 JSON

```powershell
python -m pds_harness.cli validate path\to\function_catalog.json
```

## 启动本地 Web 工作台

推荐使用 Session Bridge 启动脚本：

```powershell
cd C:\Users\宇宙\OneDrive\文档\ChatGPT\test_project\pds-registration-harness
.\start-harness.ps1
```

脚本会在本机回环地址 `127.0.0.1:8766` 启动独立的 PDS Session
Bridge，再启动 `127.0.0.1:8765` 工作台。工作台重启时 Bridge 和专用
Chrome 保持运行，所以不需要重复登录。只有 PDS 会话过期、用户关闭专用
会话或 Bridge 进程退出时才需要重新登录。Bridge 不提供 Token 读取接口，
Token 仍只存在于专用浏览器和 Bridge 进程内存中。

开发时也可以不使用 Bridge，直接启动单进程工作台：

```powershell
$env:PYTHONPATH = "src"
python -m uvicorn pds_harness.webapp:app --host 127.0.0.1 --port 8765
```

然后访问 `http://127.0.0.1:8765`。服务只监听本机回环地址。

### 连接 PDS（只读）

1. 在工作台点击“打开 PDS 登录窗口”；
2. 在弹出的专用 Chrome 窗口中由测试人员手工完成登录；
3. 回到工作台点击“检测登录状态”；
4. 显示“已连接”后点击“只读连接测试”。

专用浏览器配置保存在 `D:\myfiles\test_job\pds-browser-profile`，与日常
Chrome 配置隔离。只读测试调用项目统计查询接口，最多请求一条记录，界面
只显示业务状态码和数量，不显示项目、客户或联系人资料。

## 安全约束

- `submitYcsRegPlanInfo` 默认禁用；即使调用代码路径，也必须同时满足运行开关和人工确认对象。
- 保密状态只有精确规范化为“不保密”时才允许云调用。
- Token 不进入异常文本、普通日志或归档 JSON。
- 所有同步 Playwright 操作固定在单独的所有者线程中，避免 Web 请求线程
  切换导致浏览器会话失效。
- 现有唯一草稿应恢复使用；发现多个候选草稿时由上层停止并要求人工选择。

