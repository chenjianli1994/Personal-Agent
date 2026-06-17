from __future__ import annotations

from typing import Any


LOW_CONFIDENCE_THRESHOLD = 0.55


def apply_personal_policy(route: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    guarded = dict(route)
    policy = {"allowed": True, "fallback": False, "reason": ""}
    intent = str(guarded.get("intent") or "answer_only")
    confidence = float(guarded.get("confidence") or 0.0)
    has_source = bool(context.get("active_source_uids"))
    has_draft = bool((context.get("active_draft") or {}).get("draft_uid"))

    if guarded.get("writes_project_files"):
        _force_answer_only(guarded, policy, "本阶段禁止 LLM 直接写项目文件。")
    elif confidence < LOW_CONFIDENCE_THRESHOLD and intent not in {"answer_only", "analyze_input_source"}:
        _force_answer_only(guarded, policy, "LLM 路由置信度过低，已降级为安全回答。")
    elif intent == "generate_document" and not has_source:
        _force_answer_only(guarded, policy, "当前没有激活输入材料，不能生成文档草稿。")
    elif intent == "revise_draft" and not has_draft:
        _force_answer_only(guarded, policy, "当前没有激活草稿，不能修订草稿。")
    elif intent in {"propose_code_patch", "run_validation"}:
        guarded["requires_user_confirmation"] = True

    guarded["policy"] = policy
    return guarded


def _force_answer_only(route: dict[str, Any], policy: dict[str, Any], reason: str) -> None:
    route["intent"] = "answer_only"
    route["creates_draft"] = False
    route["revises_draft"] = False
    route["writes_project_files"] = False
    route["requires_user_confirmation"] = False
    route["answer_mode"] = "general_chat"
    policy["allowed"] = False
    policy["fallback"] = True
    policy["reason"] = reason
