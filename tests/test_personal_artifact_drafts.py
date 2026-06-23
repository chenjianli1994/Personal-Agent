from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent.core.database import connect
from personal_agent.app import create_personal_app
from personal_agent.core.database import init_db
from personal_agent.context_builder import PersonalContextBuilder


def test_personal_artifact_draft_create_revise_and_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    source = client.post(
        "/api/personal/sources/text",
        json={"title": "输入资料", "content": "需求输入资料"},
    )
    assert source.status_code == 200

    created = client.post(
        "/api/personal/artifacts/drafts",
        json={
            "artifact_type": "requirement_analysis_report",
            "title": "需求分析报告草稿",
            "content": "# v1\n初版内容",
            "content_format": "markdown",
            "source_uid": source.json()["source_uid"],
            "metadata": {"author": "test"},
        },
    )
    assert created.status_code == 200
    draft = created.json()
    assert draft["draft_uid"].startswith("draft_")
    assert draft["current_revision"] == 1
    assert draft["revision_count"] == 1
    assert draft["is_active"] is True
    assert draft["content"] == "# v1\n初版内容"
    assert draft["revisions"][0]["revision_index"] == 1

    content = client.get(f"/api/personal/artifacts/{draft['draft_uid']}/content")
    assert content.status_code == 200
    assert content.json()["content"] == "# v1\n初版内容"

    revised = client.post(
        f"/api/personal/artifacts/{draft['draft_uid']}/revise-manual",
        json={"content": "# v2\n补充边界条件", "metadata": {"reason": "manual"}},
    )
    assert revised.status_code == 200
    payload = revised.json()
    assert payload["current_revision"] == 2
    assert payload["revision_count"] == 2
    assert payload["content"] == "# v2\n补充边界条件"
    assert [item["revision_index"] for item in payload["revisions"]] == [1, 2]
    assert payload["revisions"][0]["content"] == "# v1\n初版内容"
    assert payload["revisions"][1]["content"] == "# v2\n补充边界条件"

    old_content = client.get(
        f"/api/personal/artifacts/{draft['draft_uid']}/content",
        params={"revision_index": 1},
    )
    assert old_content.status_code == 200
    assert old_content.json()["content"] == "# v1\n初版内容"

    drafts = client.get("/api/personal/artifacts/drafts")
    assert drafts.status_code == 200
    assert drafts.json()[0]["draft_uid"] == draft["draft_uid"]
    assert drafts.json()[0]["is_active"] is True

    with connect(db_path) as conn:
        formal_artifact_count = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        draft_count = conn.execute("SELECT COUNT(*) FROM personal_artifact_drafts").fetchone()[0]
        revision_count = conn.execute("SELECT COUNT(*) FROM personal_artifact_revisions").fetchone()[0]
    assert formal_artifact_count == 0
    assert draft_count == 1
    assert revision_count == 2


def test_personal_artifact_active_draft_switching(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client = _client(tmp_path)

    first = _create_draft(client, "第一份草稿", "requirement_breakdown")
    second = _create_draft(client, "第二份草稿", "functional_spec")
    assert second["is_active"] is True

    drafts = client.get("/api/personal/artifacts/drafts").json()
    assert drafts[0]["draft_uid"] == second["draft_uid"]

    activated = client.post(f"/api/personal/artifacts/{first['draft_uid']}/activate")
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    refreshed = client.get("/api/personal/artifacts/drafts").json()
    assert refreshed[0]["draft_uid"] == first["draft_uid"]
    assert refreshed[0]["is_active"] is True
    assert refreshed[1]["is_active"] is False


def test_personal_artifact_draft_validation_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client = _client(tmp_path)

    bad_type = client.post(
        "/api/personal/artifacts/drafts",
        json={"artifact_type": "unknown", "title": "x", "content": "x"},
    )
    assert bad_type.status_code == 400
    assert "unsupported artifact_type" in bad_type.json()["detail"]

    missing = client.get("/api/personal/artifacts/draft_missing/content")
    assert missing.status_code == 404


def test_personal_drafts_schema_migrates_session_lineage_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    init_db(db_path)

    with connect(db_path) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(personal_drafts)").fetchall()}

    assert {"session_uid", "derived_from_draft_uid", "lineage_stale"} <= columns


def test_personal_draft_create_and_list_preserve_session_uid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client = _client(tmp_path)

    created = client.post(
        "/api/personal/drafts",
        json={
            "document_type": "functional_spec",
            "session_uid": "session_alpha",
            "title": "会话草稿",
            "content": "# 会话草稿\n内容",
        },
    )
    assert created.status_code == 200
    draft = created.json()
    assert draft["session_uid"] == "session_alpha"
    assert draft["derived_from_draft_uid"] == ""
    assert draft["lineage_stale"] is False

    scoped = client.get("/api/personal/drafts", params={"session_uid": "session_alpha"})
    assert scoped.status_code == 200
    assert [item["draft_uid"] for item in scoped.json()] == [draft["draft_uid"]]


def test_personal_drafts_allow_multiple_active_per_session_and_document_type_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client = _client(tmp_path)

    requirement = client.post(
        "/api/personal/drafts",
        json={"document_type": "requirement_analysis_report", "session_uid": "session_a", "title": "需求", "content": "# 需求"},
    )
    spec = client.post(
        "/api/personal/drafts",
        json={"document_type": "functional_spec", "session_uid": "session_a", "title": "规格", "content": "# 规格"},
    )
    other_session = client.post(
        "/api/personal/drafts",
        json={"document_type": "functional_spec", "session_uid": "session_b", "title": "规格B", "content": "# 规格B"},
    )

    assert requirement.status_code == 200
    assert spec.status_code == 200
    assert other_session.status_code == 200

    drafts_a = client.get("/api/personal/drafts", params={"session_uid": "session_a"}).json()
    by_uid = {item["draft_uid"]: item for item in drafts_a}
    assert by_uid[requirement.json()["draft_uid"]]["is_active"] is True
    assert by_uid[spec.json()["draft_uid"]]["is_active"] is True

    drafts_b = client.get("/api/personal/drafts", params={"session_uid": "session_b"}).json()
    assert drafts_b[0]["draft_uid"] == other_session.json()["draft_uid"]
    assert drafts_b[0]["is_active"] is True


def test_context_builder_prefers_session_active_draft_uid_and_activate_updates_focus(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client = _client(tmp_path)
    first_turn = client.post("/api/personal/chat/turn", json={"content": "创建会话A"})
    second_turn = client.post("/api/personal/chat/turn", json={"content": "创建会话B"})
    session_a = first_turn.json()["session"]["session_uid"]
    session_b = second_turn.json()["session"]["session_uid"]

    requirement = client.post(
        "/api/personal/drafts",
        json={"document_type": "requirement_analysis_report", "session_uid": session_a, "title": "需求A", "content": "# 需求A"},
    ).json()
    spec_v1 = client.post(
        "/api/personal/drafts",
        json={"document_type": "functional_spec", "session_uid": session_a, "title": "规格A1", "content": "# 规格A1"},
    ).json()
    spec_v2 = client.post(
        "/api/personal/drafts",
        json={"document_type": "functional_spec", "session_uid": session_a, "title": "规格A2", "content": "# 规格A2", "make_active": False},
    ).json()
    client.post(
        "/api/personal/drafts",
        json={"document_type": "functional_spec", "session_uid": session_b, "title": "规格B", "content": "# 规格B"},
    )

    db_path = tmp_path / "agent.db"
    builder = PersonalContextBuilder(db_path, 1)

    before_activate = builder.build(session_uid=session_a, prompt="查看当前草稿")
    assert before_activate["active_draft"]["draft_uid"] == spec_v1["draft_uid"]

    activated = client.post(f"/api/personal/drafts/{spec_v2['draft_uid']}/activate")
    assert activated.status_code == 200

    after_activate = builder.build(session_uid=session_a, prompt="查看当前草稿")
    assert after_activate["active_draft"]["draft_uid"] == spec_v2["draft_uid"]
    assert after_activate["active_draft"]["session_uid"] == session_a

    other_context = builder.build(session_uid=session_b, prompt="查看当前草稿")
    assert other_context["active_draft"]["session_uid"] == session_b
    assert other_context["active_draft"]["draft_uid"] != spec_v2["draft_uid"]

    with connect(db_path) as conn:
        session_row = conn.execute("SELECT active_draft_uid FROM personal_sessions WHERE session_uid=?", (session_a,)).fetchone()
    assert session_row["active_draft_uid"] == spec_v2["draft_uid"]
    assert requirement["draft_uid"] != after_activate["active_draft"]["draft_uid"]


def test_draft_list_session_filter_excludes_other_sessions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client = _client(tmp_path)

    session_a = client.post("/api/personal/chat/turn", json={"content": "会话A"}).json()["session"]["session_uid"]
    session_b = client.post("/api/personal/chat/turn", json={"content": "会话B"}).json()["session"]["session_uid"]
    draft_a = client.post(
        "/api/personal/drafts",
        json={"document_type": "functional_spec", "session_uid": session_a, "title": "A", "content": "# A"},
    ).json()
    client.post(
        "/api/personal/drafts",
        json={"document_type": "functional_spec", "session_uid": session_b, "title": "B", "content": "# B"},
    )

    scoped = client.get("/api/personal/drafts", params={"session_uid": session_a})
    assert scoped.status_code == 200
    assert [item["draft_uid"] for item in scoped.json()] == [draft_a["draft_uid"]]


def test_draft_list_task_filter_only_returns_matching_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client = _client(tmp_path)

    session_uid = client.post("/api/personal/chat/turn", json={"content": "任务过滤会话"}).json()["session"]["session_uid"]
    kept = client.post(
        "/api/personal/drafts",
        json={
            "document_type": "functional_spec",
            "session_uid": session_uid,
            "task_uid": "task_keep",
            "title": "Keep",
            "content": "# Keep",
        },
    ).json()
    client.post(
        "/api/personal/drafts",
        json={
            "document_type": "requirement_breakdown",
            "session_uid": session_uid,
            "task_uid": "task_other",
            "title": "Other",
            "content": "# Other",
        },
    )

    scoped = client.get("/api/personal/drafts", params={"task_uid": "task_keep"})
    assert scoped.status_code == 200
    assert [item["draft_uid"] for item in scoped.json()] == [kept["draft_uid"]]


def test_draft_list_session_and_task_filters_take_intersection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client = _client(tmp_path)

    session_a = client.post("/api/personal/chat/turn", json={"content": "交集会话A"}).json()["session"]["session_uid"]
    session_b = client.post("/api/personal/chat/turn", json={"content": "交集会话B"}).json()["session"]["session_uid"]
    kept = client.post(
        "/api/personal/drafts",
        json={
            "document_type": "functional_spec",
            "session_uid": session_a,
            "task_uid": "task_shared",
            "title": "A",
            "content": "# A",
        },
    ).json()
    client.post(
        "/api/personal/drafts",
        json={
            "document_type": "functional_spec",
            "session_uid": session_b,
            "task_uid": "task_shared",
            "title": "B",
            "content": "# B",
        },
    )

    scoped = client.get("/api/personal/drafts", params={"session_uid": session_a, "task_uid": "task_shared"})
    assert scoped.status_code == 200
    assert [item["draft_uid"] for item in scoped.json()] == [kept["draft_uid"]]


def _client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TestClient(create_personal_app(db_path, workspace))


def _create_draft(client: TestClient, title: str, artifact_type: str) -> dict:
    response = client.post(
        "/api/personal/artifacts/drafts",
        json={
            "artifact_type": artifact_type,
            "title": title,
            "content": f"# {title}\n正文",
            "content_format": "markdown",
        },
    )
    assert response.status_code == 200
    return response.json()
