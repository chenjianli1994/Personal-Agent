from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps


EVIDENCE_VERSION = "conversation_evidence_v3"

TENTATIVE_MARKERS = [
    "可能",
    "建议",
    "待确认",
    "需要确认",
    "还需要确认",
    "也许",
    "或许",
    "猜测",
    "推测",
    "倾向于",
    "可以考虑",
    "可考虑",
    "暂不确定",
    "不确定",
    "预计",
    "perhaps",
    "maybe",
    "suggest",
    "need confirm",
    "to be confirmed",
]

USER_NON_EVIDENCE_PATTERNS = [
    "好的",
    "收到",
    "继续",
    "开始吧",
    "下一步",
    "可以",
    "没问题",
    "行",
    "嗯",
    "好",
]

ASSISTANT_CONTEXT_EXCLUDE = {
    "learning_feedback",
    "learning_review",
    "skill_update_review",
    "tool_guidance",
    "dev_task_start",
    "dev_task_continue",
}

TOPIC_STOP_WORDS = {
    "请",
    "需要",
    "必须",
    "应该",
    "保持",
    "改成",
    "改为",
    "补充",
    "采用",
    "使用",
    "默认",
    "继续",
    "当前",
    "文档",
    "正文",
    "结论",
    "方案",
    "实现",
    "说明",
    "一个",
    "这个",
    "那个",
    "以及",
    "并且",
    "接口",
    "返回结构",
    "字段",
}


def build_conversation_evidence_snapshot(
    db_path: Path,
    *,
    project_id: int,
    session_uid: str,
    document_type: str,
    active_source_uids: list[str] | None = None,
    sources: list[dict[str, Any]] | None = None,
    task_uid: str = "",
    active_draft_uid: str = "",
) -> dict[str, Any]:
    session_uid = str(session_uid or "").strip()
    if not session_uid:
        return _empty_snapshot(document_type=document_type, active_source_uids=active_source_uids, task_uid=task_uid, active_draft_uid=active_draft_uid)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, message_uid, role, content, metadata_json, created_at
            FROM personal_session_messages
            WHERE session_uid=?
            ORDER BY id
            """,
            (session_uid,),
        ).fetchall()
    messages = [_decode_message_row(row) for row in rows]
    candidates = _candidate_statements(messages)
    topic_keys = _topic_keys_from_llm(
        db_path,
        project_id=project_id,
        session_uid=session_uid,
        document_type=document_type,
        statements=candidates,
    )
    scope_key = _scope_key(
        task_uid=task_uid,
        active_draft_uid=active_draft_uid,
        session_uid=session_uid,
        document_type=document_type,
        active_source_uids=active_source_uids or [],
    )
    evidence_items = _materialize_evidence_items(scope_key=scope_key, candidates=candidates, topic_keys=topic_keys)
    active_items, weak_items = _resolve_active_items(evidence_items)
    conflicts = _detect_source_conflicts(active_items=active_items, sources=sources or [])
    return {
        "conversation_scope_key": scope_key,
        "conversation_evidence_version": EVIDENCE_VERSION,
        "conversation_evidence": evidence_items,
        "active_conversation_decisions": [_public_evidence_item(item) for item in active_items],
        "weak_conversation_references": [_public_evidence_item(item) for item in weak_items],
        "conversation_conflicts": conflicts,
    }


def sanitize_conversation_evidence_for_prompt(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_scope_key": str(snapshot.get("conversation_scope_key") or ""),
        "conversation_evidence_version": str(snapshot.get("conversation_evidence_version") or EVIDENCE_VERSION),
        "active_conversation_decisions": list(snapshot.get("active_conversation_decisions") or []),
        "weak_conversation_references": list(snapshot.get("weak_conversation_references") or []),
        "conversation_conflicts": list(snapshot.get("conversation_conflicts") or []),
    }


def _empty_snapshot(*, document_type: str, active_source_uids: list[str] | None, task_uid: str, active_draft_uid: str) -> dict[str, Any]:
    return {
        "conversation_scope_key": _scope_key(
            task_uid=task_uid,
            active_draft_uid=active_draft_uid,
            session_uid="",
            document_type=document_type,
            active_source_uids=active_source_uids or [],
        ),
        "conversation_evidence_version": EVIDENCE_VERSION,
        "conversation_evidence": [],
        "active_conversation_decisions": [],
        "weak_conversation_references": [],
        "conversation_conflicts": [],
    }


def _decode_message_row(row: Any) -> dict[str, Any]:
    metadata = _loads_json(row["metadata_json"], {})
    return {
        "id": int(row["id"]),
        "message_uid": str(row["message_uid"] or ""),
        "role": str(row["role"] or ""),
        "content": str(row["content"] or ""),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "created_at": str(row["created_at"] or ""),
    }


def _candidate_statements(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "assistant" and _should_skip_assistant_message(message):
            continue
        for index, statement in enumerate(_split_sentences(str(message.get("content") or ""))):
            cleaned = _clean_statement(statement)
            if not cleaned:
                continue
            if role == "user":
                if _is_non_evidence_user_statement(cleaned):
                    continue
                assertiveness = "assertive"
                evidence_state = "active"
            elif role == "assistant":
                assertiveness = "tentative" if _is_tentative_statement(cleaned) else "assertive"
                evidence_state = "weak" if assertiveness == "tentative" else "active"
            else:
                continue
            candidates.append(
                {
                    "statement_id": f"{message['message_uid']}:{index}",
                    "message_uid": message["message_uid"],
                    "message_id": message["id"],
                    "source_role": role,
                    "statement": cleaned,
                    "assertiveness": assertiveness,
                    "evidence_state": evidence_state,
                    "created_at": message["created_at"],
                }
            )
    return candidates


def _should_skip_assistant_message(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    context_name = str(metadata.get("context") or "").strip()
    if context_name in ASSISTANT_CONTEXT_EXCLUDE:
        return True
    if metadata.get("draft"):
        return True
    if metadata.get("fallback"):
        return True
    return False


def _split_sentences(content: str) -> list[str]:
    text = str(content or "").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    sentences: list[str] = []
    for part in parts:
        item = part.strip(" \t-*")
        if item:
            sentences.append(item)
    return sentences


def _clean_statement(statement: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(statement or "")).strip()
    return cleaned.strip("。；;，, ")


def _is_non_evidence_user_statement(statement: str) -> bool:
    compact = re.sub(r"\s+", "", statement)
    if compact in USER_NON_EVIDENCE_PATTERNS:
        return True
    if any(mark in statement for mark in {"？", "?", "吗", "么", "呢"}):
        return True
    workflow_patterns = (
        r"^(继续|开始|先|现在|马上)?(生成|创建|输出|写|继续写|继续生成)",
        r"^(继续|开始|下一步|继续吧|开始吧)$",
    )
    if any(re.search(pattern, compact) for pattern in workflow_patterns):
        return True
    if re.search(r"(需求分析|需求拆解|功能规范|详细设计|测试用例|报告|文档)$", compact) and any(
        token in compact for token in {"继续", "生成", "创建", "输出", "开始", "写"}
    ):
        return True
    if len(compact) <= 8 and any(token in compact for token in {"创建会话", "继续写", "继续生成", "继续", "开始写", "开始生成"}):
        return True
    return len(compact) <= 3 and compact in {"好", "嗯", "行", "继续", "收到"}


def _is_tentative_statement(statement: str) -> bool:
    lowered = statement.lower()
    return any(marker in lowered for marker in TENTATIVE_MARKERS)


def _topic_keys_from_llm(
    db_path: Path,
    *,
    project_id: int,
    session_uid: str,
    document_type: str,
    statements: list[dict[str, Any]],
) -> dict[str, str]:
    if not statements:
        return {}
    payload = {
        "task": "Normalize each conversation statement into a stable topic_key so later versions of the same topic can override earlier ones.",
        "session_uid": session_uid,
        "document_type": document_type,
        "statements": [
            {
                "statement_id": item["statement_id"],
                "source_role": item["source_role"],
                "statement": item["statement"],
                "assertiveness": item["assertiveness"],
            }
            for item in statements
        ],
    }
    try:
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        result = gateway_class(db_path).complete_json(
            purpose="personal_conversation_evidence_model",
            system_prompt=(
                "You normalize conversation statements into stable topic keys. "
                "Return strict JSON only. Keep distinct topics separate and map restatements of the same topic to one key."
            ),
            user_prompt=json_dumps(payload),
            project_id=project_id,
            task_uid=session_uid,
        )
        parsed = result.parsed if isinstance(result.parsed, dict) else {}
        items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
        llm_keys = {
            str(item.get("statement_id") or ""): str(item.get("topic_key") or "").strip()
            for item in items
            if isinstance(item, dict)
        }
    except Exception:
        llm_keys = {}
    resolved: dict[str, str] = {}
    for item in statements:
        statement_id = str(item["statement_id"])
        resolved[statement_id] = llm_keys.get(statement_id) or _fallback_topic_key(str(item["statement"]))
    return resolved


def _materialize_evidence_items(
    *,
    scope_key: str,
    candidates: list[dict[str, Any]],
    topic_keys: dict[str, str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        statement_id = str(candidate["statement_id"])
        topic_key = topic_keys.get(statement_id) or _fallback_topic_key(str(candidate["statement"]))
        evidence_uid = _evidence_uid(scope_key=scope_key, topic_key=topic_key, statement_id=statement_id)
        items.append(
            {
                "evidence_uid": evidence_uid,
                "scope_key": scope_key,
                "topic_key": topic_key,
                "statement": candidate["statement"],
                "source_role": candidate["source_role"],
                "evidence_state": candidate["evidence_state"],
                "assertiveness": candidate["assertiveness"],
                "source_message_ids": [candidate["message_uid"]],
                "message_id": candidate["message_id"],
                "created_at": candidate["created_at"],
                "statement_id": statement_id,
                "superseded_by": "",
                "conflicts_with_source": False,
            }
        )
    return items


def _resolve_active_items(evidence_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    latest_assertive_by_topic: dict[str, dict[str, Any]] = {}
    weak_items: list[dict[str, Any]] = []
    for item in evidence_items:
        if item["assertiveness"] == "tentative":
            weak_items.append(item)
            continue
        latest_assertive_by_topic[item["topic_key"]] = item
    active_items: list[dict[str, Any]] = []
    for item in evidence_items:
        if item["assertiveness"] == "tentative":
            continue
        active = latest_assertive_by_topic.get(item["topic_key"])
        if active is item:
            active_items.append(item)
        else:
            item["evidence_state"] = "superseded"
            item["superseded_by"] = str(active.get("evidence_uid") or "")
    weak_public = []
    for item in weak_items:
        if item["topic_key"] not in latest_assertive_by_topic:
            weak_public.append(item)
    return active_items, weak_public


def _detect_source_conflicts(*, active_items: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_sentences = _source_topic_index(sources)
    conflicts: list[dict[str, Any]] = []
    for item in active_items:
        topic_key = str(item.get("topic_key") or "")
        statement = str(item.get("statement") or "")
        source_item = source_sentences.get(topic_key)
        if not source_item:
            continue
        source_statement = str(source_item.get("statement") or "")
        if not source_statement or _semantically_same(statement, source_statement):
            continue
        item["conflicts_with_source"] = True
        conflicts.append(
            {
                "topic_key": topic_key,
                "source_quote": source_statement[:240],
                "conversation_statement": statement,
                "resolution_policy": "conversation_overrides_source_but_source_conflict_must_be_rendered",
            }
        )
    return conflicts


def _source_topic_index(sources: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for source in sources:
        text = str(source.get("plain_text") or "")
        for statement in _split_sentences(text):
            cleaned = _clean_statement(statement)
            if len(cleaned) < 4:
                continue
            topic_key = _fallback_topic_key(cleaned)
            index.setdefault(topic_key, {"statement": cleaned})
    return index


def _semantically_same(left: str, right: str) -> bool:
    if left.strip() == right.strip():
        return True
    left_key = _fallback_topic_key(left)
    right_key = _fallback_topic_key(right)
    if left_key != right_key:
        return False
    left_norm = _normalized_statement(left)
    right_norm = _normalized_statement(right)
    if left_norm == right_norm:
        return True
    overlap = _token_overlap_ratio(left_norm, right_norm)
    return overlap >= 0.82


def _public_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic_key": str(item.get("topic_key") or ""),
        "statement": str(item.get("statement") or ""),
        "source_role": str(item.get("source_role") or ""),
        "evidence_state": str(item.get("evidence_state") or ""),
        "assertiveness": str(item.get("assertiveness") or ""),
    }


def _scope_key(
    *,
    task_uid: str,
    active_draft_uid: str,
    session_uid: str,
    document_type: str,
    active_source_uids: list[str],
) -> str:
    task_uid = str(task_uid or "").strip()
    if task_uid:
        return f"task:{task_uid}"
    active_draft_uid = str(active_draft_uid or "").strip()
    if active_draft_uid:
        return f"draft:{active_draft_uid}"
    digest = hashlib.sha1("|".join(active_source_uids).encode("utf-8")).hexdigest()[:10] if active_source_uids else "no_source"
    return f"session:{session_uid}:document:{document_type}:sources:{digest}"


def _evidence_uid(*, scope_key: str, topic_key: str, statement_id: str) -> str:
    digest = hashlib.sha1(f"{scope_key}|{topic_key}|{statement_id}".encode("utf-8")).hexdigest()[:16]
    return f"evidence_{digest}"


def _fallback_topic_key(statement: str) -> str:
    normalized = _manual_topic_key(statement)
    if normalized:
        return normalized
    tokens = _topic_tokens(statement)
    if tokens:
        return "|".join(tokens[:6])
    digest = hashlib.sha1(statement.encode("utf-8")).hexdigest()[:10]
    return f"topic_{digest}"


def _normalized_statement(statement: str) -> str:
    tokens = _topic_tokens(statement, keep_numbers=True)
    return " ".join(tokens)


def _topic_tokens(statement: str, *, keep_numbers: bool = False) -> list[str]:
    text = re.sub(r"\s+", " ", statement.lower())
    for left, right in (
        ("超时", "timeout"),
        ("时延", "timeout"),
        ("延迟", "timeout"),
        ("返回结构", "response_format"),
        ("字段命名", "field_naming"),
        ("字段", "field"),
        ("json", "json"),
        ("xml", "xml"),
        ("测试用例", "test_case"),
        ("重试", "retry"),
        ("日志", "logging"),
        ("标题", "title"),
    ):
        text = text.replace(left, f" {right} ")
    text = re.sub(r"[，,。；;：:（）()【】\[\]<>《》\"'`]", " ", text)
    raw_tokens = re.findall(r"[a-z0-9_./+-]+|[\u4e00-\u9fff]{1,8}", text)
    tokens: list[str] = []
    for token in raw_tokens:
        token = token.strip()
        if not token:
            continue
        if not keep_numbers and re.fullmatch(r"\d+(?:\.\d+)?(?:ms|s|分钟|分|小时|h|%)?", token):
            continue
        if token in TOPIC_STOP_WORDS:
            continue
        if len(token) == 1 and not re.search(r"[a-z0-9]", token):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _token_overlap_ratio(left: str, right: str) -> float:
    left_tokens = set(_topic_tokens(left, keep_numbers=True))
    right_tokens = set(_topic_tokens(right, keep_numbers=True))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))


def _manual_topic_key(statement: str) -> str:
    text = statement.lower()
    if "超时" in text or "时延" in text or "延迟" in text:
        return "timeout"
    if "返回结构" in text or "json" in text or "xml" in text:
        return "response_format"
    if "字段命名" in text:
        return "field_naming"
    if "测试用例" in text and "重试" in text:
        return "test_case|retry"
    return ""


def _loads_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default
