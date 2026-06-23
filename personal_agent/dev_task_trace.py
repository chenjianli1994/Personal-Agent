from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps, utc_now


TRACE_LINK_TYPES = {
    "source_to_requirement",
    "requirement_to_draft",
    "requirement_to_code_file",
    "requirement_to_code_symbol",
    "requirement_to_test_case",
    "requirement_to_patch_candidate",
    "requirement_to_validation",
}

SYSTEM_TRACE_MANAGER = "dev_task_trace_rebuild"


class DevTaskTraceService:
    def __init__(self, db_path: Path, *, project_id: int):
        self.db_path = db_path
        self.project_id = project_id

    def rebuild_for_task(self, task_uid: str) -> dict[str, Any]:
        self._task_row(task_uid)
        source_draft = self._source_requirement_draft(task_uid)
        if source_draft is None:
            return self._trace_payload(task_uid)

        requirements = self._extract_and_upsert_requirements(task_uid=task_uid, draft=source_draft)
        active_requirement_ids = {item["requirement_id"] for item in requirements}
        now = utc_now()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE trace_links
                SET status='stale', metadata_json=?, source_agent_run_id=('stale:' || id || ':' || ?)
                WHERE project_id=? AND task_uid=? AND managed_by=? AND status='active'
                """,
                (
                    json_dumps({"staled_at": now, "reason": "rebuild"}),
                    now,
                    self.project_id,
                    task_uid,
                    SYSTEM_TRACE_MANAGER,
                ),
            )
        self._write_requirement_traces(task_uid=task_uid, source_draft=source_draft, requirements=requirements)
        self._write_draft_traces(task_uid=task_uid, requirements=requirements)
        self._write_validation_traces(task_uid=task_uid, active_requirement_ids=active_requirement_ids)
        return self._trace_payload(task_uid)

    def trace_for_task(self, task_uid: str) -> dict[str, Any]:
        self._task_row(task_uid)
        return self._trace_payload(task_uid)

    def requirement_summaries(self, task_uid: str) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM requirements
                WHERE project_id=? AND task_uid=?
                ORDER BY id
                """,
                (self.project_id, task_uid),
            ).fetchall()
        return [self._requirement_payload(row) for row in rows]

    def active_requirement_id_for_task(self, task_uid: str) -> str:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT requirement_id
                FROM requirements
                WHERE project_id=? AND task_uid=? AND deprecated_at='' AND status IN ('active', 'candidate')
                ORDER BY id LIMIT 1
                """,
                (self.project_id, task_uid),
            ).fetchone()
        return str(row["requirement_id"] or "") if row else ""

    def write_patch_candidate_trace(self, *, task_uid: str, requirement_id: str, draft_uid: str) -> None:
        self._upsert_trace(
            task_uid=task_uid,
            requirement_id=requirement_id,
            link_type="requirement_to_patch_candidate",
            target_ref=f"patch:{draft_uid}",
            metadata={"draft_uid": draft_uid},
            confidence=1.0,
        )

    def _task_row(self, task_uid: str) -> Any:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM agent_tasks WHERE project_id=? AND task_uid=?",
                (self.project_id, task_uid),
            ).fetchone()
        if row is None:
            raise ValueError("dev task not found")
        return row

    def _source_requirement_draft(self, task_uid: str) -> Any:
        with connect(self.db_path) as conn:
            return conn.execute(
                """
                SELECT d.*, r.content AS current_content
                FROM personal_drafts d
                JOIN personal_draft_revisions r
                  ON r.draft_uid=d.draft_uid AND r.revision_index=d.current_revision
                WHERE d.project_id=? AND d.task_uid=? AND d.document_type='requirement_analysis_report'
                  AND d.status IN ('active', 'quality_failed')
                ORDER BY d.is_active DESC, d.updated_at DESC, d.id DESC
                LIMIT 1
                """,
                (self.project_id, task_uid),
            ).fetchone()

    def _extract_and_upsert_requirements(self, *, task_uid: str, draft: Any) -> list[dict[str, Any]]:
        parsed = _parse_requirements_from_markdown(str(draft["current_content"] or ""))
        if not parsed:
            parsed = [{"title": str(draft["title"] or "Task Requirement"), "description": str(draft["current_content"] or "").strip(), "external_id": ""}]
        with connect(self.db_path) as conn:
            existing_rows = conn.execute(
                """
                SELECT *
                FROM requirements
                WHERE project_id=? AND task_uid=?
                ORDER BY id
                """,
                (self.project_id, task_uid),
            ).fetchall()
            existing = [self._requirement_payload(row) for row in existing_rows]
            matched = self._match_existing_requirements(task_uid=task_uid, parsed=parsed, existing=existing)
            now = utc_now()
            results: list[dict[str, Any]] = []
            matched_existing_ids: set[int] = set()
            for index, item in enumerate(parsed, start=1):
                current = matched[index - 1] if index - 1 < len(matched) else None
                anchor = _anchor_fingerprint(item["title"], item["description"])
                metadata = {
                    "external_id": item["external_id"],
                    "ordinal": index,
                    "title": item["title"],
                    "managed_by": SYSTEM_TRACE_MANAGER,
                }
                if current is None:
                    requirement_id = self._next_requirement_id(conn, task_uid)
                    conn.execute(
                        """
                        INSERT INTO requirements(
                            project_id, task_uid, source_draft_uid, requirement_id, title, description,
                            anchor_fingerprint, metadata_json, status, deprecated_at, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', '', ?, ?)
                        """,
                        (
                            self.project_id,
                            task_uid,
                            str(draft["draft_uid"] or ""),
                            requirement_id,
                            item["title"],
                            item["description"],
                            anchor,
                            json_dumps(metadata),
                            now,
                            now,
                        ),
                    )
                    row = conn.execute(
                        "SELECT * FROM requirements WHERE project_id=? AND requirement_id=?",
                        (self.project_id, requirement_id),
                    ).fetchone()
                else:
                    matched_existing_ids.add(int(current["id"]))
                    conn.execute(
                        """
                        UPDATE requirements
                        SET source_draft_uid=?, title=?, description=?, anchor_fingerprint=?,
                            metadata_json=?, status='active', deprecated_at='', updated_at=?
                        WHERE id=?
                        """,
                        (
                            str(draft["draft_uid"] or ""),
                            item["title"],
                            item["description"],
                            anchor,
                            json_dumps(metadata),
                            now,
                            int(current["id"]),
                        ),
                    )
                    row = conn.execute("SELECT * FROM requirements WHERE id=?", (int(current["id"]),)).fetchone()
                results.append(self._requirement_payload(row))
            for item in existing:
                if int(item["id"]) in matched_existing_ids:
                    continue
                if item["deprecated_at"]:
                    continue
                conn.execute(
                    """
                    UPDATE requirements
                    SET deprecated_at=?, status='deprecated', updated_at=?
                    WHERE id=?
                    """,
                    (now, now, int(item["id"])),
                )
        return self.requirement_summaries(task_uid)

    def _match_existing_requirements(
        self,
        *,
        task_uid: str,
        parsed: list[dict[str, str]],
        existing: list[dict[str, Any]],
    ) -> list[dict[str, Any] | None]:
        available = [item for item in existing if not item["deprecated_at"]]
        result: list[dict[str, Any] | None] = [None] * len(parsed)
        for idx, item in enumerate(parsed):
            external_id = item["external_id"]
            if not external_id:
                continue
            for candidate in list(available):
                metadata = candidate.get("metadata") or {}
                if str(metadata.get("external_id") or "") == external_id:
                    result[idx] = candidate
                    available.remove(candidate)
                    break
        for idx, item in enumerate(parsed):
            if result[idx] is not None:
                continue
            anchor = _anchor_fingerprint(item["title"], item["description"])
            for candidate in list(available):
                if str(candidate.get("anchor_fingerprint") or "") == anchor:
                    result[idx] = candidate
                    available.remove(candidate)
                    break
        for idx, item in enumerate(parsed):
            if result[idx] is not None:
                continue
            for candidate in list(available):
                metadata = candidate.get("metadata") or {}
                if int(metadata.get("ordinal") or 0) == idx + 1:
                    result[idx] = candidate
                    available.remove(candidate)
                    break
        return result

    def _next_requirement_id(self, conn: Any, task_uid: str) -> str:
        prefix = f"REQ-{_task_short_id(task_uid)}-"
        row = conn.execute(
            """
            SELECT requirement_id
            FROM requirements
            WHERE project_id=? AND requirement_id LIKE ?
            ORDER BY requirement_id DESC LIMIT 1
            """,
            (self.project_id, f"{prefix}%"),
        ).fetchone()
        next_index = 1
        if row is not None:
            current = str(row["requirement_id"] or "")
            try:
                next_index = int(current.rsplit("-", 1)[-1]) + 1
            except Exception:
                next_index = 1
        return f"{prefix}{next_index:03d}"

    def _write_requirement_traces(self, *, task_uid: str, source_draft: Any, requirements: list[dict[str, Any]]) -> None:
        source_uid = str(source_draft["source_uid"] or "")
        for requirement in requirements:
            if requirement["deprecated_at"]:
                continue
            self._upsert_trace(
                task_uid=task_uid,
                requirement_id=requirement["requirement_id"],
                link_type="source_to_requirement",
                target_ref=f"source:{source_uid}",
                metadata={
                    "source_uid": source_uid,
                    "draft_uid": str(source_draft["draft_uid"] or ""),
                    "source_draft_uid": str(source_draft["draft_uid"] or ""),
                },
                confidence=1.0,
            )

    def _write_draft_traces(self, *, task_uid: str, requirements: list[dict[str, Any]]) -> None:
        active_requirements = [item for item in requirements if not item["deprecated_at"]]
        if not active_requirements:
            return
        with connect(self.db_path) as conn:
            drafts = conn.execute(
                """
                SELECT d.*, r.content AS current_content
                FROM personal_drafts d
                JOIN personal_draft_revisions r
                  ON r.draft_uid=d.draft_uid AND r.revision_index=d.current_revision
                WHERE d.project_id=? AND d.task_uid=? AND d.status IN ('active', 'quality_failed')
                ORDER BY d.id
                """,
                (self.project_id, task_uid),
            ).fetchall()
        for draft in drafts:
            current_content = str(draft["current_content"] or "")
            for requirement in active_requirements:
                if self._draft_matches_requirement(current_content=current_content, requirement=requirement):
                    self._upsert_trace(
                        task_uid=task_uid,
                        requirement_id=requirement["requirement_id"],
                        link_type="requirement_to_draft",
                        target_ref=f"draft:{draft['draft_uid']}",
                        metadata={"document_type": str(draft["document_type"] or ""), "draft_uid": str(draft["draft_uid"] or "")},
                        confidence=0.9,
                    )

    def _draft_matches_requirement(self, *, current_content: str, requirement: dict[str, Any]) -> bool:
        metadata = requirement.get("metadata") or {}
        external_id = str(metadata.get("external_id") or "")
        title = str(requirement.get("title") or "")
        description = str(requirement.get("description") or "")
        normalized_content = _normalize_text(current_content)
        if external_id and external_id.lower() in current_content.lower():
            return True
        title_tokens = [token for token in re.split(r"[\s,，。:：;；()（）/-]+", title) if len(token) >= 2]
        if any(token.lower() in normalized_content for token in [_normalize_text(token) for token in title_tokens]):
            return True
        description_tokens = [token for token in re.split(r"[\s,，。:：;；()（）/-]+", description) if len(token) >= 4]
        return any(_normalize_text(token) in normalized_content for token in description_tokens[:3])

    def _write_validation_traces(self, *, task_uid: str, active_requirement_ids: set[str]) -> None:
        if not active_requirement_ids:
            return
        with connect(self.db_path) as conn:
            invocations = conn.execute(
                """
                SELECT invocation_uid, tool_name, output_json, requirement_id
                FROM personal_tool_invocations
                WHERE project_id=? AND task_uid=?
                ORDER BY id
                """,
                (self.project_id, task_uid),
            ).fetchall()
        for invocation in invocations:
            tool_name = str(invocation["tool_name"] or "")
            if tool_name not in {"run_build", "run_tests", "run_static_analysis"}:
                continue
            explicit_requirement_id = str(invocation["requirement_id"] or "")
            target_requirements = (
                [explicit_requirement_id]
                if explicit_requirement_id and explicit_requirement_id in active_requirement_ids
                else sorted(active_requirement_ids)
            )
            for requirement_id in target_requirements:
                self._upsert_trace(
                    task_uid=task_uid,
                    requirement_id=requirement_id,
                    link_type="requirement_to_validation",
                    target_ref=f"validation:{invocation['invocation_uid']}",
                    metadata={"tool_name": tool_name, "invocation_uid": str(invocation["invocation_uid"] or "")},
                    confidence=0.8,
                )

    def _upsert_trace(
        self,
        *,
        task_uid: str,
        requirement_id: str,
        link_type: str,
        target_ref: str,
        metadata: dict[str, Any],
        confidence: float,
    ) -> None:
        if link_type not in TRACE_LINK_TYPES:
            raise ValueError(f"unsupported trace link type: {link_type}")
        now = utc_now()
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id
                FROM trace_links
                WHERE project_id=? AND requirement_id=? AND link_type=? AND target_ref=? AND source_agent_run_id=''
                """,
                (self.project_id, requirement_id, link_type, target_ref),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO trace_links(
                        project_id, task_uid, requirement_id, link_type, target_ref,
                        metadata_json, status, confidence, managed_by, source_agent_run_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, '', ?)
                    """,
                    (
                        self.project_id,
                        task_uid,
                        requirement_id,
                        link_type,
                        target_ref,
                        json_dumps(metadata),
                        confidence,
                        SYSTEM_TRACE_MANAGER,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE trace_links
                    SET task_uid=?, metadata_json=?, status='active', confidence=?, managed_by=?
                    WHERE id=?
                    """,
                    (task_uid, json_dumps(metadata), confidence, SYSTEM_TRACE_MANAGER, int(row["id"])),
                )

    def _trace_payload(self, task_uid: str) -> dict[str, Any]:
        requirements = self.requirement_summaries(task_uid)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM trace_links
                WHERE project_id=? AND task_uid=?
                ORDER BY id
                """,
                (self.project_id, task_uid),
            ).fetchall()
        traces = [self._trace_row_payload(row) for row in rows]
        active_traces = [item for item in traces if item["status"] == "active"]
        summary = {
            "total": len(traces),
            "active": len(active_traces),
            "stale": sum(1 for item in traces if item["status"] == "stale"),
            "by_type": self._count_by_type(active_traces),
        }
        return {
            "task_uid": task_uid,
            "requirements": requirements,
            "trace_links": traces,
            "trace_summary": summary,
        }

    def _count_by_type(self, traces: list[dict[str, Any]]) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in traces:
            link_type = str(item["link_type"])
            result[link_type] = result.get(link_type, 0) + 1
        return result

    def _trace_row_payload(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_uid": row["task_uid"],
            "requirement_id": row["requirement_id"],
            "link_type": row["link_type"],
            "target_ref": row["target_ref"],
            "metadata": _loads_json(row["metadata_json"], {}),
            "status": row["status"],
            "confidence": float(row["confidence"] or 0),
            "managed_by": row["managed_by"],
            "source_agent_run_id": row["source_agent_run_id"],
            "created_at": row["created_at"],
        }

    def _requirement_payload(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_uid": row["task_uid"],
            "source_draft_uid": row["source_draft_uid"],
            "requirement_id": row["requirement_id"],
            "title": row["title"],
            "description": row["description"],
            "anchor_fingerprint": row["anchor_fingerprint"],
            "metadata": _loads_json(row["metadata_json"], {}),
            "status": row["status"],
            "deprecated_at": row["deprecated_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def _parse_requirements_from_markdown(content: str) -> list[dict[str, str]]:
    lines = [line.rstrip() for line in content.splitlines()]
    items: list[dict[str, str]] = []
    current_title = ""
    current_external_id = ""
    current_body: list[str] = []
    found_heading = False
    saw_explicit_requirement = False
    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip()
            if found_heading and current_title and saw_explicit_requirement:
                items.append(
                    {
                        "title": current_title,
                        "description": "\n".join(part for part in current_body if part.strip()).strip(),
                        "external_id": current_external_id,
                    }
                )
                current_body = []
            current_external_id = ""
            matched = re.match(r"^(REQ-\d+)\s*[:：\-]?\s*(.+)$", heading, flags=re.IGNORECASE)
            if matched:
                current_external_id = matched.group(1).upper()
                current_title = matched.group(2).strip() or current_external_id
                saw_explicit_requirement = True
            else:
                current_title = heading
            found_heading = True
            continue
        if found_heading and saw_explicit_requirement:
            current_body.append(line)
    if found_heading and current_title and saw_explicit_requirement:
        items.append(
            {
                "title": current_title,
                "description": "\n".join(part for part in current_body if part.strip()).strip(),
                "external_id": current_external_id,
            }
        )
    cleaned = [item for item in items if item["title"].strip() and item["external_id"].strip()]
    return cleaned


def _anchor_fingerprint(title: str, description: str) -> str:
    normalized = f"{_normalize_text(title)}::{_normalize_text(description)}"
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _task_short_id(task_uid: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", task_uid.replace("task_", ""))[:6].upper() or "TASK"


def _loads_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default
