from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..content_guard import RETIRED_PROJECT_INPUT_KEYS, assert_personal_content_clean
from .database import connect
from .knowledge_base import (
    ensure_knowledge_search_index,
    find_knowledge_code_archive_roots,
    import_knowledge_code_directory,
    index_knowledge_directory,
    index_knowledge_item_search_entry,
)
from .knowledge_governance_min import build_knowledge_governance_summary
from .utils import json_dumps, utc_now


def create_project(db_path: Path, code: str, name: str, description: str = "") -> dict[str, Any]:
    with connect(db_path) as conn:
        project_id = ensure_project(conn, code, name, description)
        _ensure_default_project_inputs(conn, project_id)
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        _audit(conn, project_id, "PROJECT_CREATED", f"Created project {name}", {"code": code})
        return dict(project)


def bootstrap_knowledge(
    db_path: Path,
    knowledge_root: Path,
    project_id: int | None = None,
    include_code_archives: bool = False,
) -> dict[str, Any]:
    result = index_knowledge_directory(db_path, knowledge_root, project_id)
    code_reports: list[dict[str, Any]] = []
    if include_code_archives:
        for archive_root in find_knowledge_code_archive_roots(knowledge_root):
            code_reports.append(import_knowledge_code_directory(db_path, archive_root, project_id))
        if code_reports:
            result["template_indexed"] = result["indexed"]
            result["template_skipped"] = result["skipped"]
            result["code_indexed"] = sum(int(item.get("indexed", 0)) for item in code_reports)
            result["code_skipped"] = sum(int(item.get("skipped", 0)) for item in code_reports)
            result["code_archives"] = code_reports
            result["indexed"] = int(result["indexed"]) + int(result["code_indexed"])
            result["skipped"] = int(result["skipped"]) + int(result["code_skipped"])
    search_index = ensure_knowledge_search_index(db_path)
    result["search_indexed"] = int(search_index.get("indexed", 0))
    result["search_index_rebuilt"] = bool(search_index.get("rebuilt", False))
    with connect(db_path) as conn:
        project = _select_project(conn, project_id)
        _audit(
            conn,
            int(project["id"]) if project else None,
            "KNOWLEDGE_INDEXED",
            f"Indexed knowledge root {knowledge_root}",
            {"root": str(knowledge_root), **result},
        )
    return result


def list_knowledge_items(db_path: Path, project_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM knowledge_items WHERE project_id=? OR project_id IS NULL ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM knowledge_items ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [_decode_json_fields(dict(row), ["tags_json"]) for row in rows]


def create_knowledge_item(db_path: Path, payload: Any) -> dict[str, Any]:
    now = utc_now()
    uid_source = f"{payload.project_id or 'global'}:{payload.title}:{payload.source_ref}:{now}"
    item_uid = "kb_manual_" + hashlib.sha1(uid_source.encode("utf-8")).hexdigest()[:16]
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.project_id,
                item_uid,
                payload.title,
                payload.category,
                payload.source_type,
                payload.source_ref,
                payload.content,
                json_dumps(payload.tags),
                payload.confidence,
                payload.status,
                now,
                now,
            ),
        )
        _audit(
            conn,
            payload.project_id,
            "KNOWLEDGE_ITEM_CREATED",
            f"Created knowledge item {payload.title}",
            {"item_uid": item_uid, "category": payload.category},
        )
        row = conn.execute("SELECT * FROM knowledge_items WHERE item_uid=?", (item_uid,)).fetchone()
    index_knowledge_item_search_entry(db_path, item_uid)
    return _decode_json_fields(dict(row), ["tags_json"])


def build_knowledge_summary(db_path: Path, project_id: int | None = None) -> dict[str, Any]:
    with connect(db_path) as conn:
        if project_id:
            project_clause = "AND (project_id=? OR project_id IS NULL)"
            params = (project_id,)
        else:
            project_clause = ""
            params = ()
        categories = conn.execute(
            f"SELECT category, COUNT(*) AS count FROM knowledge_items WHERE status='active' {project_clause} GROUP BY category ORDER BY category",
            params,
        ).fetchall()
        memory = conn.execute(
            f"SELECT status, COUNT(*) AS count FROM memory_candidates WHERE 1=1 {project_clause} GROUP BY status ORDER BY status",
            params,
        ).fetchall()
        documents = conn.execute(
            f"SELECT COUNT(*) AS count FROM knowledge_documents WHERE status='active' {project_clause}",
            params,
        ).fetchone()
        chunks = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM knowledge_chunks kc
            JOIN knowledge_documents kd ON kd.id = kc.document_id
            WHERE kc.status='active' AND kd.status='active'
            """
            + (" AND (kd.project_id=? OR kd.project_id IS NULL)" if project_id else ""),
            params,
        ).fetchone()
    return {
        "categories": [dict(row) for row in categories],
        "memory": [dict(row) for row in memory],
        "documents": int(documents["count"]) if documents else 0,
        "chunks": int(chunks["count"]) if chunks else 0,
        "items": list_knowledge_items(db_path, project_id, limit=20),
        "governance": build_knowledge_governance_summary(db_path, project_id),
    }


def list_memory_candidates(db_path: Path, project_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM memory_candidates WHERE project_id=? OR project_id IS NULL ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memory_candidates ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_decode_json_fields(dict(row), ["evidence_refs_json", "applicability_json", "counterexamples_json"]) for row in rows]


def build_learning_summary(db_path: Path, project_id: int | None = None) -> dict[str, Any]:
    with connect(db_path) as conn:
        if project_id:
            params = (project_id,)
            memory = conn.execute(
                "SELECT status, COUNT(*) AS count FROM memory_candidates WHERE project_id=? OR project_id IS NULL GROUP BY status",
                params,
            ).fetchall()
            lessons = conn.execute(
                "SELECT COUNT(*) AS count FROM knowledge_items WHERE category='memory_lesson' AND status='active' AND (project_id=? OR project_id IS NULL)",
                params,
            ).fetchone()
            regression = conn.execute(
                "SELECT COUNT(*) AS count FROM knowledge_items WHERE category='regression_case' AND status='active' AND (project_id=? OR project_id IS NULL)",
                params,
            ).fetchone()
        else:
            memory = conn.execute("SELECT status, COUNT(*) AS count FROM memory_candidates GROUP BY status").fetchall()
            lessons = conn.execute("SELECT COUNT(*) AS count FROM knowledge_items WHERE category='memory_lesson' AND status='active'").fetchone()
            regression = conn.execute("SELECT COUNT(*) AS count FROM knowledge_items WHERE category='regression_case' AND status='active'").fetchone()
    return {
        "memory": [dict(row) for row in memory],
        "approved_lessons": int(lessons["count"]) if lessons else 0,
        "regression_cases": int(regression["count"]) if regression else 0,
        "recent": list_memory_candidates(db_path, project_id, limit=8),
    }


def create_learning_feedback(db_path: Path, payload: Any) -> dict[str, Any]:
    title = f"{payload.failure_mode} / {payload.requirement_id or 'project'}"
    problem = "\n".join(
        [
            f"source: {payload.source}",
            f"requirement: {payload.requirement_id or '-'}",
            f"failure_mode: {payload.failure_mode}",
            f"user_feedback: {payload.user_feedback}",
            f"root_cause: {payload.root_cause or 'pending'}",
        ]
    )
    lesson = payload.corrected_behavior
    evidence = {
        **payload.evidence_refs,
        "requirement_id": payload.requirement_id,
        "source": payload.source,
        "failure_mode": payload.failure_mode,
        "root_cause": payload.root_cause,
        "add_to_regression": payload.add_to_regression,
        "lesson_type": _lesson_type_for_failure_mode(payload.failure_mode),
        "validation_query": payload.user_feedback,
        "expected_behavior": payload.corrected_behavior,
    }
    memory = create_memory_candidate(
        db_path,
        type(
            "MemoryPayload",
            (),
            {
                "project_id": payload.project_id,
                "title": title,
                "problem": problem,
                "lesson": lesson,
                "evidence_refs": evidence,
                "source_decision_uid": str(payload.evidence_refs.get("decision_uid", "")) if isinstance(payload.evidence_refs, dict) else "",
                "lesson_type": _lesson_type_for_failure_mode(payload.failure_mode),
                "expected_behavior": payload.corrected_behavior,
                "anti_behavior": getattr(payload, "anti_behavior", "") or payload.user_feedback,
                "validation_query": payload.user_feedback,
                "scope": getattr(payload, "scope", "") or ("project" if payload.project_id else "org"),
                "failure_type": payload.failure_mode,
                "applicability": {
                    "project_id": payload.project_id,
                    "requirement_id": payload.requirement_id,
                    "source": payload.source,
                },
                "counterexamples": [],
            },
        )(),
    )
    if payload.add_to_regression:
        create_knowledge_item(
            db_path,
            type(
                "KnowledgePayload",
                (),
                {
                    "project_id": payload.project_id,
                    "title": f"Regression case: {title}",
                    "category": "regression_case",
                    "source_type": "learning_feedback",
                    "source_ref": f"memory_candidate:{memory['id']}",
                    "content": f"input: {payload.user_feedback}\nexpected: {payload.corrected_behavior}\nfailure_mode: {payload.failure_mode}\nroot_cause: {payload.root_cause}",
                    "tags": ["agent-regression", "learning", payload.failure_mode],
                    "confidence": 0.78,
                    "status": "active",
                },
            )(),
        )
    return memory


def create_memory_candidate(db_path: Path, payload: Any) -> dict[str, Any]:
    now = utc_now()
    governance = _memory_governance_from_payload(payload)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO memory_candidates(
                project_id, title, problem, lesson, evidence_refs_json, status,
                source_decision_uid, lesson_type, expected_behavior, anti_behavior,
                validation_query, scope, failure_type, applicability_json, counterexamples_json,
                regression_case_uid, expires_at, superseded_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.project_id,
                payload.title,
                payload.problem,
                payload.lesson,
                json_dumps(payload.evidence_refs),
                getattr(payload, "source_decision_uid", ""),
                getattr(payload, "lesson_type", "conversation_lesson"),
                getattr(payload, "expected_behavior", ""),
                getattr(payload, "anti_behavior", ""),
                getattr(payload, "validation_query", ""),
                governance["scope"],
                governance["failure_type"],
                json_dumps(governance["applicability"]),
                json_dumps(governance["counterexamples"]),
                governance["regression_case_uid"],
                governance["expires_at"],
                governance["superseded_by"],
                now,
                now,
            ),
        )
        memory_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        _audit(conn, payload.project_id, "MEMORY_CANDIDATE_CREATED", f"Created memory candidate {payload.title}", {"memory_candidate_id": memory_id})
        row = conn.execute("SELECT * FROM memory_candidates WHERE id=?", (memory_id,)).fetchone()
    return _decode_json_fields(dict(row), ["evidence_refs_json", "applicability_json", "counterexamples_json"])


def approve_memory_candidate(db_path: Path, memory_id: int, reviewer: str, comment: str = "") -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM memory_candidates WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise ValueError(f"MemoryCandidate not found: {memory_id}")
        _validate_memory_governance(row)
        replay = _replay_memory_row(row)
        conn.execute(
            "UPDATE memory_candidates SET status='approved', last_replay_status=?, last_replay_at=?, updated_at=? WHERE id=?",
            (replay["status"], now, now, memory_id),
        )
        item_uid = f"kb_memory_{memory_id}"
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, policy_effect_scope, created_at, updated_at)
            VALUES (?, ?, ?, 'memory_lesson', 'memory_gate', ?, ?, ?, 0.85, 'active', ?, ?, ?)
            ON CONFLICT(item_uid) DO UPDATE SET content=excluded.content, status='active', policy_effect_scope=excluded.policy_effect_scope, updated_at=excluded.updated_at
            """,
            (
                row["project_id"],
                item_uid,
                row["title"],
                f"memory_candidate:{memory_id}",
                f"problem: {row['problem']}\nlesson: {row['lesson']}\nexpected_behavior: {row['expected_behavior'] if 'expected_behavior' in row.keys() else ''}\nreplay: {replay['status']}\nreview: {reviewer} {comment}",
                json_dumps(["memory", "lesson", "approved", row["lesson_type"] if "lesson_type" in row.keys() else "conversation_lesson"]),
                row["lesson_type"] if "lesson_type" in row.keys() else "conversation_lesson",
                now,
                now,
            ),
        )
        _ensure_memory_regression_case(conn, row, memory_id, now)
        _audit(conn, row["project_id"], "MEMORY_CANDIDATE_APPROVED", f"Approved memory candidate {memory_id}", {"reviewer": reviewer, "comment": comment, "knowledge_item_uid": item_uid})
        updated = conn.execute("SELECT * FROM memory_candidates WHERE id=?", (memory_id,)).fetchone()
    index_knowledge_item_search_entry(db_path, item_uid)
    return _decode_json_fields(dict(updated), ["evidence_refs_json", "applicability_json", "counterexamples_json"])


def reject_memory_candidate(db_path: Path, memory_id: int, reviewer: str, comment: str = "") -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM memory_candidates WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise ValueError(f"MemoryCandidate not found: {memory_id}")
        conn.execute("UPDATE memory_candidates SET status='rejected', updated_at=? WHERE id=?", (now, memory_id))
        _audit(conn, row["project_id"], "MEMORY_CANDIDATE_REJECTED", f"Rejected memory candidate {memory_id}", {"reviewer": reviewer, "comment": comment})
        updated = conn.execute("SELECT * FROM memory_candidates WHERE id=?", (memory_id,)).fetchone()
    return _decode_json_fields(dict(updated), ["evidence_refs_json", "applicability_json", "counterexamples_json"])


def ensure_project(conn, code: str, name: str, description: str = "") -> int:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO projects(code, name, description, status, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?)
        ON CONFLICT(code) DO UPDATE SET name=excluded.name, description=excluded.description, updated_at=excluded.updated_at
        """,
        (code, name, description, now, now),
    )
    row = conn.execute("SELECT id FROM projects WHERE code = ?", (code,)).fetchone()
    return int(row["id"])


def _select_project(conn, project_id: int | None = None):
    if project_id is not None:
        return conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return conn.execute("SELECT * FROM projects ORDER BY id LIMIT 1").fetchone()


def _ensure_default_project_inputs(conn, project_id: int) -> None:
    now = utc_now()
    defaults = [
        ("personal_test_command", "Personal test command", "toolchain", "python -m pytest"),
    ]
    conn.execute(
        """
        DELETE FROM project_inputs
        WHERE project_id=? AND input_key IN (?, ?, ?, ?)
        """,
        (project_id, *RETIRED_PROJECT_INPUT_KEYS),
    )
    for input_key, label, category, value in defaults:
        conn.execute(
            """
            INSERT INTO project_inputs(project_id, input_key, label, category, value, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(project_id, input_key) DO NOTHING
            """,
            (project_id, input_key, label, category, value, now, now),
        )


def _lesson_type_for_failure_mode(failure_mode: str) -> str:
    text = (failure_mode or "").lower()
    if "route" in text or "intent" in text or "decision" in text:
        return "routing_lesson"
    if "tool" in text or "schema" in text:
        return "tool_lesson"
    if "process" in text or "check" in text or "review" in text:
        return "workflow_lesson"
    if "code" in text or "test" in text:
        return "code_lesson"
    if "permission" in text or "release" in text or "safety" in text:
        return "safety_lesson"
    return "conversation_lesson"


def _memory_governance_from_payload(payload: Any) -> dict[str, Any]:
    evidence_refs = getattr(payload, "evidence_refs", {}) if isinstance(getattr(payload, "evidence_refs", {}), dict) else {}
    failure_type = str(getattr(payload, "failure_type", "") or evidence_refs.get("failure_mode") or evidence_refs.get("failure_type") or "").strip()
    requirement_id = str(evidence_refs.get("requirement_id") or getattr(payload, "requirement_id", "") or "")
    applicability = getattr(payload, "applicability", None)
    if not isinstance(applicability, dict) or not applicability:
        applicability = {
            "project_id": getattr(payload, "project_id", None),
            "requirement_id": requirement_id,
            "lesson_type": getattr(payload, "lesson_type", "conversation_lesson"),
        }
    counterexamples = getattr(payload, "counterexamples", None)
    if counterexamples is None:
        counterexamples = []
    if not isinstance(counterexamples, list):
        counterexamples = [str(counterexamples)]
    return {
        "scope": str(getattr(payload, "scope", "") or "project"),
        "failure_type": failure_type or "runtime",
        "applicability": applicability,
        "counterexamples": counterexamples,
        "regression_case_uid": str(getattr(payload, "regression_case_uid", "") or ""),
        "expires_at": str(getattr(payload, "expires_at", "") or ""),
        "superseded_by": str(getattr(payload, "superseded_by", "") or ""),
    }


def _validate_memory_governance(row: Any) -> None:
    scope = str(row["scope"] if "scope" in row.keys() else "").strip()
    failure_type = str(row["failure_type"] if "failure_type" in row.keys() else "").strip()
    applicability_text = str(row["applicability_json"] if "applicability_json" in row.keys() else "{}")
    if scope not in {"session", "project", "org", "organization", "process", "technical"}:
        raise ValueError("MemoryCandidate governance scope is required.")
    if not failure_type:
        raise ValueError("MemoryCandidate governance failure_type is required.")
    try:
        applicability = json.loads(applicability_text or "{}")
    except Exception as exc:
        raise ValueError("MemoryCandidate governance applicability_json must be valid JSON.") from exc
    if not isinstance(applicability, dict) or not applicability:
        raise ValueError("MemoryCandidate governance applicability is required.")


def _replay_memory_row(row: Any) -> dict[str, Any]:
    lesson = str(row["lesson"] or "")
    expected = str(row["expected_behavior"] if "expected_behavior" in row.keys() else "")
    validation_query = str(row["validation_query"] if "validation_query" in row.keys() else "")
    assert_personal_content_clean(lesson, label="memory lesson")
    assert_personal_content_clean(expected, label="memory expected_behavior")
    assert_personal_content_clean(validation_query, label="memory validation_query")
    passed = bool(lesson.strip()) and (not expected.strip() or expected.strip() in lesson or lesson.strip() in expected)
    if not validation_query.strip():
        passed = False
    return {
        "status": "passed" if passed else "failed",
        "checks": {
            "has_lesson": bool(lesson.strip()),
            "has_validation_query": bool(validation_query.strip()),
            "expected_behavior_bound": bool(not expected.strip() or expected.strip() in lesson or lesson.strip() in expected),
        },
    }


def _ensure_memory_regression_case(conn, row: Any, memory_id: int, now: str) -> None:
    item_uid = f"kb_memory_regression_{memory_id}"
    lesson_type = row["lesson_type"] if "lesson_type" in row.keys() else "conversation_lesson"
    expected = row["expected_behavior"] if "expected_behavior" in row.keys() else row["lesson"]
    validation_query = row["validation_query"] if "validation_query" in row.keys() else row["problem"]
    conn.execute(
        """
        INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, policy_effect_scope, created_at, updated_at)
        VALUES (?, ?, ?, 'regression_case', 'memory_replay', ?, ?, ?, 0.82, 'active', ?, ?, ?)
        ON CONFLICT(item_uid) DO UPDATE SET content=excluded.content, status='active', policy_effect_scope=excluded.policy_effect_scope, updated_at=excluded.updated_at
        """,
        (
            row["project_id"],
            item_uid,
            f"Regression case: {row['title']}",
            f"memory_candidate:{memory_id}",
            f"validation_query: {validation_query}\nexpected_behavior: {expected}\nlesson: {row['lesson']}",
            json_dumps(["memory", "regression", lesson_type]),
            lesson_type,
            now,
            now,
        ),
    )


def _audit(conn, project_id: int | None, event_type: str, message: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_events(project_id, event_type, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, event_type, message, json_dumps(payload), utc_now()),
    )


def _decode_json_fields(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    for field in fields:
        if field in row:
            public_name = field.removesuffix("_json")
            row[public_name] = _loads_json(row.pop(field))
    return row


def _loads_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return text
