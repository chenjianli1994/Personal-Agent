from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent.app import create_personal_app
from personal_agent.conversation_evidence import build_conversation_evidence_snapshot
from personal_agent.core.database import connect


def _create_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    monkeypatch.setenv("PERSONAL_AGENT_ENABLE_FAKE_LLM", "1")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TestClient(create_personal_app(db_path, workspace)), db_path


def _insert_assistant_message(db_path: Path, *, session_uid: str, content: str, metadata: dict | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personal_session_messages(message_uid, session_uid, role, content, metadata_json, created_at)
            VALUES ('msg_test_' || hex(randomblob(8)), ?, 'assistant', ?, ?, datetime('now'))
            """,
            (session_uid, content, "{}" if metadata is None else json.dumps(metadata, ensure_ascii=False)),
        )


def _latest_assistant_message_metadata(db_path: Path, session_uid: str) -> dict:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT metadata_json
            FROM personal_session_messages
            WHERE session_uid=? AND role='assistant'
            ORDER BY id DESC LIMIT 1
            """,
            (session_uid,),
        ).fetchone()
    return json.loads(row["metadata_json"] or "{}") if row else {}


def test_runtime_general_assistant_assertive_statement_becomes_active_conversation_evidence(tmp_path: Path, monkeypatch) -> None:
    client, db_path = _create_client(tmp_path, monkeypatch)

    response = client.post("/api/personal/chat/turn", json={"content": "接口超时默认 30 秒。"})
    assert response.status_code == 200, response.json()
    session_uid = response.json()["session"]["session_uid"]
    metadata = _latest_assistant_message_metadata(db_path, session_uid)
    assert metadata.get("context") == "general"

    snapshot = build_conversation_evidence_snapshot(
        db_path,
        project_id=1,
        session_uid=session_uid,
        document_type="functional_spec",
        active_source_uids=[],
        sources=[],
    )

    assert any(item["statement"] == "接口超时默认 30 秒" for item in snapshot["active_conversation_decisions"])


def test_user_same_topic_update_supersedes_previous_statement(tmp_path: Path, monkeypatch) -> None:
    client, db_path = _create_client(tmp_path, monkeypatch)

    response = client.post("/api/personal/chat/turn", json={"content": "接口超时默认 30 秒。"})
    session_uid = response.json()["session"]["session_uid"]
    _insert_assistant_message(db_path, session_uid=session_uid, content="接口超时默认 30 秒。", metadata={"context": "general"})
    client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "超时改成 45 秒。"})

    snapshot = build_conversation_evidence_snapshot(
        db_path,
        project_id=1,
        session_uid=session_uid,
        document_type="functional_spec",
        active_source_uids=[],
        sources=[],
    )

    statements = [item["statement"] for item in snapshot["active_conversation_decisions"]]
    assert "超时改成 45 秒" in statements
    assert "接口超时默认 30 秒" not in statements


def test_tentative_and_mixed_assistant_reply_only_keeps_assertive_sentence(tmp_path: Path, monkeypatch) -> None:
    client, db_path = _create_client(tmp_path, monkeypatch)

    response = client.post("/api/personal/chat/turn", json={"content": "创建会话"})
    session_uid = response.json()["session"]["session_uid"]
    _insert_assistant_message(
        db_path,
        session_uid=session_uid,
        content="返回结构使用 JSON。建议后续再确认字段命名。",
        metadata={"context": "general"},
    )

    snapshot = build_conversation_evidence_snapshot(
        db_path,
        project_id=1,
        session_uid=session_uid,
        document_type="functional_spec",
        active_source_uids=[],
        sources=[],
    )

    assert any(item["statement"] == "返回结构使用 JSON" for item in snapshot["active_conversation_decisions"])
    assert any(item["statement"] == "建议后续再确认字段命名" for item in snapshot["weak_conversation_references"])


def test_other_topic_interruption_does_not_invalidate_previous_evidence(tmp_path: Path, monkeypatch) -> None:
    client, db_path = _create_client(tmp_path, monkeypatch)

    response = client.post("/api/personal/chat/turn", json={"content": "接口超时默认 30 秒。"})
    session_uid = response.json()["session"]["session_uid"]
    _insert_assistant_message(db_path, session_uid=session_uid, content="接口超时默认 30 秒。", metadata={"context": "general"})
    client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "顺便把日志标题改短一点。"})
    client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "现在继续生成功能规范。"})

    snapshot = build_conversation_evidence_snapshot(
        db_path,
        project_id=1,
        session_uid=session_uid,
        document_type="functional_spec",
        active_source_uids=[],
        sources=[],
    )

    statements = [item["statement"] for item in snapshot["active_conversation_decisions"]]
    assert "接口超时默认 30 秒" in statements
    assert "现在继续生成功能规范" not in statements


def test_user_workflow_instruction_and_question_do_not_enter_active_evidence(tmp_path: Path, monkeypatch) -> None:
    client, db_path = _create_client(tmp_path, monkeypatch)

    response = client.post("/api/personal/chat/turn", json={"content": "创建会话"})
    session_uid = response.json()["session"]["session_uid"]
    client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "继续写功能规范"})
    client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "这个字段要不要保留？"})

    snapshot = build_conversation_evidence_snapshot(
        db_path,
        project_id=1,
        session_uid=session_uid,
        document_type="functional_spec",
        active_source_uids=[],
        sources=[],
    )

    statements = [item["statement"] for item in snapshot["active_conversation_decisions"]]
    assert "继续写功能规范" not in statements
    assert "这个字段要不要保留" not in statements

