from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

from personal_agent.core.database import connect
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
    assert draft["content_format"] == "diff"
    assert "return 0;" in draft["content"]
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


def test_personal_phase5_unit_test_code_draft_and_validation_allowlist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    client, _db_path, _repo, test_command = _client_with_code_repo(tmp_path)

    draft = client.post("/api/personal/artifacts/unit-test-code", json={"prompt": "为 VehicleSpeed_Read 生成单元测试代码"})
    assert draft.status_code == 200
    payload = draft.json()
    assert payload["artifact_type"] == "unit_test_code_or_diff"
    assert payload["content_format"] == "diff"
    assert "test_normal_path" in payload["content"]
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
