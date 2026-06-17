from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent.core.database import connect
from personal_agent.app import create_personal_app
from personal_agent.skill_registry import ensure_default_document_skills


def test_personal_skill_registry_initializes_five_default_document_skills(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    response = client.get("/api/personal/skills")
    assert response.status_code == 200
    skills = response.json()
    assert len(skills) == 5
    assert {item["document_type"] for item in skills} == {
        "requirement_analysis_report",
        "requirement_breakdown",
        "functional_spec",
        "detailed_design",
        "test_case_spec",
    }

    skills_root = workspace / ".personal_agent" / "skills"
    for item in skills:
        skill_dir = skills_root / item["name"]
        assert skill_dir.is_dir()
        skill_file = skill_dir / "SKILL.md"
        assert skill_file.is_file()
        text = skill_file.read_text(encoding="utf-8")
        assert "document_type:" in text
        assert "template:" in text
        assert "required_sections:" in text
        assert "allowed_tools" in text

    detail = client.get("/api/personal/skills/functional-spec")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["name"] == "functional-spec"
    assert payload["document_type"] == "functional_spec"
    assert "SKILL.md" in payload["path"]
    assert payload["template"]["name"] == "default"
    assert payload["template"]["format"] == "markdown"
    assert payload["template"]["loaded"] is True
    assert "功能目标" in payload["required_sections"]

    functional_template = skills_root / "functional-spec" / "templates" / "default.md"
    test_case_template = skills_root / "test-case-spec" / "templates" / "default.json"
    assert functional_template.is_file()
    assert test_case_template.is_file()
    functional_template.write_text("USER CUSTOM TEMPLATE\n", encoding="utf-8")

    second = client.get("/api/personal/skills")
    assert second.status_code == 200
    assert functional_template.read_text(encoding="utf-8") == "USER CUSTOM TEMPLATE\n"

    versions = client.get("/api/personal/skills/functional-spec/versions")
    assert versions.status_code == 200
    assert versions.json()[0]["version_index"] == 1

    evaluated = client.post("/api/personal/skills/functional-spec/evaluate")
    assert evaluated.status_code == 200
    assert evaluated.json()["status"] == "passed"


def test_legacy_default_requirement_analysis_skill_is_upgraded_with_template_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    legacy_markdown = """---
name: requirement-analysis-report
description: legacy
skill_kind: document
document_type: requirement_analysis_report
content_format: markdown
allowed_tools:
  - source_read
---
# 需求分析报告

## Instructions
生成 Markdown 需求分析报告。必须包含：输入摘要、需求理解、关键假设、风险与边界、待澄清问题、验收建议、证据引用。
"""
    TestClient(create_personal_app(db_path, workspace))
    skill_dir = workspace / ".personal_agent" / "skills" / "requirement-analysis-report"
    (skill_dir / "SKILL.md").write_text(legacy_markdown, encoding="utf-8")
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT s.skill_uid, s.active_version_uid
            FROM personal_skills s
            WHERE s.name='requirement-analysis-report'
            """
        ).fetchone()
        conn.execute(
            "UPDATE personal_skill_versions SET skill_markdown=?, metadata_json=? WHERE version_uid=?",
            (legacy_markdown, "{}", row["active_version_uid"]),
        )

    ensure_default_document_skills(db_path, workspace=workspace, project_id=1)

    upgraded = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "template:" in upgraded
    assert "required_sections:" in upgraded
    assert "原文事实表" in upgraded
    assert "术语与变量定义" in upgraded
    assert "状态机必须区分" in upgraded
    assert (skill_dir / "templates" / "default.md").is_file()

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT s.active_version_uid, v.version_index, v.created_by, v.skill_markdown
            FROM personal_skills s
            JOIN personal_skill_versions v ON v.version_uid=s.active_version_uid
            WHERE s.name='requirement-analysis-report'
            """
        ).fetchone()
    assert row is not None
    assert row["version_index"] == 2
    assert row["created_by"] == "system_default_upgrade"
    assert "template:" in row["skill_markdown"]
    assert "required_sections:" in row["skill_markdown"]
