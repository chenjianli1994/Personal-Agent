# AGENTS.md

本文件用于 Codex、Claude Code 以及其他工程代理在新会话中快速接手本仓库。

## 项目定位

`PersonalAgent` 是单用户、本地优先的智能助手项目。

- 工作目录：`E:\codex_pro\PersonalAgent`
- 后端：`personal_agent/`
- 前端：`frontend/`
- 本地数据库：默认 `.personal_agent/agent.db`
- 远端：`https://github.com/chenjianli1994/Personal-Agent.git`

新会话开始时，先读：

1. `PROJECT_HANDOFF.md`
2. `README.md`
3. `CLAUDE.md`
4. `personal_agent/app.py`
5. `personal_agent/runtime.py`
6. `personal_agent/routes.py`
7. `personal_agent/core/database.py`

## 必须遵守

- 默认用中文回复。
- 不提交 `.personal_agent/`、`.env`、`.venv/`、`frontend/node_modules/`、`frontend/dist/`。
- 不要把运行态、本地配置、依赖目录或构建产物纳入提交。
- `personal_agent/content_guard.py` 是退休词和退休 project input key 的唯一来源。
- 需要构造污染测试数据时，从 `FORBIDDEN_PERSONAL_TERMS` / `RETIRED_PROJECT_INPUT_KEYS` 取值，不要在测试里手写同一套词表。
- `tests/test_personal_forbidden_scan.py` 必须继续覆盖 `personal_agent/core/`。
- 保留 `personal_agent/core/collaboration.py` 和 bootstrap 调用，除非任务明确要求移除该能力。
- 代码 patch 能力默认产出 candidate draft；不要绕过策略直接应用真实代码变更。

## 常用命令

后端测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

禁用词护栏测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_personal_forbidden_scan.py -q
```

后端离线运行：

```powershell
.\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870 --fake-llm
```

前端：

```powershell
cd frontend
npm run typecheck
npm run dev
```

当前验收结果：

- `pytest -q`：`65 passed`
- `npm run typecheck`：通过

## 关键入口

- `personal_agent/app.py`：FastAPI app factory
- `personal_agent/bootstrap.py`：启动初始化、本地 DB、默认项目、文档技能 seed
- `personal_agent/routes.py`：API 路由
- `personal_agent/runtime.py`：chat turn 编排
- `personal_agent/intent_router.py`：意图路由
- `personal_agent/policy_guard.py`：策略边界
- `personal_agent/artifact_generation.py`：草稿生成与质量失败候选记录
- `personal_agent/artifact_quality.py`：文档质量检查
- `personal_agent/core/llm_gateway.py`：LLM 网关
- `personal_agent/core/fake_llm_provider.py`：离线测试 fixture
- `personal_agent/core/database.py`：schema 与兼容迁移
- `personal_agent/core/codebase/`：代码库索引、检索、影响分析、patch planning

## 提交流程

- 提交信息默认中文。
- 提交前确认 `git status --short` 中没有误加本地文件。
- 跨模块或护栏相关变更至少跑 `pytest -q` 和 `frontend` 下 `npm run typecheck`。
- 如果改了文案、fake provider、bootstrap、知识导入、core 模块或 content guard，额外跑 forbidden scan 测试。
