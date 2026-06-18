from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent.app import create_personal_app
from personal_agent.context_builder import PersonalContextBuilder
from personal_agent.intent_router import PersonalIntentRouter
from personal_agent.policy_guard import apply_personal_policy


def test_llm_intent_router_classifies_document_generation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    context_payload = client.get("/api/personal/context").json()
    client.post(
        "/api/personal/sources/text",
        json={"title": "输入", "content": "需求：生成可验证行为说明。", "make_active": True},
    )

    context = PersonalContextBuilder(db_path, context_payload["project_id"]).build(
        session_uid="session_test",
        prompt="生成需求分析报告",
    )
    route = PersonalIntentRouter(db_path, context_payload["project_id"]).route(context)
    guarded = apply_personal_policy(route, context)

    assert guarded["intent"] == "generate_document"
    assert guarded["target_document_type"] == "requirement_analysis_report"
    assert guarded["creates_draft"] is True
    assert guarded["router_source"] == "llm"
    assert guarded["llm"]["purpose"] == "personal_intent_route"
    assert guarded["policy"]["fallback"] is False


def test_policy_guard_blocks_missing_active_draft_revision(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    context_payload = client.get("/api/personal/context").json()

    context = PersonalContextBuilder(db_path, context_payload["project_id"]).build(
        session_uid="session_test",
        prompt="把刚才草稿补充异常场景",
    )
    route = PersonalIntentRouter(db_path, context_payload["project_id"]).route(context)
    guarded = apply_personal_policy(route, context)

    assert route["intent"] == "revise_draft"
    assert guarded["intent"] == "answer_only"
    assert guarded["policy"]["fallback"] is True
    assert "没有激活草稿" in guarded["policy"]["reason"]


def test_policy_guard_blocks_direct_file_writes_and_marks_tool_confirmation() -> None:
    blocked = apply_personal_policy(
        {
            "intent": "answer_only",
            "confidence": 0.99,
            "writes_project_files": True,
            "creates_draft": False,
        },
        {"active_source_uids": ["src_1"], "active_draft": {}},
    )
    assert blocked["intent"] == "answer_only"
    assert blocked["policy"]["fallback"] is True
    assert "禁止" in blocked["policy"]["reason"]

    tool = apply_personal_policy(
        {
            "intent": "propose_code_patch",
            "confidence": 0.99,
            "writes_project_files": False,
        },
        {"active_source_uids": ["src_1"], "active_draft": {}},
    )
    assert tool["intent"] == "propose_code_patch"
    assert tool["requires_user_confirmation"] is True
    assert tool["policy"]["fallback"] is False
