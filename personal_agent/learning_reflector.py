from __future__ import annotations

from pathlib import Path
from typing import Any

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.utils import json_dumps
from personal_agent.learning_signal import compact_learning_text, detect_learning_signal


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
LEARNABLE_FEEDBACK_TYPES = {"style_preference", "correction", "workflow_preference", "quality_bar"}
MIN_LESSON_LEN = 8
MAX_LESSON_LEN = 500


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
            reflection["signal_reason"] = gate.get("signal_reason", "")
            reflection["signal_categories"] = gate.get("signal_categories", [])
            reflection["matched_terms"] = gate.get("matched_terms", [])
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
                "CRITICAL: Facts, domain rules, parameter relationships, and task-specific instructions from the current task, source material, or draft are not long-term learning signals.",
                "CRITICAL: One-off requests about the current material or draft are not durable preferences, even if they are important for this turn.",
                "CRITICAL: The active_sources field only contains source metadata and titles, not the source plain_text; do not infer long-term preferences from source titles alone.",
                "CRITICAL: If the candidate lesson is mainly a summary or paraphrase of the current source material or active draft, set has_learning_signal=false and confidence=0.",
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
                "signal_reason": gate.get("signal_reason", ""),
                "signal_categories": gate.get("signal_categories", []),
                "matched_terms": gate.get("matched_terms", []),
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
        reflection["signal_reason"] = gate.get("signal_reason", "")
        reflection["signal_categories"] = gate.get("signal_categories", [])
        reflection["matched_terms"] = gate.get("matched_terms", [])
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
    compact = compact_learning_text(prompt)
    signal = detect_learning_signal(prompt)
    events = implicit_learning_events(context)
    if signal.has_signal or _has_non_skippable_signal(compact, events):
        return {
            "skip": False,
            "reason": signal.reason if signal.has_signal else "implicit_learning_event",
            "signal_reason": signal.reason,
            "signal_categories": list(signal.categories),
            "matched_terms": list(signal.matched_terms),
            "implicit_learning_events": events,
        }
    if _is_low_value_chat(compact):
        return {
            "skip": True,
            "reason": "low_value_chat",
            "signal_reason": signal.reason,
            "signal_categories": list(signal.categories),
            "matched_terms": list(signal.matched_terms),
            "implicit_learning_events": events,
        }
    return {
        "skip": True,
        "reason": "no_learning_signal",
        "signal_reason": signal.reason,
        "signal_categories": list(signal.categories),
        "matched_terms": list(signal.matched_terms),
        "implicit_learning_events": events,
    }


def implicit_learning_events(context: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    prompt = str(context.get("prompt") or "")
    signal = detect_learning_signal(prompt)
    if "correction" in signal.categories:
        events.append({"type": "explicit_correction", "confidence": 0.95})
    if signal.reason in {
        "correction_with_future_scope",
        "correction_with_preference",
        "preference_with_future_scope",
        "preference_with_analogous_scope",
    }:
        events.append(
            {
                "type": "explicit_negative_preference",
                "confidence": 0.88,
                "reason": signal.reason,
                "categories": list(signal.categories),
            }
        )

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


def _has_non_skippable_signal(compact: str, events: list[dict[str, Any]]) -> bool:
    return bool(events)


def _is_low_value_chat(compact: str) -> bool:
    if not compact:
        return True
    confirmations = {"好", "好的", "可以", "行", "嗯", "嗯嗯", "ok", "okay", "收到", "明白", "了解", "是的", "对", "没问题"}
    thanks = {"谢谢", "感谢", "辛苦了", "thanks", "thankyou", "thx"}
    greetings = {"你好", "hi", "hello", "嗨", "早上好", "晚上好"}
    if compact in confirmations | thanks | greetings:
        return True
    if len(compact) <= 8 and any(term in compact for term in confirmations | thanks | greetings):
        return True
    return False


def _coerce_reflection(parsed: dict[str, Any]) -> dict[str, Any]:
    approval_intent = str(parsed.get("approval_intent") or "none").strip()
    if approval_intent not in APPROVAL_INTENTS:
        approval_intent = "none"
    feedback_type = str(parsed.get("feedback_type") or "none").strip()
    if feedback_type not in FEEDBACK_TYPES:
        feedback_type = "none"
    lesson = str(parsed.get("candidate_lesson") or "").strip()
    confidence = _bounded_float(parsed.get("confidence"), 0.0)
    has_signal = (
        bool(parsed.get("has_learning_signal"))
        and bool(lesson)
        and MIN_LESSON_LEN <= len(lesson) <= MAX_LESSON_LEN
        and approval_intent == "none"
        and confidence >= 0.75
        and feedback_type in LEARNABLE_FEEDBACK_TYPES
    )
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
        "signal_reason": "",
        "signal_categories": [],
        "matched_terms": [],
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
        "signal_reason": "",
        "signal_categories": [],
        "matched_terms": [],
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
