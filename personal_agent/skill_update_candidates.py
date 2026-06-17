from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps, utc_now


def list_skill_update_candidates(db_path: Path, *, project_id: int, status: str = "") -> list[dict[str, Any]]:
    query = "SELECT * FROM personal_skill_update_candidates WHERE project_id=?"
    params: list[Any] = [project_id]
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY id DESC"
    with connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_payload(row) for row in rows]


def create_skill_update_candidate(
    db_path: Path,
    *,
    project_id: int,
    target_skill: str,
    reason: str,
    proposed_change: str,
    risk: str = "",
    evidence_refs: dict[str, Any] | None = None,
    session_uid: str = "",
    source: str = "",
) -> dict[str, Any]:
    now = utc_now()
    candidate_uid = f"skillcand_{uuid4().hex}"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personal_skill_update_candidates(
                candidate_uid, project_id, target_skill, reason, proposed_change, risk,
                evidence_refs_json, status, source, session_uid, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?)
            """,
            (
                candidate_uid,
                project_id,
                target_skill,
                reason,
                proposed_change,
                risk,
                json_dumps(evidence_refs or {}),
                source,
                session_uid,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM personal_skill_update_candidates WHERE candidate_uid=?", (candidate_uid,)).fetchone()
    return _payload(row)


def approve_skill_update_candidate(db_path: Path, *, project_id: int, candidate_id: int, reviewer: str, comment: str = "") -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as conn:
        candidate = conn.execute(
            "SELECT * FROM personal_skill_update_candidates WHERE project_id=? AND id=?",
            (project_id, candidate_id),
        ).fetchone()
        if candidate is None:
            raise ValueError("skill update candidate not found")
        if str(candidate["status"]) == "approved":
            return _payload(candidate)
        if str(candidate["status"]) == "rejected":
            raise ValueError("skill update candidate already rejected")
        version_uid = f"skillver_{uuid4().hex}"
        skill = conn.execute(
            "SELECT * FROM personal_skills WHERE project_id=? AND name=?",
            (project_id, candidate["target_skill"]),
        ).fetchone()
        if skill is None:
            raise ValueError("target skill not found")
        active = conn.execute(
            "SELECT * FROM personal_skill_versions WHERE skill_uid=? ORDER BY version_index DESC LIMIT 1",
            (skill["skill_uid"],),
        ).fetchone()
        next_index = int(active["version_index"] if active is not None else 0) + 1
        base_markdown = str(active["skill_markdown"] or "") if active is not None else ""
        next_markdown = _apply_change(base_markdown, str(candidate["proposed_change"] or ""))
        skill_path = _skill_file_path(active)
        if skill_path:
            path = Path(skill_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(next_markdown, encoding="utf-8")
        conn.execute(
            """
            INSERT INTO personal_skill_versions(
                version_uid, skill_uid, project_id, version_index, skill_markdown,
                metadata_json, status, created_by, created_at, activated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                version_uid,
                skill["skill_uid"],
                project_id,
                next_index,
                next_markdown,
                json_dumps(
                    {
                        "approval_comment": comment,
                        "candidate_id": candidate_id,
                        "change_reason": candidate["reason"],
                        "change_kind": "skill_update_candidate",
                    }
                ),
                reviewer,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE personal_skills SET active_version_uid=?, updated_at=? WHERE skill_uid=?",
            (version_uid, now, skill["skill_uid"]),
        )
        conn.execute(
            "UPDATE personal_skill_update_candidates SET status='approved', reviewed_by=?, reviewed_at=?, review_comment=?, updated_at=? WHERE id=?",
            (reviewer, now, comment, now, candidate_id),
        )
        row = conn.execute("SELECT * FROM personal_skill_update_candidates WHERE id=?", (candidate_id,)).fetchone()
    return _payload(row)


def reject_skill_update_candidate(db_path: Path, *, project_id: int, candidate_id: int, reviewer: str, comment: str = "") -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as conn:
        candidate = conn.execute(
            "SELECT * FROM personal_skill_update_candidates WHERE project_id=? AND id=?",
            (project_id, candidate_id),
        ).fetchone()
        if candidate is None:
            raise ValueError("skill update candidate not found")
        conn.execute(
            "UPDATE personal_skill_update_candidates SET status='rejected', reviewed_by=?, reviewed_at=?, review_comment=?, updated_at=? WHERE id=?",
            (reviewer, now, comment, now, candidate_id),
        )
        row = conn.execute("SELECT * FROM personal_skill_update_candidates WHERE id=?", (candidate_id,)).fetchone()
    return _payload(row)


def record_skill_update_candidate_review(
    db_path: Path,
    *,
    project_id: int,
    candidate_id: int,
    reviewer: str,
    decision: str,
    comment: str = "",
) -> dict[str, Any]:
    if decision == "approve":
        return approve_skill_update_candidate(db_path, project_id=project_id, candidate_id=candidate_id, reviewer=reviewer, comment=comment)
    if decision == "reject":
        return reject_skill_update_candidate(db_path, project_id=project_id, candidate_id=candidate_id, reviewer=reviewer, comment=comment)
    raise ValueError("unsupported decision")


def resolve_latest_candidate(db_path: Path, *, project_id: int, target_skill: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM personal_skill_update_candidates
            WHERE project_id=? AND target_skill=?
            ORDER BY id DESC LIMIT 1
            """,
            (project_id, target_skill),
        ).fetchone()
    return _payload(row) if row is not None else None


def _apply_change(markdown: str, proposed_change: str) -> str:
    if not markdown.strip():
        return proposed_change.strip()
    return markdown.rstrip() + "\n\n" + proposed_change.strip()


def _skill_file_path(version_row: Any) -> str:
    if version_row is None:
        return ""
    metadata = _loads_json(version_row["metadata_json"], {})
    return str(metadata.get("path") or "")


def _payload(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row["id"],
        "candidate_uid": row["candidate_uid"],
        "project_id": row["project_id"],
        "target_skill": row["target_skill"],
        "reason": row["reason"],
        "proposed_change": row["proposed_change"],
        "risk": row["risk"],
        "evidence_refs": _loads_json(row["evidence_refs_json"], {}),
        "status": row["status"],
        "source": row["source"],
        "session_uid": row["session_uid"],
        "reviewed_by": row["reviewed_by"],
        "review_comment": row["review_comment"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "reviewed_at": row["reviewed_at"],
    }


def _loads_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default
