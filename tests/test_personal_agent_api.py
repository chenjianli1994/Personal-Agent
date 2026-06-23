from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core import llm_admin as llm_admin_module
from personal_agent.bootstrap import bootstrap_personal_agent, load_personal_env
from personal_agent.content_guard import FORBIDDEN_PERSONAL_TERMS, RETIRED_PROJECT_INPUT_KEYS
from personal_agent.context_builder import PersonalContextBuilder
from personal_agent.core.database import connect, init_db
from personal_agent.app import create_personal_app


LLMResult = getattr(llm_gateway_module, "LLMResult")
LLMBridge = getattr(llm_gateway_module, "PersonalLLMGateway")


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path, Path]:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TestClient(create_personal_app(db_path, workspace)), db_path, workspace


def _add_source(client: TestClient) -> dict:
    response = client.post(
        "/api/personal/sources/text",
        json={
            "title": "热管理需求",
            "content": "水泵需要根据充电状态、环境温度、水温差值和水温阈值进行控制。\n电子风扇需要根据环境温度和水温区间控制启停与转速。",
            "make_active": True,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_personal_app_bootstrap_uses_personal_tables(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)

    context = client.get("/api/personal/context").json()
    assert context["workspace_uid"] == "local"
    assert "requirement" + "_id" not in context

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM requirements").fetchone()[0] == 0
        old_session_table = "agent" + "_tasks"
        assert conn.execute(f"SELECT COUNT(*) FROM {old_session_table}").fetchone()[0] == 0
        for table in [
            "personal_sessions",
            "personal_session_messages",
            "personal_session_events",
            "personal_input_sources",
            "personal_drafts",
            "personal_draft_revisions",
            "personal_skills",
            "personal_skill_versions",
            "personal_skill_eval_runs",
        ]:
            assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        requirement_columns = {row["name"] for row in conn.execute("PRAGMA table_info(requirements)").fetchall()}
        trace_columns = {row["name"] for row in conn.execute("PRAGMA table_info(trace_links)").fetchall()}
        assert {"task_uid", "source_draft_uid", "anchor_fingerprint", "metadata_json", "deprecated_at"} <= requirement_columns
        assert {"task_uid", "metadata_json", "status", "confidence", "managed_by"} <= trace_columns


def test_dev_task_patch_propose_route_is_registered(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    app = create_personal_app(tmp_path / "agent.db", tmp_path / "workspace")
    paths = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}
    assert ("/api/personal/dev-tasks/{task_uid}/code-patch/propose", ("POST",)) in paths
    assert ("/api/personal/dev-tasks/{task_uid}/code-patch/repair", ("POST",)) in paths


def test_bootstrap_fresh_workspace_does_not_require_knowledge_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    context = bootstrap_personal_agent(db_path, workspace)

    assert context.workspace == workspace.resolve()
    assert not (workspace / "knowledge").exists()
    with connect(db_path) as conn:
        inputs = {
            str(row["input_key"]): str(row["value"])
            for row in conn.execute("SELECT input_key, value FROM project_inputs WHERE project_id=?", (context.project_id,)).fetchall()
        }
        assert inputs == {"personal_test_command": "python -m pytest"}


def test_json_responses_preserve_utf8_text(tmp_path: Path, monkeypatch) -> None:
    client, _, _ = _client(tmp_path, monkeypatch)
    title = "中文标题"
    content = "水泵和风扇需要保留中文内容。"

    response = client.post(
        "/api/personal/sources/text",
        json={"title": title, "content": content, "make_active": True},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    raw_text = response.content.decode("utf-8")
    assert title in raw_text
    assert "\ufffd" not in raw_text
    payload = response.json()
    assert payload["title"] == title

    detail = client.get(f"/api/personal/sources/{payload['source_uid']}")
    assert detail.status_code == 200, detail.text
    assert detail.headers["content-type"].startswith("application/json")
    detail_text = detail.content.decode("utf-8")
    assert title in detail_text
    assert content in detail_text
    assert "\ufffd" not in detail_text
    assert detail.json()["plain_text"] == content


def test_new_session_does_not_inherit_project_active_source(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)

    context = PersonalContextBuilder(db_path, project_id=1).build(session_uid="session_new", prompt="你好")
    proposed = client.post(
        "/api/personal/artifacts/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report"},
    )

    assert source["is_active"] is True
    assert context["active_source_uids"] == []
    assert context["sources"] == []
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["metadata"]["generation"]["evidence_refs"]["active_source_uids"] == []


def test_session_followup_reuses_only_its_own_attached_sources(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    first_source = _add_source(client)
    second_source = client.post(
        "/api/personal/sources/text",
        json={"title": "另一个会话材料", "content": "这份材料不应该进入当前会话。", "make_active": True},
    ).json()

    turn = client.post(
        "/api/personal/chat/turn",
        json={"content": "先看这份材料", "source_uids": [first_source["source_uid"]]},
    )
    assert turn.status_code == 200, turn.text
    session_uid = turn.json()["session"]["session_uid"]

    context = PersonalContextBuilder(db_path, project_id=1).build(session_uid=session_uid, prompt="继续")

    assert context["active_source_uids"] == [first_source["source_uid"]]
    assert second_source["source_uid"] not in context["active_source_uids"]


def test_bootstrap_cleans_polluted_db_records(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = bootstrap_personal_agent(db_path, workspace)
    retired_profile_key = RETIRED_PROJECT_INPUT_KEYS[-1]
    retired_process = FORBIDDEN_PERSONAL_TERMS[0]
    retired_step = FORBIDDEN_PERSONAL_TERMS[2]
    retired_check = FORBIDDEN_PERSONAL_TERMS[4]
    retired_version = FORBIDDEN_PERSONAL_TERMS[5]
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_search_fts USING fts5(
                title, category, source_type, source_ref, heading, tags, process_codes, content
            )
            """
        )
        conn.execute(
            """
            INSERT INTO project_inputs(project_id, input_key, label, category, value, status, created_at, updated_at)
            VALUES (?, ?, 'legacy', 'quality', ?, 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """,
            (first.project_id, retired_profile_key, f"{retired_step}1 {retired_check}"),
        )
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
            VALUES (?, 'kb_polluted', ?, 'reference', 'manual', ?, ?, '[]', 0.8, 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """,
            (
                first.project_id,
                f"legacy {retired_process} note",
                f"legacy/{retired_step}1.md",
                f"{retired_check} {retired_version} item",
            ),
        )
        conn.execute(
            """
            INSERT INTO knowledge_documents(project_id, doc_uid, title, category, source_type, source_ref, source_title, source_uri, trust_level, import_batch_id, source_owner, source_trust_level, source_version, applicable_project, applicable_process_json, applicable_domain, approval_status, expires_at, supersedes, material_type, code_refs_json, process_codes_json, tags_json, summary, content_hash, status, created_at, updated_at)
            VALUES (?, 'doc_polluted', ?, 'reference', 'manual', ?, ?, ?, 'internal', '', '', 'internal', '', '', '[]', '', 'approved', '', '', 'reference_document', '[]', '[]', '[]', ?, 'hash', 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """,
            (
                first.project_id,
                f"legacy {retired_version}",
                f"legacy/{retired_version}.md",
                f"legacy {retired_step}",
                f"legacy/{retired_check}",
                f"{retired_process} {retired_version} summary",
            ),
        )
        document_id = int(conn.execute("SELECT id FROM knowledge_documents WHERE doc_uid='doc_polluted'").fetchone()[0])
        conn.execute(
            """
            INSERT INTO knowledge_chunks(document_id, chunk_index, heading, content, token_hint, status, created_at, updated_at)
            VALUES (?, 0, 'legacy', ?, 4, 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """,
            (document_id, f"{retired_step}1 {retired_version} chunk"),
        )
        conn.execute(
            """
            INSERT INTO knowledge_search_entries(project_id, source_kind, source_id, document_id, item_uid, title, category, source_type, source_ref, heading, tags_json, process_codes_json, status, content_hash, content_preview, updated_at)
            VALUES (?, 'item', 1, ?, 'kb_polluted', ?, 'reference', 'manual', 'legacy', '', '[]', '[]', 'active', 'hash', ?, '2026-01-01T00:00:00Z')
            """,
            (first.project_id, document_id, f"legacy {retired_check}", f"{retired_check} preview"),
        )
        entry_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO knowledge_search_fts(rowid, title, category, source_type, source_ref, heading, tags, process_codes, content)
            VALUES (?, ?, 'reference', 'manual', 'legacy', '', '', '', ?)
            """,
            (entry_id, f"legacy {retired_check}", f"{retired_process} {retired_version} content"),
        )

    second = bootstrap_personal_agent(db_path, workspace)
    assert second.project_id == first.project_id
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_inputs WHERE input_key=?", (retired_profile_key,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE item_uid='kb_polluted'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_documents WHERE doc_uid='doc_polluted'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_search_entries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_search_fts").fetchone()[0] == 0


def test_personal_chat_turn_reuses_session(tmp_path: Path, monkeypatch) -> None:
    client, _, _ = _client(tmp_path, monkeypatch)

    first = client.post("/api/personal/chat/turn", json={"content": "你好，先介绍一下能力边界"})
    assert first.status_code == 200
    session_uid = first.json()["session"]["session_uid"]

    second = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "继续"})
    assert second.status_code == 200
    assert second.json()["session"]["session_uid"] == session_uid

    sessions = client.get("/api/personal/sessions").json()
    assert [item["session_uid"] for item in sessions].count(session_uid) == 1
    assert len(second.json()["session"]["messages"]) == 4


def test_personal_analysis_uses_llm_intent_route_and_answer(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)

    answer = client.post("/api/personal/chat/turn", json={"content": "分析一下我现在的需求资料", "source_uids": [source["source_uid"]]})
    assert answer.status_code == 200, answer.json()
    message = answer.json()["message"]
    route = message["metadata"]["intent_route"]
    assert route["intent"] == "analyze_input_source"
    assert route["router_source"] == "llm"
    assert route["llm"]["purpose"] == "personal_intent_route"
    assert message["metadata"]["llm"]["purpose"] == "personal_chat_answer"
    assert "我会基于当前输入材料和本会话上下文继续处理" not in message["content"]
    assert "水泵" in message["content"]

    with connect(db_path) as conn:
        purposes = [
            row["purpose"]
            for row in conn.execute("SELECT purpose FROM llm_call_logs ORDER BY id DESC LIMIT 3").fetchall()
        ]
    assert purposes == ["personal_chat_answer", "personal_learning_reflect", "personal_intent_route"]


def test_personal_chat_turn_activates_attached_sources_before_routing(tmp_path: Path, monkeypatch) -> None:
    client, _, _ = _client(tmp_path, monkeypatch)
    old = client.post(
        "/api/personal/sources/text",
        json={"title": "旧输入材料", "content": "旧材料只描述热管理水泵，不是本轮附件。", "make_active": True},
    )
    first = client.post(
        "/api/personal/sources/text",
        json={"title": "AGENTS", "content": "AGENTS.md 是项目协作规范文件，要求默认中文回复。", "make_active": False},
    )
    second = client.post(
        "/api/personal/sources/text",
        json={"title": "风扇需求", "content": "电子风扇需要根据水温区间调速。", "make_active": False},
    )
    assert old.status_code == 200
    assert first.status_code == 200
    assert second.status_code == 200

    answer = client.post(
        "/api/personal/chat/turn",
        json={
            "content": "分析这个文件的需求点",
            "source_uids": [first.json()["source_uid"], second.json()["source_uid"]],
        },
    )
    assert answer.status_code == 200, answer.json()
    message = answer.json()["message"]
    assert message["metadata"]["intent_route"]["intent"] == "analyze_input_source"
    assert "AGENTS.md" in message["content"]
    assert "旧材料" not in message["content"]
    user_message = next(item for item in answer.json()["session"]["messages"] if item["role"] == "user")
    attachments = user_message["metadata"]["attachments"]
    assert [item["source_uid"] for item in attachments] == [first.json()["source_uid"], second.json()["source_uid"]]
    assert attachments[0]["title"] == "AGENTS"
    assert attachments[0]["source_type"] == "text"
    assert attachments[0]["original_name"] == "AGENTS"

    sources = client.get("/api/personal/sources").json()
    active_uids = {item["source_uid"] for item in sources if item["is_active"]}
    assert active_uids == {first.json()["source_uid"], second.json()["source_uid"]}


def test_personal_chat_turn_rejects_too_many_attached_sources(tmp_path: Path, monkeypatch) -> None:
    client, _, _ = _client(tmp_path, monkeypatch)
    created = [
        client.post(
            "/api/personal/sources/text",
            json={"title": f"资料 {index}", "content": f"需求资料 {index}", "make_active": False},
        ).json()["source_uid"]
        for index in range(6)
    ]

    answer = client.post("/api/personal/chat/turn", json={"content": "分析这些附件", "source_uids": created})
    assert answer.status_code == 400
    assert "at most 5" in answer.json()["detail"]


def test_personal_document_generation_uses_llm_route_and_skill_generation(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)

    answer = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告", "source_uids": [source["source_uid"]]})
    assert answer.status_code == 200
    message = answer.json()["message"]
    route = message["metadata"]["intent_route"]
    assert route["intent"] == "generate_document"
    assert route["target_document_type"] == "requirement_analysis_report"
    assert message["metadata"]["draft"]["document_type"] == "requirement_analysis_report"
    assert message["metadata"]["draft"]["draft_uid"]
    assert "draft_uid" not in message["content"]

    with connect(db_path) as conn:
        purposes = [
            row["purpose"]
            for row in conn.execute("SELECT purpose FROM llm_call_logs ORDER BY id DESC LIMIT 5").fetchall()
        ]
        draft_count = conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0]
    assert "personal_intent_route" in purposes
    assert "personal_artifact_generate" in purposes
    assert draft_count == 1


def test_chat_and_unified_turn_document_generation_are_equivalent(tmp_path: Path, monkeypatch) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)

    chat = client.post("/api/personal/chat/turn", json={"content": "生成详细设计文档", "source_uids": [source["source_uid"]]})
    unified = client.post("/api/agent/unified-turn", json={"content": "生成详细设计文档", "source_uids": [source["source_uid"]]})

    assert chat.status_code == 200, chat.json()
    assert unified.status_code == 200, unified.json()
    chat_message = chat.json()["message"]
    unified_payload = unified.json()
    assert chat_message["metadata"]["intent_route"]["intent"] == "generate_document"
    assert chat_message["metadata"]["context"] == "dev_task_start"
    assert chat_message["metadata"]["dev_task"]["task_uid"].startswith("task_")
    assert chat_message["metadata"]["draft"]["document_type"] == "requirement_analysis_report"
    assert unified_payload["mode"] == "personal_phase4_artifact"
    assert unified_payload["metadata"]["personal_intent"]["intent"] == "generate_document"
    assert unified_payload["message"]["metadata"]["context"] == "dev_task_start"
    assert unified_payload["message"]["metadata"]["dev_task"]["task_uid"].startswith("task_")
    assert unified_payload["message"]["metadata"]["draft"]["document_type"] == "requirement_analysis_report"
    assert unified_payload["metadata"]["personal_intent"]["created_draft_uids"] == [
        unified_payload["message"]["metadata"]["draft"]["draft_uid"]
    ]


def test_personal_chat_runtime_split_keeps_response_shape(tmp_path: Path, monkeypatch) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)

    answer = client.post("/api/personal/chat/turn", json={"content": "普通问题：你能做什么？"})

    assert answer.status_code == 200, answer.json()
    payload = answer.json()
    assert set(payload) == {"session", "message"}
    assert payload["session"]["session_uid"] == payload["message"]["session_uid"]
    assert payload["message"]["metadata"]["intent_route"]["intent"] == "answer_only"


def test_recall_feedback_failure_logs_warning_without_interrupting_response(tmp_path: Path, monkeypatch, caplog) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    created = client.post(
        "/api/personal/learning/feedback",
        json={"feedback": "以后回答 AlphaWarning 时先说明这是测试记忆。"},
    )
    assert created.status_code == 200
    approved = client.post(
        f"/api/personal/learning/candidates/{created.json()['id']}/approve",
        json={"reviewer": "tester", "comment": "warning log test"},
    )
    assert approved.status_code == 200

    def fail_feedback(*args: Any, **kwargs: Any) -> None:
        raise ValueError("synthetic recall feedback failure")

    monkeypatch.setattr("personal_agent.runtime.record_recall_feedback", fail_feedback)
    caplog.set_level("WARNING", logger="personal_agent.runtime")

    answer = client.post("/api/personal/chat/turn", json={"content": "普通问题：AlphaWarning 现在怎么答？"})

    assert answer.status_code == 200, answer.json()
    assert "synthetic recall feedback failure" in caplog.text


def test_chat_document_quality_failure_returns_failed_draft_and_skill_candidate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_artifact_generate":
            return LLMResult(
                call_id=901,
                provider="fake-test",
                model="quality-failure-fixture",
                status="ok",
                parsed={
                    "title": "缺章节功能规范",
                    "content_format": "markdown",
                    "content": "# 功能规范\n\n## 功能目标\n- 有目标。\n\n## 证据引用\n- source: current_prompt\n",
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)

    answer = client.post("/api/personal/chat/turn", json={"content": "生成功能规范", "source_uids": [source["source_uid"]]})
    assert answer.status_code == 200, answer.json()
    message = answer.json()["message"]
    draft = message["metadata"]["draft"]
    assert draft["status"] == "quality_failed"
    assert draft["generation"]["quality"]["passed"] is False
    assert "required_sections" in draft["generation"]["quality"]["blocking_failures"][0]
    assert "质量门未通过" in message["content"]
    assert "draft_uid" not in message["content"]
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1
        candidate = conn.execute("SELECT * FROM personal_skill_update_candidates ORDER BY id DESC LIMIT 1").fetchone()
    assert candidate is not None
    assert candidate["target_skill"] == "requirement-analysis-report"
    assert candidate["source"] == "quality_check_failure"


def test_quality_failed_draft_remains_active_for_followup_revision(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_intent_route" and "你把证据引用这些内容去掉" in user_prompt:
            return LLMResult(
                call_id=901,
                provider="fake-test",
                model="router-fixture",
                status="ok",
                parsed={
                    "intent": "revise_draft",
                    "confidence": 0.94,
                    "requires_active_draft": True,
                    "revises_draft": True,
                    "creates_draft": False,
                    "answer_mode": "general_chat",
                    "reason": "LLM routed the follow-up as an explicit draft revision.",
                },
                raw_text="{}",
            )
        if purpose == "personal_artifact_generate":
            return LLMResult(
                call_id=902,
                provider="fake-test",
                model="quality-failure-fixture",
                status="ok",
                parsed={
                    "title": "证据引用过密的需求分析",
                    "content_format": "markdown",
                    "content": "# 需求分析报告\n\n## 输入摘要\n- source: current_prompt\n\n## 需求理解\n- 只写了证据引用，缺少章节。\n\n## 证据引用\n- source: current_prompt\n",
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)

    generated = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告", "source_uids": [source["source_uid"]]})
    assert generated.status_code == 200, generated.json()
    session_uid = generated.json()["session"]["session_uid"]
    draft_uid = generated.json()["message"]["metadata"]["draft"]["draft_uid"]
    assert generated.json()["message"]["metadata"]["draft"]["status"] == "quality_failed"

    session = client.get(f"/api/personal/sessions/{session_uid}").json()
    assert session["active_draft_uid"] == draft_uid
    from personal_agent.context_builder import PersonalContextBuilder

    context = PersonalContextBuilder(db_path, project_id=1).build(session_uid=session_uid, prompt="把证据引用这些内容去掉")
    assert context["active_draft"]["draft_uid"] == draft_uid
    assert context["active_draft"]["status"] == "quality_failed"

    followup = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "你把证据引用这些内容去掉"})
    assert followup.status_code == 200, followup.json()
    route = followup.json()["message"]["metadata"]["intent_route"]
    assert route["intent"] == "revise_draft"
    assert route["policy"]["fallback"] is False


def test_policy_guard_blocks_document_generation_without_active_source(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)

    answer = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告"})
    assert answer.status_code == 200
    message = answer.json()["message"]
    route = message["metadata"]["intent_route"]
    assert route["intent"] == "answer_only"
    assert route["policy"]["fallback"] is True
    assert route["route_degraded"] is True
    assert "没有激活输入材料" in route["policy"]["reason"]

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 0


def test_low_confidence_route_does_not_execute_generation(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    _add_source(client)

    answer = client.post("/api/personal/chat/turn", json={"content": "低置信 生成需求分析报告"})
    assert answer.status_code == 200
    route = answer.json()["message"]["metadata"]["intent_route"]
    assert route["intent"] == "answer_only"
    assert route["policy"]["fallback"] is True
    assert route["route_degraded"] is True
    assert "置信度过低" in route["policy"]["reason"]

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 0


def test_active_draft_can_be_revised_via_llm_route(tmp_path: Path, monkeypatch) -> None:
    client, _, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)

    generated = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告", "source_uids": [source["source_uid"]]})
    assert generated.status_code == 200
    session_uid = generated.json()["session"]["session_uid"]
    draft_uid = generated.json()["message"]["metadata"]["draft"]["draft_uid"]

    revised = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "把刚才草稿补充异常场景"})
    assert revised.status_code == 200
    message = revised.json()["message"]
    route = message["metadata"]["intent_route"]
    assert route["intent"] == "revise_draft"
    assert message["metadata"]["draft"]["draft_uid"] == draft_uid
    assert message["metadata"]["draft"]["current_revision"] == 2
    assert "draft_uid" not in message["content"]


def test_answer_only_route_does_not_revise_active_draft_by_local_keywords(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = client.post("/api/personal/chat/turn", json={"content": "创建会话"}).json()["session"]["session_uid"]
    draft = client.post(
        "/api/personal/drafts",
        json={
            "document_type": "requirement_analysis_report",
            "session_uid": session_uid,
            "title": "待修订草稿",
            "content": "# 待修订草稿\n\n原始内容",
        },
    ).json()
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_intent_route":
            return LLMResult(
                call_id=940,
                provider="deepseek-test",
                model="router",
                status="ok",
                parsed={
                    "intent": "answer_only",
                    "confidence": 0.95,
                    "answer_mode": "general_chat",
                    "reason": "LLM chose not to revise the draft.",
                },
                raw_text="{}",
            )
        if purpose == "personal_chat_answer":
            return LLMResult(
                call_id=941,
                provider="deepseek-test",
                model="answer",
                status="ok",
                parsed={"answer": "这是普通回答，没有修订草稿。", "memory_item_uids_used": []},
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "把刚才草稿这些删掉"})

    assert response.status_code == 200, response.text
    message = response.json()["message"]
    assert message["metadata"]["intent_route"]["intent"] == "answer_only"
    assert "draft" not in message["metadata"]
    with connect(db_path) as conn:
        row = conn.execute("SELECT current_revision FROM personal_drafts WHERE draft_uid=?", (draft["draft_uid"],)).fetchone()
    assert row["current_revision"] == 1


def test_llm_route_target_revision_revises_from_requested_base_version(tmp_path: Path, monkeypatch) -> None:
    client, _, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)
    session = client.post("/api/personal/chat/turn", json={"content": "创建会话"}).json()["session"]
    session_uid = session["session_uid"]
    created = client.post(
        "/api/personal/drafts",
        json={
            "document_type": "requirement_analysis_report",
            "session_uid": session_uid,
            "source_uid": source["source_uid"],
            "title": "需求分析报告（水泵占空比计算）",
            "content": "# 需求分析报告\n\n## 输入摘要\nBASE V1\n\n## 需求理解\nBASE V1\n\n## 证据引用\n- source: current_prompt\n\n## 边界与假设\n- 待确认。\n\n## 风险点\n- 待确认。\n\n## 待确认问题\n- 待确认。",
        },
    )
    assert created.status_code == 200, created.text
    draft_uid = created.json()["draft_uid"]
    for marker in ["BASE V2", "BASE V3", "LATEST V4"]:
        revised = client.post(
            f"/api/personal/drafts/{draft_uid}/revise-manual",
            json={
                "content": f"# 需求分析报告\n\n## 输入摘要\n{marker}\n\n## 需求理解\n{marker}\n\n## 证据引用\n- source: current_prompt\n\n## 边界与假设\n- 待确认。\n\n## 风险点\n- 待确认。\n\n## 待确认问题\n- 待确认。",
                "make_active": True,
            },
        )
        assert revised.status_code == 200, revised.text

    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_intent_route":
            return LLMResult(
                call_id=930,
                provider="deepseek-test",
                model="router",
                status="ok",
                parsed={
                    "intent": "revise_draft",
                    "confidence": 0.96,
                    "target_document_type": "",
                    "requires_active_source": False,
                    "requires_active_draft": True,
                    "requires_codebase": False,
                    "creates_draft": False,
                    "revises_draft": True,
                    "writes_project_files": False,
                    "requires_user_confirmation": False,
                    "answer_mode": "general_chat",
                    "target_draft_revision": 3,
                    "reason": "LLM understood that the user wants to revise from v3.",
                },
                raw_text="{}",
            )
        if purpose == "personal_artifact_revise":
            payload = json.loads(user_prompt)
            current = payload["current_draft"]
            assert current["current_revision"] == 3
            assert "BASE V3" in current["content"]
            assert "LATEST V4" not in current["content"]
            return LLMResult(
                call_id=931,
                provider="deepseek-test",
                model="revision",
                status="ok",
                parsed={
                    "title": "需求分析报告（水泵占空比计算）",
                    "content_format": "markdown",
                    "content": "# 需求分析报告\n\n## 输入摘要\n基于 V3 重新修订。\n\n## 需求理解\n沿用 BASE V3 的基底。\n\n## 证据引用\n- source: current_prompt\n\n## 边界与假设\n- 待确认。\n\n## 风险点\n- 待确认。\n\n## 待确认问题\n- 待确认。",
                    "memory_item_uids_used": [],
                },
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post(
        "/api/personal/chat/turn",
        json={
            "session_uid": session_uid,
            "content": "有点错误了，你要在V3版本上进行更改，而不是在最新版本上，重新生成一版吧",
        },
    )

    assert response.status_code == 200, response.text
    message = response.json()["message"]
    assert message["metadata"]["intent_route"]["target_draft_revision"] == 3
    assert message["metadata"]["revision_target"]["base_revision_index"] == 3
    assert message["metadata"]["draft"]["draft_uid"] == draft_uid
    assert message["metadata"]["draft"]["current_revision"] == 5
    assert "基于《需求分析报告（水泵占空比计算）》v3" in message["content"]


def test_unified_turn_answer_only_route_does_not_create_document_by_local_keywords(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    _add_source(client)
    original_complete_json = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_intent_route":
            return LLMResult(
                call_id=950,
                provider="deepseek-test",
                model="router",
                status="ok",
                parsed={
                    "intent": "answer_only",
                    "confidence": 0.94,
                    "answer_mode": "general_chat",
                    "reason": "LLM chose answer only.",
                },
                raw_text="{}",
            )
        if purpose == "personal_chat_answer":
            return LLMResult(
                call_id=951,
                provider="deepseek-test",
                model="answer",
                status="ok",
                parsed={"answer": "只是回答，不生成文档。", "memory_item_uids_used": []},
                raw_text="{}",
            )
        return original_complete_json(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)

    response = client.post("/api/agent/unified-turn", json={"content": "生成详细设计文档"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mode"] == "personal_chat"
    assert payload["metadata"]["personal_intent"]["intent"] == "answer_only"
    assert payload["metadata"]["personal_intent"]["created_draft_uids"] == []
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 0


def test_active_draft_is_not_reused_across_sessions(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source = _add_source(client)

    generated = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告", "source_uids": [source["source_uid"]]})
    assert generated.status_code == 200

    revised = client.post("/api/personal/chat/turn", json={"content": "把刚才草稿补充异常场景"})
    assert revised.status_code == 200
    route = revised.json()["message"]["metadata"]["intent_route"]
    assert route["intent"] == "answer_only"
    assert route["policy"]["fallback"] is True

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_personal_session_management(tmp_path: Path, monkeypatch) -> None:
    client, _, _ = _client(tmp_path, monkeypatch)

    created = client.post("/api/personal/chat/turn", json={"content": "创建一条可管理会话"}).json()
    session_uid = created["session"]["session_uid"]

    renamed = client.put(f"/api/personal/sessions/{session_uid}/title", json={"title": "我的会话名称"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "我的会话名称"

    deleted = client.delete(f"/api/personal/sessions/{session_uid}")
    assert deleted.status_code == 200
    assert client.get(f"/api/personal/sessions/{session_uid}").status_code == 404


def test_personal_llm_config_writes_personal_env_keys(tmp_path: Path, monkeypatch) -> None:
    client, _, workspace = _client(tmp_path, monkeypatch)
    (workspace / ".env").write_text("OPENROUTER_API_KEY=old-openrouter\nXAI_API_KEY=old-xai\n", encoding="utf-8")
    monkeypatch.setattr(
        llm_admin_module,
        "_fetch_provider_model_ids",
        lambda provider, api_key: {
            "deepseek": ["deepseek-v4-flash"],
            "dashscope": ["qwen3-coder-plus"],
            "mimo": ["mimo-v2.5", "mimo-v2.5-pro"],
        }.get(provider, []),
    )

    saved = client.put(
        "/api/personal/llm-config",
        json={
            "provider": "mimo",
            "model": "mimo-v2.5-pro",
            "api_key": "test-mimo-key",
            "clear_other_provider_keys": True,
        },
    )
    assert saved.status_code == 200
    providers = [item["value"] for item in saved.json()["available_providers"]]
    assert providers == ["deepseek", "dashscope", "mimo"]
    mimo_provider = next(item for item in saved.json()["available_providers"] if item["value"] == "mimo")
    assert mimo_provider["default_model"] == "mimo-v2.5-pro"
    assert mimo_provider["model_options"] == ["mimo-v2.5", "mimo-v2.5-pro"]
    env_text = (workspace / ".env").read_text(encoding="utf-8")
    assert "PERSONAL_AGENT_LLM_PROVIDER=mimo" in env_text
    assert "PERSONAL_AGENT_LLM_MODEL=mimo-v2.5-pro" in env_text
    assert "MIMO_API_KEY=test-mimo-key" in env_text
    assert "OPENROUTER_API_KEY" not in env_text
    assert "XAI_API_KEY" not in env_text


def test_personal_env_ignores_retired_llm_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PERSONAL_AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("PERSONAL_AGENT_LLM_PROVIDER=openrouter\nMIMO_API_KEY=test-mimo-key\n", encoding="utf-8")

    load_personal_env(env_path)

    assert "PERSONAL_AGENT_LLM_PROVIDER" not in os.environ
    assert os.environ["MIMO_API_KEY"] == "test-mimo-key"


def test_personal_llm_config_discovers_model_options_for_all_supported_providers(tmp_path: Path, monkeypatch) -> None:
    for key in [
        "PERSONAL_AGENT_LLM_PROVIDER",
        "PERSONAL_AGENT_LLM_MODEL",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "MIMO_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)
    db_path = tmp_path / "agent.db"
    init_db(db_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=deepseek-key",
                "DASHSCOPE_API_KEY=dashscope-key",
                "MIMO_API_KEY=mimo-key",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        llm_admin_module,
        "_fetch_provider_model_ids",
        lambda provider, api_key: [f"{provider}-model-a", f"{provider}-model-b"],
    )

    config = llm_admin_module.read_personal_llm_admin_config(db_path, env_path)

    options = {item["value"]: item["model_options"] for item in config["available_providers"]}
    assert options["deepseek"] == ["deepseek-v4-flash", "deepseek-model-a", "deepseek-model-b"]
    assert options["dashscope"] == ["qwen3-coder-plus", "dashscope-model-a", "dashscope-model-b"]
    assert options["mimo"] == ["mimo-v2.5-pro", "mimo-model-a", "mimo-model-b"]
