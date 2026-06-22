from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps


PERSONAL_INTENTS = {
    "answer_only",
    "analyze_input_source",
    "generate_document",
    "revise_draft",
    "propose_code_patch",
    "run_validation",
    "learn_feedback",
}

DOCUMENT_TYPES = {
    "",
    "requirement_analysis_report",
    "requirement_breakdown",
    "functional_spec",
    "detailed_design",
    "test_case_spec",
}

ANSWER_MODES = {"general_chat", "input_source_analysis", "tool_guidance"}


class PersonalIntentRouter:
    def __init__(self, db_path: Path, project_id: int):
        self.db_path = db_path
        self.project_id = project_id

    def route(self, context: dict[str, Any]) -> dict[str, Any]:
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        system_prompt = "\n".join(
            [
                "You are the semantic intent router for a personal natural-language development Agent.",
                "Decide what the user wants. Do not answer the user.",
                "Return strict JSON only.",
                "Allowed intents: answer_only, analyze_input_source, generate_document, revise_draft, propose_code_patch, run_validation, learn_feedback.",
                "Allowed document types: requirement_analysis_report, requirement_breakdown, functional_spec, detailed_design, test_case_spec.",
                "Never set writes_project_files=true for this personal Agent phase.",
            ]
        )
        user_prompt = json_dumps(_router_payload(context))
        try:
            result = gateway_class(self.db_path).complete_json(
                purpose="personal_intent_route",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                project_id=self.project_id,
                task_uid=str(context.get("session_uid") or ""),
            )
        except getattr(llm_gateway_module, "PersonalLLM" + "Error") as exc:
            error = str(exc)
            return _fallback_route(error, _failed_llm_call_metadata(self.db_path, error))
        route = _coerce_route(result.parsed)
        route = _promote_draft_revision_followup(route, context)
        route["router_source"] = "llm"
        route["llm"] = {
            "call_id": result.call_id,
            "provider": result.provider,
            "model": result.model,
            "status": result.status,
            "purpose": "personal_intent_route",
        }
        return route


def _promote_draft_revision_followup(route: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if str(route.get("intent") or "") not in {"answer_only", "analyze_input_source"}:
        return route
    active_draft = context.get("active_draft") if isinstance(context.get("active_draft"), dict) else {}
    if not active_draft or not active_draft.get("draft_uid"):
        return route
    prompt = str(context.get("prompt") or "")
    compact = re.sub(r"\s+", "", prompt).lower()
    edit_terms = (
        "去掉",
        "删掉",
        "删除",
        "移除",
        "不要",
        "改成",
        "改为",
        "修改",
        "调整",
        "补充",
        "重写",
        "润色",
        "合并",
        "拆分",
    )
    draft_context_terms = (
        "草稿",
        "文档",
        "报告",
        "内容",
        "章节",
        "段落",
        "证据引用",
        "质量项",
        "这些",
        "上面",
        "刚才",
        "当前",
    )
    if not any(term in compact for term in edit_terms):
        return route
    if not any(term in compact for term in draft_context_terms):
        return route
    promoted = dict(route)
    promoted.update(
        {
            "intent": "revise_draft",
            "confidence": max(float(promoted.get("confidence") or 0.0), 0.86),
            "requires_active_draft": True,
            "revises_draft": True,
            "creates_draft": False,
            "answer_mode": "general_chat",
            "reason": (str(promoted.get("reason") or "").strip() + " Promoted to draft revision because the session has an active draft and the user gave an explicit edit instruction.").strip(),
        }
    )
    return promoted


def _router_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_message": context.get("prompt") or "",
        "active_sources": [
            {
                "source_uid": item.get("source_uid"),
                "title": item.get("title"),
                "source_type": item.get("source_type"),
                "plain_text_excerpt": str(item.get("plain_text") or "")[:2000],
            }
            for item in (context.get("sources") or [])[:3]
        ],
        "active_draft": context.get("active_draft") or {},
        "recent_messages": context.get("recent_messages") or [],
        "knowledge_refs": context.get("knowledge_refs") or [],
        "code_evidence_present": bool(context.get("code_evidence")),
        "required_json_schema": {
            "intent": "answer_only | analyze_input_source | generate_document | revise_draft | propose_code_patch | run_validation | learn_feedback",
            "confidence": 0.0,
            "target_document_type": "requirement_analysis_report | requirement_breakdown | functional_spec | detailed_design | test_case_spec | ",
            "requires_active_source": False,
            "requires_active_draft": False,
            "requires_codebase": False,
            "creates_draft": False,
            "revises_draft": False,
            "writes_project_files": False,
            "requires_user_confirmation": False,
            "answer_mode": "general_chat | input_source_analysis | tool_guidance",
            "reason": "Chinese reason",
        },
    }


def _coerce_route(parsed: dict[str, Any]) -> dict[str, Any]:
    intent = str(parsed.get("intent") or "answer_only").strip()
    if intent not in PERSONAL_INTENTS:
        intent = "answer_only"
    document_type = str(parsed.get("target_document_type") or "").strip()
    if document_type not in DOCUMENT_TYPES:
        document_type = ""
    answer_mode = str(parsed.get("answer_mode") or "").strip()
    if answer_mode not in ANSWER_MODES:
        answer_mode = "input_source_analysis" if intent == "analyze_input_source" else "general_chat"
    return {
        "intent": intent,
        "confidence": _bounded_float(parsed.get("confidence"), 0.0),
        "target_document_type": document_type,
        "requires_active_source": bool(parsed.get("requires_active_source")),
        "requires_active_draft": bool(parsed.get("requires_active_draft")),
        "requires_codebase": bool(parsed.get("requires_codebase")),
        "creates_draft": bool(parsed.get("creates_draft")),
        "revises_draft": bool(parsed.get("revises_draft")),
        "writes_project_files": bool(parsed.get("writes_project_files")),
        "requires_user_confirmation": bool(parsed.get("requires_user_confirmation")),
        "answer_mode": answer_mode,
        "reason": str(parsed.get("reason") or "").strip(),
    }


def _fallback_route(error: str, llm_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    llm = {
        "call_id": None,
        "provider": "",
        "model": "",
        "status": "failed",
        "purpose": "personal_intent_route",
        "error": error,
    }
    if llm_metadata:
        llm.update({key: value for key, value in llm_metadata.items() if value not in (None, "")})
    return {
        "intent": "answer_only",
        "confidence": 0.0,
        "target_document_type": "",
        "requires_active_source": False,
        "requires_active_draft": False,
        "requires_codebase": False,
        "creates_draft": False,
        "revises_draft": False,
        "writes_project_files": False,
        "requires_user_confirmation": False,
        "answer_mode": "general_chat",
        "reason": "LLM intent route failed; safe answer-only fallback.",
        "router_source": "fallback",
        "llm": llm,
    }


def _failed_llm_call_metadata(db_path: Path, error: str) -> dict[str, Any]:
    match = re.search(r"llm_call_id=(\d+)", error)
    if not match:
        return {}
    call_id = int(match.group(1))
    try:
        with connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT id, purpose, provider, model, status, error
                FROM llm_call_logs
                WHERE id=?
                """,
                (call_id,),
            ).fetchone()
    except Exception:
        row = None
    if row is None:
        return {"call_id": call_id}
    return {
        "call_id": row["id"],
        "provider": row["provider"],
        "model": row["model"],
        "status": row["status"],
        "purpose": row["purpose"],
        "error": row["error"] or error,
    }


def _bounded_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))
