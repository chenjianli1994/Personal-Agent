from __future__ import annotations

from pathlib import Path
from typing import Any

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.utils import json_dumps


FEEDBACK_TYPES = {
    "style_preference",
    "correction",
    "workflow_preference",
    "quality_bar",
    "memory_approval",
    "memory_rejection",
    "none",
}

APPROVAL_INTENTS = {"none", "approve_latest", "reject_latest"}


class PersonalLearningReflector:
    def __init__(self, db_path: Path, project_id: int):
        self.db_path = db_path
        self.project_id = project_id

    def reflect(self, context: dict[str, Any], *, task_uid: str = "") -> dict[str, Any]:
        gate = learning_reflection_gate(context)
        if gate["skip"]:
            reflection = _empty_reflection()
            reflection["skip_reason"] = gate["reason"]
            reflection["implicit_learning_events"] = gate["implicit_learning_events"]
            return reflection
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        system_prompt = "\n".join(
            [
                "You are the learning-signal reflector for a personal natural-language development Agent.",
                "Do not answer the user and do not route the main task.",
                "Decide whether the latest user message contains a durable preference, principle, correction, quality bar, or work-style requirement.",
                "Also detect whether the user is approving or rejecting the most recent pending memory candidate.",
                "Only learn abstract preferences, principles, corrections, and work methods. Do not mechanically copy one response format.",
                "Do not treat ordinary questions, one-off content requests, or task instructions as long-term learning signals.",
                "Return strict JSON only.",
            ]
        )
        user_prompt = json_dumps(
            {
                "user_message": context.get("prompt") or "",
                "session_uid": context.get("session_uid") or "",
                "task_uid": task_uid,
                "recent_messages": context.get("recent_messages") or [],
                "active_draft": context.get("active_draft") or {},
                "active_sources": [
                    {
                        "source_uid": item.get("source_uid"),
                        "title": item.get("title"),
                        "source_type": item.get("source_type"),
                    }
                    for item in (context.get("sources") or [])[:3]
                ],
                "implicit_learning_events": gate["implicit_learning_events"],
                "required_json_schema": {
                    "has_learning_signal": False,
                    "confidence": 0.0,
                    "feedback_type": "style_preference | correction | workflow_preference | quality_bar | memory_approval | memory_rejection | none",
                    "scope": "session | project | global_personal",
                    "candidate_lesson": "abstract reusable lesson, Chinese, empty if none",
                    "anti_behavior": "behavior to avoid, Chinese, empty if none",
                    "approval_intent": "none | approve_latest | reject_latest",
                    "reason": "Chinese reason",
                },
            }
        )
        try:
            result = gateway_class(self.db_path).complete_json(
                purpose="personal_learning_reflect",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                project_id=self.project_id,
                task_uid=task_uid or str(context.get("session_uid") or ""),
            )
        except getattr(llm_gateway_module, "PersonalLLM" + "Error") as exc:
            return _empty_reflection(error=str(exc))
        reflection = _coerce_reflection(result.parsed)
        reflection["skip_reason"] = ""
        reflection["implicit_learning_events"] = gate["implicit_learning_events"]
        reflection["llm"] = {
            "call_id": result.call_id,
            "provider": result.provider,
            "model": result.model,
            "status": result.status,
            "purpose": "personal_learning_reflect",
        }
        return reflection


def learning_reflection_gate(context: dict[str, Any]) -> dict[str, Any]:
    prompt = str(context.get("prompt") or "").strip()
    compact = _compact(prompt)
    events = implicit_learning_events(context)
    if _has_non_skippable_signal(compact, context, events):
        return {"skip": False, "reason": "non_skippable_signal", "implicit_learning_events": events}
    if _is_low_value_chat(compact):
        return {"skip": True, "reason": "low_value_chat", "implicit_learning_events": events}
    return {"skip": False, "reason": "substantive_turn", "implicit_learning_events": events}


def implicit_learning_events(context: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    prompt = str(context.get("prompt") or "")
    compact = _compact(prompt)
    if _looks_like_correction(compact):
        events.append({"type": "explicit_correction", "confidence": 0.95})

    recent_messages = context.get("recent_messages") if isinstance(context.get("recent_messages"), list) else []
    fallback_count = 0
    draft_revision_count = 0
    quality_failure_count = 0
    active_draft_uid = str((context.get("active_draft") or {}).get("draft_uid") or "") if isinstance(context.get("active_draft"), dict) else ""
    for message in recent_messages[-8:]:
        metadata = message.get("metadata") if isinstance(message, dict) else {}
        if not isinstance(metadata, dict):
            continue
        if metadata.get("fallback"):
            fallback_count += 1
        draft = metadata.get("draft") if isinstance(metadata.get("draft"), dict) else {}
        draft_uid = str(draft.get("draft_uid") or "")
        if active_draft_uid and draft_uid == active_draft_uid and str(metadata.get("context") or "") == "draft_revision":
            draft_revision_count += 1
        generation = draft.get("generation") if isinstance(draft.get("generation"), dict) else {}
        if generation.get("quality_gate_passed") is False:
            quality_failure_count += 1
    if fallback_count >= 2:
        events.append({"type": "repeated_fallback", "count": fallback_count, "confidence": 0.75})
    if draft_revision_count >= 2:
        events.append({"type": "repeated_draft_revision", "draft_uid": active_draft_uid, "count": draft_revision_count, "confidence": 0.8})
    if quality_failure_count:
        events.append({"type": "draft_quality_failure", "count": quality_failure_count, "confidence": 0.85})
    return events


def _has_non_skippable_signal(compact: str, context: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    if events:
        return True
    approval_terms = ("批准", "同意", "驳回", "拒绝", "不要记", "别记", "approve", "reject")
    correction_terms = ("错", "不对", "误解", "理解错", "纠正", "修正")
    document_terms = ("生成", "草稿", "需求", "功能规范", "详细设计", "测试用例", "文档")
    code_terms = ("代码", "patch", "diff", "测试", "运行", "函数", "文件", "commit")
    if any(term in compact for term in approval_terms + correction_terms + document_terms + code_terms):
        return True
    route = context.get("intent_route") if isinstance(context.get("intent_route"), dict) else {}
    intent = str(route.get("intent") or "")
    return intent in {"generate_document", "revise_draft", "propose_code_patch", "run_validation", "learn_feedback"}


def _is_low_value_chat(compact: str) -> bool:
    if not compact:
        return True
    confirmations = {"好", "好的", "可以", "行", "嗯", "嗯嗯", "ok", "okay", "收到", "明白", "了解", "是的", "对", "没问题"}
    thanks = {"谢谢", "感谢", "辛苦了", "thanks", "thankyou", "thx"}
    greetings = {"你好", "hi", "hello", "早", "早上好", "晚上好"}
    if compact in confirmations | thanks | greetings:
        return True
    if len(compact) <= 8 and any(term in compact for term in confirmations | thanks | greetings):
        return True
    return False


def _looks_like_correction(compact: str) -> bool:
    return any(term in compact for term in ("你理解错", "理解错", "不对", "错了", "不是这个意思", "纠正", "应该是"))


def _compact(text: str) -> str:
    return "".join(str(text or "").lower().split())


def _coerce_reflection(parsed: dict[str, Any]) -> dict[str, Any]:
    approval_intent = str(parsed.get("approval_intent") or "none").strip()
    if approval_intent not in APPROVAL_INTENTS:
        approval_intent = "none"
    feedback_type = str(parsed.get("feedback_type") or "none").strip()
    if feedback_type not in FEEDBACK_TYPES:
        feedback_type = "none"
    lesson = str(parsed.get("candidate_lesson") or "").strip()
    confidence = _bounded_float(parsed.get("confidence"), 0.0)
    has_signal = bool(parsed.get("has_learning_signal")) and bool(lesson) and approval_intent == "none" and confidence >= 0.55
    scope = str(parsed.get("scope") or "project").strip()
    if scope not in {"session", "project", "global_personal"}:
        scope = "project"
    return {
        "has_learning_signal": has_signal,
        "confidence": confidence,
        "feedback_type": feedback_type,
        "scope": scope,
        "candidate_lesson": lesson if has_signal else "",
        "anti_behavior": str(parsed.get("anti_behavior") or "").strip() if has_signal else "",
        "approval_intent": approval_intent,
        "reason": str(parsed.get("reason") or "").strip(),
    }


def _empty_reflection(*, error: str = "") -> dict[str, Any]:
    result = {
        "has_learning_signal": False,
        "confidence": 0.0,
        "feedback_type": "none",
        "scope": "project",
        "candidate_lesson": "",
        "anti_behavior": "",
        "approval_intent": "none",
        "reason": "learning reflection unavailable",
        "llm": {
            "call_id": None,
            "provider": "",
            "model": "",
            "status": "failed" if error else "skipped",
            "purpose": "personal_learning_reflect",
        },
    }
    if error:
        result["llm"]["error"] = error
    return result


def _bounded_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))
