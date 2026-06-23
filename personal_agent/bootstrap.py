from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personal_agent.content_guard import RETIRED_PROJECT_INPUT_KEYS, personal_forbidden_hits
from personal_agent.core.collaboration import ensure_collaboration_seed
from personal_agent.core.database import connect, init_db
from personal_agent.core.services_min import bootstrap_knowledge, create_project

from .capabilities import PersonalAgentCapabilities, load_personal_capabilities
from .skill_registry import ensure_default_document_skills


DEFAULT_PROJECT_CODE = "PERSONAL_AGENT"
DEFAULT_PROJECT_NAME = "本地个人助手"
DEFAULT_WORKSPACE_UID = "local"

PERSONAL_ENV_KEYS = {
    "PERSONAL_AGENT_LLM_PROVIDER",
    "PERSONAL_AGENT_LLM_MODEL",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MIMO_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
}

RETIRED_PERSONAL_LLM_PROVIDERS = {"openrouter", "xai"}


@dataclass(frozen=True)
class PersonalAgentContext:
    db_path: Path
    workspace: Path
    env_path: Path
    capabilities: PersonalAgentCapabilities
    project_id: int
    project_code: str
    project_name: str
    workspace_uid: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_path": str(self.db_path),
            "workspace": str(self.workspace),
            "env_path": str(self.env_path),
            "capabilities": self.capabilities.to_dict(),
            "project_id": self.project_id,
            "project_code": self.project_code,
            "project_name": self.project_name,
            "workspace_uid": self.workspace_uid,
        }


def load_personal_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in PERSONAL_ENV_KEYS:
            clean_value = value.strip().strip('"')
            if key == "PERSONAL_AGENT_LLM_PROVIDER" and clean_value.lower() in RETIRED_PERSONAL_LLM_PROVIDERS:
                continue
            os.environ.setdefault(key, clean_value)


def bootstrap_personal_agent(
    db_path: Path,
    workspace: Path,
    *,
    project_code: str = DEFAULT_PROJECT_CODE,
    project_name: str = DEFAULT_PROJECT_NAME,
    workspace_uid: str = DEFAULT_WORKSPACE_UID,
) -> PersonalAgentContext:
    db_path = db_path.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    env_path = workspace / ".env"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _backup_legacy_personal_db(db_path)
    capabilities = load_personal_capabilities(workspace)
    init_db(db_path)
    project = create_project(db_path, project_code, project_name, "本机单人 Agent 工作区")
    project_id = int(project["id"])
    _cleanup_legacy_records(db_path, project_id)
    ensure_default_document_skills(db_path, workspace=workspace, project_id=project_id)
    ensure_collaboration_seed(db_path)
    knowledge_root = workspace / "knowledge"
    if knowledge_root.exists():
        bootstrap_knowledge(db_path, knowledge_root, project_id=project_id)
        _cleanup_legacy_records(db_path, project_id)
    return PersonalAgentContext(
        db_path=db_path,
        workspace=workspace,
        env_path=env_path,
        capabilities=capabilities,
        project_id=project_id,
        project_code=project_code,
        project_name=project_name,
        workspace_uid=workspace_uid,
    )


def _backup_legacy_personal_db(db_path: Path) -> None:
    if not db_path.exists():
        return
    with connect(db_path) as conn:
        marker = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='personal_sessions'"
        ).fetchone()
    if marker:
        return
    backup = db_path.with_name(f"{db_path.stem}.pre_personal_v2{db_path.suffix}")
    index = 1
    while backup.exists():
        backup = db_path.with_name(f"{db_path.stem}.pre_personal_v2.{index}{db_path.suffix}")
        index += 1
    try:
        shutil.copy2(db_path, backup)
    except PermissionError:
        # Backup is best effort. Startup must never fail just because a dev
        # backend, browser, or SQLite handle is still holding the file.
        return


def _cleanup_legacy_records(db_path: Path, project_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            DELETE FROM project_inputs
            WHERE project_id=? AND input_key IN (?, ?, ?, ?)
            """,
            (project_id, *RETIRED_PROJECT_INPUT_KEYS),
        )
        polluted_item_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id, title, source_ref, content FROM knowledge_items").fetchall()
            if _row_has_forbidden_text(row, ("title", "source_ref", "content"))
        ]
        polluted_doc_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id, title, source_ref, source_title, source_uri, summary FROM knowledge_documents").fetchall()
            if _row_has_forbidden_text(row, ("title", "source_ref", "source_title", "source_uri", "summary"))
        ]
        if polluted_doc_ids:
            placeholders = ",".join("?" for _ in polluted_doc_ids)
            conn.execute(
                f"DELETE FROM knowledge_chunks WHERE document_id IN ({placeholders})",
                polluted_doc_ids,
            )
            entry_ids = [
                int(row["id"])
                for row in conn.execute(
                    f"SELECT id FROM knowledge_search_entries WHERE document_id IN ({placeholders})",
                    polluted_doc_ids,
                ).fetchall()
            ]
            if entry_ids:
                entry_placeholders = ",".join("?" for _ in entry_ids)
                conn.execute(f"DELETE FROM knowledge_search_fts WHERE rowid IN ({entry_placeholders})", entry_ids)
            conn.execute(
                f"DELETE FROM knowledge_search_entries WHERE document_id IN ({placeholders})",
                polluted_doc_ids,
            )
            conn.execute(f"DELETE FROM knowledge_documents WHERE id IN ({placeholders})", polluted_doc_ids)
        if polluted_item_ids:
            placeholders = ",".join("?" for _ in polluted_item_ids)
            entry_ids = [
                int(row["id"])
                for row in conn.execute(
                    f"SELECT id FROM knowledge_search_entries WHERE source_kind='item' AND source_id IN ({placeholders})",
                    polluted_item_ids,
                ).fetchall()
            ]
            if entry_ids:
                entry_placeholders = ",".join("?" for _ in entry_ids)
                conn.execute(f"DELETE FROM knowledge_search_fts WHERE rowid IN ({entry_placeholders})", entry_ids)
            conn.execute(
                f"DELETE FROM knowledge_search_entries WHERE source_kind='item' AND source_id IN ({placeholders})",
                polluted_item_ids,
            )
            conn.execute(f"DELETE FROM knowledge_items WHERE id IN ({placeholders})", polluted_item_ids)


def _row_has_forbidden_text(row: Any, fields: tuple[str, ...]) -> bool:
    for field in fields:
        value = str(row[field] or "")
        if personal_forbidden_hits(value):
            return True
    return False
