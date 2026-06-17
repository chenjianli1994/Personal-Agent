# PersonalAgent 记忆召回与自学习优化方案 v4

## Summary

P0+P1 合并为一个补丁交付：修复 approved memory 不能进入聊天回答的问题，补齐统计列、实际使用记账、保守 helpful/unhelpful 回灌，并把批准经验后的索引同步行为固化为回归契约。P2 后续独立推进检索质量和记忆巩固。

## Key Changes

- `personal_agent/core/database.py` 给 `knowledge_items` 增加幂等统计列：`use_count`、`helpful_count`、`unhelpful_count`、`last_used_at`。
- 新增 `personal_agent/knowledge_recall.py`，依赖方向固定为 `context_builder/artifact_generation/runtime -> knowledge_recall -> core/database + core/knowledge_base`。
- `recall_knowledge(...)` 只召回不记账，返回稳定字段，并复用 `search_knowledge`；对 score 做固定归一化后再套统计排序。
- 保留并验证 `approve_memory_candidate()` 批准后索引同步行为，确保 `kb_memory_{id}` 立刻进入 knowledge search index。
- `context_builder.build()` 返回 `memory_refs/knowledge/memories`；refs 轻量，正文只放在正文层。
- `artifact_generation._generation_context()` 移除本地 `_load_knowledge/_keywords`，改用统一召回。
- `_llm_answer()` 注入普通 knowledge 摘录和 approved memory 安全摘录；只有 assistant message 成功写库后才记 `use`。
- 文档生成/修订成功写 draft 后记 `use`；质量门通过时对实际注入的 approved memory 记 `helpful`。
- 下一轮 learning reflector 判定 correction 时，对上一条 assistant metadata 中的 injected memory uid 记 `unhelpful`。
- 不安全 memory 摘录不进入 prompt、不计 use、不绕过 `content_guard`。

## Public Interfaces / Data Shape

- `context_builder.build()` 新增字段：`memory_refs`、`knowledge`、`memories`。
- recalled item 固定字段：`item_uid/title/category/source_type/source_ref/content/confidence/score/use_count/helpful_count/unhelpful_count/last_used_at`。
- assistant message metadata 新增：`injected_knowledge_item_uids`、`injected_memory_item_uids`。
- 不改公开 API 路由，前端无需变更。

## Test Plan

- candidate 创建 -> approve -> 断言写入 `knowledge_items(memory_lesson, active)`。
- approve 前 `recall_knowledge(category="memory_lesson")` 不返回该 lesson；approve 后同进程立刻召回能返回，保护现有索引同步调用。
- 断言 `context_builder.build()` 返回 `memory_refs/memories`，且 `memories.content` 包含 approved lesson 正文。
- monkeypatch `PersonalLLMGateway.complete_json` 捕获完整 `personal_chat_answer.user_prompt`，断言 approved memory 正文进入 prompt。
- successful chat answer 写库后，断言 assistant metadata 有 injected uid，且 `use_count++/last_used_at` 更新。
- fallback、审批轮次、单纯刷新 context 不增加 `use_count`。
- 旧库重复 `init_db()` 后新增列存在、默认值正确、迁移幂等。
- 增加某条 memory 的 `unhelpful_count` 后，断言其排序下降。
- 文档质量门通过时 helpful 增加；质量失败只记 use，不记 helpful。
- 含 `FORBIDDEN_PERSONAL_TERMS` 的 legacy memory 召回/聊天不崩，禁用词不进入 LLM prompt，不计 use。
- 回归命令：`.\.venv\Scripts\python.exe -m pytest -q` 和 `.\.venv\Scripts\python.exe -m pytest tests\test_personal_forbidden_scan.py -q`。

## Assumptions

- P0+P1 一次性交付，P2 后续独立交付。
- `approve_memory_candidate()` 的 item 写入与索引同步不是同一事务；若索引同步失败，后续 `ensure_knowledge_search_index()` 的计数重建作为兜底恢复。
- helpful 是近似归因，后续淘汰按比率判断，不按绝对 helpful 数判断。
- 不引入 embedding、jieba 或新依赖；P2 优先基于现有 `search_knowledge`、FTS 和 CJK n-gram。
- 不修改 `content_guard.py`，不为长期记忆开豁免。
