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
from personal_agent.knowledge_recall import recall_knowledge, recall_rank_components, record_recall_feedback, safe_recall_prompt_item, _recall_rank


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
    stats = _item_stats(db_path, item_uid)
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 0
    assert stats["unhelpful_count"] == 0
    assert stats["last_used_at"]
    context = client.get(f"/api/personal/sessions/{response.json()['session']['session_uid']}").json()
    assert any(item["metadata"].get("injected_memory_item_uids") == [item_uid] for item in context["messages"] if item["role"] == "assistant")
    assert recall_knowledge(db_path, project_id=project_id, query="AlphaPrompt", category="memory_lesson")[0]["use_count"] == 1


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
    _add_active_source(client)
    candidate_id = _create_candidate(client, "以后生成 AlphaDoc 功能规范时保留用户可观察行为")
    _approve_candidate(client, candidate_id)
    item_uid = f"kb_memory_{candidate_id}"

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaDoc 功能规范"})

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
    _add_active_source(client)
    first_id = _create_candidate(client, "以后生成 AlphaPrecise 功能规范时保留用户可观察行为")
    second_id = _create_candidate(client, "以后生成 AlphaPrecise 功能规范时先列边界")
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
                    "content": "# 功能规范\n\n## 功能目标\n- AlphaPrecise\n\n## 用户可观察行为\n- 保留用户可观察行为\n\n## 输入与输出\n- 输入来自资料\n\n## 状态与异常场景\n- 覆盖边界\n\n## 非目标\n- 不写实现细节\n\n## 验收标准\n- 可验证\n\n## 证据引用\n- source: current_prompt\n",
                    "memory_item_uids_used": [second_uid],
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaPrecise 功能规范"})

    assert response.status_code == 200, response.json()
    first_stats = _item_stats(db_path, first_uid)
    second_stats = _item_stats(db_path, second_uid)
    assert first_stats["use_count"] == 1
    assert first_stats["helpful_count"] == 0
    assert second_stats["use_count"] == 1
    assert second_stats["helpful_count"] == 1


def test_document_generation_without_explicit_used_memory_does_not_mark_helpful(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    _add_active_source(client)
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

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaNoHelpful 功能规范"})

    assert response.status_code == 200, response.json()
    stats = _item_stats(db_path, item_uid)
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 0


def test_document_generation_filters_forbidden_legacy_memory_from_prompt_and_accounting(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    project_id = _project_id(client)
    _add_active_source(client)
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

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaDocPolluted 功能规范"})

    assert response.status_code == 200, response.json()
    assert polluted not in captured["user_prompt"]
    memories = json.loads(captured["user_prompt"])["memories"]
    assert memories and memories[0]["content_excerpt"] == ""
    assert memories[0]["content_redacted"] is True
    assert "content" in memories[0]["redacted_fields"]
    stats = _item_stats(db_path, "kb_memory_doc_polluted")
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 0


def test_failed_quality_gate_records_use_but_not_helpful(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    _add_active_source(client)
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

    response = client.post("/api/personal/chat/turn", json={"content": "生成 AlphaQuality 功能规范"})

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
    second_id = _create_candidate(client, "以后处理 AlphaRank 时使用新规则")
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
    assert metadata["memory_item_uids_used"] == []
    stats = _item_stats(db_path, "kb_memory_polluted")
    assert stats["use_count"] == 1
    assert stats["helpful_count"] == 0
