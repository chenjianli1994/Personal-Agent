from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent.core.database import connect
from personal_agent.core.knowledge_base import import_knowledge_code_directory, import_knowledge_document
from personal_agent.app import create_personal_app


def test_personal_knowledge_import_search_and_deprecate_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    source = client.post(
        "/api/personal/sources/text",
        json={"title": "诊断资料", "content": "AlphaBetaUnique DTC 诊断资料：传感器无效时必须记录故障码。"},
    )
    assert source.status_code == 200
    source_uid = source.json()["source_uid"]

    imported = client.post("/api/personal/knowledge/import-source", json={"source_uid": source_uid})
    assert imported.status_code == 200
    payload = imported.json()
    assert payload["status"] == "imported"
    assert payload["knowledge_item"]["source_ref"] == f"personal_source:{source_uid}"
    assert payload["knowledge_document"]["chunk_count"] >= 1

    hits = client.post("/api/personal/knowledge/search", json={"query": "AlphaBetaUnique 故障码", "limit": 5})
    assert hits.status_code == 200
    assert any(item["source_ref"] == f"personal_source:{source_uid}" for item in hits.json())

    listed = client.get("/api/personal/knowledge")
    assert listed.status_code == 200
    assert any(item["item_uid"] == payload["knowledge_item"]["item_uid"] for item in listed.json()["items"])

    deprecated = client.post(
        f"/api/personal/knowledge/{payload['knowledge_item']['id']}/deprecate",
        json={"reviewer": "tester", "comment": "outdated"},
    )
    assert deprecated.status_code == 200
    assert deprecated.json()["status"] == "deprecated"


def test_manual_knowledge_document_import_rejects_forbidden_terms(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    project_id = client.get("/api/personal/context").json()["project_id"]

    try:
        import_knowledge_document(
            db_path,
            project_id=project_id,
            title="legacy note",
            content="This mentions " + "AS" + "PICE and " + "Ga" + "te.",
            source_ref="notes/legacy.md",
            tags=["legacy"],
        )
    except ValueError as exc:
        assert "forbidden" in str(exc)
    else:
        raise AssertionError("expected forbidden knowledge import to fail")

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 0


def test_directory_knowledge_import_skips_forbidden_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    project_id = client.get("/api/personal/context").json()["project_id"]
    knowledge_root = tmp_path / "import_knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "clean.md").write_text("normal personal note", encoding="utf-8")
    (knowledge_root / "legacy.md").write_text("legacy " + "AS" + "PICE " + "base" + "line " + "Ga" + "te note", encoding="utf-8")

    result = import_knowledge_code_directory(db_path, knowledge_root, project_id)

    assert result["indexed"] == 1
    assert result["skipped"] >= 1
    with connect(db_path) as conn:
        refs = [str(row["source_ref"]) for row in conn.execute("SELECT source_ref FROM knowledge_items ORDER BY source_ref").fetchall()]
    assert refs == ["clean.md", "clean.md"]


def test_personal_learning_candidate_immediate_and_approved_memory_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    learn = client.post("/api/agent/unified-turn", json={"content": "以后功能规范不要写实现细节"})
    assert learn.status_code == 200
    learn_payload = learn.json()
    assert learn_payload["mode"] == "personal_phase6_learning"
    candidate_id = learn_payload["metadata"]["personal_intent"]["learning_candidate_id"]
    assert candidate_id
    task_uid = learn_payload["task"]["task_uid"]

    candidates = client.get("/api/personal/learning/candidates").json()
    candidate = next(item for item in candidates if item["id"] == candidate_id)
    assert candidate["status"] == "candidate"
    assert "功能规范不要写实现细节" in candidate["lesson"]

    immediate = client.post(
        "/api/agent/unified-turn",
        json={"task_uid": task_uid, "content": "生成功能规范说明"},
    )
    assert immediate.status_code == 200
    draft_uid = immediate.json()["metadata"]["personal_intent"]["created_draft_uids"][0]
    draft = client.get(f"/api/personal/artifacts/{draft_uid}").json()
    assert "本会话即时遵守：功能规范不要写实现细节" in draft["content"]
    assert "candidate:" in draft["content"]

    approved = client.post(
        f"/api/personal/learning/candidates/{candidate_id}/approve",
        json={"reviewer": "tester", "comment": "长期使用"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    long_term = client.post("/api/personal/artifacts/propose", json={"prompt": "生成功能规范说明", "artifact_type": "functional_spec"})
    assert long_term.status_code == 200
    assert "长期经验：" in long_term.json()["content"]
    assert "功能规范不要写实现细节" in long_term.json()["content"]

    reject_feedback = client.post("/api/personal/learning/feedback", json={"feedback": "以后功能规范必须写伪代码"})
    assert reject_feedback.status_code == 200
    rejected_id = reject_feedback.json()["id"]
    rejected = client.post(
        f"/api/personal/learning/candidates/{rejected_id}/reject",
        json={"reviewer": "tester", "comment": "不作为长期规则"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    with connect(db_path) as conn:
        rejected_knowledge = conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE source_ref=? AND status='active'",
            (f"memory_candidate:{rejected_id}",),
        ).fetchone()[0]
    assert rejected_knowledge == 0

    summary = client.get("/api/personal/learning/summary")
    assert summary.status_code == 200
    assert any(item["status"] == "approved" for item in summary.json()["memory"])


def test_personal_learning_approval_backfills_legacy_governance_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    created = client.post(
        "/api/personal/learning/feedback",
        json={"feedback": "Answer with concise bullets when I ask for a summary."},
    )
    assert created.status_code == 200
    candidate_id = created.json()["id"]
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE memory_candidates
            SET scope='', failure_type='', applicability_json='{}', validation_query=''
            WHERE id=?
            """,
            (candidate_id,),
        )

    approved = client.post(
        f"/api/personal/learning/candidates/{candidate_id}/approve",
        json={"reviewer": "tester", "comment": "legacy candidate"},
    )

    assert approved.status_code == 200
    payload = approved.json()
    assert payload["status"] == "approved"
    assert payload["scope"] == "project"
    assert payload["failure_type"]
    assert payload["applicability"]["personal_agent"] is True


def test_personal_learning_approval_accepts_session_scoped_candidates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    created = client.post(
        "/api/personal/learning/feedback",
        json={
            "feedback": "When summarizing, use bullet points.",
            "scope": "session",
        },
    )
    assert created.status_code == 200
    candidate_id = created.json()["id"]

    approved = client.post(
        f"/api/personal/learning/candidates/{candidate_id}/approve",
        json={"reviewer": "tester", "comment": "session preference"},
    )

    assert approved.status_code == 200
    payload = approved.json()
    assert payload["status"] == "approved"
    assert payload["scope"] == "session"


def test_personal_chat_reflects_approves_and_rejects_learning_candidates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    learn = client.post("/api/personal/chat/turn", json={"content": "以后回答要更有条理，但不要固定模板"})
    assert learn.status_code == 200
    session_uid = learn.json()["session"]["session_uid"]
    learning = learn.json()["message"]["metadata"]["learning_reflection"]
    candidate_id = learning["candidate_id"]
    assert candidate_id

    candidates = client.get("/api/personal/learning/candidates").json()
    candidate = next(item for item in candidates if item["id"] == candidate_id)
    assert candidate["status"] == "candidate"
    assert "有条理" in candidate["lesson"]
    assert "固定模板" in candidate["lesson"]

    follow_up = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "普通问题：你现在能做什么？"})
    assert follow_up.status_code == 200
    assert "已记录为待批准经验" not in follow_up.json()["message"]["content"]
    assert "更有条理" in follow_up.json()["message"]["content"] or "避免固定模板" in follow_up.json()["message"]["content"]

    approved = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "批准这条经验"})
    assert approved.status_code == 200
    candidates = client.get("/api/personal/learning/candidates").json()
    assert next(item for item in candidates if item["id"] == candidate_id)["status"] == "approved"
    with connect(db_path) as conn:
        lesson_count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE category='memory_lesson' AND source_ref=? AND status='active'",
            (f"memory_candidate:{candidate_id}",),
        ).fetchone()[0]
    assert lesson_count == 1

    learn_again = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "下次不要这样回答，先确认我纠正的点"})
    assert learn_again.status_code == 200
    rejected_id = learn_again.json()["message"]["metadata"]["learning_reflection"]["candidate_id"]
    rejected = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "驳回刚才那条"})
    assert rejected.status_code == 200
    candidates = client.get("/api/personal/learning/candidates").json()
    assert next(item for item in candidates if item["id"] == rejected_id)["status"] == "rejected"


def test_personal_chat_ordinary_question_does_not_create_learning_candidate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    answer = client.post("/api/personal/chat/turn", json={"content": "普通问题：这个 Agent 能做什么？"})
    assert answer.status_code == 200
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0]
    assert count == 0


def test_personal_skill_update_candidate_approval_and_rejection_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    learn = client.post("/api/personal/chat/turn", json={"content": "以后功能规范不要写实现细节"})
    assert learn.status_code == 200
    session_uid = learn.json()["session"]["session_uid"]

    candidates = client.get("/api/personal/skills/update-candidates").json()
    candidate = candidates[0]
    assert candidate["status"] == "candidate"
    assert candidate["target_skill"] == "functional-spec"
    assert "实现细节" in candidate["proposed_change"]
    skill_path = workspace / ".personal_agent" / "skills" / "functional-spec" / "SKILL.md"
    before_text = skill_path.read_text(encoding="utf-8")
    assert candidate["proposed_change"] not in before_text

    with connect(db_path) as conn:
        version_count_before = conn.execute("SELECT COUNT(*) FROM personal_skill_versions").fetchone()[0]

    approved = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "批准这个 Skill 修改"})
    assert approved.status_code == 200
    reviewed = client.get("/api/personal/skills/update-candidates").json()[0]
    assert reviewed["status"] == "approved"
    with connect(db_path) as conn:
        version_count_after = conn.execute("SELECT COUNT(*) FROM personal_skill_versions").fetchone()[0]
    assert version_count_after == version_count_before + 1
    assert candidate["proposed_change"] in skill_path.read_text(encoding="utf-8")

    learn_again = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "以后功能规范不要写实现细节"})
    assert learn_again.status_code == 200
    second = next(item for item in client.get("/api/personal/skills/update-candidates").json() if item["status"] == "candidate")
    with connect(db_path) as conn:
        version_count_before_reject = conn.execute("SELECT COUNT(*) FROM personal_skill_versions").fetchone()[0]

    rejected = client.post("/api/personal/chat/turn", json={"session_uid": session_uid, "content": "驳回刚才那个 Skill 更新"})
    assert rejected.status_code == 200
    rejected_candidate = next(item for item in client.get("/api/personal/skills/update-candidates").json() if item["id"] == second["id"])
    assert rejected_candidate["status"] == "rejected"
    with connect(db_path) as conn:
        version_count_after_reject = conn.execute("SELECT COUNT(*) FROM personal_skill_versions").fetchone()[0]
    assert version_count_after_reject == version_count_before_reject


def test_draft_revision_feedback_can_create_temporary_skill_candidate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    source = client.post(
        "/api/personal/sources/text",
        json={"title": "功能材料", "content": "水泵需要根据充电状态和水温阈值控制启停。", "make_active": True},
    )
    assert source.status_code == 200
    generated = client.post("/api/personal/chat/turn", json={"content": "生成功能规范"})
    assert generated.status_code == 200
    session_uid = generated.json()["session"]["session_uid"]
    draft_uid = generated.json()["message"]["metadata"]["draft"]["draft_uid"]

    revised = client.post(
        "/api/personal/chat/turn",
        json={"session_uid": session_uid, "content": "修订草稿，并且以后功能规范不要写实现细节"},
    )
    assert revised.status_code == 200
    assert revised.json()["message"]["metadata"]["draft"]["draft_uid"] == draft_uid

    candidates = client.get("/api/personal/skills/update-candidates").json()
    candidate = candidates[0]
    assert candidate["status"] == "candidate"
    assert candidate["target_skill"] == "functional-spec"

    draft = client.get(f"/api/personal/drafts/{draft_uid}").json()
    assert draft["current_revision"] == 2
