# PersonalAgent 项目交接

本文档用于让新会话快速接手 `E:\codex_pro\PersonalAgent`。优先读本文，再读 `AGENTS.md`、`README.md` 和关键入口文件。

## 当前状态

- 项目定位：单用户、本地优先的 PersonalAgent，不再按旧平台子模块理解。
- 后端：`personal_agent/`，FastAPI 应用与 CLI。
- 前端：`frontend/`，React + Vite。
- 数据库：本地 SQLite，默认 `.personal_agent/agent.db`，该目录是运行态，不提交。
- LLM：统一经 `personal_agent/core/llm_gateway.py`；测试默认使用 fake provider。
- 最近验收：`pytest -q` 为 `65 passed`，`npm run typecheck` 通过。
- GitHub 远端：`origin` -> `https://github.com/chenjianli1994/Personal-Agent.git`。
- 本地常见未跟踪文件：`.env`；本地配置不提交。
- 待落地方案：`MEMORY_RECALL_OPTIMIZATION_PLAN.md`，目标是修复 approved memory 在聊天回答中的正文召回失效，并补齐 P0+P1 的记忆使用统计回路。

## 快速启动

后端安装与测试：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pytest -q
```

后端离线冒烟：

```powershell
.\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870 --fake-llm
```

前端：

```powershell
cd frontend
npm install
npm run dev
npm run typecheck
```

前端开发地址通常是 `http://127.0.0.1:5173`。

## 核心入口

- 应用工厂：[personal_agent/app.py](E:\codex_pro\PersonalAgent\personal_agent\app.py)
- CLI：[personal_agent/cli.py](E:\codex_pro\PersonalAgent\personal_agent\cli.py)
- 路由：[personal_agent/routes.py](E:\codex_pro\PersonalAgent\personal_agent\routes.py)
- 会话运行时：[personal_agent/runtime.py](E:\codex_pro\PersonalAgent\personal_agent\runtime.py)
- 启动初始化：[personal_agent/bootstrap.py](E:\codex_pro\PersonalAgent\personal_agent\bootstrap.py)
- 数据库 schema：[personal_agent/core/database.py](E:\codex_pro\PersonalAgent\personal_agent\core\database.py)
- LLM 网关：[personal_agent/core/llm_gateway.py](E:\codex_pro\PersonalAgent\personal_agent\core\llm_gateway.py)
- fake provider：[personal_agent/core/fake_llm_provider.py](E:\codex_pro\PersonalAgent\personal_agent\core\fake_llm_provider.py)
- 禁用词护栏：[personal_agent/content_guard.py](E:\codex_pro\PersonalAgent\personal_agent\content_guard.py)
- 前端主界面：[frontend/src/personal/PersonalAgentApp.tsx](E:\codex_pro\PersonalAgent\frontend\src\personal\PersonalAgentApp.tsx)

典型后端调用链：

```text
cli.py -> app.py -> bootstrap.py -> routes.py -> runtime.py -> feature modules -> personal_agent/core/**
```

## 关键模块

- 对话与意图：`runtime.py`、`intent_router.py`、`policy_guard.py`、`context_builder.py`
- 草稿与质量检查：`artifact_generation.py`、`artifact_drafts.py`、`artifact_export.py`、`artifact_quality.py`
- 输入材料：`input_documents.py`、`source_semantic_model.py`
- 知识与学习：`knowledge_learning.py`、`learning_reflector.py`、`skill_reflector.py`、`skill_runtime.py`、`skill_registry.py`
- 代码库能力：`personal_agent/core/codebase/`、`personal_agent/core/tool_registry.py`
- 协作与角色 seed：`personal_agent/core/collaboration.py`

## 近期整改结果

已经完成旧流程残留清理与回归护栏：

- tracked 的旧样例/种子目录已移除。
- 默认 project inputs 只保留 `personal_test_command`。
- `content_guard.py` 是禁用词和退休输入键的唯一来源。
- 知识导入、bootstrap 清理和扫描测试都复用同一套护栏。
- 扫描测试覆盖 `personal_agent/core/`，避免核心代码再次成为盲区。
- fake provider 已删除旧死分支，并清理无效乱码触发词和软残留文案。
- `collaboration.py` 保留模块和 bootstrap 调用，只做术语与权限名收敛。

## 下一步计划

记忆召回与自学习优化已完成方案评审，方案文件为 `MEMORY_RECALL_OPTIMIZATION_PLAN.md`。下一会话可以按该文件直接落地 P0+P1 合并补丁：

1. `personal_agent/core/database.py` 给 `knowledge_items` 增加 `use_count`、`helpful_count`、`unhelpful_count`、`last_used_at` 幂等列。
2. 新建 `personal_agent/knowledge_recall.py`，统一聊天与文档生成的 knowledge/memory 召回，不在召回阶段记账。
3. 接入 `context_builder.py`、`artifact_generation.py`、`runtime.py`，让 approved memory 正文进入 `_llm_answer()` prompt，并只在 assistant message 或 draft 成功写库后记录 use/helpful/unhelpful。
4. 新增 `tests/test_personal_memory_recall.py`，覆盖 approve 前后同进程召回、正文注入、统计记账、content guard 安全摘录和排序回归。
5. 验收至少运行 `.\.venv\Scripts\python.exe -m pytest -q` 与 `.\.venv\Scripts\python.exe -m pytest tests\test_personal_forbidden_scan.py -q`。

关键约束：

- `knowledge_recall.py` 依赖方向固定为 `context_builder/artifact_generation/runtime -> knowledge_recall -> core/database + core/knowledge_base`，不要反向依赖运行时或生成模块。
- `recall_knowledge(...)` 只召回不记账；统计只通过显式 `record_recall_feedback(..., event="use|helpful|unhelpful")` 更新。
- 保留并用测试保护 `approve_memory_candidate()` 批准后调用 `index_knowledge_item_search_entry()` 的索引同步行为。
- 不修改 `content_guard.py`，不为长期记忆正文开豁免；不安全 memory 摘录跳过注入且不计 use。

## 工程约束

- 不提交 `.personal_agent/`、`.env`、`.venv/`、`frontend/node_modules/`、`frontend/dist/` 等运行态或本地文件。
- 不要重新引入旧平台依赖或旧流程词汇。需要判断某个词是否可用时，先看 `personal_agent/content_guard.py`。
- 不要在业务代码、测试或文档里手写禁用词清单。测试需要污染样例时，从 `FORBIDDEN_PERSONAL_TERMS` 取值。
- `PersonalLLMGateway` 是安全标识，`content_guard` 的边界规则不会误伤它。
- 用户可见文本默认中文；代码标识符和注释优先英文，遵守既有风格。
- 数据库 schema 统一维护在 `personal_agent/core/database.py`，迁移兼容逻辑走该文件里的 helper。
- 代码 patch 能力默认只产出 personal candidate draft；实际应用或运行验证必须走显式确认和能力开关。

## 验收命令

日常变更至少跑：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run typecheck
```

涉及文案、知识导入、bootstrap、fake provider 或 core 模块时，额外确认：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_personal_forbidden_scan.py -q
```

如果需要人工禁用词审计，使用 `personal_forbidden_hits()`，不要另写一套规则。

## 新会话接手提示

可直接把下面这段给新协作者：

```text
项目路径：E:\codex_pro\PersonalAgent

这是一个本地 PersonalAgent 项目，后端在 personal_agent/，前端在 frontend/。
请先读 PROJECT_HANDOFF.md、AGENTS.md、README.md、MEMORY_RECALL_OPTIMIZATION_PLAN.md。
当前验证状态：pytest -q 为 65 passed，frontend 下 npm run typecheck 通过。
运行态目录和本地配置不提交。
旧流程禁用词和退休输入键只以 personal_agent/content_guard.py 为准；不要在业务代码、测试或文档里重新手写清单。
```

## 已知非阻塞事项

- `pytest` 会出现 `fastapi.testclient` 的 deprecation warning，不阻塞当前工作。
- 前端构建可能出现 chunk size warning，不影响 typecheck。
- `.env` 是本地配置，保持未跟踪。
