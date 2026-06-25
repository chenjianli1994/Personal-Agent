from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps, utc_now

DEV_TASK_TYPE = "personal_dev_document_pipeline_v3"

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

# Canonical document pipeline order (single source of truth). A document type's
# downstream = everything after it in this tuple. artifact_generation derives its
# predecessor map from this so lineage order is not duplicated across modules.
DOCUMENT_LINEAGE_ORDER = (
    "requirement_analysis_report",
    "requirement_breakdown",
    "functional_spec",
    "detailed_design",
    "test_case_spec",
    "unit_test_code_or_diff",
)


def create_artifact_draft(
    db_path: Path,
    *,
    project_id: int,
    title: str,
    content: str,
    document_type: str = "",
    content_format: str = "markdown",
    source_uid: str = "",
    session_uid: str = "",
    task_uid: str = "",
    derived_from_draft_uid: str = "",
    lineage_stale: bool = False,
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
            _clear_active_scope(conn, project_id=project_id, session_uid=session_uid, document_type=document_type)
        conn.execute(
            """
            INSERT INTO personal_drafts(
                draft_uid, project_id, source_uid, session_uid, task_uid, document_type, title, content_format,
                current_revision, derived_from_draft_uid, lineage_stale, status, is_active, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_uid,
                project_id,
                source_uid.strip(),
                session_uid.strip(),
                task_uid.strip(),
                document_type,
                title,
                content_format,
                derived_from_draft_uid.strip(),
                1 if lineage_stale else 0,
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
        if make_active:
            _update_session_active_draft(conn, session_uid=session_uid, draft_uid=draft_uid)
            # Regenerating an upstream document (new draft_uid, so the derived_from chain
            # can't catch it) must still flag its downstream as stale. Match by document_type
            # within the session rather than by derived_from.
            _mark_session_downstream_stale(conn, project_id=project_id, session_uid=session_uid, document_type=document_type)
    return get_artifact_draft(db_path, project_id=project_id, draft_uid=draft_uid)


def list_artifact_drafts(
    db_path: Path,
    *,
    project_id: int,
    session_uid: str | None = None,
    task_uid: str | None = None,
) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        where = ["d.project_id=?", "d.status IN ('active', 'quality_failed')"]
        params: list[Any] = [project_id]
        if session_uid is not None:
            where.append("d.session_uid=?")
            params.append(session_uid)
        if task_uid is not None:
            where.append("d.task_uid=?")
            params.append(task_uid)
        rows = conn.execute(
            """
            SELECT d.*,
                   r.content AS current_content,
                   (SELECT COUNT(*) FROM personal_draft_revisions rr WHERE rr.draft_uid=d.draft_uid) AS revision_count
            FROM personal_drafts d
            LEFT JOIN personal_draft_revisions r
              ON r.draft_uid=d.draft_uid AND r.revision_index=d.current_revision
            WHERE """
            + " AND ".join(where)
            + """
            ORDER BY d.is_active DESC, d.id DESC
            """,
            params,
        ).fetchall()
        drafts = [_draft_row_to_payload(row, include_content=False) for row in rows]
        _enrich_draft_task_context(conn, drafts=drafts, project_id=project_id)
    return drafts


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
        _enrich_draft_task_context(conn, drafts=[payload], project_id=project_id)
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
            SELECT id, current_revision, session_uid, document_type
            FROM personal_drafts
            WHERE project_id=? AND draft_uid=? AND status IN ('active', 'quality_failed')
            """,
            (project_id, draft_uid),
        ).fetchone()
        if row is None:
            raise ValueError("draft not found")
        draft_session_uid = str(row["session_uid"] or "").strip()
        document_type = str(row["document_type"] or "").strip()
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
            _clear_active_scope(conn, project_id=project_id, session_uid=draft_session_uid, document_type=document_type)
        conn.execute(
            """
            UPDATE personal_drafts
            SET current_revision=?, metadata_json=?, status=COALESCE(?, status),
                lineage_stale=CASE WHEN ? != 'quality_failed' THEN 0 ELSE lineage_stale END,
                is_active=CASE WHEN ? THEN 1 ELSE is_active END, updated_at=?
            WHERE id=?
            """,
            (next_index, json_dumps(metadata or {}), status, status or "", 1 if make_active else 0, now, row["id"]),
        )
        if status != "quality_failed":
            _mark_downstream_lineage_stale(conn, project_id=project_id, draft_uid=draft_uid)
        if make_active:
            _update_session_active_draft(conn, session_uid=draft_session_uid, draft_uid=draft_uid)
    return get_artifact_draft(db_path, project_id=project_id, draft_uid=draft_uid)


def activate_artifact_draft(db_path: Path, *, project_id: int, draft_uid: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, session_uid, document_type
            FROM personal_drafts
            WHERE project_id=? AND draft_uid=? AND status IN ('active', 'quality_failed')
            """,
            (project_id, draft_uid),
        ).fetchone()
        if row is None:
            raise ValueError("draft not found")
        draft_session_uid = str(row["session_uid"] or "").strip()
        document_type = str(row["document_type"] or "").strip()
        _clear_active_scope(conn, project_id=project_id, session_uid=draft_session_uid, document_type=document_type)
        conn.execute(
            "UPDATE personal_drafts SET is_active=1, updated_at=? WHERE id=?",
            (utc_now(), row["id"]),
        )
        _mark_downstream_lineage_stale(conn, project_id=project_id, draft_uid=draft_uid)
        _update_session_active_draft(conn, session_uid=draft_session_uid, draft_uid=draft_uid)
    return get_artifact_draft(db_path, project_id=project_id, draft_uid=draft_uid)


def _clear_active_scope(conn: Any, *, project_id: int, session_uid: str, document_type: str) -> None:
    if not session_uid:
        conn.execute("UPDATE personal_drafts SET is_active=0 WHERE project_id=?", (project_id,))
        return
    conn.execute(
        """
        UPDATE personal_drafts
        SET is_active=0
        WHERE project_id=? AND session_uid=? AND document_type=?
        """,
        (project_id, session_uid, document_type),
    )


def _update_session_active_draft(conn: Any, *, session_uid: str, draft_uid: str) -> None:
    if not session_uid:
        return
    conn.execute(
        """
        UPDATE personal_sessions
        SET active_draft_uid=?, updated_at=?
        WHERE session_uid=? AND status='active'
        """,
        (draft_uid, utc_now(), session_uid),
    )


def _mark_downstream_lineage_stale(conn: Any, *, project_id: int, draft_uid: str) -> None:
    conn.execute(
        """
        UPDATE personal_drafts
        SET lineage_stale=1, updated_at=?
        WHERE project_id=? AND derived_from_draft_uid=? AND status IN ('active', 'quality_failed')
        """,
        (utc_now(), project_id, draft_uid),
    )


def _downstream_document_types(document_type: str) -> list[str]:
    if document_type not in DOCUMENT_LINEAGE_ORDER:
        return []
    index = DOCUMENT_LINEAGE_ORDER.index(document_type)
    return list(DOCUMENT_LINEAGE_ORDER[index + 1 :])


def _mark_session_downstream_stale(conn: Any, *, project_id: int, session_uid: str, document_type: str) -> None:
    downstream = _downstream_document_types(document_type)
    if not session_uid or not downstream:
        return
    placeholders = ",".join("?" for _ in downstream)
    conn.execute(
        f"""
        UPDATE personal_drafts
        SET lineage_stale=1, updated_at=?
        WHERE project_id=? AND session_uid=? AND document_type IN ({placeholders})
          AND status IN ('active', 'quality_failed')
        """,
        (utc_now(), project_id, session_uid, *downstream),
    )


def _draft_row_to_payload(row: Any, *, include_content: bool) -> dict[str, Any]:
    current_content = row["current_content"] or ""
    payload = {
        "id": row["id"],
        "draft_uid": row["draft_uid"],
        "project_id": row["project_id"],
        "source_uid": row["source_uid"],
        "session_uid": row["session_uid"],
        "task_uid": row["task_uid"],
        "document_type": row["document_type"],
        "title": row["title"],
        "content_format": row["content_format"],
        "current_revision": row["current_revision"],
        "revision_count": row["revision_count"] or 0,
        "derived_from_draft_uid": row["derived_from_draft_uid"],
        "lineage_stale": bool(row["lineage_stale"]),
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


def _enrich_draft_task_context(conn: Any, *, drafts: list[dict[str, Any]], project_id: int) -> None:
    for draft in drafts:
        draft.update(_default_draft_task_context())
    task_uids = sorted({str(draft.get("task_uid") or "").strip() for draft in drafts if str(draft.get("task_uid") or "").strip()})
    if not task_uids:
        return
    task_rows = _task_rows_by_uid(conn, project_id=project_id, task_uids=task_uids)
    if not task_rows:
        return
    task_display_metadata = _task_display_metadata(task_rows=task_rows, conn=conn, project_id=project_id)
    effective_stages_by_task = _effective_stages_by_task(conn, project_id=project_id, task_uids=task_uids)
    current_stage_draft_uids = {
        (task_uid, stage["document_type"]): str(stage["draft_uid"] or "")
        for task_uid, stages in effective_stages_by_task.items()
        for stage in stages
        if str(stage.get("draft_uid") or "")
    }
    task_context_by_uid: dict[str, dict[str, Any]] = {}
    for task_uid, row in task_rows.items():
        plan = _loads_json(row["plan_json"], {})
        stages = effective_stages_by_task.get(task_uid, _build_effective_stages([]))
        blocked_reason = str(plan.get("blocked_reason") or row["error_message"] or "")
        task_context_by_uid[task_uid] = {
            "task_display_code": task_display_metadata.get(task_uid, {}).get("display_code", ""),
            "task_session_display_index": task_display_metadata.get(task_uid, {}).get("session_display_index"),
            "task_title": str(row["title"] or ""),
            "task_status": str(row["status"] or ""),
            "task_current_step": str(row["current_step"] or ""),
            "task_next_action": _next_action(stages, blocked_reason),
            "task_display_scope": task_display_metadata.get(task_uid, {}).get("display_scope", ""),
        }
    candidate_metadata = _candidate_metadata_by_draft_uid(conn, project_id=project_id, task_uids=task_uids)
    stage_order_index = {document_type: index for index, document_type in enumerate(_dev_task_stage_order())}
    for draft in drafts:
        task_uid = str(draft.get("task_uid") or "").strip()
        if not task_uid:
            continue
        draft.update(task_context_by_uid.get(task_uid, {}))
        document_type = str(draft.get("document_type") or "").strip()
        draft["stage_index"] = stage_order_index.get(document_type)
        candidate = candidate_metadata.get(str(draft.get("draft_uid") or ""))
        draft["candidate_index"] = candidate["candidate_index"] if candidate else None
        draft["stage_candidate_count"] = candidate["stage_candidate_count"] if candidate else 0
        draft["is_stage_current_candidate"] = current_stage_draft_uids.get((task_uid, document_type), "") == str(draft.get("draft_uid") or "")


def _default_draft_task_context() -> dict[str, Any]:
    return {
        "task_display_code": "",
        "task_session_display_index": None,
        "task_title": "",
        "task_status": "",
        "task_current_step": "",
        "task_next_action": {},
        "task_display_scope": "",
        "stage_index": None,
        "candidate_index": None,
        "stage_candidate_count": 0,
        "is_stage_current_candidate": False,
    }


def _task_rows_by_uid(conn: Any, *, project_id: int, task_uids: list[str]) -> dict[str, Any]:
    placeholders = ",".join("?" for _ in task_uids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM agent_tasks
        WHERE project_id=? AND task_type=? AND task_uid IN ({placeholders})
        """,
        (project_id, DEV_TASK_TYPE, *task_uids),
    ).fetchall()
    return {str(row["task_uid"] or "").strip(): row for row in rows if str(row["task_uid"] or "").strip()}


def _task_display_metadata(*, task_rows: dict[str, Any], conn: Any, project_id: int) -> dict[str, dict[str, Any]]:
    metadata_by_uid: dict[str, dict[str, Any]] = {}
    session_uids = {str(row["session_uid"] or "").strip() for row in task_rows.values() if str(row["session_uid"] or "").strip()}
    for session_uid in session_uids:
        indexes = _task_display_indexes(conn, project_id=project_id, session_uid=session_uid)
        for task_uid, index in indexes.items():
            if task_uid not in task_rows:
                continue
            metadata_by_uid[task_uid] = {
                "display_code": f"T{index}" if index else "",
                "session_display_index": index,
                "display_scope": "session",
            }
    if any(not str(row["session_uid"] or "").strip() for row in task_rows.values()):
        indexes = _task_display_indexes(conn, project_id=project_id, session_uid="")
        for task_uid, index in indexes.items():
            if task_uid not in task_rows or task_uid in metadata_by_uid:
                continue
            metadata_by_uid[task_uid] = {
                "display_code": f"T{index}" if index else "",
                "session_display_index": index,
                "display_scope": "project_fallback",
            }
    return metadata_by_uid


def _task_display_indexes(conn: Any, *, project_id: int, session_uid: str) -> dict[str, int]:
    if session_uid.strip():
        rows = conn.execute(
            """
            SELECT task_uid
            FROM agent_tasks
            WHERE project_id=? AND session_uid=? AND task_type=?
            ORDER BY id ASC
            """,
            (project_id, session_uid.strip(), DEV_TASK_TYPE),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT task_uid
            FROM agent_tasks
            WHERE project_id=? AND task_type=?
            ORDER BY id ASC
            """,
            (project_id, DEV_TASK_TYPE),
        ).fetchall()
    return {str(row["task_uid"] or "").strip(): index + 1 for index, row in enumerate(rows) if str(row["task_uid"] or "").strip()}


def _candidate_metadata_by_draft_uid(conn: Any, *, project_id: int, task_uids: list[str]) -> dict[str, dict[str, int]]:
    placeholders = ",".join("?" for _ in task_uids)
    rows = conn.execute(
        f"""
        SELECT id, draft_uid, task_uid, document_type
        FROM personal_drafts
        WHERE project_id=? AND task_uid IN ({placeholders}) AND status IN ('active', 'quality_failed')
        ORDER BY id ASC
        """,
        (project_id, *task_uids),
    ).fetchall()
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        task_uid = str(row["task_uid"] or "").strip()
        document_type = str(row["document_type"] or "").strip()
        draft_uid = str(row["draft_uid"] or "").strip()
        if not task_uid or not draft_uid:
            continue
        grouped.setdefault((task_uid, document_type), []).append(draft_uid)
    metadata_by_uid: dict[str, dict[str, int]] = {}
    for draft_uids in grouped.values():
        total = len(draft_uids)
        for index, draft_uid in enumerate(draft_uids, start=1):
            metadata_by_uid[draft_uid] = {"candidate_index": index, "stage_candidate_count": total}
    return metadata_by_uid


def _effective_stages_by_task(conn: Any, *, project_id: int, task_uids: list[str]) -> dict[str, list[dict[str, Any]]]:
    placeholders = ",".join("?" for _ in task_uids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM personal_drafts
        WHERE project_id=? AND task_uid IN ({placeholders}) AND status IN ('active', 'quality_failed')
        ORDER BY task_uid ASC, is_active DESC, id DESC
        """,
        (project_id, *task_uids),
    ).fetchall()
    grouped_rows: dict[str, list[Any]] = {task_uid: [] for task_uid in task_uids}
    for row in rows:
        grouped_rows.setdefault(str(row["task_uid"] or "").strip(), []).append(row)
    return {task_uid: _build_effective_stages(grouped_rows.get(task_uid, [])) for task_uid in task_uids}


def _build_effective_stages(rows: list[Any]) -> list[dict[str, Any]]:
    latest_by_type: dict[str, Any] = {}
    for row in rows:
        latest_by_type.setdefault(str(row["document_type"]), row)
    stages: list[dict[str, Any]] = []
    for index, document_type in enumerate(_dev_task_stage_order()):
        draft = latest_by_type.get(document_type)
        if draft is None:
            status = "pending"
            draft_uid = ""
            lineage_stale = False
            draft_status = ""
        else:
            draft_uid = str(draft["draft_uid"])
            draft_status = str(draft["status"])
            lineage_stale = bool(draft["lineage_stale"])
            status = "needs_revision" if draft_status == "quality_failed" or lineage_stale else "done"
        stages.append(
            {
                "index": index,
                "document_type": document_type,
                "effective_status": status,
                "draft_uid": draft_uid,
                "draft_status": draft_status,
                "lineage_stale": lineage_stale,
            }
        )
    return stages


def _next_action(stages: list[dict[str, Any]], blocked_reason: str) -> dict[str, Any]:
    for stage in stages:
        if stage["effective_status"] == "needs_revision":
            return {"action": "revise_draft", "stage": stage["document_type"], "reason": blocked_reason or "stage needs revision"}
    for stage in stages:
        if stage["effective_status"] == "pending":
            return {"action": "continue", "stage": stage["document_type"], "reason": blocked_reason}
    return {"action": "completed", "stage": "", "reason": ""}


def _dev_task_stage_order() -> list[str]:
    return list(DOCUMENT_LINEAGE_ORDER[: DOCUMENT_LINEAGE_ORDER.index("test_case_spec") + 1])


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
