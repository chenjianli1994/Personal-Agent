from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from ..content_guard import assert_personal_payload_clean
from .database import connect
from .knowledge_governance_min import (
    apply_document_governance,
    ensure_knowledge_import_batch,
    extract_code_refs,
    infer_knowledge_material_type,
)
from .utils import json_dumps, read_text, utc_now


TEXT_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json", ".c", ".h"}
CODE_IMPORT_SKIP_DIRS = {".git", ".svn", ".hg", "__pycache__", "node_modules", "dist", "build", "out", "output", "coverage", "vendor", "third_party", "external"}
BOOTSTRAP_CODE_ARCHIVE_DIRS = {"过往项目代码", "code_archive", "project_code_archive", "local_code", "repo", "repos"}
MAX_BOOTSTRAP_KNOWLEDGE_FILES = 200
MAX_BOOTSTRAP_FILE_BYTES = 256_000
SEARCH_INDEX_PREVIEW_CHARS = 1200
SEARCH_INDEX_CANDIDATE_LIMIT = 80
_SEARCH_INDEX_READY_DBS: set[str] = set()


@dataclass(frozen=True)
class _KnowledgeQuery:
    raw: str
    normalized: str
    terms: list[str]
    ngrams: set[str]


def index_knowledge_directory(db_path: Path, root: Path, project_id: int | None = None) -> dict[str, int]:
    root = root.resolve()
    if not root.exists():
        return {"indexed": 0, "skipped": 0}
    indexed = 0
    skipped = 0
    pending_documents: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        batch_uid = ensure_knowledge_import_batch(
            conn,
            project_id=project_id,
            source_type="file_directory",
            source_ref=str(root),
            source_owner="platform_import",
            source_trust_level="internal",
        )
        for path in sorted(root.rglob("*")):
            if _is_bootstrap_skipped_path(root, path):
                continue
            if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
                skipped += 1
                continue
            if indexed >= MAX_BOOTSTRAP_KNOWLEDGE_FILES or path.stat().st_size > MAX_BOOTSTRAP_FILE_BYTES:
                skipped += 1
                continue
            content = read_text(path).strip()
            if not content:
                skipped += 1
                continue
            relative = str(path.relative_to(root)).replace("\\", "/")
            payload = {
                "title": path.stem,
                "content": content,
                "source_ref": relative,
                "tags": _tags_for_path(path),
                "process_codes": _process_codes_for_text(str(path)),
            }
            if has_forbidden_payload(payload):
                skipped += 1
                continue
            item_uid = "kb_" + hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
            now = utc_now()
            conn.execute(
                """
                INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'file', ?, ?, ?, 0.8, 'active', ?, ?)
                ON CONFLICT(item_uid) DO UPDATE SET
                    title=excluded.title,
                    category=excluded.category,
                    source_ref=excluded.source_ref,
                    content=excluded.content,
                    tags_json=excluded.tags_json,
                    updated_at=excluded.updated_at
                """,
                (
                    project_id,
                    item_uid,
                    path.stem,
                    _category_for_path(path),
                    relative,
                    content[:12000],
                    json_dumps(_tags_for_path(path)),
                    now,
                    now,
                ),
            )
            _upsert_item_search_entry(conn, item_uid)
            indexed += 1
            pending_documents.append(
                {
                    "title": path.stem,
                    "content": content,
                    "category": _category_for_path(path),
                    "source_type": "file",
                    "source_ref": relative,
                    "tags": payload["tags"],
                    "process_codes": payload["process_codes"],
                    "project_id": project_id,
                    "doc_uid": item_uid + "_doc",
                    "import_batch_id": batch_uid,
                    "source_owner": "platform_import",
                    "source_trust_level": "internal",
                    "source_version": "",
                }
            )
    for payload in pending_documents:
        import_knowledge_document(db_path, **payload)
    return {"indexed": indexed, "skipped": skipped}


def find_knowledge_code_archive_roots(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    if not root.exists():
        return []
    archives: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_dir() or path.name not in BOOTSTRAP_CODE_ARCHIVE_DIRS:
            continue
        if any(parent in archives for parent in path.parents):
            continue
        archives.append(path)
    return archives


def _is_bootstrap_skipped_path(root: Path, path: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    lowered = {part.lower() for part in parts}
    if lowered & CODE_IMPORT_SKIP_DIRS:
        return True
    return any(part in BOOTSTRAP_CODE_ARCHIVE_DIRS for part in parts)


def import_knowledge_code_directory(db_path: Path, root: Path, project_id: int | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"knowledge import directory not found: {root}")
    indexed = 0
    skipped = 0
    by_category: dict[str, int] = {}
    examples: list[dict[str, str]] = []
    pending_documents: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        batch_uid = ensure_knowledge_import_batch(
            conn,
            project_id=project_id,
            source_type="project_code_import",
            source_ref=str(root),
            source_owner="platform_import",
            source_trust_level="historical",
        )
        for path in root.rglob("*"):
            if _is_skipped_path(root, path) or not path.is_file():
                skipped += 1
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                skipped += 1
                continue
            content = read_text(path).strip()
            if not content or _looks_like_noise(path, content):
                skipped += 1
                continue
            relative = str(path.relative_to(root)).replace("\\", "/")
            category = _category_for_path(path)
            tags = sorted(set(_tags_for_path(path) + ["project_code_archive"]))
            payload = {
                "title": path.name,
                "content": content,
                "source_ref": relative,
                "tags": tags,
                "process_codes": _process_codes_for_text(str(path)),
            }
            if has_forbidden_payload(payload):
                skipped += 1
                continue
            item_uid = "kb_code_import_" + hashlib.sha1(f"{project_id}:{path.resolve()}".encode("utf-8")).hexdigest()[:18]
            now = utc_now()
            conn.execute(
                """
                INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'project_code_import', ?, ?, ?, 0.86, 'active', ?, ?)
                ON CONFLICT(item_uid) DO UPDATE SET
                    title=excluded.title,
                    category=excluded.category,
                    source_type=excluded.source_type,
                    source_ref=excluded.source_ref,
                    content=excluded.content,
                    tags_json=excluded.tags_json,
                    status='active',
                    updated_at=excluded.updated_at
                """,
                (project_id, item_uid, path.name, category, relative, content[:12000], json_dumps(tags), now, now),
            )
            _upsert_item_search_entry(conn, item_uid)
            indexed += 1
            by_category[category] = by_category.get(category, 0) + 1
            if len(examples) < 12:
                examples.append({"path": relative, "category": category})
            pending_documents.append(
                {
                    "title": path.name,
                    "content": content,
                    "category": category,
                    "source_type": "project_code_import",
                    "source_ref": relative,
                    "tags": tags,
                    "process_codes": payload["process_codes"],
                    "project_id": project_id,
                    "doc_uid": item_uid + "_doc",
                    "import_batch_id": batch_uid,
                    "source_owner": "platform_import",
                    "source_trust_level": "historical",
                    "source_version": str(root),
                    "material_type": infer_knowledge_material_type(category, "project_code_import", relative, tags),
                }
            )
    for payload in pending_documents:
        import_knowledge_document(db_path, **payload)
    return {
        "root_path": str(root),
        "indexed": indexed,
        "skipped": skipped,
        "by_category": by_category,
        "examples": examples,
        "recommendations": _import_recommendations(indexed, by_category),
    }


def import_knowledge_document(
    db_path: Path,
    *,
    title: str,
    content: str,
    category: str = "reference",
    source_type: str = "manual",
    source_ref: str = "",
    tags: list[str] | None = None,
    process_codes: list[str] | None = None,
    project_id: int | None = None,
    doc_uid: str | None = None,
    status: str = "active",
    import_batch_id: str = "",
    source_owner: str = "",
    source_title: str = "",
    source_uri: str = "",
    trust_level: str = "",
    source_trust_level: str = "internal",
    source_version: str = "",
    applicable_project: str = "",
    applicable_process: list[str] | None = None,
    applicable_domain: str = "",
    approval_status: str | None = None,
    expires_at: str = "",
    supersedes: str = "",
    material_type: str = "",
    code_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tags = tags or []
    process_codes = process_codes or _process_codes_for_text(" ".join([title, content[:1000], source_ref]))
    assert_personal_payload_clean(
        {
            "title": title,
            "content": content,
            "source_ref": source_ref,
            "source_title": source_title or title,
            "source_uri": source_uri or source_ref,
            "tags": tags,
            "process_codes": process_codes,
            "applicable_process": applicable_process or process_codes,
        },
        label="knowledge document",
    )
    applicable_process = applicable_process or process_codes
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()
    doc_uid = doc_uid or "kbd_" + hashlib.sha1(f"{project_id}:{title}:{source_ref}:{digest}".encode("utf-8")).hexdigest()[:18]
    doc_status = _normalize_status(status)
    approval_status = approval_status or ("approved" if doc_status == "active" else doc_status)
    material_type = material_type or infer_knowledge_material_type(category, source_type, source_ref, tags)
    source_title = source_title or title
    source_uri = source_uri or source_ref
    trust_level = trust_level or _canonical_trust_level(source_trust_level)
    now = utc_now()
    with connect(db_path) as conn:
        batch_uid = ensure_knowledge_import_batch(
            conn,
            project_id=project_id,
            source_type=source_type,
            source_ref=source_ref,
            source_owner=source_owner,
            source_trust_level=source_trust_level,
            source_version=source_version,
            import_batch_id=import_batch_id,
        )
        code_refs = code_refs or extract_code_refs(
            content,
            source_ref=source_ref,
            import_batch_id=batch_uid,
            source_version=source_version,
            source_trust_level=source_trust_level,
            material_type=material_type,
        )
        existing = conn.execute("SELECT * FROM knowledge_documents WHERE doc_uid=?", (doc_uid,)).fetchone()
        if existing and str(existing["content_hash"]) == digest and str(existing["status"]) == doc_status:
            doc_id = int(existing["id"])
            chunk_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?", (doc_id,)).fetchone()[0])
            indexed_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_search_entries WHERE document_id=?", (doc_id,)).fetchone()[0])
            if indexed_count < chunk_count:
                _upsert_document_chunk_search_entries(conn, doc_id)
                _upsert_item_search_entry(conn, _document_item_uid(str(existing["doc_uid"])), document_id=doc_id)
            payload = dict(existing)
            payload["chunk_count"] = chunk_count
            return _decode_knowledge_document_payload(payload)
        chunks = _split_chunks(content)
        conn.execute(
            """
            INSERT INTO knowledge_documents(
                project_id, doc_uid, title, category, source_type, source_ref,
                source_title, source_uri, trust_level,
                import_batch_id, source_owner, source_trust_level, source_version,
                applicable_project, applicable_process_json, applicable_domain,
                approval_status, expires_at, supersedes, material_type, code_refs_json,
                process_codes_json, tags_json, summary, content_hash, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_uid) DO UPDATE SET
                title=excluded.title,
                category=excluded.category,
                source_type=excluded.source_type,
                source_ref=excluded.source_ref,
                source_title=excluded.source_title,
                source_uri=excluded.source_uri,
                trust_level=excluded.trust_level,
                import_batch_id=excluded.import_batch_id,
                source_owner=excluded.source_owner,
                source_trust_level=excluded.source_trust_level,
                source_version=excluded.source_version,
                applicable_project=excluded.applicable_project,
                applicable_process_json=excluded.applicable_process_json,
                applicable_domain=excluded.applicable_domain,
                approval_status=excluded.approval_status,
                expires_at=excluded.expires_at,
                supersedes=excluded.supersedes,
                material_type=excluded.material_type,
                code_refs_json=excluded.code_refs_json,
                process_codes_json=excluded.process_codes_json,
                tags_json=excluded.tags_json,
                summary=excluded.summary,
                content_hash=excluded.content_hash,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                project_id,
                doc_uid,
                title,
                category,
                source_type,
                source_ref,
                source_title,
                source_uri,
                trust_level,
                batch_uid,
                source_owner,
                source_trust_level,
                source_version,
                applicable_project,
                json_dumps(applicable_process),
                applicable_domain,
                approval_status,
                expires_at,
                supersedes,
                material_type,
                json_dumps(code_refs),
                json_dumps(process_codes),
                json_dumps(tags),
                _summary(content),
                digest,
                doc_status,
                now,
                now,
            ),
        )
        doc_id = int(conn.execute("SELECT id FROM knowledge_documents WHERE doc_uid=?", (doc_uid,)).fetchone()["id"])
        if supersedes:
            conn.execute("UPDATE knowledge_documents SET superseded_by=? WHERE doc_uid=?", (doc_uid, supersedes))
        conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (doc_id,))
        _delete_document_search_entries(conn, doc_id)
        for index, chunk in enumerate(chunks):
            conn.execute(
                """
                INSERT INTO knowledge_chunks(document_id, chunk_index, heading, content, token_hint, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, index, chunk["heading"], chunk["content"], len(chunk["content"]), doc_status, now, now),
            )
        item_uid = _document_item_uid(doc_uid)
        conn.execute(
            """
            INSERT INTO knowledge_items(project_id, item_uid, title, category, source_type, source_ref, content, tags_json, confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.82, ?, ?, ?)
            ON CONFLICT(item_uid) DO UPDATE SET title=excluded.title, category=excluded.category, source_ref=excluded.source_ref, content=excluded.content, tags_json=excluded.tags_json, status=excluded.status, updated_at=excluded.updated_at
            """,
            (project_id, item_uid, title, category, source_type, source_ref, content[:12000], json_dumps(tags + process_codes), doc_status, now, now),
        )
        _upsert_item_search_entry(conn, item_uid, document_id=doc_id)
        _upsert_document_chunk_search_entries(conn, doc_id)
        governance = apply_document_governance(conn, doc_id)
        row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (doc_id,)).fetchone()
    payload = dict(row)
    payload["chunk_count"] = len(chunks)
    decoded = _decode_knowledge_document_payload(payload)
    decoded["duplicate_of"] = governance["duplicate_of"]
    decoded["conflicts"] = governance["conflicts"]
    return decoded


def list_knowledge_documents(db_path: Path, project_id: int | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        if project_id:
            rows = conn.execute(
                """
                SELECT kd.*, COUNT(kc.id) AS chunk_count
                FROM knowledge_documents kd
                LEFT JOIN knowledge_chunks kc ON kc.document_id = kd.id
                WHERE kd.project_id=? OR kd.project_id IS NULL
                GROUP BY kd.id
                ORDER BY kd.updated_at DESC
                """,
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT kd.*, COUNT(kc.id) AS chunk_count
                FROM knowledge_documents kd
                LEFT JOIN knowledge_chunks kc ON kc.document_id = kd.id
                GROUP BY kd.id
                ORDER BY kd.updated_at DESC
                """
            ).fetchall()
    result = []
    for row in rows:
        result.append(_decode_knowledge_document_payload(dict(row)))
    return result


def search_knowledge(
    db_path: Path,
    query: str,
    project_id: int | None = None,
    limit: int = 5,
    *,
    category: str | None = None,
    exclude_category: str | None = None,
    source_type: str | None = None,
    process_code: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query_context = _knowledge_query(query)
    if not query_context.normalized:
        return []
    allowed_statuses = _search_statuses(status)
    if _search_index_key(db_path) not in _SEARCH_INDEX_READY_DBS:
        ensure_knowledge_search_index(db_path)
    indexed = _search_knowledge_index(
        db_path,
        query_context,
        project_id=project_id,
        limit=limit,
        allowed_statuses=allowed_statuses,
        category=category,
        exclude_category=exclude_category,
        source_type=source_type,
        process_code=process_code,
    )
    if indexed is not None:
        return [_standardize_knowledge_result(item) for item in indexed]
    legacy = _search_knowledge_legacy_scan(
        db_path,
        query_context,
        project_id=project_id,
        limit=limit,
        allowed_statuses=allowed_statuses,
        category=category,
        exclude_category=exclude_category,
        source_type=source_type,
        process_code=process_code,
    )
    return [_standardize_knowledge_result(item) for item in legacy]


def _standardize_knowledge_result(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    source_ref = str(result.get("source_ref") or "")
    title = str(result.get("title") or "")
    result["source_type"] = str(result.get("source_type") or "unknown")
    result["source_title"] = str(result.get("source_title") or title)
    result["source_uri"] = str(result.get("source_uri") or source_ref)
    result["trust_level"] = _canonical_trust_level(result.get("trust_level") or result.get("source_trust_level") or "")
    return result


def _canonical_trust_level(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "reference"
    aliases = {
        "internal": "reference",
        "historical": "reference",
        "approved_internal": "approved_process_asset",
        "approved": "approved_process_asset",
        "manual": "reference",
        "external": "external_reference",
    }
    return aliases.get(text, text)


def _search_knowledge_legacy_scan(
    db_path: Path,
    query: _KnowledgeQuery,
    project_id: int | None,
    limit: int,
    *,
    allowed_statuses: set[str],
    category: str | None,
    exclude_category: str | None,
    source_type: str | None,
    process_code: str | None,
) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        if project_id:
            where = ["(project_id=? OR project_id IS NULL)"]
            params: list[Any] = [project_id]
        else:
            where = ["1=1"]
            params = []
        if category:
            where.append("LOWER(category)=LOWER(?)")
            params.append(category)
        if exclude_category:
            where.append("LOWER(category)<>LOWER(?)")
            params.append(exclude_category)
        rows = conn.execute(
            f"SELECT * FROM knowledge_items WHERE {' AND '.join(where)} ORDER BY updated_at DESC",
            tuple(params),
        ).fetchall()
    ranked = []
    for row in rows:
        payload = dict(row)
        tags = _loads_json(payload.pop("tags_json", "[]"))
        if not _matches_filters(payload, tags, allowed_statuses, category, exclude_category, source_type, process_code):
            continue
        text = _normalize(" ".join([payload["title"], payload["category"], payload["source_ref"], payload["content"][:2000]]))
        score = _lexical_score(query.normalized, text, query_terms=query.terms, query_grams=query.ngrams)
        if score <= 0:
            continue
        payload["score"] = round(score, 4)
        payload["tags"] = tags
        payload["excerpt"] = _excerpt(payload["content"], query.raw, query_terms=query.terms)
        ranked.append(payload)
    with connect(db_path) as conn:
        if project_id:
            where = ["(kd.project_id=? OR kd.project_id IS NULL)"]
            params: list[Any] = [project_id]
        else:
            where = ["1=1"]
            params = []
        if category:
            where.append("LOWER(kd.category)=LOWER(?)")
            params.append(category)
        if exclude_category:
            where.append("LOWER(kd.category)<>LOWER(?)")
            params.append(exclude_category)
        chunks = conn.execute(
            f"""
            SELECT kc.*, kd.project_id, kd.title, kd.category, kd.source_type, kd.source_ref, kd.tags_json, kd.process_codes_json, kd.doc_uid
            FROM knowledge_chunks kc
            JOIN knowledge_documents kd ON kd.id = kc.document_id
            WHERE {" AND ".join(where)}
            ORDER BY kd.updated_at DESC, kc.chunk_index
            """,
            tuple(params),
        ).fetchall()
    for row in chunks:
        payload = dict(row)
        tags = _loads_json(payload.pop("tags_json", "[]"))
        process_codes = _loads_json(payload.pop("process_codes_json", "[]"))
        if not _matches_filters(payload, tags, allowed_statuses, category, exclude_category, source_type, process_code, process_codes):
            continue
        text = _normalize(" ".join([payload["title"], payload["category"], payload["source_ref"], payload.get("heading", ""), payload["content"][:2500]]))
        score = _lexical_score(query.normalized, text, query_terms=query.terms, query_grams=query.ngrams)
        if score <= 0:
            continue
        ranked.append(
            {
                "id": payload["id"],
                "item_uid": f"chunk_{payload['id']}",
                "title": payload["title"],
                "category": payload["category"],
                "source_type": "document_chunk",
                "source_ref": payload["source_ref"],
                "content": payload["content"],
                "tags": tags,
                "process_codes": process_codes,
                "confidence": 0.86,
                "status": payload["status"],
                "score": round(score, 4),
                "excerpt": _excerpt(payload["content"], query.raw, query_terms=query.terms),
                "document_id": payload["document_id"],
                "chunk_id": payload["id"],
                "chunk_index": payload["chunk_index"],
                "heading": payload.get("heading", ""),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[: max(1, limit)]


def update_knowledge_document_status(db_path: Path, document_id: int, status: str) -> dict[str, Any]:
    next_status = _normalize_status(status)
    now = utc_now()
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            raise ValueError(f"KnowledgeDocument not found: {document_id}")
        conn.execute("UPDATE knowledge_documents SET status=?, updated_at=? WHERE id=?", (next_status, now, document_id))
        conn.execute("UPDATE knowledge_chunks SET status=?, updated_at=? WHERE document_id=?", (next_status, now, document_id))
        conn.execute(
            "UPDATE knowledge_items SET status=?, updated_at=? WHERE item_uid=?",
            (next_status, now, _document_item_uid(str(row["doc_uid"]))),
        )
        _sync_document_search_status(conn, document_id, next_status, now)
        _upsert_item_search_entry(conn, _document_item_uid(str(row["doc_uid"])), document_id=document_id)
        updated = conn.execute(
            """
            SELECT kd.*, COUNT(kc.id) AS chunk_count
            FROM knowledge_documents kd
            LEFT JOIN knowledge_chunks kc ON kc.document_id = kd.id
            WHERE kd.id=?
            GROUP BY kd.id
            """,
            (document_id,),
        ).fetchone()
    return _decode_knowledge_document_payload(dict(updated))


def knowledge_refs_for_task(db_path: Path, prompt: str, project_id: int | None = None, limit: int = 4) -> list[dict[str, Any]]:
    refs = search_knowledge(db_path, prompt, project_id=project_id, limit=limit)
    return [
        {
            "id": item["id"],
            "item_uid": item["item_uid"],
            "title": item["title"],
            "category": item["category"],
            "source_ref": item["source_ref"],
            "score": item["score"],
            "document_id": item.get("document_id"),
            "doc_uid": item.get("doc_uid", ""),
            "approval_status": item.get("approval_status", item.get("status", "")),
            "source_trust_level": item.get("source_trust_level", ""),
            "source_version": item.get("source_version", ""),
            "material_type": item.get("material_type", ""),
            "code_refs": item.get("code_refs", [])[:5] if isinstance(item.get("code_refs"), list) else [],
        }
        for item in refs
    ]


def ensure_knowledge_search_index(db_path: Path) -> dict[str, int | bool]:
    """Keep the lightweight search index aligned with the full knowledge store.

    The complete document and chunk bodies remain in knowledge_documents /
    knowledge_chunks. The index is only a retrieval surface so regular Agent
    turns do not need to scan every chunk body.
    """

    with connect(db_path) as conn:
        fts_ready = _ensure_search_schema(conn)
        _backfill_document_item_entry_ids(conn)
        expected = _source_entry_count(conn)
        actual = int(conn.execute("SELECT COUNT(*) FROM knowledge_search_entries").fetchone()[0])
        fts_rows = _fts_row_count(conn) if fts_ready else 0
        rebuilt = False
        if expected and (actual < expected or (fts_ready and fts_rows < actual)):
            _rebuild_knowledge_search_index(conn)
            actual = int(conn.execute("SELECT COUNT(*) FROM knowledge_search_entries").fetchone()[0])
            fts_rows = _fts_row_count(conn) if fts_ready else 0
            rebuilt = True
        if actual >= expected and (not fts_ready or fts_rows >= actual):
            _SEARCH_INDEX_READY_DBS.add(_search_index_key(db_path))
        return {"expected": expected, "indexed": actual, "fts_rows": fts_rows, "fts_ready": fts_ready, "rebuilt": rebuilt}


def index_knowledge_item_search_entry(db_path: Path, item_uid: str) -> None:
    with connect(db_path) as conn:
        _upsert_item_search_entry(conn, item_uid)


def _search_index_key(db_path: Path) -> str:
    return str(db_path.resolve()).lower()


def _ensure_search_schema(conn: sqlite3.Connection) -> bool:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_search_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            source_kind TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            document_id INTEGER,
            item_uid TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            heading TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            process_codes_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            content_hash TEXT NOT NULL DEFAULT '',
            content_preview TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(source_kind, source_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_project_status ON knowledge_search_entries(project_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_source ON knowledge_search_entries(source_kind, source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_document ON knowledge_search_entries(document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_search_entries_item_uid ON knowledge_search_entries(source_kind, item_uid)")
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_search_fts USING fts5(
                title,
                category,
                source_type,
                source_ref,
                heading,
                tags,
                process_codes,
                content
            )
            """
        )
    except sqlite3.OperationalError:
        return False
    return True


def _backfill_document_item_entry_ids(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, doc_uid FROM knowledge_documents").fetchall()
    for row in rows:
        conn.execute(
            """
            UPDATE knowledge_search_entries
            SET document_id=?
            WHERE source_kind='item' AND item_uid=? AND document_id IS NULL
            """,
            (int(row["id"]), _document_item_uid(str(row["doc_uid"]))),
        )


def _source_entry_count(conn: sqlite3.Connection) -> int:
    items = int(conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0])
    chunks = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM knowledge_chunks kc
            JOIN knowledge_documents kd ON kd.id = kc.document_id
            """
        ).fetchone()[0]
    )
    return items + chunks


def _fts_row_count(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM knowledge_search_fts").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _rebuild_knowledge_search_index(conn: sqlite3.Connection) -> None:
    _ensure_search_schema(conn)
    conn.execute("DELETE FROM knowledge_search_entries")
    _clear_search_fts(conn)
    for row in conn.execute("SELECT item_uid FROM knowledge_items ORDER BY id").fetchall():
        _upsert_item_search_entry(conn, str(row["item_uid"]))
    for row in conn.execute("SELECT id FROM knowledge_documents ORDER BY id").fetchall():
        _upsert_document_chunk_search_entries(conn, int(row["id"]))
    _backfill_document_item_entry_ids(conn)


def _search_knowledge_index(
    db_path: Path,
    query: _KnowledgeQuery,
    *,
    project_id: int | None,
    limit: int,
    allowed_statuses: set[str],
    category: str | None,
    exclude_category: str | None,
    source_type: str | None,
    process_code: str | None,
) -> list[dict[str, Any]] | None:
    match_query = _fts_query(query)
    if not match_query:
        return []
    with connect(db_path) as conn:
        if not _ensure_search_schema(conn):
            return None
        where = ["knowledge_search_fts MATCH ?"]
        params: list[Any] = [match_query]
        if project_id:
            where.append("(e.project_id=? OR e.project_id IS NULL)")
            params.append(project_id)
        if category:
            where.append("LOWER(e.category)=LOWER(?)")
            params.append(category)
        if exclude_category:
            where.append("LOWER(e.category)<>LOWER(?)")
            params.append(exclude_category)
        if allowed_statuses:
            placeholders = ",".join("?" for _ in allowed_statuses)
            where.append(f"e.status IN ({placeholders})")
            params.extend(sorted(allowed_statuses))
        rows = conn.execute(
            f"""
            SELECT e.*, bm25(knowledge_search_fts) AS rank
            FROM knowledge_search_fts
            JOIN knowledge_search_entries e ON e.id = knowledge_search_fts.rowid
            WHERE {" AND ".join(where)}
            ORDER BY rank
            LIMIT ?
            """,
            (*params, max(SEARCH_INDEX_CANDIDATE_LIMIT, limit * 8)),
        ).fetchall()
        ranked: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            tags = _loads_json(payload.get("tags_json", "[]"))
            process_codes = _loads_json(payload.get("process_codes_json", "[]"))
            if not _matches_filters(payload, tags, allowed_statuses, category, exclude_category, source_type, process_code, process_codes):
                continue
            result = _materialize_search_result(conn, payload, tags, process_codes, query)
            if result:
                ranked.append(result)
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[: max(1, limit)]


def _materialize_search_result(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    tags: list[str],
    process_codes: list[str],
    query: _KnowledgeQuery,
) -> dict[str, Any] | None:
    source_kind = str(payload.get("source_kind") or "")
    source_id = int(payload.get("source_id") or 0)
    rank = float(payload.get("rank") or 0)
    if source_kind == "item":
        row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (source_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        content = str(item.get("content") or "")
        score = _indexed_score(rank, query.raw, " ".join([str(item.get("title", "")), content[:2500]]), query_context=query)
        item["tags"] = _loads_json(item.pop("tags_json", "[]"))
        item["score"] = score
        item["excerpt"] = _excerpt(content, query.raw, query_terms=query.terms)
        item["search_mode"] = "indexed"
        doc_payload = _document_for_item_uid(conn, str(item.get("item_uid", "")))
        if doc_payload:
            item.update(_governance_search_payload(doc_payload))
        return item
    if source_kind == "chunk":
        row = conn.execute("SELECT * FROM knowledge_chunks WHERE id=?", (source_id,)).fetchone()
        if not row:
            return None
        doc = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (payload["document_id"],)).fetchone()
        doc_payload = dict(doc) if doc else {}
        content = str(row["content"] or "")
        score = _indexed_score(rank, query.raw, " ".join([str(payload.get("title", "")), str(payload.get("heading", "")), content[:2500]]), query_context=query)
        return {
            "id": source_id,
            "item_uid": f"chunk_{source_id}",
            "title": payload["title"],
            "category": payload["category"],
            "source_type": "document_chunk",
            "source_ref": payload["source_ref"],
            "content": content,
            "tags": tags,
            "process_codes": process_codes,
            "confidence": 0.86,
            "status": row["status"],
            "score": score,
            "excerpt": _excerpt(content, query.raw, query_terms=query.terms),
            "document_id": payload["document_id"],
            "chunk_id": source_id,
            "chunk_index": row["chunk_index"],
            "heading": payload.get("heading", ""),
            "search_mode": "indexed",
            "doc_uid": doc_payload.get("doc_uid", ""),
            "import_batch_id": doc_payload.get("import_batch_id", ""),
            "source_owner": doc_payload.get("source_owner", ""),
            "source_trust_level": doc_payload.get("source_trust_level", ""),
            "source_version": doc_payload.get("source_version", ""),
            "applicable_project": doc_payload.get("applicable_project", ""),
            "applicable_domain": doc_payload.get("applicable_domain", ""),
            "approval_status": doc_payload.get("approval_status", row["status"]),
            "expires_at": doc_payload.get("expires_at", ""),
            "supersedes": doc_payload.get("supersedes", ""),
            "superseded_by": doc_payload.get("superseded_by", ""),
            "material_type": doc_payload.get("material_type", "reference_document"),
            "code_refs": _loads_json(doc_payload.get("code_refs_json", "[]")) if doc_payload else [],
            "duplicate_of": doc_payload.get("duplicate_of", ""),
        }
    return None


def _upsert_item_search_entry(conn: sqlite3.Connection, item_uid: str, document_id: int | None = None) -> None:
    if not _ensure_search_schema(conn):
        return
    row = conn.execute("SELECT * FROM knowledge_items WHERE item_uid=?", (item_uid,)).fetchone()
    if not row:
        return
    payload = dict(row)
    tags = _loads_json(payload.get("tags_json", "[]"))
    _replace_search_entry(
        conn,
        source_kind="item",
        source_id=int(payload["id"]),
        document_id=document_id,
        item_uid=str(payload["item_uid"]),
        project_id=payload.get("project_id"),
        title=str(payload["title"]),
        category=str(payload["category"]),
        source_type=str(payload["source_type"]),
        source_ref=str(payload["source_ref"]),
        heading="",
        tags=tags,
        process_codes=[],
        status=str(payload["status"]),
        content=str(payload["content"]),
        updated_at=str(payload["updated_at"]),
    )


def _upsert_document_chunk_search_entries(conn: sqlite3.Connection, document_id: int) -> None:
    if not _ensure_search_schema(conn):
        return
    doc = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
    if not doc:
        return
    doc_payload = dict(doc)
    tags = _loads_json(doc_payload.get("tags_json", "[]"))
    process_codes = _loads_json(doc_payload.get("process_codes_json", "[]"))
    rows = conn.execute("SELECT * FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index", (document_id,)).fetchall()
    for row in rows:
        chunk = dict(row)
        _replace_search_entry(
            conn,
            source_kind="chunk",
            source_id=int(chunk["id"]),
            document_id=document_id,
            item_uid=f"chunk_{chunk['id']}",
            project_id=doc_payload.get("project_id"),
            title=str(doc_payload["title"]),
            category=str(doc_payload["category"]),
            source_type=str(doc_payload["source_type"]),
            source_ref=str(doc_payload["source_ref"]),
            heading=str(chunk.get("heading") or ""),
            tags=tags,
            process_codes=process_codes,
            status=str(chunk["status"]),
            content=str(chunk["content"]),
            updated_at=str(chunk["updated_at"]),
        )


def _replace_search_entry(
    conn: sqlite3.Connection,
    *,
    source_kind: str,
    source_id: int,
    document_id: int | None,
    item_uid: str,
    project_id: int | None,
    title: str,
    category: str,
    source_type: str,
    source_ref: str,
    heading: str,
    tags: list[str],
    process_codes: list[str],
    status: str,
    content: str,
    updated_at: str,
) -> None:
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()
    existing = conn.execute(
        "SELECT id FROM knowledge_search_entries WHERE source_kind=? AND source_id=?",
        (source_kind, source_id),
    ).fetchone()
    if existing:
        entry_id = int(existing["id"])
        _delete_search_fts_row(conn, entry_id)
        conn.execute(
            """
            UPDATE knowledge_search_entries
            SET project_id=?, document_id=?, item_uid=?, title=?, category=?, source_type=?, source_ref=?,
                heading=?, tags_json=?, process_codes_json=?, status=?, content_hash=?, content_preview=?, updated_at=?
            WHERE id=?
            """,
            (
                project_id,
                document_id,
                item_uid,
                title,
                category,
                source_type,
                source_ref,
                heading,
                json_dumps(tags),
                json_dumps(process_codes),
                status,
                digest,
                content[:SEARCH_INDEX_PREVIEW_CHARS],
                updated_at,
                entry_id,
            ),
        )
    else:
        cursor = conn.execute(
            """
            INSERT INTO knowledge_search_entries(
                project_id, source_kind, source_id, document_id, item_uid, title, category, source_type,
                source_ref, heading, tags_json, process_codes_json, status, content_hash, content_preview, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                source_kind,
                source_id,
                document_id,
                item_uid,
                title,
                category,
                source_type,
                source_ref,
                heading,
                json_dumps(tags),
                json_dumps(process_codes),
                status,
                digest,
                content[:SEARCH_INDEX_PREVIEW_CHARS],
                updated_at,
            ),
        )
        entry_id = int(cursor.lastrowid)
    _insert_search_fts_row(conn, entry_id, title, category, source_type, source_ref, heading, tags, process_codes, content)


def _insert_search_fts_row(
    conn: sqlite3.Connection,
    entry_id: int,
    title: str,
    category: str,
    source_type: str,
    source_ref: str,
    heading: str,
    tags: list[str],
    process_codes: list[str],
    content: str,
) -> None:
    try:
        conn.execute(
            """
            INSERT INTO knowledge_search_fts(rowid, title, category, source_type, source_ref, heading, tags, process_codes, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entry_id, title, category, source_type, source_ref, heading, " ".join(tags), " ".join(process_codes), content),
        )
    except sqlite3.OperationalError:
        return


def _delete_document_search_entries(conn: sqlite3.Connection, document_id: int) -> None:
    if not _ensure_search_schema(conn):
        return
    rows = conn.execute("SELECT id FROM knowledge_search_entries WHERE document_id=?", (document_id,)).fetchall()
    for row in rows:
        _delete_search_fts_row(conn, int(row["id"]))
    conn.execute("DELETE FROM knowledge_search_entries WHERE document_id=?", (document_id,))


def _sync_document_search_status(conn: sqlite3.Connection, document_id: int, status: str, updated_at: str) -> None:
    if not _ensure_search_schema(conn):
        return
    conn.execute("UPDATE knowledge_search_entries SET status=?, updated_at=? WHERE document_id=?", (status, updated_at, document_id))


def _clear_search_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DELETE FROM knowledge_search_fts")
    except sqlite3.OperationalError:
        return


def _delete_search_fts_row(conn: sqlite3.Connection, rowid: int) -> None:
    try:
        conn.execute("DELETE FROM knowledge_search_fts WHERE rowid=?", (rowid,))
    except sqlite3.OperationalError:
        return


def _fts_query(query: _KnowledgeQuery | str) -> str:
    terms = []
    query_terms = query.terms if isinstance(query, _KnowledgeQuery) else _terms(query)
    for term in query_terms:
        cleaned = term.replace('"', '""').strip()
        if cleaned and len(cleaned) <= 80:
            terms.append(f'"{cleaned}"')
    return " OR ".join(dict.fromkeys(terms[:12]))


def _indexed_score(rank: float, query: str, text: str, *, query_context: _KnowledgeQuery | None = None) -> float:
    context = query_context or _knowledge_query(query)
    lexical = _lexical_score(context.normalized, _normalize(text), query_terms=context.terms, query_grams=context.ngrams)
    # SQLite FTS5 bm25() uses lower values as better matches and commonly
    # returns negative scores. The magnitude carries the match strength.
    fts_score = abs(rank) / (1.0 + abs(rank))
    return round(fts_score * 0.55 + lexical * 0.45, 4)


def _document_item_uid(doc_uid: str) -> str:
    return "kb_doc_" + hashlib.sha1(doc_uid.encode("utf-8")).hexdigest()[:16]


def has_forbidden_payload(payload: dict[str, Any]) -> bool:
    try:
        assert_personal_payload_clean(payload, label="knowledge payload")
    except ValueError:
        return True
    return False


def _normalize_status(status: str | None) -> str:
    value = (status or "active").strip().lower()
    aliases = {
        "approved": "active",
        "enabled": "active",
        "candidate": "candidate",
        "draft": "draft",
        "deprecated": "deprecated",
        "archived": "deprecated",
        "rejected": "deprecated",
    }
    return aliases.get(value, value or "active")


def _search_statuses(status: str | None) -> set[str]:
    if status:
        normalized = _normalize_status(status)
        return {"active", "approved"} if normalized == "active" else {normalized}
    return {"active", "approved"}


def _matches_filters(
    payload: dict[str, Any],
    tags: Any,
    allowed_statuses: set[str],
    category: str | None,
    exclude_category: str | None,
    source_type: str | None,
    process_code: str | None,
    process_codes: Any = None,
) -> bool:
    if _normalize_status(str(payload.get("status", ""))) not in allowed_statuses:
        return False
    if category and str(payload.get("category", "")).lower() != category.lower():
        return False
    if exclude_category and str(payload.get("category", "")).lower() == exclude_category.lower():
        return False
    if source_type and str(payload.get("source_type", "")).lower() != source_type.lower():
        return False
    if process_code:
        needle = process_code.upper()
        tag_values = [str(item).upper() for item in tags] if isinstance(tags, list) else []
        code_values = [str(item).upper() for item in process_codes] if isinstance(process_codes, list) else []
        if needle not in set(tag_values + code_values):
            return False
    return True


def _category_for_path(path: Path) -> str:
    name = str(path).lower()
    if "review" in name or "check" in name:
        return "review_rule"
    if "需求" in name or "requirement" in name or "srs" in name:
        return "requirement_template"
    if "架构" in name or "architecture" in name:
        return "architecture_template"
    if "详细" in name or "design" in name or "sdd" in name:
        return "design_template"
    if path.suffix.lower() in {".c", ".h"}:
        return "c_code_template"
    if "测试" in name or "test" in name:
        return "test_template"
    return "template"


def _is_skipped_path(root: Path, path: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in CODE_IMPORT_SKIP_DIRS for part in parts)


def _looks_like_noise(path: Path, content: str) -> bool:
    lower = path.name.lower()
    if lower in {"license", "license.txt", "copying", "readme.license"}:
        return True
    if len(content) > 300000:
        return True
    return False


def _import_recommendations(indexed: int, by_category: dict[str, int]) -> list[str]:
    notes = []
    if indexed == 0:
        notes.append("未导入可用资料，请确认目录中包含 .c/.h/.md/.txt/.json/.yaml 文件。")
    if not by_category.get("c_code_template"):
        notes.append("未发现 C/H 源码样例，代码风格 Profile 的可靠性会降低。")
    if not by_category.get("test_template"):
        notes.append("测试样例偏少，后续单元测试生成可能缺少项目惯用写法。")
    if not notes:
        notes.append("资料导入可用于抽取代码规范 Profile，并进入 Agent 生成上下文。")
    return notes


def _tags_for_path(path: Path) -> list[str]:
    tags = ["knowledge", path.suffix.lower().lstrip(".")]
    text = str(path)
    for token in ["C"]:
        if token.lower() in text.lower():
            tags.append(token)
    return tags


def _process_codes_for_text(text: str) -> list[str]:
    return []


def _split_chunks(content: str, max_chars: int = 1800) -> list[dict[str, str]]:
    lines = content.splitlines()
    chunks: list[dict[str, str]] = []
    current: list[str] = []
    heading = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if current:
                chunks.extend(_flush_chunk(current, heading, max_chars))
                current = []
            heading = stripped.lstrip("#").strip()
        current.append(line)
        if sum(len(item) + 1 for item in current) >= max_chars:
            chunks.extend(_flush_chunk(current, heading, max_chars))
            current = []
    if current:
        chunks.extend(_flush_chunk(current, heading, max_chars))
    if not chunks and content.strip():
        chunks.append({"heading": heading, "content": content[:max_chars]})
    return chunks


def _flush_chunk(lines: list[str], heading: str, max_chars: int) -> list[dict[str, str]]:
    text = "\n".join(lines).strip()
    if not text:
        return []
    result = []
    start = 0
    while start < len(text):
        result.append({"heading": heading, "content": text[start : start + max_chars]})
        start += max_chars
    return result


def _summary(content: str) -> str:
    return content.strip().replace("\n", " ")[:400]


def _knowledge_query(query: str) -> _KnowledgeQuery:
    normalized = _normalize(query)
    terms = _terms(query)
    return _KnowledgeQuery(raw=query, normalized=normalized, terms=terms, ngrams=_ngrams(normalized))


def _lexical_score(query: str, text: str, *, query_terms: list[str] | None = None, query_grams: set[str] | None = None) -> float:
    query_terms_set = set(query_terms if query_terms is not None else _terms(query))
    text_terms = set(_terms(text))
    if not query_terms_set or not text_terms:
        return 0.0
    term_overlap = len(query_terms_set & text_terms) / len(query_terms_set)
    query_grams = query_grams if query_grams is not None else _ngrams(query)
    text_grams = _ngrams(text[:4000])
    gram_overlap = len(query_grams & text_grams) / max(1, len(query_grams))
    return term_overlap * 0.72 + gram_overlap * 0.28


def _terms(text: str) -> list[str]:
    rough = []
    current = []
    for char in text:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            current.append(char)
        else:
            if current:
                rough.append("".join(current))
                current = []
    if current:
        rough.append("".join(current))
    return rough


def _ngrams(text: str, size: int = 2) -> set[str]:
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _normalize(text: str) -> str:
    return "".join(text.lower().split())


def _excerpt(content: str, query: str, *, query_terms: list[str] | None = None) -> str:
    if not content:
        return ""
    query_terms = query_terms if query_terms is not None else _terms(query)
    lowered = content.lower()
    for term in query_terms:
        index = lowered.find(term.lower())
        if index >= 0:
            start = max(0, index - 80)
            end = min(len(content), index + 220)
            return content[start:end].replace("\n", " ").strip()
    return content[:260].replace("\n", " ").strip()


def _loads_json(text: str) -> Any:
    import json

    try:
        return json.loads(text)
    except Exception:
        return text


def _decode_knowledge_document_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload["process_codes"] = _loads_json(payload.pop("process_codes_json", "[]"))
    payload["tags"] = _loads_json(payload.pop("tags_json", "[]"))
    payload["applicable_process"] = _loads_json(payload.pop("applicable_process_json", "[]"))
    payload["code_refs"] = _loads_json(payload.pop("code_refs_json", "[]"))
    payload["conflicts"] = _loads_json(payload.pop("conflict_set_json", "[]"))
    payload["source_title"] = str(payload.get("source_title") or payload.get("title") or "")
    payload["source_uri"] = str(payload.get("source_uri") or payload.get("source_ref") or "")
    payload["trust_level"] = _canonical_trust_level(payload.get("trust_level") or payload.get("source_trust_level") or "")
    return payload


def _document_for_item_uid(conn: sqlite3.Connection, item_uid: str) -> dict[str, Any] | None:
    if not item_uid.startswith("kb_doc_"):
        return None
    entry = conn.execute(
        """
        SELECT document_id
        FROM knowledge_search_entries
        WHERE source_kind='item' AND item_uid=? AND document_id IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (item_uid,),
    ).fetchone()
    if not entry:
        return None
    row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (int(entry["document_id"]),)).fetchone()
    return dict(row) if row else None


def _governance_search_payload(doc_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": doc_payload.get("id"),
        "doc_uid": doc_payload.get("doc_uid", ""),
        "import_batch_id": doc_payload.get("import_batch_id", ""),
        "source_owner": doc_payload.get("source_owner", ""),
        "source_title": doc_payload.get("source_title", doc_payload.get("title", "")),
        "source_uri": doc_payload.get("source_uri", doc_payload.get("source_ref", "")),
        "trust_level": _canonical_trust_level(doc_payload.get("trust_level") or doc_payload.get("source_trust_level", "")),
        "source_trust_level": doc_payload.get("source_trust_level", ""),
        "source_version": doc_payload.get("source_version", ""),
        "applicable_project": doc_payload.get("applicable_project", ""),
        "applicable_domain": doc_payload.get("applicable_domain", ""),
        "approval_status": doc_payload.get("approval_status", doc_payload.get("status", "")),
        "expires_at": doc_payload.get("expires_at", ""),
        "supersedes": doc_payload.get("supersedes", ""),
        "superseded_by": doc_payload.get("superseded_by", ""),
        "material_type": doc_payload.get("material_type", "reference_document"),
        "code_refs": _loads_json(doc_payload.get("code_refs_json", "[]")),
        "duplicate_of": doc_payload.get("duplicate_of", ""),
    }
