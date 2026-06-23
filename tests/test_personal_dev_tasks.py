from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_agent.app import create_personal_app
from personal_agent.artifact_drafts import DOCUMENT_LINEAGE_ORDER
from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.database import connect
from personal_agent.dev_tasks import DevTaskOrchestrator, dev_task_stage_order, validation_summary


LLMResult = getattr(llm_gateway_module, "LLMResult")
LLMBridge = getattr(llm_gateway_module, "PersonalLLMGateway")


def _client(tmp_path: Path, monkeypatch: Any) -> tuple[TestClient, Path, Path]:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TestClient(create_personal_app(db_path, workspace)), db_path, workspace


def _session(client: TestClient) -> str:
    response = client.post("/api/personal/chat/turn", json={"content": "创建开发任务会话"})
    assert response.status_code == 200, response.text
    return response.json()["session"]["session_uid"]


def _source(client: TestClient) -> str:
    response = client.post(
        "/api/personal/sources/text",
        json={
            "title": "热管理需求",
            "content": "水泵需要根据充电状态、环境温度、水温差值和水温阈值进行控制。电子风扇需要根据环境温度和水温区间控制启停与转速。",
            "make_active": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["source_uid"]


def _start_task(client: TestClient, session_uid: str, source_uids: list[str] | None = None) -> dict[str, Any]:
    response = client.post(
        "/api/personal/dev-tasks/start",
        json={"session_uid": session_uid, "prompt": "落地热管理控制需求", "source_uids": source_uids or []},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _continue_task(client: TestClient, task_uid: str) -> dict[str, Any]:
    response = client.post("/api/personal/dev-tasks/continue", json={"task_uid": task_uid})
    assert response.status_code == 200, response.text
    return response.json()


def test_start_without_active_source_is_policy_blocked_and_creates_no_draft(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)

    task = _start_task(client, session_uid)

    assert task["status"] == "blocked"
    assert task["last_action"]["status"] == "blocked"
    assert task["next_action"]["stage"] == "requirement_analysis_report"
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 0


def test_start_with_source_generates_first_stage_draft_and_writes_task_uid(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)

    task = _start_task(client, session_uid, [source_uid])

    assert task["status"] == "active"
    assert task["stages"][0]["effective_status"] == "done"
    assert task["next_action"]["stage"] == "requirement_breakdown"
    with connect(db_path) as conn:
        row = conn.execute("SELECT task_uid, session_uid FROM personal_drafts ORDER BY id DESC LIMIT 1").fetchone()
    assert row["task_uid"] == task["task_uid"]
    assert row["session_uid"] == session_uid


def test_continue_advances_exactly_one_pending_stage(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])

    advanced = _continue_task(client, task["task_uid"])

    assert [stage["effective_status"] for stage in advanced["stages"][:3]] == ["done", "done", "pending"]
    assert advanced["last_action"]["stage"] == "requirement_breakdown"
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts WHERE task_uid=?", (task["task_uid"],)).fetchone()[0] == 2


def test_stage_order_is_derived_from_document_lineage_order() -> None:
    assert dev_task_stage_order() == list(DOCUMENT_LINEAGE_ORDER[: DOCUMENT_LINEAGE_ORDER.index("test_case_spec") + 1])


def test_regenerated_upstream_marks_multiple_downstream_stages_needs_revision(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])

    regenerated = client.post(
        "/api/personal/drafts",
        json={
            "document_type": "requirement_analysis_report",
            "session_uid": session_uid,
            "task_uid": task["task_uid"],
            "title": "需求分析 v2",
            "content": "# 需求分析 v2\n\n更新上游。",
        },
    )
    assert regenerated.status_code == 200, regenerated.text
    refreshed = client.get(f"/api/personal/dev-tasks/{task['task_uid']}").json()

    statuses = {stage["document_type"]: stage["effective_status"] for stage in refreshed["stages"]}
    assert statuses["requirement_breakdown"] == "needs_revision"
    assert statuses["functional_spec"] == "needs_revision"
    assert refreshed["next_action"]["action"] == "revise_draft"
    assert refreshed["next_action"]["stage"] == "requirement_breakdown"


def test_continue_blocks_when_needs_revision_exists_and_does_not_auto_revise(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    draft_uid = task["stages"][1]["draft_uid"]
    client.post(
        f"/api/personal/drafts/{task['stages'][0]['draft_uid']}/revise-manual",
        json={"content": "# 需求分析 v2", "make_active": True},
    )

    blocked = _continue_task(client, task["task_uid"])

    assert blocked["last_action"]["status"] == "blocked"
    assert blocked["next_action"]["stage"] == "requirement_breakdown"
    with connect(db_path) as conn:
        assert conn.execute("SELECT current_revision FROM personal_drafts WHERE draft_uid=?", (draft_uid,)).fetchone()[0] == 1

    revised = client.post(
        f"/api/personal/drafts/{draft_uid}/revise-manual",
        json={"content": "# 需求拆解 v2\n\n已根据上游重做同步。", "make_active": True, "status": "active"},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["lineage_stale"] is False

    advanced = _continue_task(client, task["task_uid"])
    assert advanced["stages"][1]["effective_status"] == "done"
    assert advanced["stages"][2]["effective_status"] == "done"


def test_quality_failed_stage_stops_current_stage(tmp_path: Path, monkeypatch: Any) -> None:
    original = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_artifact_generate":
            return LLMResult(
                call_id=700,
                provider="fake-test",
                model="quality-failure",
                status="ok",
                parsed={
                    "title": "缺章节需求分析",
                    "content_format": "markdown",
                    "content": "# 需求分析报告\n\n## 输入摘要\n- source: current_prompt\n",
                },
                raw_text="{}",
            )
        return original(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)

    task = _start_task(client, session_uid, [source_uid])

    assert task["status"] == "blocked"
    assert task["stages"][0]["effective_status"] == "needs_revision"
    assert task["next_action"]["action"] == "revise_draft"


def test_detailed_design_requires_code_index(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])

    blocked = _continue_task(client, task["task_uid"])

    assert blocked["last_action"]["status"] == "blocked"
    assert blocked["last_action"]["stage"] == "detailed_design"
    assert "indexed codebase" in blocked["blocked_reason"]
    assert blocked["stages"][3]["effective_status"] == "pending"


def test_start_archives_old_active_like_task_in_same_session(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    first = _start_task(client, session_uid, [source_uid])
    second = _start_task(client, session_uid, [source_uid])

    assert first["task_uid"] != second["task_uid"]
    with connect(db_path) as conn:
        first_status = conn.execute("SELECT status FROM agent_tasks WHERE task_uid=?", (first["task_uid"],)).fetchone()[0]
    assert first_status == "archived"


def test_archived_task_cannot_be_continued(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    first = _start_task(client, session_uid, [source_uid])
    _start_task(client, session_uid, [source_uid])

    response = client.post("/api/personal/dev-tasks/continue", json={"task_uid": first["task_uid"]})

    assert response.status_code == 400
    assert "not active" in response.json()["detail"]


def test_runtime_continue_prompt_intercepts_and_other_prompts_use_intent_router(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])

    continued = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "继续"})
    assert continued.status_code == 200, continued.text
    assert continued.json()["message"]["metadata"]["context"] == "dev_task_continue"

    ordinary = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "普通问题：现在状态是什么？"})
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["message"]["metadata"]["context"] != "dev_task_continue"
    assert ordinary.json()["message"]["metadata"]["intent_route"]["intent"] == "answer_only"
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts WHERE task_uid=?", (task["task_uid"],)).fetchone()[0] == 2


def test_runtime_document_generation_starts_dev_task_and_returns_metadata(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    source_uid = _source(client)

    response = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告", "source_uids": [source_uid]})

    assert response.status_code == 200, response.text
    message = response.json()["message"]
    task = message["metadata"]["dev_task"]
    draft = message["metadata"]["draft"]
    assert message["metadata"]["context"] == "dev_task_start"
    assert task["task_uid"].startswith("task_")
    assert task["last_action"]["status"] == "generated"
    assert task["stages"][0]["effective_status"] == "done"
    assert draft["task_uid"] == task["task_uid"]
    assert draft["document_type"] == "requirement_analysis_report"
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE session_uid=?", (task["session_uid"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts WHERE task_uid=?", (task["task_uid"],)).fetchone()[0] == 1


def test_runtime_document_generation_without_source_falls_back_without_dev_task(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)

    response = client.post("/api/personal/chat/turn", json={"content": "生成需求分析报告"})

    assert response.status_code == 200, response.text
    message = response.json()["message"]
    route = message["metadata"]["intent_route"]
    assert "dev_task" not in message["metadata"]
    assert message["metadata"]["context"] == "general"
    assert route["intent"] == "answer_only"
    assert route["policy"]["fallback"] is True
    assert "draft" not in message["metadata"]
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 0


def test_runtime_continue_word_without_active_task_does_not_start_dev_task(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    _source(client)

    response = client.post("/api/personal/chat/turn", json={"content": "按计划"})

    assert response.status_code == 200, response.text
    message = response.json()["message"]
    assert "dev_task" not in message["metadata"]
    assert message["metadata"]["intent_route"]["intent"] == "answer_only"
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 0


def test_validation_summary_classification_uses_kind_returncode_timeout_and_config_only() -> None:
    assert validation_summary(kind="build", result={"status": "ok", "output": {"command_kind": "run_build", "passed": False, "returncode": 2}})["category"] == "code_logic"
    assert validation_summary(kind="static-analysis", result={"status": "ok", "output": {"command_kind": "run_static_analysis", "passed": False, "returncode": 1}})["category"] == "code_logic"
    assert validation_summary(kind="tests", result={"status": "ok", "output": {"command_kind": "run_tests", "passed": False, "returncode": 1}})["category"] == "test_expectation"
    assert validation_summary(kind="tests", result={"status": "ok", "output": {"command_kind": "run_tests", "passed": False, "returncode": -1, "limitations": ["command timed out"]}})["category"] == "timeout"
    assert validation_summary(kind="build", result={"status": "rejected", "error": "requested command is not in the personal allowlist", "output": {}})["category"] == "config"


def test_validation_without_task_identity_is_not_attached_to_latest_task(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, workspace = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    orchestrator = DevTaskOrchestrator(db_path, workspace=workspace, project_id=1)

    attached = orchestrator.record_validation(
        task_uid="",
        kind="tests",
        result={"status": "ok", "output": {"command_kind": "run_tests", "passed": False, "returncode": 1}},
    )

    assert attached is None
    refreshed = client.get(f"/api/personal/dev-tasks/{task['task_uid']}").json()
    assert refreshed["validation_summary"] == {}


def test_validation_with_invalid_task_uid_returns_controlled_error(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, _workspace = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/personal/validation/tests",
        json={"task_uid": "task_missing", "command": "", "confirmed": True},
    )

    assert response.status_code == 404
    assert "dev task not found" in response.json()["detail"]


def test_task_progress_invokes_policy_guard(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def fake_policy(route: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        calls.append({"route": route, "context": context})
        guarded = dict(route)
        guarded["intent"] = "answer_only"
        guarded["policy"] = {"allowed": False, "fallback": True, "reason": "forced by test policy"}
        return guarded

    monkeypatch.setattr("personal_agent.dev_tasks.apply_personal_policy", fake_policy)
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)

    task = _start_task(client, session_uid, [source_uid])

    assert calls
    assert task["status"] == "blocked"
    assert task["blocked_reason"] == "forced by test policy"
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts WHERE task_uid=?", (task["task_uid"],)).fetchone()[0] == 0
