from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from personal_agent.core.codebase.index_store import latest_repository
from personal_agent.core.database import connect
from .knowledge_recall import recall_knowledge_for_context
from .knowledge_learning import pending_session_memory_candidates


class PersonalContextBuilder:
    def __init__(self, db_path: Path, project_id: int):
        self.db_path = db_path
        self.project_id = project_id

    def build(self, *, session_uid: str, prompt: str, source_uids: list[str] | None = None) -> dict[str, Any]:
        requested_source_uids = list(dict.fromkeys(uid.strip() for uid in (source_uids or []) if uid.strip()))
        with connect(self.db_path) as conn:
            if requested_source_uids:
                placeholders = ",".join("?" for _ in requested_source_uids)
                rows = conn.execute(
                    f"""
                    SELECT source_uid, title, source_type, plain_text, sections_json, tables_json
                    FROM personal_input_sources
                    WHERE project_id=? AND status='active' AND source_uid IN ({placeholders})
                    """,
                    (self.project_id, *requested_source_uids),
                ).fetchall()
                by_uid = {str(row["source_uid"]): row for row in rows}
                missing = [uid for uid in requested_source_uids if uid not in by_uid]
                if missing:
                    raise ValueError(f"source not found: {missing[0]}")
                sources = [by_uid[uid] for uid in requested_source_uids]
            else:
                sources = conn.execute(
                    """
                    SELECT source_uid, title, source_type, plain_text, sections_json, tables_json
                    FROM personal_input_sources
                    WHERE project_id=? AND status='active' AND is_active=1
                    ORDER BY id DESC
                    """,
                    (self.project_id,),
                ).fetchall()
            draft = conn.execute(
                """
                SELECT draft_uid, document_type, title, current_revision
                FROM personal_drafts
                WHERE project_id=? AND status='active' AND is_active=1
                ORDER BY id DESC LIMIT 1
                """,
                (self.project_id,),
            ).fetchone()
            messages = conn.execute(
                """
                SELECT role, content, metadata_json FROM personal_session_messages
                WHERE session_uid=?
                ORDER BY id DESC LIMIT 12
                """,
                (session_uid,),
            ).fetchall()
        source_payload = [
            {
                "source_uid": row["source_uid"],
                "title": row["title"],
                "source_type": row["source_type"],
                "plain_text": row["plain_text"],
                "sections": _loads_json(row["sections_json"], []),
                "tables": _loads_json(row["tables_json"], []),
            }
            for row in sources
        ]
        recalled = recall_knowledge_for_context(self.db_path, project_id=self.project_id, query=prompt, limit=8)
        session_memories = pending_session_memory_candidates(self.db_path, project_id=self.project_id, session_uid=session_uid)
        return {
            "prompt": prompt,
            "session_uid": session_uid,
            "sources": source_payload,
            "active_source_uids": [item["source_uid"] for item in source_payload],
            "active_draft": dict(draft) if draft else {},
            "recent_messages": [_message_context(row) for row in reversed(messages)],
            "knowledge_refs": [{"item_uid": item["item_uid"], "title": item["title"]} for item in recalled["knowledge"]],
            "memory_refs": [{"item_uid": item["item_uid"], "title": item["title"]} for item in recalled["memories"]],
            "knowledge": recalled["knowledge"],
            "memories": recalled["memories"],
            "pending_memory_candidates": session_memories,
            "code_evidence": latest_repository(self.db_path, self.project_id) or {},
            "requirement_summary": _requirement_summary(source_payload, prompt),
        }


def _requirement_summary(sources: list[dict[str, Any]], prompt: str) -> str:
    if not sources:
        return prompt[:200]
    text = str(sources[0].get("plain_text") or "").replace("\r\n", "\n")
    lines = [line.strip(" \t-#*") for line in text.splitlines() if line.strip()]
    if lines:
        return "；".join(lines[:3])[:500]
    return text[:500]


def _message_context(row: Any) -> dict[str, Any]:
    payload = dict(row)
    payload["metadata"] = _loads_json(payload.pop("metadata_json", "{}"), {})
    return payload


def _loads_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default
