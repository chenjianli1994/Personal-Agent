from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent.app import create_personal_app
from personal_agent.core.llm_gateway import PersonalLLMError, PersonalLLMGateway
from personal_agent.core.database import connect, init_db
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


def test_llm_gateway_selects_fast_model_for_fast_purposes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_MODEL_FAST", "openai/gpt-4.1-mini")

    db_path = tmp_path / "agent.db"
    init_db(db_path)
    gateway = PersonalLLMGateway(db_path)

    fast_provider = gateway._select_provider(purpose="personal_intent_route")
    default_provider = gateway._select_provider(purpose="personal_artifact_generate")

    assert fast_provider["model"] == "openai/gpt-4.1-mini"
    assert default_provider["model"] == "openai/gpt-4o-mini"


def test_llm_gateway_fast_model_falls_back_to_default_when_not_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.delenv("PERSONAL_AGENT_LLM_MODEL_FAST", raising=False)

    db_path = tmp_path / "agent.db"
    init_db(db_path)
    gateway = PersonalLLMGateway(db_path)

    provider = gateway._select_provider(purpose="personal_learning_reflect")

    assert provider["model"] == "openai/gpt-4o-mini"


def test_llm_gateway_defaults_to_deepseek(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PERSONAL_AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_MODEL", "deepseek-test-model")

    gateway = PersonalLLMGateway(tmp_path / "agent.db")

    provider = gateway._select_provider(purpose="personal_artifact_generate")

    assert provider["name"] == "deepseek"
    assert provider["model"] == "deepseek-test-model"
    assert provider["base_url"] == "https://api.deepseek.com/chat/completions"


def test_llm_gateway_rejects_unknown_explicit_provider_without_key_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "unknown")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    gateway = PersonalLLMGateway(tmp_path / "agent.db")

    try:
        gateway._select_provider(purpose="personal_intent_route")
    except PersonalLLMError as exc:
        assert "Unsupported or unconfigured LLM provider" in str(exc)
    else:
        raise AssertionError("unknown explicit provider should not fall back to other configured keys")


def test_llm_gateway_rejects_fake_provider_without_test_switch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    monkeypatch.delenv("PERSONAL_AGENT_ENABLE_FAKE_LLM", raising=False)

    gateway = PersonalLLMGateway(tmp_path / "agent.db")

    try:
        gateway._select_provider(purpose="personal_intent_route")
    except PersonalLLMError as exc:
        assert "Fake LLM provider is disabled" in str(exc)
    else:
        raise AssertionError("fake provider should be disabled outside the test switch")


def test_llm_gateway_fake_provider_ignores_tier_model_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_ENABLE_FAKE_LLM", "1")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_MODEL_FAST", "openai/gpt-4.1-mini")

    gateway = PersonalLLMGateway(tmp_path / "agent.db")

    provider = gateway._select_provider(purpose="personal_intent_route")

    assert provider["name"] == "fake"
    assert provider["model"] == "personal-fake-semantic-fixture"


def test_llm_gateway_logs_fast_tier_model_on_fast_purpose(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_MODEL_FAST", "openai/gpt-4.1-mini")

    db_path = tmp_path / "agent.db"
    init_db(db_path)
    gateway = PersonalLLMGateway(db_path)

    def fake_chat_completion(provider: dict[str, str], system_prompt: str, user_prompt: str) -> str:
        assert provider["model"] == "openai/gpt-4.1-mini"
        return '{"intent":"answer_only","confidence":1,"answer_mode":"general_chat","reason":"ok"}'

    monkeypatch.setattr(gateway, "_chat_completion", fake_chat_completion)

    result = gateway.complete_json(
        purpose="personal_intent_route",
        system_prompt="system",
        user_prompt="user",
        task_uid="session_test",
    )

    assert result.model == "openai/gpt-4.1-mini"
    with connect(db_path) as conn:
        row = conn.execute("SELECT purpose, model FROM llm_call_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["purpose"] == "personal_intent_route"
    assert row["model"] == "openai/gpt-4.1-mini"
