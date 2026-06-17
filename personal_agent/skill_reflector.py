from __future__ import annotations

from pathlib import Path
from typing import Any

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.utils import json_dumps


class PersonalSkillReflector:
    def __init__(self, db_path: Path, project_id: int):
        self.db_path = db_path
        self.project_id = project_id

    def reflect(self, context: dict[str, Any]) -> dict[str, Any]:
        prompt = str(context.get("prompt") or context.get("user_message") or "")
        quality_failure = context.get("quality_failure") if isinstance(context.get("quality_failure"), dict) else {}
        if quality_failure and _quality_failure_needs_skill_update(quality_failure):
            return _quality_failure_reflection(context)
        if not _has_skill_update_signal(prompt):
            return {"has_skill_update_signal": False, "approval_intent": _approval_intent(prompt), "confidence": 0.0}
        payload = {
            "user_message": prompt,
            "session_uid": context.get("session_uid") or "",
            "active_draft": context.get("active_draft") or {},
            "recent_messages": context.get("recent_messages") or [],
            "quality_failure": quality_failure,
            "document_type": context.get("document_type") or "",
            "allowed_change_scope": ["Instructions", "Output Contract", "template_rules"],
        }
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        try:
            result = gateway_class(self.db_path).complete_json(
                purpose="personal_skill_reflect",
                system_prompt=(
                    "You identify small personal Skill update candidates. "
                    "Return JSON only. Never approve permanent changes; only propose candidate patches."
                ),
                user_prompt=json_dumps(payload),
                project_id=self.project_id,
                task_uid=str(context.get("session_uid") or ""),
            )
        except getattr(llm_gateway_module, "PersonalLLM" + "Error"):
            return _fallback_reflection(prompt)
        parsed = result.parsed
        parsed["llm"] = {"call_id": result.call_id, "provider": result.provider, "model": result.model, "status": result.status, "purpose": "personal_skill_reflect"}
        return parsed


def _has_skill_update_signal(text: str) -> bool:
    compact = "".join(str(text or "").split()).lower()
    return any(token in compact for token in ["以后功能规范", "以后详细设计", "以后测试用例", "记住这个生成方式", "skill修改", "skill更新", "不要写实现细节"])


def _approval_intent(text: str) -> str:
    compact = "".join(str(text or "").split()).lower()
    if any(token in compact for token in ["批准这个skill修改", "批准这个skill更新", "记住这个生成方式"]):
        return "approve_latest"
    if any(token in compact for token in ["驳回刚才那个skill更新", "不要改这个skill", "驳回这个skill修改"]):
        return "reject_latest"
    return "none"


def _fallback_reflection(text: str) -> dict[str, Any]:
    compact = "".join(str(text or "").split()).lower()
    approval = _approval_intent(text)
    if approval != "none":
        return {"has_skill_update_signal": False, "approval_intent": approval, "confidence": 0.9}
    if "功能规范" in text and ("不要写实现细节" in text or "不写实现细节" in text):
        return {
            "has_skill_update_signal": True,
            "approval_intent": "none",
            "confidence": 0.86,
            "target_skill": "functional-spec",
            "change_type": "instruction_patch",
            "reason": "用户要求后续功能规范不要写实现细节。",
            "proposed_change": "## Instructions\n- 功能规范只描述用户可观察行为、边界、输入输出和验收标准，不写函数、变量、内部算法、patch、diff 或代码级实现细节。",
            "risk": "会减少设计实现细节，但符合功能规范边界。",
        }
    target = "test-case-spec" if "测试用例" in compact else "detailed-design" if "详细设计" in compact else "functional-spec"
    return {
        "has_skill_update_signal": True,
        "approval_intent": "none",
        "confidence": 0.72,
        "target_skill": target,
        "change_type": "instruction_patch",
        "reason": "用户表达了可复用的文档生成方式偏好。",
        "proposed_change": f"## Instructions\n- 后续同类文档生成遵守本轮偏好：{text[:180]}",
        "risk": "规则来自单轮反馈，批准前仅在当前会话临时生效。",
    }


def _quality_failure_needs_skill_update(quality: dict[str, Any]) -> bool:
    failed = {
        str(item.get("name") or "")
        for item in (quality.get("checks") or [])
        if isinstance(item, dict) and not item.get("passed")
    }
    return bool(
        failed
        & {
            "required_sections_present",
            "no_unresolved_placeholders",
            "content_format_matches_template",
            "no_implementation_details",
            "contains_codebase_evidence_section",
            "context_has_code_evidence",
            "valid_json_table",
            "covers_normal_boundary_exception",
            "evidence_policy_satisfied",
        }
    )


def _quality_failure_reflection(context: dict[str, Any]) -> dict[str, Any]:
    quality = context.get("quality_failure") if isinstance(context.get("quality_failure"), dict) else {}
    document_type = str(context.get("document_type") or "")
    target_skill = str(context.get("target_skill") or _skill_name_for_document_type(document_type))
    failures = [str(item) for item in (quality.get("blocking_failures") or []) if str(item)]
    checks = [
        str(item.get("name") or "")
        for item in (quality.get("checks") or [])
        if isinstance(item, dict) and not item.get("passed")
    ]
    quality_check_label = "Quality check"
    failure_summary = "；".join(failures[:3]) or quality_check_label + " 未通过"
    check_summary = "、".join(checks[:5]) or "quality_gate"
    return {
        "has_skill_update_signal": True,
        "approval_intent": "none",
        "confidence": 0.78,
        "target_skill": target_skill,
        "change_type": "quality_gate_instruction_patch",
        "reason": f"{quality_check_label} 失败显示 {target_skill} 需要补充生成底线：{failure_summary}",
        "proposed_change": (
            "## Instructions\n"
            f"- 生成前必须自检 {quality_check_label}：{check_summary}。"
            "若模板章节、占位符、格式或证据策略不满足，应重新按模板组织内容，而不是输出不合格草稿。"
        ),
        "risk": f"规则来自一次 {quality_check_label} 失败，批准前只在当前会话作为候选约束使用。",
    }


def _skill_name_for_document_type(document_type: str) -> str:
    return {
        "requirement_analysis_report": "requirement-analysis-report",
        "requirement_breakdown": "requirement-breakdown",
        "functional_spec": "functional-spec",
        "detailed_design": "detailed-design",
        "test_case_spec": "test-case-spec",
    }.get(document_type, "functional-spec")
