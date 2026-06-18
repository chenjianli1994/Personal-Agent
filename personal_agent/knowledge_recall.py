from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from personal_agent.core.database import connect
from personal_agent.core.knowledge_base import index_knowledge_item_search_entry, search_knowledge
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
    query_variants = _recall_query_variants(query)
    results = _recall_search_results(
        db_path,
        query_variants=query_variants,
        project_id=project_id,
        limit=limit,
        category=category,
        exclude_category=exclude_category,
        source_type=source_type,
        status=status,
    )
    if category == "memory_lesson":
        by_uid = {str(item.get("item_uid") or ""): dict(item) for item in results if str(item.get("item_uid") or "").strip()}
        for item in _recall_memory_rows(
            db_path,
            project_id=project_id,
            query=query,
            query_variants=query_variants,
            limit=limit,
            source_type=source_type,
            status=status,
        ):
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


def consolidate_memory_lessons(
    db_path: Path,
    *,
    project_id: int,
    duplicate_threshold: float = 0.7,
    conflict_threshold: float = 0.4,
    low_utility_unhelpful_threshold: int = 2,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM knowledge_items
            WHERE category='memory_lesson'
              AND status='active'
              AND (project_id=? OR project_id IS NULL)
            ORDER BY id ASC
            """,
            (project_id,),
        ).fetchall()
    items = [_normalize_recall_item(dict(row)) for row in rows]
    item_by_uid = {str(item.get("item_uid") or ""): dict(item) for item in items if str(item.get("item_uid") or "").strip()}
    duplicate_pairs: list[dict[str, Any]] = []
    conflict_pairs: list[dict[str, Any]] = []
    duplicate_losers: dict[str, str] = {}
    conflict_tags: dict[str, set[str]] = {}

    for left_index, left in enumerate(items):
        left_uid = str(left.get("item_uid") or "").strip()
        if not left_uid:
            continue
        for right in items[left_index + 1 :]:
            right_uid = str(right.get("item_uid") or "").strip()
            if not right_uid:
                continue
            similarity = _memory_similarity(left, right)
            if similarity >= duplicate_threshold and not _memory_is_conflict(left, right):
                winner_uid, loser_uid = _memory_consolidation_pair(left, right)
                if loser_uid not in duplicate_losers:
                    duplicate_losers[loser_uid] = winner_uid
                    duplicate_pairs.append(
                        {
                            "winner": winner_uid,
                            "loser": loser_uid,
                            "similarity": similarity,
                        }
                    )
                continue
            if similarity >= conflict_threshold and _memory_is_conflict(left, right):
                conflict_pairs.append(
                    {
                        "left": left_uid,
                        "right": right_uid,
                        "similarity": similarity,
                    }
                )
                conflict_tags.setdefault(left_uid, set()).add(right_uid)
                conflict_tags.setdefault(right_uid, set()).add(left_uid)

    updated_item_uids: list[str] = []
    with connect(db_path) as conn:
        for item in items:
            item_uid = str(item.get("item_uid") or "").strip()
            if not item_uid:
                continue
            tags = _item_tags(item)
            updated = False
            if item_uid in duplicate_losers:
                winner_uid = duplicate_losers[item_uid]
                tags = _merge_tags(tags, ["suspected_duplicate", f"duplicate_of:{winner_uid}"])
                if str(item.get("status") or "").lower() != "deprecated":
                    conn.execute(
                        "UPDATE knowledge_items SET status='deprecated', tags_json=?, updated_at=? WHERE item_uid=?",
                        (json_dumps(tags), utc_now(), item_uid),
                    )
                    updated = True
                else:
                    conn.execute(
                        "UPDATE knowledge_items SET tags_json=?, updated_at=? WHERE item_uid=?",
                        (json_dumps(tags), utc_now(), item_uid),
                    )
                    updated = True
            elif _memory_is_low_utility(item, threshold=low_utility_unhelpful_threshold):
                tags = _merge_tags(tags, ["low_utility"])
                if str(item.get("status") or "").lower() != "deprecated":
                    conn.execute(
                        "UPDATE knowledge_items SET status='deprecated', tags_json=?, updated_at=? WHERE item_uid=?",
                        (json_dumps(tags), utc_now(), item_uid),
                    )
                    updated = True
                else:
                    conn.execute(
                        "UPDATE knowledge_items SET tags_json=?, updated_at=? WHERE item_uid=?",
                        (json_dumps(tags), utc_now(), item_uid),
                    )
                    updated = True
            if item_uid in conflict_tags:
                tags = _merge_tags(tags, ["suspected_conflict", *[f"conflict_with:{uid}" for uid in sorted(conflict_tags[item_uid])]])
                conn.execute(
                    "UPDATE knowledge_items SET tags_json=?, updated_at=? WHERE item_uid=?",
                    (json_dumps(tags), utc_now(), item_uid),
                )
                updated = True
            if updated:
                updated_item_uids.append(item_uid)
    for item_uid in dict.fromkeys(updated_item_uids):
        index_knowledge_item_search_entry(db_path, item_uid)
    return {
        "project_id": project_id,
        "duplicate_pairs": duplicate_pairs,
        "conflict_pairs": conflict_pairs,
        "low_utility_item_uids": [item_uid for item_uid in item_by_uid if _memory_is_low_utility(item_by_uid[item_uid], threshold=low_utility_unhelpful_threshold)],
        "updated_item_uids": list(dict.fromkeys(updated_item_uids)),
        "duplicate_loser_uids": list(dict.fromkeys(duplicate_losers)),
        "conflict_item_uids": sorted(conflict_tags),
    }


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
    item_uid = str(item.get("item_uid") or "").strip()
    category = str(item.get("category") or "").strip()
    source_type = str(item.get("source_type") or "").strip()
    if not item_uid or not category:
        return {}
    if _field_has_forbidden(item_uid, forbidden_text_checker) or _field_has_forbidden(category, forbidden_text_checker):
        return {}
    if source_type and _field_has_forbidden(source_type, forbidden_text_checker):
        return {}

    redacted_fields: list[str] = []
    title = str(item.get("title") or "").strip()
    if _field_has_forbidden(title, forbidden_text_checker):
        title = ""
        redacted_fields.append("title")
    source_ref = str(item.get("source_ref") or "").strip()
    if _field_has_forbidden(source_ref, forbidden_text_checker):
        source_ref = ""
        redacted_fields.append("source_ref")
    content = str(item.get("content") or "").strip()[:content_limit]
    content_redacted = _field_has_forbidden(content, forbidden_text_checker)
    if content_redacted:
        content = ""
        redacted_fields.append("content")
    payload = {
        "item_uid": item_uid,
        "title": title,
        "category": category,
        "source_type": source_type,
        "source_ref": source_ref,
        "content": content,
        "content_redacted": content_redacted,
        "redacted_fields": redacted_fields,
        "confidence": item.get("confidence"),
        "score": item.get("score"),
        "use_count": int(item.get("use_count") or 0),
        "helpful_count": int(item.get("helpful_count") or 0),
        "unhelpful_count": int(item.get("unhelpful_count") or 0),
        "last_used_at": str(item.get("last_used_at") or ""),
    }
    return payload


def billable_memory_item_uids(items: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for item in items:
        if not is_billable_memory_item(item):
            continue
        item_uid = str(item.get("item_uid") or "").strip()
        if item_uid and item_uid not in result:
            result.append(item_uid)
    return result


def is_billable_memory_item(item: dict[str, Any]) -> bool:
    if str(item.get("category") or "").strip() != "memory_lesson":
        return False
    return not bool(item.get("content_redacted"))


def _normalize_recall_item(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    if "tags_json" in result:
        result["tags"] = _loads_json(result.pop("tags_json"), [])
    for key in ("use_count", "helpful_count", "unhelpful_count"):
        result[key] = int(result.get(key) or 0)
    result["last_used_at"] = str(result.get("last_used_at") or "")
    result["content_excerpt"] = str(result.get("content") or "")
    return result


def recall_rank_components(item: dict[str, Any]) -> dict[str, float]:
    score = _bounded_float(item.get("score"))
    helpful = int(item.get("helpful_count") or 0)
    use_count = int(item.get("use_count") or 0)
    unhelpful = int(item.get("unhelpful_count") or 0)
    confidence = _bounded_float(item.get("confidence"))
    helpful_rate = helpful / max(1, helpful + unhelpful)
    use_boost = min(use_count, 20) / 20 * 0.04
    unhelpful_penalty = min(unhelpful, 20) * 0.04
    return {
        "score": round(score * 0.7, 6),
        "confidence": round(confidence * 0.18, 6),
        "helpful_rate": round(helpful_rate * 0.08, 6),
        "use_boost": round(use_boost, 6),
        "unhelpful_penalty": round(unhelpful_penalty, 6),
    }


def _recall_rank(item: dict[str, Any]) -> tuple[float, int, float, str, str]:
    components = recall_rank_components(item)
    adjusted = components["score"] + components["confidence"] + components["helpful_rate"] + components["use_boost"] - components["unhelpful_penalty"]
    helpful = int(item.get("helpful_count") or 0)
    unhelpful = int(item.get("unhelpful_count") or 0)
    confidence = _bounded_float(item.get("confidence"))
    return (round(adjusted, 6), helpful - unhelpful, confidence, str(item.get("last_used_at") or ""), str(item.get("item_uid") or ""))


def _field_has_forbidden(text: str, forbidden_text_checker: Callable[[str], Any] | None) -> bool:
    if not forbidden_text_checker or not text:
        return False
    return bool(forbidden_text_checker(text))


def _recall_search_results(
    db_path: Path,
    *,
    query_variants: list[str],
    project_id: int | None,
    limit: int,
    category: str | None,
    exclude_category: str | None,
    source_type: str | None,
    status: str | None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for variant in query_variants:
        for item in search_knowledge(
            db_path,
            variant,
            project_id=project_id,
            limit=limit,
            category=category,
            exclude_category=exclude_category,
            source_type=source_type,
            status=status,
        ):
            item_uid = str(item.get("item_uid") or "").strip()
            if not item_uid:
                continue
            normalized = _normalize_recall_item(item)
            existing = merged.get(item_uid)
            if existing is None or _recall_rank(normalized) > _recall_rank(existing):
                merged[item_uid] = normalized
    results = list(merged.values())
    results.sort(key=_recall_rank, reverse=True)
    return results[: max(1, limit)]


def _recall_memory_rows(
    db_path: Path,
    *,
    project_id: int,
    query: str,
    query_variants: list[str],
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
        score = max(_memory_match_score(_normalize_query(variant), haystack, item) for variant in query_variants) if query_variants else _memory_match_score(normalized_query, haystack, item)
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


def _recall_query_variants(query: str) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = _normalize_query(candidate)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        variants.append(candidate)

    add(query)
    for term in _terms(query):
        if len(term) >= 2:
            add(term)
    for chunk in _split_cjk_chunks(query):
        if len(chunk) >= 2:
            add(chunk)
            if _has_cjk(chunk):
                for window in _window_terms(chunk, 3):
                    if len(window) >= 2:
                        add(window)
    return variants or [query]


def _terms(text: str) -> list[str]:
    rough: list[str] = []
    current: list[str] = []
    for char in text:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            current.append(char)
        elif current:
            rough.append("".join(current))
            current = []
    if current:
        rough.append("".join(current))
    return rough


def _memory_terms(text: str) -> list[str]:
    compact = _normalize_query(text)
    if not compact:
        return []
    chunks = _split_cjk_chunks(compact)
    windows: list[str] = []
    for chunk in chunks:
        if _has_cjk(chunk):
            windows.extend(_window_terms(chunk))
    terms = list(dict.fromkeys(chunks + windows))
    return [term for term in terms if len(term) >= 2]


def _split_cjk_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_kind = ""
    for char in text:
        kind = _char_kind(char)
        if kind:
            if current and kind != current_kind:
                chunks.append("".join(current))
                current = []
            current.append(char)
            current_kind = kind
        else:
            if current:
                chunks.append("".join(current))
                current = []
                current_kind = ""
    if current:
        chunks.append("".join(current))
    return chunks


def _char_kind(char: str) -> str:
    if "\u4e00" <= char <= "\u9fff":
        return "cjk"
    if char.isalnum():
        return "alnum"
    return ""


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


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


def _item_tags(item: dict[str, Any]) -> list[str]:
    tags = item.get("tags")
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    return []


def _merge_tags(tags: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    for tag in [*tags, *extra]:
        value = str(tag).strip()
        if value and value not in merged:
            merged.append(value)
    return merged


def _memory_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_terms = set(_memory_terms(_normalize_query(" ".join([str(left.get("title") or ""), str(left.get("content") or "")]))))
    right_terms = set(_memory_terms(_normalize_query(" ".join([str(right.get("title") or ""), str(right.get("content") or "")]))))
    if not left_terms or not right_terms:
        return 0.0
    overlap = len(left_terms & right_terms)
    union = len(left_terms | right_terms)
    jaccard = overlap / max(1, union)
    coverage = overlap / max(1, min(len(left_terms), len(right_terms)))
    title_overlap = len(set(_memory_terms(_normalize_query(str(left.get("title") or "")))) & set(_memory_terms(_normalize_query(str(right.get("title") or ""))))) / max(
        1,
        min(
            len(set(_memory_terms(_normalize_query(str(left.get("title") or ""))))),
            len(set(_memory_terms(_normalize_query(str(right.get("title") or ""))))),
        ),
    )
    return round(jaccard * 0.5 + coverage * 0.35 + title_overlap * 0.15, 4)


def _memory_is_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_polarity = _memory_polarity_stable(left)
    right_polarity = _memory_polarity_stable(right)
    if left_polarity == 0 or right_polarity == 0:
        return False
    return left_polarity != right_polarity


def _memory_polarity_stable(item: dict[str, Any]) -> int:
    text = _normalize_query(" ".join([str(item.get("title") or ""), str(item.get("content") or "")]))
    negative_markers = (
        "\u4e0d\u8981",
        "\u4e0d\u80fd",
        "\u522b",
        "\u907f\u514d",
        "\u7981\u6b62",
        "\u4e0d\u5e94",
        "\u4e0d\u5f97",
        "\u62d2\u7edd",
        "\u9519\u8bef",
        "\u53cd\u4f8b",
        "fail",
    )
    positive_markers = (
        "\u5e94\u8be5",
        "\u5fc5\u987b",
        "\u4f18\u5148",
        "\u59cb\u7ec8",
        "\u5148",
        "\u4fdd\u6301",
        "\u5efa\u8bae",
        "prefer",
        "always",
    )
    negative = sum(1 for marker in negative_markers if marker in text)
    positive = sum(1 for marker in positive_markers if marker in text)
    if negative > 0:
        return -1
    if negative > positive:
        return -1
    if positive > negative:
        return 1
    return 0


def _memory_polarity(item: dict[str, Any]) -> int:
    text = _normalize_query(" ".join([str(item.get("title") or ""), str(item.get("content") or "")]))
    negative_markers = ("不要", "不能", "别", "避免", "禁止", "不应", "不得", "拒绝", "错误", "反例", "fail")
    positive_markers = ("应该", "应", "必须", "优先", "始终", "先", "保持", "建议", "prefer", "always")
    negative = sum(1 for marker in negative_markers if marker in text)
    positive = sum(1 for marker in positive_markers if marker in text)
    if negative > positive:
        return -1
    if positive > negative:
        return 1
    return 0


def _memory_is_low_utility(item: dict[str, Any], *, threshold: int = 2) -> bool:
    use_count = int(item.get("use_count") or 0)
    helpful_count = int(item.get("helpful_count") or 0)
    unhelpful_count = int(item.get("unhelpful_count") or 0)
    if unhelpful_count >= threshold and helpful_count == 0:
        return True
    if unhelpful_count >= threshold and use_count <= 1:
        return True
    return False


def _memory_consolidation_pair(left: dict[str, Any], right: dict[str, Any]) -> tuple[str, str]:
    left_score = _memory_consolidation_rank(left)
    right_score = _memory_consolidation_rank(right)
    if left_score >= right_score:
        return str(left.get("item_uid") or ""), str(right.get("item_uid") or "")
    return str(right.get("item_uid") or ""), str(left.get("item_uid") or "")


def _memory_consolidation_rank(item: dict[str, Any]) -> tuple[int, int, int, float, str]:
    helpful = int(item.get("helpful_count") or 0)
    use_count = int(item.get("use_count") or 0)
    unhelpful = int(item.get("unhelpful_count") or 0)
    confidence = _bounded_float(item.get("confidence"))
    return (helpful - unhelpful, use_count, helpful, confidence, str(item.get("item_uid") or ""))


def _loads_json(text: Any, default: Any) -> Any:
    import json

    try:
        return json.loads(text or "")
    except Exception:
        return default
