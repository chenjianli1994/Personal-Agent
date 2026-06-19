from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

from personal_agent.core.database import connect, init_db
from personal_agent.core.codebase.file_text import CODE_FILE_RELEVANCE_CHARS, read_code_text
from personal_agent.core.codebase.retriever import _files_containing_type
from personal_agent.app import create_personal_app


def test_personal_phase5_detailed_design_uses_code_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, db_path, _repo, _test_command = _client_with_code_repo(tmp_path)

    design = client.post(
        "/api/personal/artifacts/propose",
        json={"prompt": "生成详细设计，分析 VehicleSpeed_Read 无效速度默认值", "artifact_type": "detailed_design"},
    )
    assert design.status_code == 200
    payload = design.json()
    assert payload["artifact_type"] == "detailed_design"
    assert "Codebase Impact 证据" in payload["content"]
    assert "Symbol Lookup 证据" in payload["content"]
    assert "Call Graph 证据" in payload["content"]
    assert "Include Impact 证据" in payload["content"]
    assert "Macro / Type / Variable 证据" in payload["content"]
    assert "VehicleSpeed_Read" in payload["content"]
    assert payload["metadata"]["generation"]["boundaries"]["generates_code_patch"] is False
    assert payload["metadata"]["generation"]["evidence_refs"]

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0


def test_personal_phase5_code_patch_draft_uses_patch_propose_and_apply_policy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, db_path, repo, _test_command = _client_with_code_repo(tmp_path)
    source = repo / "speed.c"

    patch = client.post(
        "/api/personal/artifacts/code-patch",
        json={
            "prompt": "invalid speed should return default zero",
            "session_uid": "session_patch",
            "target_symbol": "VehicleSpeed_Read",
            "directives": [
                {
                    "file_path": "speed.c",
                    "find": "        return -1;\n",
                    "replace": "        return 0;\n",
                    "description": "invalid speed default",
                }
            ],
        },
    )
    assert patch.status_code == 200
    draft = patch.json()
    assert draft["artifact_type"] == "c_code_diff"
    assert draft["session_uid"] == "session_patch"
    assert draft["content_format"] == "diff"
    assert "return 0;" in draft["content"]
    assert draft["metadata"]["generation"]["patch_propose"]["patch_plan"]["trace_impact"]["implementation_stage"] == "code_change"
    assert draft["metadata"]["generation"]["boundaries"]["uses_patch_propose"] is True
    assert draft["metadata"]["generation"]["boundaries"]["writes_real_code"] is False
    assert "return -1;" in source.read_text(encoding="utf-8")

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0

    rejected = client.post("/api/personal/patch/apply", json={"patch_text": draft["content"], "dry_run": False, "confirmed": False})
    assert rejected.status_code == 400
    assert "return -1;" in source.read_text(encoding="utf-8")

    applied = client.post("/api/personal/patch/apply", json={"patch_text": draft["content"], "dry_run": False, "confirmed": True})
    assert applied.status_code == 200
    assert applied.json()["output"]["passed"] is True
    assert "return 0;" in source.read_text(encoding="utf-8")


def test_personal_patch_propose_tool_chain_preserves_session_uid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, _db_path, _repo, _test_command = _client_with_code_repo(tmp_path)

    proposed = client.post(
        "/api/personal/patch/propose",
        json={
            "change_text": "invalid speed should return default zero",
            "session_uid": "session_tool_patch",
            "target_symbol": "VehicleSpeed_Read",
            "directives": [
                {
                    "file_path": "speed.c",
                    "find": "        return -1;\n",
                    "replace": "        return 0;\n",
                    "description": "invalid speed default",
                }
            ],
            "dry_run": False,
        },
    )

    assert proposed.status_code == 200
    artifact = proposed.json()["output"]["artifact"]
    assert artifact["session_uid"] == "session_tool_patch"

    created = client.get(f"/api/personal/drafts/{artifact['draft_uid']}")
    assert created.status_code == 200
    assert created.json()["session_uid"] == "session_tool_patch"


def test_personal_phase5_unit_test_code_draft_and_validation_allowlist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, _db_path, _repo, test_command = _client_with_code_repo(tmp_path)

    draft = client.post("/api/personal/artifacts/unit-test-code", json={"prompt": "为 VehicleSpeed_Read 生成单元测试代码", "session_uid": "session_unit"})
    assert draft.status_code == 200
    payload = draft.json()
    assert payload["artifact_type"] == "unit_test_code_or_diff"
    assert payload["session_uid"] == "session_unit"
    assert payload["content_format"] == "diff"
    assert "test_normal_path" in payload["content"]
    assert payload["generation"]["impact"]
    assert payload["generation"]["impact"]["passed"] is True
    assert payload["metadata"]["generation"]["boundaries"]["requires_whitelisted_validation"] is True

    rejected = client.post(
        "/api/personal/validation/tests",
        json={"command": "python other.py", "timeout_s": 30, "confirmed": True},
    )
    assert rejected.status_code == 200
    assert rejected.json()["output"]["passed"] is False
    assert "allowlist" in " ".join(rejected.json()["output"]["limitations"])

    allowed = client.post(
        "/api/personal/validation/tests",
        json={"command": test_command, "timeout_s": 30, "confirmed": True},
    )
    assert allowed.status_code == 200
    assert allowed.json()["output"]["passed"] is True

    turn = client.post("/api/agent/unified-turn", json={"content": "生成单元测试代码"})
    assert turn.status_code == 200
    assert turn.json()["mode"] == "personal_phase4_artifact"
    metadata = turn.json()["metadata"]["personal_intent"]
    assert metadata["created_draft_uids"]
    created = client.get(f"/api/personal/artifacts/{metadata['created_draft_uids'][0]}").json()
    assert created["artifact_type"] == "unit_test_code_or_diff"
    assert "test_exception_or_diagnostic_path" in created["content"]


def test_requirement_layer_document_does_not_inject_code_impact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, _db_path, _repo, _test_command = _client_with_code_repo(tmp_path)

    requirement = client.post(
        "/api/personal/artifacts/propose",
        json={"prompt": "生成需求分析报告，描述 VehicleSpeed_Read 的需求", "artifact_type": "requirement_analysis_report"},
    )

    assert requirement.status_code == 200
    payload = requirement.json()
    assert payload["artifact_type"] == "requirement_analysis_report"
    assert payload["metadata"]["generation"].get("impact") in (None, {})
    assert "Codebase Impact 证据" not in payload["content"]


def test_codebase_search_reports_symbol_and_include_counts_for_multiple_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, _db_path, repo, _test_command = _client_with_code_repo(tmp_path)
    (repo / "diagnostic.c").write_text(
        """
#include "speed.h"

int DiagnosticSpeed_Check(void)
{
    return VehicleSpeed_Read(1);
}
""".lstrip(),
        encoding="utf-8",
    )

    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read DiagnosticSpeed", "max_files": 20})
    assert indexed.status_code == 200
    files = {item["path"]: item for item in indexed.json()["output"]["relevant_files"]}

    assert files["speed.c"]["symbol_count"] >= 2
    assert files["speed.c"]["dependency_count"] == 1
    assert files["diagnostic.c"]["symbol_count"] >= 1
    assert files["diagnostic.c"]["dependency_count"] == 1


def test_code_files_source_preview_schema_migrates_existing_db(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    init_db(db_path)

    with connect(db_path) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(code_files)").fetchall()}

    assert "source_preview" in columns


def test_codebase_index_backfills_empty_source_preview_on_unchanged_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, db_path, _repo, _test_command = _client_with_code_repo(tmp_path)
    with connect(db_path) as conn:
        conn.execute("UPDATE code_files SET source_preview=''")
        before = conn.execute("SELECT COUNT(*) FROM code_files WHERE source_preview=''").fetchone()[0]
    assert before > 0

    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read", "max_files": 20})

    assert indexed.status_code == 200
    assert indexed.json()["output"]["index_run"]["changed_file_count"] > 0
    with connect(db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM code_files WHERE source_preview=''").fetchone()[0]
    assert after == 0


def test_codebase_index_writes_ten_kb_source_preview_and_shared_decode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = tmp_path / "preview_repo"
    repo.mkdir()
    long_text = "int AlphaPreview(void) { return 1; }\n" + ("/* filler */\n" * 1200)
    (repo / "alpha.c").write_text(long_text, encoding="utf-8")
    gbk_text = "typedef int 中文速度类型;\nint ReadChineseSpeed(void) { return 0; }\n"
    (repo / "gbk.c").write_bytes(gbk_text.encode("gbk"))
    client = TestClient(create_personal_app(db_path, workspace))
    saved = client.put("/api/personal/codebase/config", json={"repo_path": str(repo)})
    assert saved.status_code == 200

    indexed = client.post("/api/personal/codebase/index", json={"query": "AlphaPreview 中文速度类型", "max_files": 20})

    assert indexed.status_code == 200
    with connect(db_path) as conn:
        rows = {row["path"]: dict(row) for row in conn.execute("SELECT * FROM code_files").fetchall()}
    assert len(rows["alpha.c"]["source_preview"]) == CODE_FILE_RELEVANCE_CHARS
    assert rows["alpha.c"]["source_preview"] == read_code_text(repo / "alpha.c")[:CODE_FILE_RELEVANCE_CHARS]
    assert rows["gbk.c"]["source_preview"] == read_code_text(repo / "gbk.c")[:CODE_FILE_RELEVANCE_CHARS]


def test_codebase_search_uses_preview_fallback_without_writing_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, db_path, repo, _test_command = _client_with_code_repo(tmp_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM code_files WHERE path='speed.c'").fetchone()
        original_preview = row["source_preview"]
        repo_id = int(row["repository_id"])
    (repo / "speed.c").write_text((repo / "speed.c").read_text(encoding="utf-8") + "\ntypedef int PhaseTwoFallbackType;\n", encoding="utf-8")

    search = client.post("/api/personal/codebase/search", json={"query": "PhaseTwoFallbackType", "limit": 5})

    assert search.status_code == 200
    assert any(item["path"] == "speed.c" for item in search.json()["output"]["file_results"])
    with connect(db_path) as conn:
        after = conn.execute("SELECT source_preview FROM code_files WHERE path='speed.c'").fetchone()["source_preview"]
        files = [dict(item) for item in conn.execute("SELECT * FROM code_files WHERE repository_id=?", (repo_id,)).fetchall()]
    assert after == original_preview
    assert "speed.c" in _files_containing_type(db_path, repo_id, files, "PhaseTwoFallbackType", 5)


def _client_with_code_repo(tmp_path: Path) -> tuple[TestClient, Path, Path, str]:
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = tmp_path / "vehicle_code"
    repo.mkdir()
    (repo / "speed.c").write_text(
        """
#include "speed.h"

static int ClampSpeed(int value)
{
    return value < 0 ? 0 : value;
}

int VehicleSpeed_Read(int valid)
{
    int speed_value = 42;
    if (!valid) {
        return -1;
    }
    return ClampSpeed(speed_value);
}
""".lstrip(),
        encoding="utf-8",
    )
    (repo / "speed.h").write_text(
        """
#ifndef SPEED_H
#define SPEED_H
#define SPEED_INVALID_DEFAULT (-1)
typedef int VehicleSpeedValue;
int VehicleSpeed_Read(int valid);
#endif
""".lstrip(),
        encoding="utf-8",
    )
    (repo / "ok.py").write_text("print('validation ok')\n", encoding="utf-8")
    python_exe = Path(sys.executable).resolve()
    test_command = f"{python_exe} ok.py"

    client = TestClient(create_personal_app(db_path, workspace))
    saved = client.put(
        "/api/personal/codebase/config",
        json={"repo_path": str(repo), "test_command": test_command, "tool_timeout_s": 30},
    )
    assert saved.status_code == 200
    indexed = client.post("/api/personal/codebase/index", json={"query": "VehicleSpeed_Read speed invalid default", "max_files": 20})
    assert indexed.status_code == 200
    assert indexed.json()["output"]["exists"] is True
    return client, db_path, repo, test_command
