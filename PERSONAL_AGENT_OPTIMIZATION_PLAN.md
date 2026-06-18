# PersonalAgent 优化完整方案

本文档用于新会话直接接手落地 PersonalAgent 性能、可维护性、测试与前端体验优化。方案按 4 个独立补丁推进：先修召回与代码检索的规模性能硬伤，再优化代码库检索读盘，然后整理可维护性与可观测性，最后补测试和前端体验。每个 Phase 单独提交、单独验收、可独立回滚；不改变公开 HTTP API，不触碰 candidate draft 策略，不提交运行态文件。

## Phase 1：召回与代码检索性能

- 文档镜像 item 映射采用 search entry 方案：`kb_doc_*` 的 `knowledge_search_entries(source_kind='item')` 行必须持久保存 `document_id`。
- `knowledge_search_entries` 是双 schema：必须在 `personal_agent/core/database.py` 的 `SCHEMA_SQL` 与 `personal_agent/core/knowledge_base.py::_ensure_search_schema()` 两处都新增 `idx_knowledge_search_entries_item_uid ON knowledge_search_entries(source_kind, item_uid)`。
- 存量库索引兼容走 `CREATE INDEX IF NOT EXISTS`，不是 `_add_columns()`；在 `_ensure_compat_columns()` 末尾或专门 compat helper 中执行同名 `CREATE INDEX IF NOT EXISTS`，覆盖旧库。
- `_upsert_item_search_entry()` 增加可选 `document_id` 参数；普通 knowledge item 写 `NULL`，文档镜像 item 写真实 `knowledge_documents.id`。
- 文档导入/更新、内容未变但索引缺失、文档状态更新这三类文档上下文调用点，都传入真实 `doc_id`。
- search schema ensure 阶段执行独立幂等回填，不依赖 `ensure_knowledge_search_index()` 的数量门控或强制 rebuild：遍历 `knowledge_documents`，计算 `_document_item_uid(doc_uid)`，对 `source_kind='item' AND item_uid=? AND document_id IS NULL` 的 entry 更新 `document_id`。
- `_document_for_item_uid()` 改为通过 `source_kind='item' AND item_uid=?` 查 `document_id`，再按主键查文档；移除全表扫描。
- `_relevant_files()` 在一次连接中读取 repo root，并对候选 `file_id IN (...)` 批量 `GROUP BY` 统计 symbol/include，删除循环内重开连接和逐文件 COUNT。
- query 解析只做局部缓存：复用 normalized query、terms、ngrams；FTS 主路径排序语义不变，legacy fallback 不再重复计算或依赖外层变量。

## Phase 2：代码检索读盘优化

- 给 `code_files` 增加内部兼容列 `source_preview TEXT NOT NULL DEFAULT ''`；不用 `content_preview`，避免和 `knowledge_search_entries.content_preview` 同名异义。
- 存量库列迁移走 `_ensure_compat_columns()` + `_add_columns(conn, "code_files", {"source_preview": ...})`；不能只改 `CREATE TABLE`。
- 不新增 `content_mtime_key`：复用现有 `code_files.last_modified` 判断 preview 是否对应当前文件；`hash` 继续用于索引刷新判定，不在搜索路径重新计算 hash。
- 破掉存量文件的 skip 门控：`personal_agent/core/codebase/index_store.py` 的“hash/last_modified 未变则复用”条件必须追加 `previous["source_preview"]` 非空；老库 preview 为空时强制走一次索引刷新写入 preview，之后恢复正常复用。
- 定义唯一常量 `CODE_FILE_RELEVANCE_CHARS = 10000`，同时替换 `_score_relevance()` 的 `text[:10000]` 与 `source_preview` 生成截断处，使打分输入长度只有一个来源。
- 解码逻辑必须单一来源：提取/复用现有 UTF-8、UTF-8-SIG、GB18030、GBK、latin-1、replace 顺序，供 scanner、`_read_indexed_file()`、preview 生成共同使用。
- `source_preview` 只在索引构建/刷新路径写入；搜索路径绝不写 DB。
- `_relevant_files()` 与 `_files_containing_type()` 共用 preview 读取入口：若 `source_preview` 为空或当前文件 stat 生成的 `last_modified` 与 DB 不一致，只临时读盘兜底，不回写。
- 重新索引时刷新 `source_preview`；不引入进程级 LRU。

## Phase 3：可维护性与可观测性

- 先补 chat 与 unified-turn 等价性测试，再抽公共文档意图/类型推断 helper，确保响应结构不变。
- `runtime.turn()` 拆为阶段方法：session/user message、context/route/reflection、dispatch、post-touch；保持副作用顺序不变。
- recall feedback 的 `ValueError` 静默吞咽改为 warning log；主流程仍不中断。
- async + SQLite 暂不异步化，只记录为后续债务，避免扩大改动面。

## Phase 4：测试稳固与前端体验

- 补 `policy_guard`、主 chat 流程、`should_run_learning_reflector()` 的直接测试。
- 清理脆弱测试：时间改 fixture/factory，浮点与排序断言显式使用 `pytest.approx()` 或明确 tie-breaker。
- 拆分 `PersonalAgentApp.tsx` 的主要 Panel；先用现有 React Context/本地状态，不引入 zustand。
- chat/upload 失败增加重试入口；保留现有提示。
- mojibake 正则先作为临时兜底保留；新增后端 UTF-8 响应测试，确认链路干净后在同 Phase 删除前端乱码正则。

## Public Interfaces / Schema

- 不新增、不删除、不改名任何公开 HTTP API 和响应字段。
- 内部 schema 只做幂等兼容迁移：
  - Phase 1：两处 schema 定义都新增 `knowledge_search_entries(source_kind, item_uid)` 索引；存量库通过 `CREATE INDEX IF NOT EXISTS` compat 路径覆盖。
  - Phase 2：新增 `code_files.source_preview`，由 `_add_columns()` compat 路径覆盖存量库。
- 所有 schema 变更走既有 `init_db()` / ensure helper；不得手工修改 `.personal_agent/agent.db` 或提交运行态数据。

## Test Plan

### Phase 1

- 新库和存量库都存在 `idx_knowledge_search_entries_item_uid`。
- 文档导入后 `kb_doc_*` item entry 带正确 `document_id`。
- 存量 `document_id IS NULL` 的文档镜像 entry 经 search schema ensure 独立回填，不依赖 rebuild。
- 搜索结果仍带 `doc_uid/document_id/approval_status` 等治理字段。
- legacy fallback 可正常返回结果。
- code retriever 对多个候选文件返回正确 symbol/include 计数。

### Phase 2

- 存量库迁移后 `code_files.source_preview` 存在。
- 老索引文件 `source_preview=''` 时，即使 hash/last_modified 未变，也会在下一次索引刷新中补写 preview。
- 索引构建写入 10KB `source_preview`。
- `_score_relevance()` 与 preview 生成共享同一常量。
- GBK/GB18030 文件的 preview 与读盘兜底解码一致。
- `_relevant_files()` 与 `_files_containing_type()` 都走 preview 读取入口。
- preview 过期时搜索临时读盘但不写库；重新索引后 preview 刷新。

### Phase 3

- chat 与 unified-turn 在文档生成判断上行为等价。
- runtime 拆分前后主流程响应与 metadata 不变。
- recall feedback 失败有 warning log 且不中断响应。

### Phase 4

- 新增直接单元测试覆盖 guard/gate/chat 分支。
- 前端拆分后 `npm run typecheck` 通过。
- chat/upload 失败可重试。
- UTF-8 响应测试通过，前端乱码正则可移除。

## Acceptance Defaults

- 每个 Phase 单独提交，提交信息使用中文。
- Phase 1/2 必跑：
  - `.\.venv\Scripts\python.exe -m pytest -q`
  - `.\.venv\Scripts\python.exe -m pytest tests\test_personal_forbidden_scan.py -q`
- Phase 4 额外跑：
  - `cd frontend`
  - `npm run typecheck`
- 若性能优化导致召回或代码检索排序变化，必须能用明确 bug 修复解释；否则视为回归。
- 不把巨型模块重构、async DB 改造、状态库引入、读盘 LRU 混入前两个性能补丁。

## 新会话 Goal 模式提示词

```text
项目路径：E:\codex_pro\PersonalAgent

请先阅读 PROJECT_HANDOFF.md、AGENTS.md、README.md、CLAUDE.md、PERSONAL_AGENT_OPTIMIZATION_PLAN.md，然后进入 goal 模式，目标是完整落地 PERSONAL_AGENT_OPTIMIZATION_PLAN.md 中的四个 Phase。

工作要求：
1. 按 Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 顺序实施；每个 Phase 都必须形成完整、自洽、可评审的补丁。
2. 不要只做第一轮性能优化；最终目标是四个 Phase 全部完成。
3. 每个 Phase 完成后运行文档指定的相关测试；如果失败，直接修到通过，不要停在“需要再修改”的中间态。
4. 完全落地后停止修改，只做最终核验和总结；不要在验收通过后继续反复微调、扩大范围或追加非计划内重构。
5. 严格遵守项目护栏：不提交 .personal_agent/、.env、.venv/、frontend/node_modules/、frontend/dist/；不改变公开 HTTP API；不触碰 candidate draft 策略；运行态和本地配置不纳入提交。
6. schema 变更必须走既有 init_db()/ensure helper 的幂等兼容迁移；不得手工修改 .personal_agent/agent.db。
7. Phase 1/2 必跑 pytest -q 和 forbidden scan；Phase 4 必跑 frontend 下 npm run typecheck。
8. 提交信息默认中文；提交前确认 git status --short 没有误加运行态文件。

完成标准：
- PERSONAL_AGENT_OPTIMIZATION_PLAN.md 中所有 Phase 的实现项、测试项、验收边界均已处理。
- 后端测试通过，前端 typecheck 通过。
- 最终只输出已完成内容、测试结果、剩余风险；不要再提出“是否继续修改”的开放式追问。
```
