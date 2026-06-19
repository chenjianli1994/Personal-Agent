from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from personal_agent.core.database import connect
from personal_agent.core.knowledge_base import import_knowledge_document, index_knowledge_item_search_entry, search_knowledge, update_knowledge_document_status
from personal_agent.core.services_min import (
    approve_memory_candidate,
    build_knowledge_summary,
    build_learning_summary,
    create_knowledge_item,
    create_learning_feedback,
    list_knowledge_items,
    list_memory_candidates,
    reject_memory_candidate,
)
from personal_agent.core.utils import json_dumps, utc_now
from .skill_update_candidates import list_skill_update_candidates as list_personal_skill_update_candidates


def list_personal_knowledge(db_path: Path, *, project_id: int, limit: int = 100) -> dict[str, Any]:
    return {
        "summary": build_knowledge_summary(db_path, project_id),
        "items": list_knowledge_items(db_path, project_id, limit=limit),
    }


def import_source_to_knowledge(db_path: Path, *, project_id: int, source_uid: str) -> dict[str, Any]:
    source = _get_source(db_path, project_id=project_id, source_uid=source_uid)
    content = str(source.get("plain_text") or "").strip()
    if not content:
        raise ValueError("source has no parsed plain_text to import")
    title = str(source.get("title") or source_uid)
    source_ref = f"personal_source:{source_uid}"
    item = create_knowledge_item(
        db_path,
        SimpleNamespace(
            project_id=project_id,
            title=title,
            category="personal_source",
            source_type="personal_input_source",
            source_ref=source_ref,
            content=content[:12000],
            tags=["personal", "source", str(source.get("source_type") or "input")],
            confidence=0.82,
            status="active",
        ),
    )
    document = import_knowledge_document(
        db_path,
        project_id=project_id,
        title=title,
        content=content,
        category="personal_source",
        source_type="personal_input_source",
        source_ref=source_ref,
        tags=["personal", "source", str(source.get("source_type") or "input")],
        source_owner="personal_agent",
        source_trust_level="internal",
        approval_status="approved",
        doc_uid=_source_document_uid(project_id, source_uid, content),
    )
    _audit(
        db_path,
        project_id,
        "PERSONAL_KNOWLEDGE_IMPORTED_SOURCE",
        f"个人输入材料导入知识库 {title}",
        {"source_uid": source_uid, "knowledge_item_uid": item["item_uid"], "document_id": document.get("id")},
    )
    return {"status": "imported", "source_uid": source_uid, "knowledge_item": item, "knowledge_document": document}


def search_personal_knowledge(db_path: Path, *, project_id: int, query: str, limit: int = 8) -> list[dict[str, Any]]:
    return search_knowledge(db_path, query, project_id=project_id, limit=limit)


def deprecate_personal_knowledge(db_path: Path, *, project_id: int, knowledge_id: int, reviewer: str = "local_user", comment: str = "") -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as conn:
        item = conn.execute(
            "SELECT * FROM knowledge_items WHERE id=? AND (project_id=? OR project_id IS NULL)",
            (knowledge_id, project_id),
        ).fetchone()
        if item:
            conn.execute("UPDATE knowledge_items SET status='deprecated', updated_at=? WHERE id=?", (now, knowledge_id))
            conn.execute(
                """
                INSERT INTO audit_events(project_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    "PERSONAL_KNOWLEDGE_DEPRECATED",
                    f"个人知识条目已废弃 {item['title']}",
                    json_dumps({"knowledge_id": knowledge_id, "reviewer": reviewer, "comment": comment}),
                    now,
                ),
            )
            updated = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)).fetchone()
            return _decode_json_fields(dict(updated), ["tags_json"])
    try:
        updated_doc = update_knowledge_document_status(db_path, knowledge_id, "deprecated")
    except ValueError as exc:
        raise ValueError(f"knowledge item/document not found: {knowledge_id}") from exc
    _audit(
        db_path,
        project_id,
        "PERSONAL_KNOWLEDGE_DOCUMENT_DEPRECATED",
        f"个人知识文档已废弃 {updated_doc.get('title', knowledge_id)}",
        {"knowledge_id": knowledge_id, "reviewer": reviewer, "comment": comment},
    )
    return updated_doc


def dismiss_personal_memory_lesson(db_path: Path, *, project_id: int, item_uid: str, reviewer: str = "local_user", comment: str = "") -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM knowledge_items
            WHERE item_uid=? AND category='memory_lesson' AND (project_id=? OR project_id IS NULL)
            """,
            (item_uid, project_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"memory lesson not found: {item_uid}")
        conn.execute(
            "UPDATE knowledge_items SET status='deprecated', updated_at=? WHERE item_uid=?",
            (now, item_uid),
        )
    index_knowledge_item_search_entry(db_path, item_uid)
    _audit(
        db_path,
        project_id,
        "PERSONAL_MEMORY_LESSON_DISMISSED",
        f"个人记忆经验已撤销：{row['title']}",
        {"item_uid": item_uid, "reviewer": reviewer, "comment": comment},
    )
    with connect(db_path) as conn:
        updated = conn.execute("SELECT * FROM knowledge_items WHERE item_uid=?", (item_uid,)).fetchone()
    return _decode_json_fields(dict(updated), ["tags_json"])


def personal_inbox(db_path: Path, *, project_id: int, limit: int = 100) -> list[dict[str, Any]]:
    learning_items = [
        {"kind": "learning_candidate", **item}
        for item in personal_learning_candidates(db_path, project_id=project_id, limit=limit)
    ]
    skill_items = [
        {"kind": "skill_update_candidate", **item}
        for item in list_personal_skill_update_candidates(db_path, project_id=project_id, status="candidate")[:limit]
    ]
    merged = [*learning_items, *skill_items]
    merged.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return merged[:limit]


def personal_learning_summary(db_path: Path, *, project_id: int) -> dict[str, Any]:
    return build_learning_summary(db_path, project_id)


def personal_learning_candidates(db_path: Path, *, project_id: int, limit: int = 100) -> list[dict[str, Any]]:
    items = list_memory_candidates(db_path, project_id, limit=limit)
    result: list[dict[str, Any]] = []
    for item in items:
        payload = dict(item)
        if str(payload.get("status") or "") == "approved" and int(payload.get("id") or 0) > 0:
            payload["item_uid"] = f"kb_memory_{int(payload['id'])}"
        result.append(payload)
    return result


def record_personal_feedback(
    db_path: Path,
    *,
    project_id: int,
    requirement_id: str = "",
    task_uid: str = "",
    session_uid: str = "",
    feedback: str,
    source: str = "personal_agent",
    corrected_behavior: str = "",
    anti_behavior: str = "",
    feedback_type: str = "personal_behavior_feedback",
    scope: str = "project",
    add_to_regression: bool = False,
) -> dict[str, Any]:
    feedback = feedback.strip()
    if not feedback:
        raise ValueError("feedback is required")
    corrected = corrected_behavior.strip() or _feedback_to_rule(feedback)
    display_type = _feedback_type_label(feedback_type)
    root_cause = "用户在个人 Agent 会话中给出行为偏好、原则、纠错或工作方式要求"
    memory = create_learning_feedback(
        db_path,
        SimpleNamespace(
            **{
                "project_id": project_id,
                "requirement" + "_id": requirement_id,
                "requirement_id": requirement_id,
                "source": source,
                "failure_mode": feedback_type or "personal_behavior_feedback",
                "user_feedback": feedback,
                "root_cause": "用户在个人 Agent 会话中给出行为偏好或纠偏",
                "corrected_behavior": corrected,
                "anti_behavior": anti_behavior.strip() or feedback,
                "scope": _memory_scope(scope),
                "evidence_refs": {
                    "session_uid": session_uid,
                    "task_uid": task_uid,
                    "immediate_session": True,
                    "feedback_text": feedback,
                    "learning_scope": scope,
                },
                "add_to_regression": add_to_regression,
            }
        ),
    )
    memory = _localize_personal_memory_candidate(
        db_path,
        memory_id=int(memory["id"]),
        title=f"{display_type} #{memory['id']}",
        problem=f"用户原始反馈：{feedback}\n提炼依据：{root_cause}",
    )
    memory["immediate_rule"] = corrected
    return memory


def approve_personal_candidate(db_path: Path, *, project_id: int, candidate_id: int, reviewer: str = "local_user", comment: str = "") -> dict[str, Any]:
    _assert_candidate_scope(db_path, project_id, candidate_id)
    _ensure_personal_candidate_governance(db_path, project_id=project_id, candidate_id=candidate_id)
    return approve_memory_candidate(db_path, candidate_id, reviewer=reviewer, comment=comment)


def reject_personal_candidate(db_path: Path, *, project_id: int, candidate_id: int, reviewer: str = "local_user", comment: str = "") -> dict[str, Any]:
    _assert_candidate_scope(db_path, project_id, candidate_id)
    return reject_memory_candidate(db_path, candidate_id, reviewer=reviewer, comment=comment)


def review_latest_session_candidate(
    db_path: Path,
    *,
    project_id: int,
    session_uid: str = "",
    task_uid: str = "",
    decision: str,
    reviewer: str = "local_user",
    comment: str = "",
) -> dict[str, Any]:
    candidate = latest_session_memory_candidate(db_path, project_id=project_id, session_uid=session_uid, task_uid=task_uid)
    if not candidate:
        raise ValueError("No pending MemoryCandidate found for this conversation")
    if decision == "approve":
        return approve_personal_candidate(db_path, project_id=project_id, candidate_id=int(candidate["id"]), reviewer=reviewer, comment=comment)
    if decision == "reject":
        return reject_personal_candidate(db_path, project_id=project_id, candidate_id=int(candidate["id"]), reviewer=reviewer, comment=comment)
    raise ValueError(f"unsupported memory decision: {decision}")


def latest_session_memory_candidate(db_path: Path, *, project_id: int, session_uid: str = "", task_uid: str = "") -> dict[str, Any] | None:
    candidates = pending_session_memory_candidates(db_path, project_id=project_id, session_uid=session_uid, task_uid=task_uid)
    return candidates[0] if candidates else None


def pending_session_memory_candidates(db_path: Path, *, project_id: int, task_uid: str = "", session_uid: str = "") -> list[dict[str, Any]]:
    if not task_uid and not session_uid:
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM memory_candidates
            WHERE status='candidate' AND (project_id=? OR project_id IS NULL)
            ORDER BY id DESC LIMIT 30
            """,
            (project_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _decode_json_fields(dict(row), ["evidence_refs_json", "applicability_json", "counterexamples_json"])
        evidence = payload.get("evidence_refs") or payload.get("evidence_refs_json") or {}
        if not isinstance(evidence, dict) or not evidence.get("immediate_session"):
            continue
        matches_task = bool(task_uid) and evidence.get("task_uid") == task_uid
        matches_session = bool(session_uid) and evidence.get("session_uid") == session_uid
        if matches_task or matches_session:
            result.append(payload)
    return result


def _memory_scope(scope: str) -> str:
    if scope == "global_personal":
        return "project"
    if scope in {"session", "project", "process", "technical", "org", "organization"}:
        return scope
    return "project"


def _get_source(db_path: Path, *, project_id: int, source_uid: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM personal_input_sources WHERE project_id=? AND source_uid=? AND status='active'",
            (project_id, source_uid),
        ).fetchone()
    if row is None:
        raise ValueError(f"source not found: {source_uid}")
    return dict(row)


def _source_document_uid(project_id: int, source_uid: str, content: str) -> str:
    digest = hashlib.sha1(f"{project_id}:{source_uid}:{content}".encode("utf-8")).hexdigest()[:18]
    return f"kbd_personal_source_{digest}"


def _feedback_to_rule(feedback: str) -> str:
    text = feedback.strip()
    for prefix in ("以后", "下次", "记住", "请记住"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip(" ，,。")
            break
    return text or feedback.strip()


def _feedback_type_label(feedback_type: str) -> str:
    labels = {
        "style_preference": "表达偏好",
        "correction": "纠错经验",
        "workflow_preference": "工作方式",
        "quality_bar": "质量要求",
        "personal_behavior_feedback": "个人反馈",
    }
    return labels.get(feedback_type, "学习经验")


def _localize_personal_memory_candidate(db_path: Path, *, memory_id: int, title: str, problem: str) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE memory_candidates SET title=?, problem=?, updated_at=? WHERE id=?",
            (title, problem, now, memory_id),
        )
        row = conn.execute("SELECT * FROM memory_candidates WHERE id=?", (memory_id,)).fetchone()
    if row is None:
        raise ValueError(f"MemoryCandidate not found: {memory_id}")
    return _decode_json_fields(dict(row), ["evidence_refs_json", "applicability_json", "counterexamples_json"])


def _assert_candidate_scope(db_path: Path, project_id: int, candidate_id: int) -> None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM memory_candidates WHERE id=? AND (project_id=? OR project_id IS NULL)",
            (candidate_id, project_id),
        ).fetchone()
    if row is None:
        raise ValueError(f"MemoryCandidate not found: {candidate_id}")


def _ensure_personal_candidate_governance(db_path: Path, *, project_id: int, candidate_id: int) -> None:
    now = utc_now()
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM memory_candidates WHERE id=? AND (project_id=? OR project_id IS NULL)",
            (candidate_id, project_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"MemoryCandidate not found: {candidate_id}")
        item = dict(row)
        evidence = _json_object(item.get("evidence_refs_json"))
        applicability = _json_object(item.get("applicability_json"))
        scope = str(item.get("scope") or "").strip()
        if scope not in {"session", "project", "org", "organization", "process", "technical"}:
            scope = "project"
        failure_type = str(item.get("failure_type") or "").strip()
        if not failure_type:
            failure_type = str(
                evidence.get("learning_scope")
                or evidence.get("source")
                or item.get("lesson_type")
                or "personal_behavior_feedback"
            )
        if not applicability:
            applicability = {
                "project_id": project_id,
                "lesson_type": item.get("lesson_type") or "conversation_lesson",
                "source": evidence.get("source") or "personal_agent",
                "personal_agent": True,
            }
        validation_query = str(item.get("validation_query") or "").strip()
        if not validation_query:
            validation_query = str(evidence.get("feedback_text") or item.get("problem") or item.get("lesson") or "").strip()
        conn.execute(
            """
            UPDATE memory_candidates
            SET scope=?, failure_type=?, applicability_json=?, validation_query=?, updated_at=?
            WHERE id=?
            """,
            (scope, failure_type, json_dumps(applicability), validation_query, now, candidate_id),
        )


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _audit(db_path: Path, project_id: int | None, event_type: str, message: str, payload: dict[str, Any]) -> None:
    now = utc_now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO audit_events(project_id, event_type, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, event_type, message, json_dumps(payload), now),
        )


def _decode_json_fields(item: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    result = dict(item)
    for field in fields:
        value = result.pop(field, None)
        target = field[:-5] if field.endswith("_json") else field
        try:
            result[target] = json.loads(value or "[]")
        except Exception:
            result[target] = [] if field.endswith("_json") else value
    return result
