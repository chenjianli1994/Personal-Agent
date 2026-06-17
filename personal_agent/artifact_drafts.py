from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps, utc_now


DOCUMENT_TYPES = {
    "requirement_analysis_report",
    "requirement_breakdown",
    "functional_spec",
    "detailed_design",
    "c_code_diff",
    "test_case_spec",
    "unit_test_code_or_diff",
}

CONTENT_FORMATS = {"markdown", "json_table", "diff", "text"}


def create_artifact_draft(
    db_path: Path,
    *,
    project_id: int,
    title: str,
    content: str,
    document_type: str = "",
    content_format: str = "markdown",
    source_uid: str = "",
    metadata: dict[str, Any] | None = None,
    make_active: bool = True,
    status: str = "active",
) -> dict[str, Any]:
    document_type = document_type.strip()
    content_format = content_format.strip() or "markdown"
    title = title.strip()
    content = content.strip()
    status = status.strip() or "active"
    if document_type not in DOCUMENT_TYPES:
        raise ValueError(f"unsupported document_type: {document_type}")
    if content_format not in CONTENT_FORMATS:
        raise ValueError(f"unsupported content_format: {content_format}")
    if status not in {"active", "quality_failed"}:
        raise ValueError(f"unsupported draft status: {status}")
    if not title:
        raise ValueError("title is required")
    if not content:
        raise ValueError("content is required")
    draft_uid = f"draft_{uuid4().hex}"
    revision_uid = f"rev_{uuid4().hex}"
    now = utc_now()
    with connect(db_path) as conn:
        if make_active:
            conn.execute("UPDATE personal_drafts SET is_active=0 WHERE project_id=?", (project_id,))
        conn.execute(
            """
            INSERT INTO personal_drafts(
                draft_uid, project_id, source_uid, document_type, title, content_format,
                current_revision, status, is_active, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                draft_uid,
                project_id,
                source_uid.strip(),
                document_type,
                title,
                content_format,
                status,
                1 if make_active else 0,
                json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO personal_draft_revisions(
                revision_uid, draft_uid, project_id, revision_index, content, metadata_json, created_at
            )
            VALUES (?, ?, ?, 1, ?, ?, ?)
            """,
            (revision_uid, draft_uid, project_id, content, json_dumps(metadata or {}), now),
        )
    return get_artifact_draft(db_path, project_id=project_id, draft_uid=draft_uid)


def list_artifact_drafts(db_path: Path, *, project_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.*,
                   r.content AS current_content,
                   (SELECT COUNT(*) FROM personal_draft_revisions rr WHERE rr.draft_uid=d.draft_uid) AS revision_count
            FROM personal_drafts d
            LEFT JOIN personal_draft_revisions r
              ON r.draft_uid=d.draft_uid AND r.revision_index=d.current_revision
            WHERE d.project_id=? AND d.status IN ('active', 'quality_failed')
            ORDER BY d.is_active DESC, d.id DESC
            """,
            (project_id,),
        ).fetchall()
    return [_draft_row_to_payload(row, include_content=False) for row in rows]


def get_artifact_draft(db_path: Path, *, project_id: int, draft_uid: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT d.*,
                   r.content AS current_content,
                   (SELECT COUNT(*) FROM personal_draft_revisions rr WHERE rr.draft_uid=d.draft_uid) AS revision_count
            FROM personal_drafts d
            LEFT JOIN personal_draft_revisions r
              ON r.draft_uid=d.draft_uid AND r.revision_index=d.current_revision
            WHERE d.project_id=? AND d.draft_uid=? AND d.status IN ('active', 'quality_failed')
            """,
            (project_id, draft_uid),
        ).fetchone()
        if row is None:
            raise ValueError("draft not found")
        revisions = conn.execute(
            """
            SELECT * FROM personal_draft_revisions
            WHERE project_id=? AND draft_uid=?
            ORDER BY revision_index
            """,
            (project_id, draft_uid),
        ).fetchall()
    payload = _draft_row_to_payload(row, include_content=True)
    payload["revisions"] = [_revision_row_to_payload(item) for item in revisions]
    return payload


def get_artifact_content(
    db_path: Path,
    *,
    project_id: int,
    draft_uid: str,
    revision_index: int | None = None,
) -> dict[str, Any]:
    draft = get_artifact_draft(db_path, project_id=project_id, draft_uid=draft_uid)
    target_revision = revision_index or int(draft["current_revision"])
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM personal_draft_revisions
            WHERE project_id=? AND draft_uid=? AND revision_index=?
            """,
            (project_id, draft_uid, target_revision),
        ).fetchone()
    if row is None:
        raise ValueError("revision not found")
    revision = _revision_row_to_payload(row)
    return {
        "draft_uid": draft_uid,
        "title": draft["title"],
        "document_type": draft["document_type"],
        "content_format": draft["content_format"],
        "current_revision": draft["current_revision"],
        "revision": revision,
        "content": revision["content"],
    }


def revise_artifact_draft_manual(
    db_path: Path,
    *,
    project_id: int,
    draft_uid: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    make_active: bool = True,
    status: str | None = None,
) -> dict[str, Any]:
    content = content.strip()
    status = status.strip() if isinstance(status, str) else None
    if not content:
        raise ValueError("content is required")
    if status is not None and status not in {"active", "quality_failed"}:
        raise ValueError(f"unsupported draft status: {status}")
    now = utc_now()
    revision_uid = f"rev_{uuid4().hex}"
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, current_revision FROM personal_drafts
            WHERE project_id=? AND draft_uid=? AND status IN ('active', 'quality_failed')
            """,
            (project_id, draft_uid),
        ).fetchone()
        if row is None:
            raise ValueError("draft not found")
        next_index = int(row["current_revision"]) + 1
        conn.execute(
            """
            INSERT INTO personal_draft_revisions(
                revision_uid, draft_uid, project_id, revision_index, content, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (revision_uid, draft_uid, project_id, next_index, content, json_dumps(metadata or {}), now),
        )
        if make_active:
            conn.execute("UPDATE personal_drafts SET is_active=0 WHERE project_id=?", (project_id,))
        conn.execute(
            """
            UPDATE personal_drafts
            SET current_revision=?, metadata_json=?, status=COALESCE(?, status),
                is_active=CASE WHEN ? THEN 1 ELSE is_active END, updated_at=?
            WHERE id=?
            """,
            (next_index, json_dumps(metadata or {}), status, 1 if make_active else 0, now, row["id"]),
        )
    return get_artifact_draft(db_path, project_id=project_id, draft_uid=draft_uid)


def activate_artifact_draft(db_path: Path, *, project_id: int, draft_uid: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM personal_drafts WHERE project_id=? AND draft_uid=? AND status IN ('active', 'quality_failed')",
            (project_id, draft_uid),
        ).fetchone()
        if row is None:
            raise ValueError("draft not found")
        conn.execute("UPDATE personal_drafts SET is_active=0 WHERE project_id=?", (project_id,))
        conn.execute(
            "UPDATE personal_drafts SET is_active=1, updated_at=? WHERE id=?",
            (utc_now(), row["id"]),
        )
    return get_artifact_draft(db_path, project_id=project_id, draft_uid=draft_uid)


def _draft_row_to_payload(row: Any, *, include_content: bool) -> dict[str, Any]:
    current_content = row["current_content"] or ""
    payload = {
        "id": row["id"],
        "draft_uid": row["draft_uid"],
        "project_id": row["project_id"],
        "source_uid": row["source_uid"],
        "document_type": row["document_type"],
        "title": row["title"],
        "content_format": row["content_format"],
        "current_revision": row["current_revision"],
        "revision_count": row["revision_count"] or 0,
        "status": row["status"],
        "is_active": bool(row["is_active"]),
        "metadata": _loads_json(row["metadata_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "preview": current_content[:500],
    }
    if include_content:
        payload["content"] = current_content
    return payload


def _revision_row_to_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "revision_uid": row["revision_uid"],
        "draft_uid": row["draft_uid"],
        "project_id": row["project_id"],
        "revision_index": row["revision_index"],
        "content": row["content"],
        "metadata": _loads_json(row["metadata_json"], {}),
        "created_at": row["created_at"],
        "preview": str(row["content"] or "")[:500],
    }


def _loads_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default
