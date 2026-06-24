from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_agent.app import create_personal_app
from personal_agent.content_guard import FORBIDDEN_PERSONAL_TERMS
from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.database import connect, init_db
from personal_agent.core.knowledge_base import index_knowledge_item_search_entry, search_knowledge
from personal_agent.core.services_min import approve_memory_candidate
from personal_agent.knowledge_recall import consolidate_memory_lessons, recall_knowledge, recall_rank_components, record_recall_feedback, safe_recall_prompt_item, _recall_rank
from personal_agent.learning_reflector import learning_reflection_gate
from personal_agent.runtime import should_run_learning_reflector


LLMResult = getattr(llm_gateway_module, "LLMResult")
LLMBridge = getattr(llm_gateway_module, "PersonalLLMGateway")


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path, Path]:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TestClient(create_personal_app(db_path, workspace)), db_path, workspace


def _project_id(client: TestClient) -> int:
    return int(client.get("/api/personal/context").json()["project_id"])


def _add_active_source(client: TestClient) -> str:
    response = client.post(
        "/api/personal/sources/text",
        json={"title": "功能材料", "content": "请根据 AlphaPreference 编写功能规范。", "make_active": True},
    )
    assert response.status_code == 200, response.json()
    return response.json()["source_uid"]


def _valid_requirement_analysis_content(topic: str) -> str:
    return (
        "# 需求分析报告\n\n"
        "## 输入摘要\n"
        "- source: current_prompt\n\n"
        "## 原文事实表\n"
        f"- 源文包含 {topic} 的用户可观察行为和边界要求。\n\n"
        "## 术语与变量定义\n"
        f"- {topic}：当前输入材料中需要分析的功能对象。\n"
        "- 输入资料：用户当前提供的 source 材料。\n\n"
        "## 需求理解\n"
        f"- 需要围绕 {topic} 识别目标、边界、输入输出和验收风险。\n\n"
        "## 条件与状态机\n"
        "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| 分析 | 请根据 {topic} 编写功能规范 | 当前功能 | 编写功能规范并提炼用户可观察行为和边界 | 形成可验证需求 | source: current_prompt |\n"
        "| 当前状态 | 当前状态 | 控制对象 | 按源文策略输出 | 状态变化后退出 | source: current_prompt |\n\n"
        "## 歧义与待确认\n"
        "- 当前输入无额外歧义。\n\n"
        "## 关键假设\n"
        "- 未确认的信息保持待澄清。\n\n"
        "## 风险与边界\n"
        "- 边界条件需要在后续拆解阶段继续确认。\n\n"
        "## 验收建议\n"
        "- 覆盖输入、输出、边界和异常场景。\n\n"
        "## 证据引用\n"
        "- source: current_prompt\n"
    )


def _create_candidate(client: TestClient, lesson: str) -> int:
    response = client.post("/api/personal/learning/feedback", json={"feedback": lesson})
    assert response.status_code == 200, response.json()
    return int(response.json()["id"])


def _approve_candidate(client: TestClient, candidate_id: int) -> None:
    response = client.post(
        f"/api/personal/learning/candidates/{candidate_id}/approve",
        json={"reviewer": "tester", "comment": "memory recall test"},
    )
    assert response.status_code == 200, response.json()


def _item_stats(db_path: Path, item_uid: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT item_uid, use_count, helpful_count, unhelpful_count, last_used_at FROM knowledge_items WHERE item_uid=?",
            (item_uid,),
        ).fetchone()
    assert row is not None
    return dict(row)


def test_init_db_adds_recall_stats_columns_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    init_db(db_path)
    init_db(db_path)

    with connect(db_path) as conn:
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(knowledge_items)").fetchall()}

    for name in ["use_count", "helpful_count", "unhelpful_count", "last_used_at"]:
        assert name in columns
    assert columns["use_count"]["dflt_value"] == "0"
    assert columns["helpful_count"]["dflt_value"] == "0"
    assert columns["unhelpful_count"]["dflt_value"] == "0"
    assert columns["last_used_at"]["dflt_value"] == "''"


def test_approve_memory_is_searchable_immediately_and_calls_indexer(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    candidate_id = _create_candidate(client, "以后处理 AlphaImmediate 时必须先说明用户可观察行为")
    item_uid = f"kb_memory_{candidate_id}"

    assert not recall_knowledge(db_path, project_id=project_id, query="AlphaImmediate", category="memory_lesson")

    calls: list[str] = []

    def fake_index(db_path_arg: Path, item_uid_arg: str) -> None:
        calls.append(item_uid_arg)
        index_knowledge_item_search_entry(db_path_arg, item_uid_arg)

    monkeypatch.setattr("personal_agent.core.services_min.index_knowledge_item_search_entry", fake_index)
    approve_memory_candidate(db_path, candidate_id, reviewer="tester", comment="direct core approval")

    assert calls == [item_uid]
    recalled = recall_knowledge(db_path, project_id=project_id, query="AlphaImmediate", category="memory_lesson")
    assert [item["item_uid"] for item in recalled] == [item_uid]
    assert "AlphaImmediate" in recalled[0]["content"]


def test_context_and_chat_prompt_inject_approved_memory_and_record_use_after_message_write(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    candidate_id = _create_candidate(client, "以后回答 AlphaPrompt 问题时先给结论，再列关键理由")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"
    captured: dict[str, Any] = {}
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_chat_answer":
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = json.loads(user_prompt)
            return LLMResult(
                call_id=701,
                provider="fake-test",
                model="memory-chat-fixture",
                status="ok",
                parsed={"answer": "已按 AlphaPrompt 经验回答。", "used_sources": [], "limitations": []},
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "普通问题：AlphaPrompt 现在怎么答？"})

    assert response.status_code == 200, response.json()
    assert "Apply the user's approved long-term lessons below; they override default behavior." in captured["system_prompt"]
    memories = captured["user_prompt"]["memories"]
    assert memories and memories[0]["item_uid"] == item_uid
    assert "AlphaPrompt" in memories[0]["content"]
    metadata = response.json()["message"]["metadata"]
    assert metadata["injected_memory_item_uids"] == [item_uid]
    assert metadata["billable_memory_item_uids"] == [item_uid]
    provenance = metadata["recall_provenance"]
    assert any(item == {"uid": item_uid, "title": "个人反馈 #1", "kind": "memory"} for item in provenance)
    stats = _item_stats(db_path, item_uid)
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 0
    assert stats["unhelpful_count"] == 0
    assert stats["last_used_at"]
    context = client.get(f"/api/personal/sessions/{response.json()['session']['session_uid']}").json()
    assert any(item["metadata"].get("injected_memory_item_uids") == [item_uid] for item in context["messages"] if item["role"] == "assistant")
    assert recall_knowledge(db_path, project_id=project_id, query="AlphaPrompt", category="memory_lesson")[0]["use_count"] == 1


def test_dismissed_memory_lesson_is_removed_from_search_recall_index(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    candidate_id = _create_candidate(client, "浠ュ悗澶勭悊 AlphaDismiss 鏃跺厛璇存槑鍙洖鏉ユ簮")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"

    before = recall_knowledge(db_path, project_id=project_id, query="AlphaDismiss", category="memory_lesson")
    assert [item["item_uid"] for item in before] == [item_uid]

    dismissed = client.post(
        f"/api/personal/learning/{item_uid}/dismiss",
        json={"reviewer": "tester", "comment": "dismiss recalled lesson"},
    )

    assert dismissed.status_code == 200, dismissed.json()
    assert dismissed.json()["status"] == "deprecated"
    with connect(db_path) as conn:
        item_row = dict(conn.execute("SELECT status FROM knowledge_items WHERE item_uid=?", (item_uid,)).fetchone())
        search_row = dict(
            conn.execute(
                "SELECT status FROM knowledge_search_entries WHERE source_kind='item' AND item_uid=?",
                (item_uid,),
            ).fetchone()
        )
    assert item_row["status"] == "deprecated"
    assert search_row["status"] == "deprecated"
    assert recall_knowledge(db_path, project_id=project_id, query="AlphaDismiss", category="memory_lesson") == []
    assert all(item["item_uid"] != item_uid for item in search_knowledge(db_path, "AlphaDismiss", project_id=project_id, limit=5))


def test_fallback_answer_does_not_record_memory_use(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    candidate_id = _create_candidate(client, "以后处理 AlphaFallback 时先说明限制")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"

    response = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告 AlphaFallback"})

    assert response.status_code == 200, response.json()
    assert response.json()["message"]["metadata"]["fallback"] is True
    stats = _item_stats(db_path, item_uid)
    assert stats["use_count"] == 0
    assert stats["last_used_at"] == ""


def test_helpful_and_unhelpful_feedback_do_not_update_last_used_at(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    candidate_id = _create_candidate(client, "以后处理 AlphaFeedbackClock 时先说明判断依据")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"

    helpful = record_recall_feedback(db_path, item_uid=item_uid, event="helpful")
    unhelpful = record_recall_feedback(db_path, item_uid=item_uid, event="unhelpful")

    assert helpful["helpful_count"] == 1
    assert helpful["last_used_at"] == ""
    assert unhelpful["unhelpful_count"] == 1
    assert unhelpful["last_used_at"] == ""


def test_safe_recall_prompt_item_redacts_fields_without_dropping_usable_item() -> None:
    polluted = FORBIDDEN_PERSONAL_TERMS[0]
    item = {
        "item_uid": "kb_memory_redaction",
        "title": f"{polluted} title",
        "category": "memory_lesson",
        "source_type": "manual",
        "source_ref": f"legacy:{polluted}",
        "content": f"{polluted} body should not be injected",
        "confidence": 0.8,
        "score": 0.7,
    }

    safe = safe_recall_prompt_item(item, forbidden_text_checker=lambda text: [term for term in FORBIDDEN_PERSONAL_TERMS if term in text])

    assert safe["item_uid"] == "kb_memory_redaction"
    assert safe["category"] == "memory_lesson"
    assert safe["title"] == ""
    assert safe["source_ref"] == ""
    assert safe["content"] == ""
    assert safe["content_redacted"] is True
    assert set(safe["redacted_fields"]) == {"title", "source_ref", "content"}


def test_safe_recall_prompt_item_drops_forbidden_system_fields() -> None:
    polluted = FORBIDDEN_PERSONAL_TERMS[0]

    safe = safe_recall_prompt_item(
        {"item_uid": f"kb_{polluted}", "title": "usable", "category": "memory_lesson", "content": "usable"},
        forbidden_text_checker=lambda text: [term for term in FORBIDDEN_PERSONAL_TERMS if term in text],
    )

    assert safe == {}


def test_document_generation_records_use_and_helpful_only_after_draft_success(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source_uid = _add_active_source(client)
    candidate_id = _create_candidate(client, "以后生成 AlphaDoc 功能规范时保留用户可观察行为")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaDoc 功能规范", "source_uids": [source_uid]})

    assert response.status_code == 200, response.json()
    draft = response.json()["message"]["metadata"]["draft"]
    assert draft["status"] == "active"
    assert draft["generation"]["quality_gate_passed"] is True
    stats = _item_stats(db_path, item_uid)
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 1
    assert stats["unhelpful_count"] == 0


def test_document_generation_only_marks_explicitly_used_memory_helpful(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source_uid = _add_active_source(client)
    first_id = _create_candidate(client, "以后生成 AlphaPrecise 功能规范时保留用户可观察行为")
    second_id = _create_candidate(client, "以后生成 AlphaPrecise 功能规范时单独列出验收标准和失败条件")
    _approve_candidate(client, first_id)
    _approve_candidate(client, second_id)
    first_uid = f"kb_memory_{first_id}"
    second_uid = f"kb_memory_{second_id}"
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_artifact_generate":
            return LLMResult(
                call_id=705,
                provider="fake-test",
                model="precise-helpful-fixture",
                status="ok",
                parsed={
                    "title": "AlphaPrecise 功能规范",
                    "content_format": "markdown",
                    "content": _valid_requirement_analysis_content("AlphaPrecise"),
                    "memory_item_uids_used": [second_uid],
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaPrecise 功能规范", "source_uids": [source_uid]})

    assert response.status_code == 200, response.json()
    first_stats = _item_stats(db_path, first_uid)
    second_stats = _item_stats(db_path, second_uid)
    assert first_stats["use_count"] == 1
    assert first_stats["helpful_count"] == 0
    assert second_stats["use_count"] == 1
    assert second_stats["helpful_count"] == 1


def test_document_generation_without_explicit_used_memory_does_not_mark_helpful(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source_uid = _add_active_source(client)
    candidate_id = _create_candidate(client, "以后生成 AlphaNoHelpful 功能规范时保留用户可观察行为")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_artifact_generate":
            return LLMResult(
                call_id=706,
                provider="fake-test",
                model="no-helpful-fixture",
                status="ok",
                parsed={
                    "title": "AlphaNoHelpful 功能规范",
                    "content_format": "markdown",
                    "content": "# 功能规范\n\n## 功能目标\n- AlphaNoHelpful\n\n## 用户可观察行为\n- 保留用户可观察行为\n\n## 输入与输出\n- 输入来自资料\n\n## 状态与异常场景\n- 覆盖边界\n\n## 非目标\n- 不写实现细节\n\n## 验收标准\n- 可验证\n\n## 证据引用\n- source: current_prompt\n",
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaNoHelpful 功能规范", "source_uids": [source_uid]})

    assert response.status_code == 200, response.json()
    stats = _item_stats(db_path, item_uid)
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 0


def test_document_generation_filters_forbidden_legacy_memory_from_prompt_and_accounting(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    source_uid = _add_active_source(client)
    polluted = FORBIDDEN_PERSONAL_TERMS[0]
    now = "2026-01-01T00:00:00Z"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
            VALUES (?, 'kb_memory_doc_polluted', 'AlphaDocPolluted legacy', 'memory_lesson', 'manual', 'legacy', ?, '[]', 0.99, 'active', ?, ?)
            """,
            (project_id, f"AlphaDocPolluted {polluted} legacy content", now, now),
        )
    captured: dict[str, Any] = {}
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_artifact_generate":
            captured["user_prompt"] = user_prompt
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaDocPolluted 功能规范", "source_uids": [source_uid]})

    assert response.status_code == 200, response.json()
    assert polluted not in captured["user_prompt"]
    memories = json.loads(captured["user_prompt"])["memories"]
    assert memories and memories[0]["content_excerpt"] == ""
    assert memories[0]["content_redacted"] is True
    assert "content" in memories[0]["redacted_fields"]
    stats = _item_stats(db_path, "kb_memory_doc_polluted")
    assert stats["use_count"] == 0
    assert stats["helpful_count"] == 0


def test_document_generation_records_locally_redacted_memory_when_content_is_safe(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    source_uid = _add_active_source(client)
    polluted = FORBIDDEN_PERSONAL_TERMS[0]
    now = "2026-01-01T00:00:00Z"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
            VALUES (?, 'kb_memory_doc_title_polluted', ?, 'memory_lesson', 'manual', ?, 'AlphaDocTitleSafe keep observable behavior', '[]', 0.99, 'active', ?, ?)
            """,
            (project_id, f"AlphaDocTitleSafe {polluted}", f"legacy:{polluted}", now, now),
        )
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_artifact_generate":
            return LLMResult(
                call_id=708,
                provider="fake-test",
                model="local-redaction-fixture",
                status="ok",
                parsed={
                    "title": "AlphaDocTitleSafe 功能规范",
                    "content_format": "markdown",
                    "content": _valid_requirement_analysis_content("AlphaDocTitleSafe"),
                    "memory_item_uids_used": ["kb_memory_doc_title_polluted"],
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaDocTitleSafe 功能规范", "source_uids": [source_uid]})

    assert response.status_code == 200, response.json()
    stats = _item_stats(db_path, "kb_memory_doc_title_polluted")
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 1


def test_failed_quality_gate_records_use_but_not_helpful(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source_uid = _add_active_source(client)
    candidate_id = _create_candidate(client, "以后生成 AlphaQuality 功能规范时保留用户可观察行为")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_artifact_generate":
            return LLMResult(
                call_id=702,
                provider="fake-test",
                model="quality-failure-fixture",
                status="ok",
                parsed={"title": "缺章功能规范", "content_format": "markdown", "content": "# 功能规范\n\n## 功能目标\n- AlphaQuality\n\n## 证据引用\n- source: current_prompt\n"},
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaQuality 功能规范", "source_uids": [source_uid]})

    assert response.status_code == 200, response.json()
    assert response.json()["message"]["metadata"]["draft"]["status"] == "quality_failed"
    stats = _item_stats(db_path, item_uid)
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 0


def test_unrelated_query_does_not_fallback_to_approved_memory(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    candidate_id = _create_candidate(client, "以后处理 AlphaOnly 时先给结论")
    _approve_candidate(client, candidate_id)

    recalled = recall_knowledge(db_path, project_id=project_id, query="CompletelyUnrelatedBeta", category="memory_lesson")

    assert recalled == []


def test_memory_does_not_crowd_out_ordinary_knowledge_recall(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    now = "2026-01-01T00:00:00Z"
    with connect(db_path) as conn:
        for index in range(30):
            conn.execute(
                """
                INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
                VALUES (?, ?, ?, 'memory_lesson', 'manual', 'test', ?, '[]', 0.99, 'active', ?, ?)
                """,
                (project_id, f"kb_memory_crowd_{index}", f"AlphaCrowd memory {index}", "AlphaCrowd memory rule", now, now),
            )
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
            VALUES (?, 'kb_manual_crowd_ordinary', 'AlphaCrowd ordinary knowledge', 'reference_note', 'manual', 'test', 'AlphaCrowd ordinary knowledge body', '[]', 0.8, 'active', ?, ?)
            """,
            (project_id, now, now),
        )

    recalled = recall_knowledge(db_path, project_id=project_id, query="AlphaCrowd", limit=1, exclude_category="memory_lesson")

    assert [item["item_uid"] for item in recalled] == ["kb_manual_crowd_ordinary"]


def test_core_search_knowledge_exclude_category_survives_memory_saturation(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    now = "2026-01-01T00:00:00Z"
    with connect(db_path) as conn:
        for index in range(100):
            conn.execute(
                """
                INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
                VALUES (?, ?, ?, 'memory_lesson', 'manual', 'test', ?, '[]', 0.99, 'active', ?, ?)
                """,
                (project_id, f"kb_memory_core_{index}", f"AlphaCore memory {index}", "AlphaCore memory rule", now, now),
            )
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
            VALUES (?, 'kb_manual_core_ordinary', 'AlphaCore ordinary knowledge', 'reference_note', 'manual', 'test', 'AlphaCore ordinary knowledge body', '[]', 0.8, 'active', ?, ?)
            """,
            (project_id, now, now),
        )

    recalled = search_knowledge(db_path, "AlphaCore", project_id=project_id, limit=1, exclude_category="memory_lesson")

    assert [item["item_uid"] for item in recalled] == ["kb_manual_core_ordinary"]


def test_unhelpful_feedback_lowers_memory_ranking(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    first_id = _create_candidate(client, "以后处理 AlphaRank 时使用旧规则")
    second_id = _create_candidate(client, "以后处理 AlphaRank 时先说明判断依据，再给出最终建议")
    _approve_candidate(client, first_id)
    _approve_candidate(client, second_id)
    first_uid = f"kb_memory_{first_id}"
    second_uid = f"kb_memory_{second_id}"

    before = [item["item_uid"] for item in recall_knowledge(db_path, project_id=project_id, query="AlphaRank", category="memory_lesson", limit=2)]
    assert first_uid in before and second_uid in before
    for _ in range(12):
        record_recall_feedback(db_path, item_uid=first_uid, event="unhelpful")

    after = [item["item_uid"] for item in recall_knowledge(db_path, project_id=project_id, query="AlphaRank", category="memory_lesson", limit=2)]

    assert after.index(first_uid) > after.index(second_uid)


def test_recall_rank_components_are_stable_and_last_used_at_only_breaks_ties() -> None:
    base = recall_rank_components(
        {
            "score": 0.6,
            "confidence": 0.5,
            "use_count": 3,
            "helpful_count": 2,
            "unhelpful_count": 1,
        }
    )
    later = recall_rank_components(
        {
            "score": 0.6,
            "confidence": 0.5,
            "use_count": 3,
            "helpful_count": 2,
            "unhelpful_count": 1,
        }
    )

    assert base == later
    assert set(base) == {"score", "confidence", "helpful_rate", "use_boost", "unhelpful_penalty"}


def test_recall_rank_uses_last_used_at_only_as_tie_breaker() -> None:
    older = {
        "score": 0.5,
        "confidence": 0.5,
        "use_count": 1,
        "helpful_count": 1,
        "unhelpful_count": 0,
        "last_used_at": "2026-01-01T00:00:00Z",
        "item_uid": "kb_old",
    }
    newer = dict(older, item_uid="kb_new", last_used_at="2026-01-02T00:00:00Z")

    assert _recall_rank(older)[:3] == _recall_rank(newer)[:3]
    assert _recall_rank(older) < _recall_rank(newer)


def test_correction_feedback_marks_previous_injected_memory_unhelpful(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    candidate_id = _create_candidate(client, "以后处理 AlphaCorrection 时先给旧规则")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_chat_answer":
            return LLMResult(
                call_id=704,
                provider="fake-test",
                model="memory-chat-fixture",
                status="ok",
                parsed={"answer": "按 AlphaCorrection 旧规则回答。", "used_sources": [], "limitations": []},
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    first = client.post("/api/personal/chat/turn", json={"content": "普通问题：AlphaCorrection 怎么处理？"})
    assert first.status_code == 200, first.json()
    session_uid = first.json()["session"]["session_uid"]
    assert first.json()["message"]["metadata"]["injected_memory_item_uids"] == [item_uid]

    correction = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "你理解错了，AlphaCorrection 应该先确认我的纠正点"})
    assert correction.status_code == 200, correction.json()
    stats = _item_stats(db_path, item_uid)
    assert stats["use_count"] >= 2
    assert stats["unhelpful_count"] == 1


def test_correction_feedback_only_marks_declared_used_memory_unhelpful(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    first_id = _create_candidate(client, "以后处理 AlphaCorrectionScope 时先给旧规则")
    second_id = _create_candidate(client, "以后处理 AlphaCorrectionScope 时先确认用户纠正点，再给出建议")
    _approve_candidate(client, first_id)
    _approve_candidate(client, second_id)
    first_uid = f"kb_memory_{first_id}"
    second_uid = f"kb_memory_{second_id}"
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_chat_answer":
            return LLMResult(
                call_id=707,
                provider="fake-test",
                model="memory-chat-fixture",
                status="ok",
                parsed={
                    "answer": "按 AlphaCorrectionScope 新规则回答。",
                    "used_sources": [],
                    "memory_item_uids_used": [second_uid],
                    "limitations": [],
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    first = client.post("/api/personal/chat/turn", json={"content": "普通问题：AlphaCorrectionScope 怎么处理？"})
    assert first.status_code == 200, first.json()
    session_uid = first.json()["session"]["session_uid"]
    metadata = first.json()["message"]["metadata"]
    assert first_uid in metadata["injected_memory_item_uids"]
    assert second_uid in metadata["injected_memory_item_uids"]
    assert metadata["memory_item_uids_used"] == [second_uid]

    correction = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "你理解错了，AlphaCorrectionScope 应该先确认纠正点"})

    assert correction.status_code == 200, correction.json()
    first_stats = _item_stats(db_path, first_uid)
    second_stats = _item_stats(db_path, second_uid)
    assert first_stats["unhelpful_count"] == 0
    assert second_stats["unhelpful_count"] == 1


def test_correction_feedback_marks_recalled_solved_failure_memory_unhelpful(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/personal/learning/feedback",
        json={"feedback": "当 speed.c VehicleSpeed_Read 的 tests 出现 test_expectation 失败时，优先恢复 SPEED_INVALID_DEFAULT 哨兵值。"},
    )
    assert response.status_code == 200, response.json()
    candidate_id = int(response.json()["id"])
    approved = client.post(
        f"/api/personal/learning/candidates/{candidate_id}/approve",
        json={"reviewer": "tester", "comment": "solved failure memory"},
    )
    assert approved.status_code == 200, approved.json()
    item_uid = f"kb_memory_{candidate_id}"
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_chat_answer":
            return LLMResult(
                call_id=708,
                provider="fake-test",
                model="memory-chat-fixture",
                status="ok",
                parsed={
                    "answer": "遇到 speed.c 的 test_expectation 失败时，我会恢复 SPEED_INVALID_DEFAULT。",
                    "used_sources": [],
                    "memory_item_uids_used": [item_uid],
                    "limitations": [],
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    first = client.post("/api/personal/chat/turn", json={"content": "speed.c 的 test_expectation 失败一般怎么修？"})
    assert first.status_code == 200, first.json()
    session_uid = first.json()["session"]["session_uid"]
    metadata = first.json()["message"]["metadata"]
    assert item_uid in metadata["injected_memory_item_uids"]
    assert metadata["memory_item_uids_used"] == [item_uid]

    correction = client.post(
        "/api/personal/chat/turn",
        json={"session_uid": session_uid, "content": "你理解错了，这类失败不一定要恢复 SPEED_INVALID_DEFAULT，先确认 requirement 和当前符号证据。"},
    )
    assert correction.status_code == 200, correction.json()
    stats = _item_stats(db_path, item_uid)
    assert stats["unhelpful_count"] == 1


def test_forbidden_legacy_memory_is_not_injected_or_counted(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    polluted = FORBIDDEN_PERSONAL_TERMS[0]
    now = "2026-01-01T00:00:00Z"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
            VALUES (?, 'kb_memory_polluted', 'AlphaPolluted legacy', 'memory_lesson', 'manual', 'legacy', ?, '[]', 0.99, 'active', ?, ?)
            """,
            (project_id, f"AlphaPolluted {polluted} legacy content", now, now),
        )
    captured: dict[str, Any] = {}
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_chat_answer":
            captured["user_prompt"] = user_prompt
            return LLMResult(
                call_id=703,
                provider="fake-test",
                model="memory-chat-fixture",
                status="ok",
                parsed={"answer": "没有注入污染记忆。", "used_sources": [], "limitations": []},
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "普通问题：AlphaPolluted 怎么处理？"})

    assert response.status_code == 200, response.json()
    assert polluted not in captured["user_prompt"]
    metadata = response.json()["message"]["metadata"]
    assert metadata["injected_memory_item_uids"] == ["kb_memory_polluted"]
    assert metadata["billable_memory_item_uids"] == []
    assert metadata["memory_item_uids_used"] == []
    stats = _item_stats(db_path, "kb_memory_polluted")
    assert stats["use_count"] == 0
    assert stats["helpful_count"] == 0


def test_query_expansion_recalls_memory_from_partial_cjk_terms(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    candidate_id = _create_candidate(client, "以后处理 AlphaHybrid 检索质量时先说明确定性召回依据")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"

    recalled = recall_knowledge(db_path, project_id=project_id, query="检索质量", category="memory_lesson")

    assert [item["item_uid"] for item in recalled] == [item_uid]
    assert recalled[0]["score"] > 0


def test_consolidation_soft_marks_duplicate_conflict_and_low_utility_without_deleting(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    now = "2026-01-01T00:00:00Z"
    rows = [
        ("kb_memory_consolidate_keep", "AlphaConsolidate keep", "AlphaConsolidate 应该先给结论再解释理由", 3, 1, 0),
        ("kb_memory_consolidate_dup", "AlphaConsolidate dup", "AlphaConsolidate 应该先给结论再解释理由", 0, 0, 0),
        ("kb_memory_consolidate_conflict", "AlphaConsolidate conflict", "AlphaConsolidate 不要先给结论再解释理由", 2, 0, 0),
        ("kb_memory_consolidate_low", "AlphaLowUtility low", "AlphaLowUtility 应该保留旧做法", 1, 0, 3),
    ]
    with connect(db_path) as conn:
        for item_uid, title, content, use_count, helpful_count, unhelpful_count in rows:
            conn.execute(
                """
                INSERT INTO knowledge_items(
                    project_id, item_uid, title, category, source_type, source_ref, content,
                    tags_json, confidence, use_count, helpful_count, unhelpful_count, status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'memory_lesson', 'manual', 'test', ?, '[]', 0.9, ?, ?, ?, 'active', ?, ?)
                """,
                (project_id, item_uid, title, content, use_count, helpful_count, unhelpful_count, now, now),
            )

    result = consolidate_memory_lessons(db_path, project_id=project_id)

    assert result["duplicate_pairs"] == [{"winner": "kb_memory_consolidate_keep", "loser": "kb_memory_consolidate_dup", "similarity": result["duplicate_pairs"][0]["similarity"]}]
    assert result["conflict_pairs"]
    assert "kb_memory_consolidate_low" in result["low_utility_item_uids"]
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE category='memory_lesson'").fetchone()[0]
        dup = dict(conn.execute("SELECT status, tags_json FROM knowledge_items WHERE item_uid='kb_memory_consolidate_dup'").fetchone())
        conflict = dict(conn.execute("SELECT status, tags_json FROM knowledge_items WHERE item_uid='kb_memory_consolidate_conflict'").fetchone())
        low = dict(conn.execute("SELECT status, tags_json FROM knowledge_items WHERE item_uid='kb_memory_consolidate_low'").fetchone())
    assert count == 4
    assert dup["status"] == "deprecated"
    assert "suspected_duplicate" in json.loads(dup["tags_json"])
    assert conflict["status"] == "active"
    assert "suspected_conflict" in json.loads(conflict["tags_json"])
    assert low["status"] == "deprecated"
    assert "low_utility" in json.loads(low["tags_json"])


def test_consolidation_ignores_deprecated_memory_lessons(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    now = "2026-01-01T00:00:00Z"
    deprecated_updated_at = "2026-01-01T00:00:01Z"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items(
                project_id, item_uid, title, category, source_type, source_ref, content,
                tags_json, confidence, use_count, helpful_count, unhelpful_count, status, created_at, updated_at
            )
            VALUES (?, 'kb_memory_active_guard', 'AlphaDeprecatedGuard active', 'memory_lesson', 'manual', 'test',
                    'AlphaDeprecatedGuard 应该先给结论', '[]', 0.9, 1, 0, 0, 'active', ?, ?)
            """,
            (project_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO knowledge_items(
                project_id, item_uid, title, category, source_type, source_ref, content,
                tags_json, confidence, use_count, helpful_count, unhelpful_count, status, created_at, updated_at
            )
            VALUES (?, 'kb_memory_deprecated_guard_dup', 'AlphaDeprecatedGuard deprecated duplicate', 'memory_lesson', 'manual', 'test',
                    'AlphaDeprecatedGuard 应该先给结论', '["legacy"]', 0.9, 0, 0, 5, 'deprecated', ?, ?)
            """,
            (project_id, now, deprecated_updated_at),
        )
        conn.execute(
            """
            INSERT INTO knowledge_items(
                project_id, item_uid, title, category, source_type, source_ref, content,
                tags_json, confidence, use_count, helpful_count, unhelpful_count, status, created_at, updated_at
            )
            VALUES (?, 'kb_memory_deprecated_guard_conflict', 'AlphaDeprecatedGuard deprecated conflict', 'memory_lesson', 'manual', 'test',
                    'AlphaDeprecatedGuard 不要先给结论', '["archived"]', 0.9, 0, 0, 5, 'deprecated', ?, ?)
            """,
            (project_id, now, deprecated_updated_at),
        )

    result = consolidate_memory_lessons(db_path, project_id=project_id)

    all_result_uids = set(result["low_utility_item_uids"] + result["updated_item_uids"] + result["duplicate_loser_uids"] + result["conflict_item_uids"])
    for pair in result["duplicate_pairs"]:
        all_result_uids.add(pair["winner"])
        all_result_uids.add(pair["loser"])
    for pair in result["conflict_pairs"]:
        all_result_uids.add(pair["left"])
        all_result_uids.add(pair["right"])
    assert "kb_memory_deprecated_guard_dup" not in all_result_uids
    assert "kb_memory_deprecated_guard_conflict" not in all_result_uids
    with connect(db_path) as conn:
        dup = dict(conn.execute("SELECT status, tags_json, updated_at FROM knowledge_items WHERE item_uid='kb_memory_deprecated_guard_dup'").fetchone())
        conflict = dict(conn.execute("SELECT status, tags_json, updated_at FROM knowledge_items WHERE item_uid='kb_memory_deprecated_guard_conflict'").fetchone())
    assert dup == {"status": "deprecated", "tags_json": '["legacy"]', "updated_at": deprecated_updated_at}
    assert conflict == {"status": "deprecated", "tags_json": '["archived"]', "updated_at": deprecated_updated_at}


def test_memory_consolidate_route_soft_marks_low_utility_active_memory(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    now = "2026-01-01T00:00:00Z"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items(
                project_id, item_uid, title, category, source_type, source_ref, content,
                tags_json, confidence, use_count, helpful_count, unhelpful_count, status, created_at, updated_at
            )
            VALUES (?, 'kb_memory_route_low_utility', 'AlphaRouteLow low utility', 'memory_lesson', 'manual', 'test',
                    'AlphaRouteLow 应该保留旧做法', '[]', 0.9, 1, 0, 3, 'active', ?, ?)
            """,
            (project_id, now, now),
        )

    response = client.post("/api/personal/memory/consolidate")

    assert response.status_code == 200, response.json()
    payload = response.json()
    assert set(payload) == {
        "project_id",
        "duplicate_pairs",
        "conflict_pairs",
        "low_utility_item_uids",
        "updated_item_uids",
        "duplicate_loser_uids",
        "conflict_item_uids",
    }
    assert payload["project_id"] == project_id
    assert payload["low_utility_item_uids"] == ["kb_memory_route_low_utility"]
    assert payload["updated_item_uids"] == ["kb_memory_route_low_utility"]
    with connect(db_path) as conn:
        row = dict(conn.execute("SELECT status, tags_json FROM knowledge_items WHERE item_uid='kb_memory_route_low_utility'").fetchone())
    assert row["status"] == "deprecated"
    assert "low_utility" in json.loads(row["tags_json"])


def test_learning_reflection_gate_skips_chitchat_but_not_material_signals() -> None:
    assert learning_reflection_gate({"prompt": "谢谢"})["skip"] is True
    assert learning_reflection_gate({"prompt": "好的"})["skip"] is True
    assert learning_reflection_gate({"prompt": "你理解错了，Alpha 应该先确认我的纠正点"})["skip"] is False
    assert learning_reflection_gate({"prompt": "批准这条经验"})["skip"] is False
    assert learning_reflection_gate({"prompt": "生成 Alpha 功能规范"})["skip"] is True
    assert learning_reflection_gate({"prompt": "给这个函数写 patch"})["skip"] is True
    assert should_run_learning_reflector({"prompt": ""}, {"intent": "answer_only"}) is False
    assert should_run_learning_reflector({"prompt": "以后都这样回答"}, {"intent": "answer_only"}) is True
    assert should_run_learning_reflector({"prompt": "谢谢"}, {"intent": "answer_only"}) is False
    assert should_run_learning_reflector({"prompt": "谢谢"}, {"intent": "generate_document"}) is False


def test_chitchat_turn_skips_learning_reflector_and_does_not_create_candidate(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    calls: list[str] = []
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        calls.append(purpose)
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "谢谢"})

    assert response.status_code == 200, response.json()
    assert "personal_learning_reflect" not in calls
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 0


def test_correction_and_document_generation_follow_new_learning_signal_gate(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source_uid = _add_active_source(client)
    calls: list[dict[str, Any]] = []
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_learning_reflect":
            calls.append(json.loads(user_prompt))
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    correction = client.post("/api/personal/chat/turn", json={"content": "你理解错了，AlphaGate 应该先确认我的纠正点"})
    assert correction.status_code == 200, correction.json()
    document = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaGate 功能规范", "source_uids": [source_uid]})
    assert document.status_code == 200, document.json()

    assert any(item["implicit_learning_events"] and item["implicit_learning_events"][0]["type"] == "explicit_correction" for item in calls)
    assert not any("生成 AlphaGate 功能规范" in item["user_message"] for item in calls)
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 1


def test_ordinary_business_followup_does_not_trigger_learning_reflector_or_create_candidate(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source_uid = _add_active_source(client)
    calls: list[str] = []
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        calls.append(purpose)
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "继续分析这份资料里的异常处理边界", "source_uids": [source_uid]})

    assert response.status_code == 200, response.json()
    assert "personal_learning_reflect" not in calls
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 0


def test_explicit_correction_still_creates_candidate_and_uses_short_hint(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)

    response = client.post("/api/personal/chat/turn", json={"content": "你理解错了，AlphaLearnable 应该先给结论，再列关键理由"})

    assert response.status_code == 200, response.json()
    message = response.json()["message"]
    assert "已记录为待批准经验，可在学习面板中查看。" in message["content"]
    assert "并会在当前会话先按它执行" not in message["content"]
    with connect(db_path) as conn:
        rows = conn.execute("SELECT lesson, expected_behavior FROM memory_candidates ORDER BY id DESC LIMIT 1").fetchall()
    assert len(rows) == 1
    assert "修正意图理解" in rows[0]["lesson"]
    assert rows[0]["expected_behavior"]


def test_duplicate_or_similar_corrections_are_rejected_without_interrupting_turn(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)

    first = client.post("/api/personal/chat/turn", json={"content": "你理解错了，以后处理 AlphaDup 时先确认纠正点，再继续回答"})
    session_uid = first.json()["session"]["session_uid"]
    second = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "你理解错了，以后处理 AlphaDup 时先确认纠正点，然后再继续回答"})

    assert first.status_code == 200, first.json()
    assert second.status_code == 200, second.json()
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0]
    assert count == 1


def test_low_confidence_reflection_does_not_create_candidate(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_learning_reflect":
            return LLMResult(
                call_id=991,
                provider="fake-test",
                model="low-confidence-learning-fixture",
                status="ok",
                parsed={
                    "has_learning_signal": True,
                    "confidence": 0.6,
                    "feedback_type": "style_preference",
                    "scope": "project",
                    "candidate_lesson": "以后回答 AlphaLowConfidence 时先给结论，再列关键理由",
                    "anti_behavior": "不要直接堆砌细节",
                    "approval_intent": "none",
                    "reason": "低置信度测试",
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "你理解错了，AlphaLowConfidence 应该先给结论，再列关键理由"})

    assert response.status_code == 200, response.json()
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 0
