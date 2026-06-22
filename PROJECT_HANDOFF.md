# PersonalAgent 项目交接

本文档用于让新会话快速接手 `E:\codex_pro\PersonalAgent`。优先读本文，再读 `AGENTS.md`、`README.md`、`CLAUDE.md` 和关键入口文件。

## 当前状态

- 项目定位：单用户、本地优先的 PersonalAgent。
- 后端：`personal_agent/`，FastAPI 应用与 CLI。
- 前端：`frontend/`，React + Vite。
- 数据库：本地 SQLite，默认 `.personal_agent/agent.db`，该目录是运行态，不提交。
- LLM：统一经 `personal_agent/core/llm_gateway.py`；产品默认 DeepSeek，离线测试通过显式内部 fixture。
- 远端：`origin` -> `https://github.com/chenjianli1994/Personal-Agent.git`。
- 当前验收：`.\.venv\Scripts\python.exe -m pytest -q` -> `126 passed`；`tests/test_personal_forbidden_scan.py` -> `2 passed`；`tests/test_personal_memory_recall.py` -> `30 passed`。
- 前端最近验收：`frontend` 下 `npm run typecheck` 通过。
- 本地常见未跟踪文件：`.env`、`.tmp/`，不要提交。

## 已完成：记忆召回与自学习 P0/P1/P2

P0/P1/P2 已落地并复审通过。整体目标是把 PersonalAgent 从“能写学习候选”推进到“能召回正文、能统计效果、能优化排序、能整理记忆”。

### P0：召回修复

- 新增统一召回模块 `personal_agent/knowledge_recall.py`。
- `context_builder.py`、`artifact_generation.py`、`runtime.py` 共用统一召回逻辑。
- approved `memory_lesson` 不再只以标题进入聊天路径，而是按 prompt 相关性召回正文，并经 `safe_recall_prompt_item()` 安全摘录后注入 prompt。
- 普通知识与长期经验分流为 `knowledge` / `memories`，同时保留轻量 `knowledge_refs` / `memory_refs`。
- 保留并测试 `approve_memory_candidate()` 后同步搜索索引的行为，避免同进程刚批准的经验检索不到。

### P1/P1.5：效果反馈回路

- `knowledge_items` 已增加幂等统计列：`use_count`、`helpful_count`、`unhelpful_count`、`last_used_at`。
- `recall_knowledge()` 只召回，不记账。
- 只有成功注入 prompt 并成功写入 assistant message / draft 后，才通过 `record_recall_feedback(..., event="use")` 记 use。
- 文档质量通过且模型声明用到的记忆才记 helpful。
- 用户纠正上一轮时，只对上一轮实际使用或可归因的 billable memory 记 unhelpful。
- P1.5 修复了污染正文的归因问题：字段级脱敏，不安全正文不注入、不计 use/helpful/unhelpful。
- 召回排序组件可解释：`score`、`confidence`、`helpful_rate`、`use_boost`、`unhelpful_penalty`，`last_used_at` 只作稳定 tie-breaker。

### P2/P2.1：检索质量、记忆巩固、反思器降本

- 召回增加 query variants、CJK 分片与窗口匹配，改善中文局部词和混合词召回。
- 新增 `consolidate_memory_lessons()`：
  - 只处理 `status='active'` 的 `memory_lesson`。
  - 检测重复经验并软标记 loser 为 `deprecated`。
  - 检测冲突经验并打 `suspected_conflict` 标签。
  - 低效经验只软退役，不物理删除。
  - 更新后同步搜索索引。
- 新增手动入口：`POST /api/personal/memory/consolidate`。
- `learning_reflector.py` 增加低价值闲聊门控和隐式学习事件：明确纠错、连续 fallback、重复草稿修订、草稿质量失败。
- `runtime.should_run_learning_reflector()` 在明显闲聊/短确认时跳过学习反思器，保留文档、代码、纠错、批准/驳回等不可跳过信号。
- `skill_reflector` 不会未经批准直接修改 skill；它只创建 `skill_update_candidate`，用户批准后才激活新的 skill 版本。

## 关键入口

- 应用工厂：[personal_agent/app.py](E:\codex_pro\PersonalAgent\personal_agent\app.py)
- CLI：[personal_agent/cli.py](E:\codex_pro\PersonalAgent\personal_agent\cli.py)
- 路由：[personal_agent/routes.py](E:\codex_pro\PersonalAgent\personal_agent\routes.py)
- 会话运行时：[personal_agent/runtime.py](E:\codex_pro\PersonalAgent\personal_agent\runtime.py)
- 上下文构建：[personal_agent/context_builder.py](E:\codex_pro\PersonalAgent\personal_agent\context_builder.py)
- 记忆召回：[personal_agent/knowledge_recall.py](E:\codex_pro\PersonalAgent\personal_agent\knowledge_recall.py)
- 学习反思：[personal_agent/learning_reflector.py](E:\codex_pro\PersonalAgent\personal_agent\learning_reflector.py)
- Skill 反思：[personal_agent/skill_reflector.py](E:\codex_pro\PersonalAgent\personal_agent\skill_reflector.py)
- 草稿生成：[personal_agent/artifact_generation.py](E:\codex_pro\PersonalAgent\personal_agent\artifact_generation.py)
- 数据库 schema：[personal_agent/core/database.py](E:\codex_pro\PersonalAgent\personal_agent\core\database.py)
- 搜索索引：[personal_agent/core/knowledge_base.py](E:\codex_pro\PersonalAgent\personal_agent\core\knowledge_base.py)
- LLM 测试夹具：[personal_agent/core/fake_llm_provider.py](E:\codex_pro\PersonalAgent\personal_agent\core\fake_llm_provider.py)
- 禁用词护栏：[personal_agent/content_guard.py](E:\codex_pro\PersonalAgent\personal_agent\content_guard.py)
- 前端主界面：[frontend/src/personal/PersonalAgentApp.tsx](E:\codex_pro\PersonalAgent\frontend\src\personal\PersonalAgentApp.tsx)

典型后端调用链：

```text
cli.py -> app.py -> bootstrap.py -> routes.py -> runtime.py -> feature modules -> personal_agent/core/**
```

## 工程约束

- 默认中文回复。
- 不提交 `.personal_agent/`、`.env`、`.venv/`、`frontend/node_modules/`、`frontend/dist/`、`.tmp/`。
- `personal_agent/content_guard.py` 是退休词和退休 project input key 的唯一来源。
- 构造污染测试数据时，从 `FORBIDDEN_PERSONAL_TERMS` / `RETIRED_PROJECT_INPUT_KEYS` 取值，不要手写同一套词表。
- `tests/test_personal_forbidden_scan.py` 必须继续覆盖 `personal_agent/core/`。
- 保留 `personal_agent/core/collaboration.py` 和 bootstrap 调用，除非任务明确要求移除。
- 代码 patch 能力默认产出 candidate draft；不要绕过策略直接应用真实代码变更。
- `knowledge_recall.py` 依赖方向保持：`context_builder/artifact_generation/runtime -> knowledge_recall -> core/database + core/knowledge_base`。
- `recall_knowledge()` 不得写统计；统计只通过显式 `record_recall_feedback()` 发生。

## 常用命令

后端测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

记忆召回专项：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_personal_memory_recall.py -q
```

禁用词护栏测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_personal_forbidden_scan.py -q
```

后端离线运行：

```powershell
.\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870
```

前端：

```powershell
cd frontend
npm run typecheck
npm run dev
```

手动触发记忆巩固：

```http
POST /api/personal/memory/consolidate
```

## 新功能开发建议

- 新会话可以直接从新功能需求开始，不需要继续 P0/P1/P2。
- 如果新功能涉及记忆、知识、反思器或 skill，请先读 `personal_agent/knowledge_recall.py`、`personal_agent/runtime.py`、`personal_agent/learning_reflector.py`、`personal_agent/skill_reflector.py`。
- 如果改 schema，统一走 `personal_agent/core/database.py` 的建表与兼容迁移 helper。
- 如果改 routes，补 TestClient 测试；如果改 runtime，优先补端到端 chat turn 测试。
- 如果涉及 content guard、LLM 测试夹具、bootstrap、知识导入或 core 模块，额外跑 forbidden scan。

## 新会话提示词

```text
项目路径：E:\codex_pro\PersonalAgent

请先阅读 PROJECT_HANDOFF.md、AGENTS.md、README.md、CLAUDE.md，然后开始新的功能开发。

当前状态：PersonalAgent 是单用户、本地优先助手。P0/P1/P2 记忆召回与自学习优化已落地并复审通过：approved memory 可正文召回，use/helpful/unhelpful 反馈闭环已接通，中文召回增强和记忆巩固入口已接入。最近后端验收：pytest -q 为 94 passed；tests/test_personal_memory_recall.py 为 29 passed；tests/test_personal_forbidden_scan.py 为 2 passed。frontend 下 npm run typecheck 最近通过。

约束：默认中文回复；不要提交 .env、.personal_agent、.venv、frontend/node_modules、frontend/dist、.tmp 等本地/运行态文件；content_guard.py 是禁用词和退休输入键唯一来源；构造污染测试数据必须从 FORBIDDEN_PERSONAL_TERMS / RETIRED_PROJECT_INPUT_KEYS 取值；不要绕过 candidate draft 策略直接应用真实代码 patch。

接下来我要开发一个新的功能：[在这里写新功能目标]。请先审查相关代码上下文，然后直接给出并落地一个完整、自洽、可评审的补丁，最后运行相关测试并汇报结果。
```

## 已知非阻塞事项

- `pytest` 会出现 `fastapi.testclient` 的 deprecation warning，不阻塞当前工作。
- 前端构建可能出现 chunk size warning，不影响 typecheck。
- `.env` 和 `.tmp/` 是本地未跟踪文件，保持不提交。
