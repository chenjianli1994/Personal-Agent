from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_agent.app import create_personal_app
from personal_agent.artifact_drafts import DOCUMENT_LINEAGE_ORDER
from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.database import connect
from personal_agent.core.services_min import approve_memory_candidate
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
    assert task["requirements"]
    assert task["trace_summary"]["active"] >= 1
    assert task["requirements"][0]["requirement_id"].startswith("REQ-")
    assert task["requirements"][0]["metadata"]["managed_by"] == "dev_task_trace_rebuild"


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


def test_dev_task_payload_includes_session_display_identity(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)

    first = _start_task(client, session_uid, [source_uid])
    second = _start_task(client, session_uid, [source_uid])

    assert first["display_code"] == "T1"
    assert first["session_display_index"] == 1
    assert first["display_scope"] == "session"
    assert second["display_code"] == "T2"
    assert second["session_display_index"] == 2
    listed = client.get("/api/personal/dev-tasks", params={"session_uid": session_uid})
    assert listed.status_code == 200
    by_uid = {item["task_uid"]: item for item in listed.json()}
    assert by_uid[first["task_uid"]]["display_code"] == "T1"
    assert by_uid[first["task_uid"]]["status"] == "archived"
    assert by_uid[second["task_uid"]]["display_code"] == "T2"
    assert by_uid[second["task_uid"]]["display_scope"] == "session"


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


def test_requirement_internal_ids_are_task_scoped_and_external_id_stays_in_metadata(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    session_a = _session(client)
    session_b = _session(client)
    source_a = _source(client)
    source_b = _source(client)

    task_a = _start_task(client, session_a, [source_a])
    task_b = _start_task(client, session_b, [source_b])

    req_a = task_a["requirements"][0]
    req_b = task_b["requirements"][0]
    assert req_a["requirement_id"] != req_b["requirement_id"]
    assert req_a["metadata"].get("external_id", "") == ""
    assert req_b["metadata"].get("external_id", "") == ""


def test_trace_rebuild_is_idempotent_and_only_touches_current_task(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_a = _session(client)
    session_b = _session(client)
    source_a = _source(client)
    source_b = _source(client)
    task_a = _start_task(client, session_a, [source_a])
    task_b = _start_task(client, session_b, [source_b])

    rebuilt_once = client.post(f"/api/personal/dev-tasks/{task_a['task_uid']}/trace/rebuild")
    rebuilt_twice = client.post(f"/api/personal/dev-tasks/{task_a['task_uid']}/trace/rebuild")

    assert rebuilt_once.status_code == 200, rebuilt_once.text
    assert rebuilt_twice.status_code == 200, rebuilt_twice.text
    first = rebuilt_once.json()
    second = rebuilt_twice.json()
    assert first["trace_summary"]["active"] == second["trace_summary"]["active"]
    assert first["trace_summary"]["by_type"] == second["trace_summary"]["by_type"]
    with connect(db_path) as conn:
        task_b_traces = conn.execute(
            "SELECT COUNT(*) FROM trace_links WHERE project_id=1 AND task_uid=? AND status='active'",
            (task_b["task_uid"],),
        ).fetchone()[0]
        stale_a_traces = conn.execute(
            "SELECT COUNT(*) FROM trace_links WHERE project_id=1 AND task_uid=? AND status='stale'",
            (task_a["task_uid"],),
        ).fetchone()[0]
    assert task_b_traces >= 1
    assert stale_a_traces >= 1


def test_requirement_revision_keeps_id_and_new_requirement_gets_new_id(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    first_requirement_id = task["requirements"][0]["requirement_id"]
    source_draft_uid = task["stages"][0]["draft_uid"]

    revised = client.post(
        f"/api/personal/drafts/{source_draft_uid}/revise-manual",
        json={
            "content": "# 更新需求分析\n\n## REQ-001: 充电控制\n- 需要根据电量和温度调节。\n\n## REQ-002: 风扇调速\n- 需要根据区间调整风扇速度。",
            "make_active": True,
            "status": "active",
        },
    )
    assert revised.status_code == 200, revised.text

    trace = client.post(f"/api/personal/dev-tasks/{task['task_uid']}/trace/rebuild")
    assert trace.status_code == 200, trace.text
    requirements = trace.json()["requirements"]
    assert requirements[0]["requirement_id"] == first_requirement_id
    assert requirements[0]["metadata"]["external_id"] == "REQ-001"
    assert requirements[1]["requirement_id"] != first_requirement_id
    assert requirements[1]["metadata"]["external_id"] == "REQ-002"

    with connect(db_path) as conn:
        deprecated_count = conn.execute(
            "SELECT COUNT(*) FROM requirements WHERE project_id=1 AND task_uid=? AND deprecated_at!=''",
            (task["task_uid"],),
        ).fetchone()[0]
    assert deprecated_count == 0


def test_removed_requirement_is_deprecated_not_reused(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    source_draft_uid = task["stages"][0]["draft_uid"]

    seed = client.post(
        f"/api/personal/drafts/{source_draft_uid}/revise-manual",
        json={
            "content": "# 更新需求分析\n\n## REQ-001: 主控制\n- 控制主流程。\n\n## REQ-002: 辅助控制\n- 控制辅助流程。",
            "make_active": True,
            "status": "active",
        },
    )
    assert seed.status_code == 200, seed.text
    client.post(f"/api/personal/dev-tasks/{task['task_uid']}/trace/rebuild")

    removed = client.post(
        f"/api/personal/drafts/{source_draft_uid}/revise-manual",
        json={
            "content": "# 更新需求分析\n\n## REQ-001: 主控制\n- 仅保留主流程。",
            "make_active": True,
            "status": "active",
        },
    )
    assert removed.status_code == 200, removed.text
    trace = client.post(f"/api/personal/dev-tasks/{task['task_uid']}/trace/rebuild")
    assert trace.status_code == 200, trace.text

    with connect(db_path) as conn:
        deprecated = conn.execute(
            """
            SELECT requirement_id, deprecated_at
            FROM requirements
            WHERE project_id=1 AND task_uid=? AND status='deprecated'
            ORDER BY id DESC LIMIT 1
            """,
            (task["task_uid"],),
        ).fetchone()
    assert deprecated is not None
    assert deprecated["deprecated_at"] != ""

    reintroduced = client.post(
        f"/api/personal/drafts/{source_draft_uid}/revise-manual",
        json={
            "content": "# 更新需求分析\n\n## REQ-001: 主控制\n- 保留主流程。\n\n## REQ-002: 新辅助控制\n- 重新引入辅助流程，但语义不同。",
            "make_active": True,
            "status": "active",
        },
    )
    assert reintroduced.status_code == 200, reintroduced.text
    trace = client.post(f"/api/personal/dev-tasks/{task['task_uid']}/trace/rebuild")
    assert trace.status_code == 200, trace.text
    requirements = trace.json()["requirements"]
    active_req2 = next(item for item in requirements if item["metadata"].get("external_id") == "REQ-002" and item["deprecated_at"] == "")
    assert active_req2["requirement_id"] != deprecated["requirement_id"]


def test_trace_query_is_scoped_by_task_and_exposes_requirement_links(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])

    trace = client.get(f"/api/personal/dev-tasks/{task['task_uid']}/trace")
    assert trace.status_code == 200, trace.text
    payload = trace.json()
    assert payload["task_uid"] == task["task_uid"]
    assert payload["requirements"]
    source_links = [item for item in payload["trace_links"] if item["link_type"] == "source_to_requirement"]
    assert source_links
    assert all(item["target_ref"] == f"source:{source_uid}" for item in source_links)
    assert any(item["link_type"] == "requirement_to_draft" for item in payload["trace_links"])


def test_validation_trace_does_not_collapse_multi_requirement_task_to_first_requirement(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _ = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    source_draft_uid = task["stages"][0]["draft_uid"]

    revised = client.post(
        f"/api/personal/drafts/{source_draft_uid}/revise-manual",
        json={
            "content": "# 更新需求分析\n\n## REQ-001: 主控制\n- 处理主流程。\n\n## REQ-002: 风扇调速\n- 根据温度调整风扇速度。",
            "make_active": True,
            "status": "active",
        },
    )
    assert revised.status_code == 200, revised.text

    rebuilt = client.post(f"/api/personal/dev-tasks/{task['task_uid']}/trace/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    requirements = rebuilt.json()["requirements"]
    requirement_ids = [item["requirement_id"] for item in requirements if item["deprecated_at"] == ""]
    assert len(requirement_ids) == 2

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personal_tool_invocations(
                invocation_uid, task_uid, decision_uid, project_id, requirement_id,
                tool_name, input_json, output_json, permission_snapshot_json,
                side_effect_level, status, error, evidence_refs_json, started_at, completed_at
            )
            VALUES (?, ?, '', 1, '', 'run_tests', '{}', '{}', '{}', 'external', 'ok', '', '{}', '2026-06-23T00:00:00Z', '2026-06-23T00:00:01Z')
            """,
            ("ptool_validation_multi_req", task["task_uid"]),
        )

    traced = client.post(f"/api/personal/dev-tasks/{task['task_uid']}/trace/rebuild")
    assert traced.status_code == 200, traced.text
    validation_links = [item for item in traced.json()["trace_links"] if item["link_type"] == "requirement_to_validation" and item["status"] == "active"]
    linked_requirements = {item["requirement_id"] for item in validation_links if item["target_ref"] == "validation:ptool_validation_multi_req"}
    assert linked_requirements == set(requirement_ids)


def test_dev_task_patch_propose_blocks_without_requirement_code_trace(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, workspace = _client(tmp_path, monkeypatch)
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        "int VehicleSpeed_Read(int valid)\n{\n    if (!valid) {\n        return -1;\n    }\n    return 42;\n}\n",
        encoding="utf-8",
    )
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200, saved.text
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read invalid default", "max_files": 20})
    assert indexed.status_code == 200, indexed.text

    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])

    blocked = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "invalid speed should return default zero"},
    )
    assert blocked.status_code == 400
    detail = blocked.json()["detail"]
    assert detail["status"] == "blocked"
    assert detail["candidate_only"] is True
    assert detail["applied"] is False
    assert "trace-linked code file or code symbol evidence" in detail["blocked_reason"]


def test_dev_task_patch_propose_generates_candidate_only_and_writes_trace(tmp_path: Path, monkeypatch: Any) -> None:
    original = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_dev_task_patch_directives":
            return LLMResult(
                call_id=990,
                provider="fake-test",
                model="patch-directives-fixture",
                status="ok",
                parsed={
                    "change_summary": "invalid speed returns zero",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "speed.c",
                            "find": "        return -1;\n",
                            "replace": "        return 0;\n",
                            "description": "invalid speed default zero",
                        }
                    ],
                    "risk_notes": ["confirm diagnostics do not depend on -1 sentinel"],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        return original(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, db_path, _workspace = _client(tmp_path, monkeypatch)
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        "int VehicleSpeed_Read(int valid)\n{\n    if (!valid) {\n        return -1;\n    }\n    return 42;\n}\n",
        encoding="utf-8",
    )
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200, saved.text
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read invalid default", "max_files": 20})
    assert indexed.status_code == 200, indexed.text

    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    requirement_id = task["requirements"][0]["requirement_id"]

    with connect(db_path) as conn:
        now = "2026-06-23T00:00:00Z"
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_file', 'code_file:speed.c', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_symbol', 'code_symbol:speed.c#VehicleSpeed_Read', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )

    result = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "invalid speed should return default zero"},
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["status"] == "ok"
    assert payload["candidate_only"] is True
    assert payload["applied"] is False
    assert payload["requirement_id"] == requirement_id
    assert payload["draft_uid"]

    draft = client.get(f"/api/personal/drafts/{payload['draft_uid']}")
    assert draft.status_code == 200, draft.text
    metadata = draft.json()["metadata"]["generation"]
    assert metadata["task_uid"] == task["task_uid"]
    assert metadata["requirement_id"] == requirement_id
    assert metadata["candidate_only"] is True
    assert metadata["applied"] is False

    trace = client.get(f"/api/personal/dev-tasks/{task['task_uid']}/trace")
    assert trace.status_code == 200, trace.text
    assert any(
        item["link_type"] == "requirement_to_patch_candidate"
        and item["requirement_id"] == requirement_id
        and item["target_ref"] == f"patch:{payload['draft_uid']}"
        for item in trace.json()["trace_links"]
    )


def test_dev_task_patch_propose_rejects_non_evidence_file_directive(tmp_path: Path, monkeypatch: Any) -> None:
    original = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_dev_task_patch_directives":
            return LLMResult(
                call_id=991,
                provider="fake-test",
                model="patch-directives-fixture",
                status="ok",
                parsed={
                    "change_summary": "invalid speed returns zero",
                    "target_files": ["other.c"],
                    "target_symbols": ["other.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "other.c",
                            "find": "return -1;\n",
                            "replace": "return 0;\n",
                            "description": "invalid speed default zero",
                        }
                    ],
                    "risk_notes": [],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        return original(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, db_path, _workspace = _client(tmp_path, monkeypatch)
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        "int VehicleSpeed_Read(int valid)\n{\n    if (!valid) {\n        return -1;\n    }\n    return 42;\n}\n",
        encoding="utf-8",
    )
    (repo / "other.c").write_text("int Other(void)\n{\n    return -1;\n}\n", encoding="utf-8")
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200, saved.text
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read invalid default", "max_files": 20})
    assert indexed.status_code == 200, indexed.text

    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    requirement_id = task["requirements"][0]["requirement_id"]

    with connect(db_path) as conn:
        now = "2026-06-23T00:00:00Z"
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_file', 'code_file:speed.c', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_symbol', 'code_symbol:speed.c#VehicleSpeed_Read', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )

    result = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "invalid speed should return default zero"},
    )
    assert result.status_code == 400
    detail = result.json()["detail"]
    assert detail["status"] == "blocked"
    assert detail["candidate_only"] is True
    assert detail["applied"] is False
    assert "outside evidence pack" in detail["blocked_reason"]


def test_dev_task_patch_propose_returns_structured_blocked_when_llm_directives_are_empty(tmp_path: Path, monkeypatch: Any) -> None:
    original = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_dev_task_patch_directives":
            return LLMResult(
                call_id=992,
                provider="fake-test",
                model="patch-directives-fixture",
                status="ok",
                parsed={
                    "change_summary": "empty directives",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [],
                    "risk_notes": [],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        return original(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, db_path, _workspace = _client(tmp_path, monkeypatch)
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        "int VehicleSpeed_Read(int valid)\n{\n    if (!valid) {\n        return -1;\n    }\n    return 42;\n}\n",
        encoding="utf-8",
    )
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200, saved.text
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read invalid default", "max_files": 20})
    assert indexed.status_code == 200, indexed.text

    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    requirement_id = task["requirements"][0]["requirement_id"]

    with connect(db_path) as conn:
        now = "2026-06-23T00:00:00Z"
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_file', 'code_file:speed.c', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_symbol', 'code_symbol:speed.c#VehicleSpeed_Read', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )

    result = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "invalid speed should return default zero"},
    )
    assert result.status_code == 400
    detail = result.json()["detail"]
    assert detail["status"] == "blocked"
    assert detail["candidate_only"] is True
    assert detail["applied"] is False
    assert "returned no directives" in detail["blocked_reason"]


def test_dev_task_patch_propose_rejects_non_unique_find_match(tmp_path: Path, monkeypatch: Any) -> None:
    original = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_dev_task_patch_directives":
            return LLMResult(
                call_id=993,
                provider="fake-test",
                model="patch-directives-fixture",
                status="ok",
                parsed={
                    "change_summary": "invalid speed returns zero",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "speed.c",
                            "find": "return -1;\n",
                            "replace": "return 0;\n",
                            "description": "invalid speed default zero",
                        }
                    ],
                    "risk_notes": [],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        return original(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, db_path, _workspace = _client(tmp_path, monkeypatch)
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        "int VehicleSpeed_Read(int valid)\n{\n    if (!valid) {\n        return -1;\n    }\n    if (valid == -1) {\n        return -1;\n    }\n    return 42;\n}\n",
        encoding="utf-8",
    )
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200, saved.text
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read invalid default", "max_files": 20})
    assert indexed.status_code == 200, indexed.text

    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    requirement_id = task["requirements"][0]["requirement_id"]

    with connect(db_path) as conn:
        now = "2026-06-23T00:00:00Z"
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_file', 'code_file:speed.c', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_symbol', 'code_symbol:speed.c#VehicleSpeed_Read', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )

    result = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "invalid speed should return default zero"},
    )
    assert result.status_code == 400
    detail = result.json()["detail"]
    assert detail["status"] == "blocked"
    assert detail["candidate_only"] is True
    assert detail["applied"] is False
    assert "match exactly once" in detail["blocked_reason"]


def test_dev_task_repair_blocks_without_failed_validation(tmp_path: Path, monkeypatch: Any) -> None:
    client, _db_path, _workspace = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])

    result = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/repair",
        json={"prompt": "根据失败结果修一个候选"},
    )
    assert result.status_code == 400
    detail = result.json()["detail"]
    assert detail["status"] == "blocked"
    assert detail["candidate_only"] is True
    assert detail["applied"] is False
    assert "no failed validation summary" in detail["blocked_reason"]


def test_dev_task_repair_blocks_without_patch_candidate(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, _workspace = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    orchestrator = DevTaskOrchestrator(db_path, workspace=tmp_path / "workspace", project_id=1)
    attached = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_failed_validation",
            "output": {"command_kind": "run_tests", "passed": False, "returncode": 1},
        },
    )
    assert attached is not None

    result = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/repair",
        json={"prompt": "根据失败结果修一个候选"},
    )
    assert result.status_code == 400
    detail = result.json()["detail"]
    assert detail["status"] == "blocked"
    assert "no patch candidate draft" in detail["blocked_reason"]


def test_dev_task_repair_generates_single_candidate_and_records_metadata(tmp_path: Path, monkeypatch: Any) -> None:
    original = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_dev_task_patch_directives":
            return LLMResult(
                call_id=994,
                provider="fake-test",
                model="patch-directives-fixture",
                status="ok",
                parsed={
                    "change_summary": "invalid speed returns zero",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "speed.c",
                            "find": "        return -1;\n",
                            "replace": "        return 0;\n",
                            "description": "invalid speed default zero",
                        }
                    ],
                    "risk_notes": [],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        if purpose == "personal_dev_task_patch_repair_directives":
            return LLMResult(
                call_id=995,
                provider="fake-test",
                model="patch-repair-fixture",
                status="ok",
                parsed={
                    "change_summary": "repair invalid speed sentinel expectation",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "speed.c",
                            "find": "        return -1;\n",
                            "replace": "        return SPEED_INVALID_DEFAULT;\n",
                            "description": "restore documented sentinel",
                        }
                    ],
                    "risk_notes": ["confirm constant is in scope"],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        return original(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, db_path, _workspace = _client(tmp_path, monkeypatch)
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        "#define SPEED_INVALID_DEFAULT (-1)\nint VehicleSpeed_Read(int valid)\n{\n    if (!valid) {\n        return -1;\n    }\n    return 42;\n}\n",
        encoding="utf-8",
    )
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200, saved.text
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read invalid default", "max_files": 20})
    assert indexed.status_code == 200, indexed.text

    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    requirement_id = task["requirements"][0]["requirement_id"]

    with connect(db_path) as conn:
        now = "2026-06-23T00:00:00Z"
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_file', 'code_file:speed.c', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_symbol', 'code_symbol:speed.c#VehicleSpeed_Read', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )

    patch = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "invalid speed should return default zero"},
    )
    assert patch.status_code == 200, patch.text
    parent_patch_draft_uid = patch.json()["draft_uid"]

    orchestrator = DevTaskOrchestrator(db_path, workspace=tmp_path / "workspace", project_id=1)
    recorded = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_failed_validation_repair",
            "output": {"command_kind": "run_tests", "passed": False, "returncode": 1},
        },
    )
    assert recorded is not None

    repair = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/repair",
        json={"prompt": "根据失败结果修一个候选"},
    )
    assert repair.status_code == 200, repair.text
    payload = repair.json()
    assert payload["status"] == "ok"
    assert payload["candidate_only"] is True
    assert payload["applied"] is False
    assert payload["parent_patch_draft_uid"] == parent_patch_draft_uid
    assert payload["repair_of_validation_uid"] == "ptool_failed_validation_repair"
    assert payload["repair_attempt_index"] == 1
    assert payload["failure_category"] == "test_expectation"

    draft = client.get(f"/api/personal/drafts/{payload['draft_uid']}")
    assert draft.status_code == 200, draft.text
    repair_metadata = draft.json()["metadata"]["generation"]["repair_candidate"]
    assert repair_metadata["parent_patch_draft_uid"] == parent_patch_draft_uid
    assert repair_metadata["repair_of_validation_uid"] == "ptool_failed_validation_repair"
    assert repair_metadata["repair_attempt_index"] == 1
    assert repair_metadata["failure_category"] == "test_expectation"

    second = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/repair",
        json={"prompt": "再修一次"},
    )
    assert second.status_code == 400
    detail = second.json()["detail"]
    assert detail["status"] == "blocked"
    assert "already exists" in detail["blocked_reason"]


def test_solved_failure_learning_candidate_is_not_created_for_unresolved_failure(tmp_path: Path, monkeypatch: Any) -> None:
    client, db_path, workspace = _client(tmp_path, monkeypatch)
    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    orchestrator = DevTaskOrchestrator(db_path, workspace=workspace, project_id=1)

    attached = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_failed_only",
            "output": {"command_kind": "run_tests", "passed": False, "returncode": 1},
        },
    )
    assert attached is not None

    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0]
    assert count == 0


def test_solved_failure_learning_candidate_is_created_after_repair_followed_by_pass(tmp_path: Path, monkeypatch: Any) -> None:
    original = LLMBridge.complete_json

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_dev_task_patch_directives":
            return LLMResult(
                call_id=996,
                provider="fake-test",
                model="patch-directives-fixture",
                status="ok",
                parsed={
                    "change_summary": "invalid speed returns zero",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "speed.c",
                            "find": "        return -1;\n",
                            "replace": "        return 0;\n",
                            "description": "invalid speed default zero",
                        }
                    ],
                    "risk_notes": [],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        if purpose == "personal_dev_task_patch_repair_directives":
            return LLMResult(
                call_id=997,
                provider="fake-test",
                model="patch-repair-fixture",
                status="ok",
                parsed={
                    "change_summary": "repair invalid speed sentinel expectation",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "speed.c",
                            "find": "        return -1;\n",
                            "replace": "        return SPEED_INVALID_DEFAULT;\n",
                            "description": "restore documented sentinel",
                        }
                    ],
                    "risk_notes": [],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        return original(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    client, db_path, workspace = _client(tmp_path, monkeypatch)
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        "#define SPEED_INVALID_DEFAULT (-1)\nint VehicleSpeed_Read(int valid)\n{\n    if (!valid) {\n        return -1;\n    }\n    return 42;\n}\n",
        encoding="utf-8",
    )
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200, saved.text
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read invalid default", "max_files": 20})
    assert indexed.status_code == 200, indexed.text

    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    requirement_id = task["requirements"][0]["requirement_id"]

    with connect(db_path) as conn:
        now = "2026-06-23T00:00:00Z"
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_file', 'code_file:speed.c', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_symbol', 'code_symbol:speed.c#VehicleSpeed_Read', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )

    patch = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "invalid speed should return default zero"},
    )
    assert patch.status_code == 200, patch.text

    orchestrator = DevTaskOrchestrator(db_path, workspace=workspace, project_id=1)
    failed = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_failed_then_passed",
            "output": {"command_kind": "run_tests", "passed": False, "returncode": 1},
        },
    )
    assert failed is not None

    repair = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/repair",
        json={"prompt": "根据失败结果修一个候选"},
    )
    assert repair.status_code == 200, repair.text

    passed = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_failed_then_passed",
            "output": {"command_kind": "run_tests", "passed": True, "returncode": 0},
        },
    )
    assert passed is not None

    with connect(db_path) as conn:
        candidates = conn.execute("SELECT * FROM memory_candidates ORDER BY id DESC").fetchall()
    assert len(candidates) == 1
    candidate = dict(candidates[0])
    evidence = json.loads(candidate["evidence_refs_json"])
    applicability = json.loads(candidate["applicability_json"])
    counterexamples = json.loads(candidate["counterexamples_json"])
    assert evidence["validation_invocation_uid"] == "ptool_failed_then_passed"
    assert evidence["solved_failure_learning"] is True
    assert evidence["requirement_id"] == requirement_id
    assert evidence["failure_signature"] == "ptool_failed_then_passed"
    assert evidence["failure_category"] == "test_expectation"
    assert evidence["validation_kind"] == "tests"
    assert evidence["modified_files"] == ["speed.c"]
    assert evidence["code_symbols"] == ["speed.c#VehicleSpeed_Read"]
    assert evidence["trigger_context"]["validation_kind"] == "tests"
    assert applicability["requirement_id"] == requirement_id
    assert applicability["failure_signature"] == "ptool_failed_then_passed"
    assert applicability["failure_category"] == "test_expectation"
    assert applicability["validation_kind"] == "tests"
    assert applicability["modified_files"] == ["speed.c"]
    assert applicability["code_symbols"] == ["speed.c#VehicleSpeed_Read"]
    assert "wrong_behavior:" in candidate["problem"]
    assert "corrected_behavior:" in candidate["lesson"]
    assert "applicability:" in candidate["lesson"]
    assert "counterexamples:" in candidate["lesson"]
    assert "evidence_refs:" in candidate["lesson"]
    assert len(counterexamples) == 2

    passed_again = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_failed_then_passed",
            "output": {"command_kind": "run_tests", "passed": True, "returncode": 0},
        },
    )
    assert passed_again is not None
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0]
    assert count == 1


def test_approved_solved_failure_learning_is_recalled_for_patch_and_repair_context(tmp_path: Path, monkeypatch: Any) -> None:
    original = LLMBridge.complete_json
    captured: dict[str, list[dict[str, Any]]] = {"patch": [], "repair": []}
    recall_queries: list[str] = []

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        payload = json.loads(user_prompt)
        if purpose == "personal_dev_task_patch_directives":
            captured["patch"] = payload["evidence_pack"].get("solved_failure_lessons") or []
            return LLMResult(
                call_id=998,
                provider="fake-test",
                model="patch-directives-fixture",
                status="ok",
                parsed={
                    "change_summary": "invalid speed returns zero",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "speed.c",
                            "find": "        return -1;\n",
                            "replace": "        return 0;\n",
                            "description": "invalid speed default zero",
                        }
                    ],
                    "risk_notes": [],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        if purpose == "personal_dev_task_patch_repair_directives":
            captured["repair"] = payload["evidence_pack"].get("solved_failure_lessons") or []
            return LLMResult(
                call_id=999,
                provider="fake-test",
                model="patch-repair-fixture",
                status="ok",
                parsed={
                    "change_summary": "repair invalid speed sentinel expectation",
                    "target_files": ["speed.c"],
                    "target_symbols": ["speed.c#VehicleSpeed_Read"],
                    "directives": [
                        {
                            "file_path": "speed.c",
                            "find": "        return -1;\n",
                            "replace": "        return SPEED_INVALID_DEFAULT;\n",
                            "description": "restore documented sentinel",
                        }
                    ],
                    "risk_notes": [],
                    "validation_plan": ["run_tests"],
                },
                raw_text="{}",
            )
        return original(self, purpose=purpose, system_prompt=system_prompt, user_prompt=user_prompt, project_id=project_id, task_uid=task_uid)

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    original_recall = getattr(__import__("personal_agent.dev_tasks", fromlist=["recall_knowledge"]), "recall_knowledge")

    def record_recall(
        db_path: Path,
        *,
        project_id: int,
        query: str,
        limit: int = 8,
        category: str | None = None,
        exclude_category: str | None = None,
        source_type: str | None = None,
        status: str | None = "active",
    ) -> list[dict[str, Any]]:
        recall_queries.append(query)
        return original_recall(
            db_path,
            project_id=project_id,
            query=query,
            limit=limit,
            category=category,
            exclude_category=exclude_category,
            source_type=source_type,
            status=status,
        )

    monkeypatch.setattr("personal_agent.dev_tasks.recall_knowledge", record_recall)
    client, db_path, workspace = _client(tmp_path, monkeypatch)
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        "#define SPEED_INVALID_DEFAULT (-1)\nint VehicleSpeed_Read(int valid)\n{\n    if (!valid) {\n        return -1;\n    }\n    return 42;\n}\n",
        encoding="utf-8",
    )
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200, saved.text
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read invalid default", "max_files": 20})
    assert indexed.status_code == 200, indexed.text

    session_uid = _session(client)
    source_uid = _source(client)
    task = _start_task(client, session_uid, [source_uid])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    task = _continue_task(client, task["task_uid"])
    requirement_id = task["requirements"][0]["requirement_id"]

    with connect(db_path) as conn:
        now = "2026-06-23T00:00:00Z"
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_file', 'code_file:speed.c', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )
        conn.execute(
            """
            INSERT INTO trace_links(
                project_id, task_uid, requirement_id, link_type, target_ref, metadata_json,
                status, confidence, managed_by, source_agent_run_id, created_at
            )
            VALUES (?, ?, ?, 'requirement_to_code_symbol', 'code_symbol:speed.c#VehicleSpeed_Read', '{}', 'active', 1.0, 'test', '', ?)
            """,
            (1, task["task_uid"], requirement_id, now),
        )

    first_patch = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "invalid speed should return default zero"},
    )
    assert first_patch.status_code == 200, first_patch.text
    orchestrator = DevTaskOrchestrator(db_path, workspace=workspace, project_id=1)
    failed = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_recall_learning",
            "output": {"command_kind": "run_tests", "passed": False, "returncode": 1},
        },
    )
    assert failed is not None
    repair = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/repair",
        json={"prompt": "根据失败结果修一个候选"},
    )
    assert repair.status_code == 200, repair.text
    passed = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_recall_learning",
            "output": {"command_kind": "run_tests", "passed": True, "returncode": 0},
        },
    )
    assert passed is not None

    with connect(db_path) as conn:
        candidate_id = int(conn.execute("SELECT id FROM memory_candidates ORDER BY id DESC LIMIT 1").fetchone()[0])
    approve_memory_candidate(db_path, candidate_id, reviewer="tester", comment="approve solved failure lesson")

    second_patch = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/propose",
        json={"prompt": "再次修改 invalid speed 默认值"},
    )
    assert second_patch.status_code == 200, second_patch.text
    assert captured["patch"]
    assert captured["patch"][0]["item_uid"] == f"kb_memory_{candidate_id}"
    assert any(requirement_id in query and "test_expectation" in query and "tests" in query and "speed.c" in query and "speed.c#VehicleSpeed_Read" in query for query in recall_queries)

    failed_again = orchestrator.record_validation(
        task_uid=task["task_uid"],
        kind="tests",
        result={
            "status": "ok",
            "invocation_uid": "ptool_recall_learning_second",
            "output": {"command_kind": "run_tests", "passed": False, "returncode": 1},
        },
    )
    assert failed_again is not None
    second_repair = client.post(
        f"/api/personal/dev-tasks/{task['task_uid']}/code-patch/repair",
        json={"prompt": "再根据失败结果修一个候选"},
    )
    assert second_repair.status_code == 200, second_repair.text
    assert captured["repair"]
    assert captured["repair"][0]["item_uid"] == f"kb_memory_{candidate_id}"
    final_task = orchestrator.get(task["task_uid"])
    summary_lessons = final_task["validation_summary"]["tests"]["solved_failure_lessons"]
    assert summary_lessons
    assert summary_lessons[0]["item_uid"] == f"kb_memory_{candidate_id}"
    assert len(summary_lessons) <= 3
