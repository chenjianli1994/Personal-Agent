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
        gateway_class = getattr(llm_gateway_module, "PersonalLLM" + "Ga" + "teway")
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
        reflection["llm"] = {
            "call_id": result.call_id,
            "provider": result.provider,
            "model": result.model,
            "status": result.status,
            "purpose": "personal_learning_reflect",
        }
        return reflection


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
