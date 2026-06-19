from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.bootstrap import bootstrap_personal_agent
from personal_agent.content_guard import FORBIDDEN_PERSONAL_TERMS, RETIRED_PROJECT_INPUT_KEYS
from personal_agent.core.database import connect
from personal_agent.app import create_personal_app
from personal_agent.document_intent import document_type_from_text, looks_like_document_generation


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
    _add_source(client)

    answer = client.post("/api/personal/chat/turn", json={"content": "分析一下我现在的需求资料"})
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
    _add_source(client)

    answer = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告"})
    assert answer.status_code == 200
    message = answer.json()["message"]
    route = message["metadata"]["intent_route"]
    assert route["intent"] == "generate_document"
    assert route["target_document_type"] == "requirement_analysis_report"
    assert message["metadata"]["draft"]["document_type"] == "requirement_analysis_report"

    with connect(db_path) as conn:
        purposes = [
            row["purpose"]
            for row in conn.execute("SELECT purpose FROM llm_call_logs ORDER BY id DESC LIMIT 5").fetchall()
        ]
        draft_count = conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0]
    assert "personal_intent_route" in purposes
    assert "personal_artifact_generate" in purposes
    assert draft_count == 1


def test_document_intent_helper_matches_chat_and_unified_turn_document_types() -> None:
    assert looks_like_document_generation("生成详细设计文档") is True
    assert document_type_from_text("生成详细设计文档") == "detailed_design"
    assert document_type_from_text("输出单元测试代码") == "unit_test_code_or_diff"
    assert looks_like_document_generation("普通问题：详细说明一下") is False


def test_chat_and_unified_turn_document_generation_are_equivalent(tmp_path: Path, monkeypatch) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    _add_source(client)

    chat = client.post("/api/personal/chat/turn", json={"content": "生成详细设计文档"})
    unified = client.post("/api/agent/unified-turn", json={"content": "生成详细设计文档"})

    assert chat.status_code == 200, chat.json()
    assert unified.status_code == 200, unified.json()
    chat_message = chat.json()["message"]
    unified_payload = unified.json()
    assert chat_message["metadata"]["intent_route"]["intent"] == "generate_document"
    assert chat_message["metadata"]["draft"]["document_type"] == "detailed_design"
    assert unified_payload["mode"] == "personal_phase4_artifact"
    assert unified_payload["metadata"]["personal_intent"]["intent"] == "generate_document"
    assert unified_payload["message"]["metadata"]["draft"]["document_type"] == "detailed_design"
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
    _add_source(client)

    answer = client.post("/api/personal/chat/turn", json={"content": "生成功能规范"})
    assert answer.status_code == 200, answer.json()
    message = answer.json()["message"]
    draft = message["metadata"]["draft"]
    assert draft["status"] == "quality_failed"
    assert draft["generation"]["quality"]["passed"] is False
    assert "required_sections" in draft["generation"]["quality"]["blocking_failures"][0]
    assert "质量门未通过" in message["content"]
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1
        candidate = conn.execute("SELECT * FROM personal_skill_update_candidates ORDER BY id DESC LIMIT 1").fetchone()
    assert candidate is not None
    assert candidate["target_skill"] == "functional-spec"
    assert candidate["source"] == "quality_check_failure"


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
    _add_source(client)

    generated = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告"})
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


def test_active_draft_is_not_reused_across_sessions(tmp_path: Path, monkeypatch) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    _add_source(client)

    generated = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告"})
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

    saved = client.put(
        "/api/personal/llm-config",
        json={
            "provider": "openrouter",
            "model": "openai/gpt-4o-mini",
            "api_key": "test-openrouter-key",
            "clear_other_provider_keys": True,
        },
    )
    assert saved.status_code == 200
    env_text = (workspace / ".env").read_text(encoding="utf-8")
    assert "PERSONAL_AGENT_LLM_PROVIDER=openrouter" in env_text
    assert "PERSONAL_AGENT_LLM_MODEL=openai/gpt-4o-mini" in env_text
