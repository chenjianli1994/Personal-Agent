from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from personal_agent.core.database import connect
from personal_agent.core.knowledge_base import search_knowledge
from personal_agent.core.utils import json_dumps, utc_now


def recall_knowledge(
    db_path: Path,
    *,
    project_id: int,
    query: str,
    limit: int = 8,
    category: str | None = None,
    exclude_category: str | None = None,
    source_type: str | None = None,
    status: str | None = "active",
) -> list[dict[str, Any]]:
    results = search_knowledge(
        db_path,
        query,
        project_id=project_id,
        limit=limit,
        category=category,
        exclude_category=exclude_category,
        source_type=source_type,
        status=status,
    )
    if category == "memory_lesson":
        by_uid = {str(item.get("item_uid") or ""): dict(item) for item in results if str(item.get("item_uid") or "").strip()}
        for item in _recall_memory_rows(db_path, project_id=project_id, query=query, limit=limit, source_type=source_type, status=status):
            item_uid = str(item.get("item_uid") or "")
            if item_uid and item_uid not in by_uid:
                by_uid[item_uid] = item
        results = list(by_uid.values())
    normalized = [_normalize_recall_item(item) for item in results]
    normalized.sort(key=_recall_rank, reverse=True)
    return normalized[: max(1, limit)]


def recall_knowledge_for_context(db_path: Path, *, project_id: int, query: str, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    memories = recall_knowledge(db_path, project_id=project_id, query=query, limit=limit, category="memory_lesson")
    memory_uids = {str(item.get("item_uid") or "") for item in memories if str(item.get("item_uid") or "").strip()}
    knowledge = recall_knowledge(db_path, project_id=project_id, query=query, limit=limit, exclude_category="memory_lesson")
    knowledge = [item for item in knowledge if str(item.get("item_uid") or "") not in memory_uids]
    return {"knowledge": knowledge, "memories": memories}


def record_recall_feedback(db_path: Path, *, item_uid: str, event: str) -> dict[str, Any]:
    event = event.strip().lower()
    if event not in {"use", "helpful", "unhelpful"}:
        raise ValueError(f"unsupported recall feedback event: {event}")
    now = utc_now()
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM knowledge_items WHERE item_uid=?", (item_uid,)).fetchone()
        if row is None:
            raise ValueError(f"knowledge item not found: {item_uid}")
        if event == "use":
            conn.execute(
                """
                UPDATE knowledge_items
                SET use_count = COALESCE(use_count, 0) + 1,
                    last_used_at=?,
                    updated_at=?
                WHERE item_uid=?
                """,
                (now, now, item_uid),
            )
        elif event == "helpful":
            conn.execute(
                """
                UPDATE knowledge_items
                SET helpful_count = COALESCE(helpful_count, 0) + 1,
                    updated_at=?
                WHERE item_uid=?
                """,
                (now, item_uid),
            )
        else:
            conn.execute(
                """
                UPDATE knowledge_items
                SET unhelpful_count = COALESCE(unhelpful_count, 0) + 1,
                    updated_at=?
                WHERE item_uid=?
                """,
                (now, item_uid),
            )
        updated = conn.execute("SELECT * FROM knowledge_items WHERE item_uid=?", (item_uid,)).fetchone()
    return _normalize_recall_item(dict(updated))


def safe_recall_prompt_item(
    item: dict[str, Any],
    *,
    forbidden_text_checker: Callable[[str], Any] | None = None,
    content_limit: int = 1600,
) -> dict[str, Any]:
    content = str(item.get("content") or "").strip()
    payload = {
        "item_uid": str(item.get("item_uid") or ""),
        "title": str(item.get("title") or ""),
        "category": str(item.get("category") or ""),
        "source_type": str(item.get("source_type") or ""),
        "source_ref": str(item.get("source_ref") or ""),
        "content": content[:content_limit],
        "confidence": item.get("confidence"),
        "score": item.get("score"),
        "use_count": int(item.get("use_count") or 0),
        "helpful_count": int(item.get("helpful_count") or 0),
        "unhelpful_count": int(item.get("unhelpful_count") or 0),
        "last_used_at": str(item.get("last_used_at") or ""),
    }
    if forbidden_text_checker and forbidden_text_checker(json_dumps(payload)):
        return {}
    return payload


def _normalize_recall_item(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    if "tags_json" in result:
        result["tags"] = _loads_json(result.pop("tags_json"), [])
    for key in ("use_count", "helpful_count", "unhelpful_count"):
        result[key] = int(result.get(key) or 0)
    result["last_used_at"] = str(result.get("last_used_at") or "")
    result["content_excerpt"] = str(result.get("content") or "")
    return result


def _recall_rank(item: dict[str, Any]) -> tuple[float, int, float]:
    score = _bounded_float(item.get("score"))
    helpful = int(item.get("helpful_count") or 0)
    use_count = int(item.get("use_count") or 0)
    unhelpful = int(item.get("unhelpful_count") or 0)
    confidence = _bounded_float(item.get("confidence"))
    helpful_rate = helpful / max(1, helpful + unhelpful)
    use_boost = min(use_count, 20) / 20
    unhelpful_penalty = min(unhelpful, 20) * 0.04
    adjusted = score * 0.7 + confidence * 0.18 + helpful_rate * 0.08 + use_boost * 0.04 - unhelpful_penalty
    return (round(adjusted, 4), helpful - unhelpful, confidence)


def _recall_memory_rows(
    db_path: Path,
    *,
    project_id: int,
    query: str,
    limit: int,
    source_type: str | None,
    status: str | None,
) -> list[dict[str, Any]]:
    normalized_query = _normalize_query(query)
    allowed_status = _normalize_status(status)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM knowledge_items
            WHERE category='memory_lesson'
              AND (project_id=? OR project_id IS NULL)
            ORDER BY id DESC
            """,
            (project_id,),
        ).fetchall()
    ranked: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        if allowed_status and _normalize_status(str(payload.get("status") or "")) != allowed_status:
            continue
        if source_type and str(payload.get("source_type") or "").lower() != source_type.lower():
            continue
        item = _normalize_recall_item(payload)
        haystack = _normalize_query(" ".join([str(item.get("title") or ""), str(item.get("content") or "")]))
        score = _memory_match_score(normalized_query, haystack, item)
        if score <= 0 and normalized_query:
            continue
        item["score"] = score
        ranked.append(item)
    if not normalized_query:
        ranked.sort(key=_recall_rank, reverse=True)
        return ranked[: max(1, limit)]
    ranked.sort(key=_recall_rank, reverse=True)
    return ranked[: max(1, limit)]


def _memory_match_score(query: str, haystack: str, item: dict[str, Any]) -> float:
    if not query:
        return 0.4
    query_terms = _memory_terms(query)
    if not query_terms:
        return 0.0
    score = 0.0
    for term in dict.fromkeys(query_terms):
        if term and term in haystack:
            score += 0.18
    if any(_normalize_query(term) in haystack for term in query_terms if len(term) >= 2):
        score += 0.12
    return round(max(0.0, score), 4)


def _memory_terms(text: str) -> list[str]:
    compact = _normalize_query(text)
    if not compact:
        return []
    terms = list(dict.fromkeys(_split_cjk_chunks(compact) + _window_terms(compact)))
    return [term for term in terms if len(term) >= 2]


def _split_cjk_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    current = []
    for char in text:
        if "\u4e00" <= char <= "\u9fff" or char.isalnum():
            current.append(char)
        else:
            if current:
                chunks.append("".join(current))
                current = []
    if current:
        chunks.append("".join(current))
    return chunks


def _window_terms(text: str, size: int = 4) -> list[str]:
    if len(text) <= size:
        return [text]
    return [text[index : index + size] for index in range(max(0, len(text) - size + 1))]


def _normalize_query(text: str) -> str:
    return "".join(ch.lower() for ch in text if not ch.isspace())


def _normalize_status(status: str | None) -> str:
    value = str(status or "active").strip().lower()
    aliases = {"approved": "active", "enabled": "active", "deprecated": "deprecated", "archived": "deprecated", "rejected": "deprecated"}
    return aliases.get(value, value or "active")


def _bounded_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _loads_json(text: Any, default: Any) -> Any:
    import json

    try:
        return json.loads(text or "")
    except Exception:
        return default
