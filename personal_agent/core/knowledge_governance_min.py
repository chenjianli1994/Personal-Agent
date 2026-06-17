from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .database import connect
from .utils import json_dumps, utc_now


CODE_EXTENSIONS = {".c", ".h", ".cpp", ".hpp", ".cc", ".py", ".ts", ".tsx", ".js", ".jsx"}
CODE_SYMBOL_RE = re.compile(r"^\s*(?:[A-Za-z_][\w\s\*\(\),]*\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{?\s*$")


def ensure_knowledge_import_batch(
    conn,
    *,
    project_id: int | None,
    source_type: str,
    source_ref: str,
    source_owner: str = "",
    source_trust_level: str = "internal",
    source_version: str = "",
    import_batch_id: str = "",
    stats: dict[str, Any] | None = None,
) -> str:
    now = utc_now()
    batch_uid = import_batch_id or "kb_batch_" + hashlib.sha1(f"{project_id}:{source_type}:{source_ref}:{now}".encode("utf-8")).hexdigest()[:18]
    conn.execute(
        """
        INSERT INTO knowledge_import_batches(batch_uid, project_id, source_type, source_ref, source_owner, source_trust_level, source_version, stats_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_uid) DO UPDATE SET
            project_id=excluded.project_id,
            source_type=excluded.source_type,
            source_ref=excluded.source_ref,
            source_owner=excluded.source_owner,
            source_trust_level=excluded.source_trust_level,
            source_version=excluded.source_version,
            stats_json=excluded.stats_json,
            updated_at=excluded.updated_at
        """,
        (batch_uid, project_id, source_type, source_ref, source_owner, source_trust_level, source_version, json_dumps(stats or {}), now, now),
    )
    return batch_uid


def infer_knowledge_material_type(category: str, source_type: str, source_ref: str, tags: list[str] | None = None) -> str:
    tags = tags or []
    text = " ".join([category, source_type, source_ref, " ".join(tags)]).lower()
    suffix = Path(source_ref).suffix.lower()
    is_code = suffix in CODE_EXTENSIONS or "code" in text or "c_code" in text
    if "generated" in text or "candidate_code" in text:
        return "generated_candidate_code"
    if "template" in text:
        return "template_code" if is_code else "template_document"
    if "historical" in text or "archive" in text or "project_code_archive" in text:
        return "historical_example_code"
    if source_type in {"project_code", "real_project_code"}:
        return "real_project_code"
    if is_code and source_type in {"reference", "manual", "external"}:
        return "reference_code"
    if is_code:
        return "historical_example_code" if source_type == "project_code_import" else "reference_code"
    return "external_reference" if source_type == "external" else "reference_document"


def extract_code_refs(
    content: str,
    *,
    source_ref: str,
    import_batch_id: str,
    source_version: str,
    source_trust_level: str,
    material_type: str,
) -> list[dict[str, Any]]:
    if Path(source_ref).suffix.lower() not in CODE_EXTENSIONS and "code" not in material_type:
        return []
    refs: list[dict[str, Any]] = []
    lines = content.splitlines()
    for index, line in enumerate(lines, start=1):
        match = CODE_SYMBOL_RE.match(line)
        if not match:
            continue
        symbol = match.group(1)
        if symbol in {"if", "for", "while", "switch", "return"}:
            continue
        refs.append(
            {
                "source_file": source_ref,
                "symbol": symbol,
                "line_start": index,
                "line_end": _guess_symbol_end(lines, index),
                "import_batch_id": import_batch_id,
                "source_version": source_version,
                "trust_level": source_trust_level,
                "material_type": material_type,
            }
        )
        if len(refs) >= 40:
            break
    if not refs:
        refs.append(
            {
                "source_file": source_ref,
                "symbol": "",
                "line_start": 1,
                "line_end": max(1, len(lines)),
                "import_batch_id": import_batch_id,
                "source_version": source_version,
                "trust_level": source_trust_level,
                "material_type": material_type,
            }
        )
    return refs


def apply_document_governance(conn, document_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
    if not row:
        raise ValueError(f"KnowledgeDocument not found: {document_id}")
    doc = dict(row)
    duplicate_of = _find_duplicate(conn, doc)
    conflicts = _find_conflicts(conn, doc)
    conn.execute(
        "UPDATE knowledge_documents SET duplicate_of=?, conflict_set_json=? WHERE id=?",
        (duplicate_of, json_dumps(conflicts), document_id),
    )
    for conflict in conflicts:
        conflict_uid = "kb_conflict_" + hashlib.sha1(f"{document_id}:{conflict['document_id']}:{conflict['conflict_type']}".encode("utf-8")).hexdigest()[:18]
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_conflicts(conflict_uid, project_id, document_id, conflicting_document_id, conflict_type, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (conflict_uid, doc.get("project_id"), document_id, conflict["document_id"], conflict["conflict_type"], conflict["summary"], utc_now()),
        )
    return {"duplicate_of": duplicate_of, "conflicts": conflicts}


def list_knowledge_import_batches(db_path: Path, project_id: int | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM knowledge_import_batches WHERE project_id=? OR project_id IS NULL ORDER BY id DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM knowledge_import_batches ORDER BY id DESC").fetchall()
    return [_decode_batch(dict(row)) for row in rows]


def build_knowledge_governance_summary(db_path: Path, project_id: int | None = None) -> dict[str, Any]:
    with connect(db_path) as conn:
        clause = "WHERE (project_id=? OR project_id IS NULL)" if project_id else ""
        and_clause = clause + " AND " if clause else "WHERE "
        params = (project_id,) if project_id else ()
        status = conn.execute(f"SELECT approval_status, COUNT(*) AS count FROM knowledge_documents {clause} GROUP BY approval_status", params).fetchall()
        material = conn.execute(f"SELECT material_type, COUNT(*) AS count FROM knowledge_documents {clause} GROUP BY material_type", params).fetchall()
        trust = conn.execute(f"SELECT source_trust_level, COUNT(*) AS count FROM knowledge_documents {clause} GROUP BY source_trust_level", params).fetchall()
        duplicates = conn.execute(f"SELECT COUNT(*) AS count FROM knowledge_documents {and_clause}duplicate_of<>''", params).fetchone()
        conflicts = conn.execute(
            "SELECT COUNT(*) AS count FROM knowledge_conflicts WHERE status='open'" + (" AND (project_id=? OR project_id IS NULL)" if project_id else ""),
            params,
        ).fetchone()
        expired = conn.execute(
            f"SELECT COUNT(*) AS count FROM knowledge_documents {and_clause}expires_at<>'' AND expires_at<?",
            (*params, utc_now()),
        ).fetchone()
    return {
        "project_id": project_id,
        "approval_status": [dict(row) for row in status],
        "material_type": [dict(row) for row in material],
        "source_trust_level": [dict(row) for row in trust],
        "duplicates": int(duplicates["count"]) if duplicates else 0,
        "open_conflicts": int(conflicts["count"]) if conflicts else 0,
        "expired": int(expired["count"]) if expired else 0,
        "guardrails": {
            "agent_routing": "llm_semantic_first",
            "knowledge_search": "governed_retrieval_context",
            "keyword_matching": "not_used_for_agent_routing",
        },
    }


def record_document_review(conn, document_id: int, action: str, reviewer: str, comment: str, from_status: str, to_status: str) -> None:
    row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
    conn.execute(
        """
        INSERT INTO knowledge_document_reviews(document_id, action, reviewer, comment, from_status, to_status, snapshot_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, action, reviewer, comment, from_status, to_status, json_dumps(dict(row) if row else {}), utc_now()),
    )


def _find_duplicate(conn, doc: dict[str, Any]) -> str:
    row = conn.execute(
        """
        SELECT doc_uid FROM knowledge_documents
        WHERE id<>? AND content_hash=? AND (project_id=? OR project_id IS NULL OR ? IS NULL)
        ORDER BY id LIMIT 1
        """,
        (doc["id"], doc.get("content_hash", ""), doc.get("project_id"), doc.get("project_id")),
    ).fetchone()
    return str(row["doc_uid"]) if row else ""


def _find_conflicts(conn, doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, doc_uid, title, source_version, content_hash, status
        FROM knowledge_documents
        WHERE id<>?
          AND category=?
          AND lower(title)=lower(?)
          AND content_hash<>?
          AND status<>'deprecated'
          AND (project_id=? OR project_id IS NULL OR ? IS NULL)
        ORDER BY id DESC LIMIT 12
        """,
        (doc["id"], doc.get("category", ""), doc.get("title", ""), doc.get("content_hash", ""), doc.get("project_id"), doc.get("project_id")),
    ).fetchall()
    conflicts = []
    for row in rows:
        conflicts.append(
            {
                "document_id": int(row["id"]),
                "doc_uid": row["doc_uid"],
                "conflict_type": "same_title_different_content",
                "summary": f"Same title/category but different content hash: {row['title']}",
                "status": row["status"],
                "source_version": row["source_version"],
            }
        )
    return conflicts


def _decode_batch(row: dict[str, Any]) -> dict[str, Any]:
    row["stats"] = _loads_json(row.pop("stats_json", "{}"))
    return row


def _loads_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return text


def _guess_symbol_end(lines: list[str], start_line: int) -> int:
    depth = 0
    seen_body = False
    for index in range(start_line, min(len(lines), start_line + 120) + 1):
        line = lines[index - 1]
        depth += line.count("{") - line.count("}")
        seen_body = seen_body or "{" in line
        if seen_body and depth <= 0:
            return index
    return start_line

