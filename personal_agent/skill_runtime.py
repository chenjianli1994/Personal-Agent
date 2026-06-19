from __future__ import annotations

from pathlib import Path
from typing import Any

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.utils import json_dumps

from .content_guard import personal_forbidden_hits
from .knowledge_recall import safe_recall_prompt_item


def generate_artifact_with_skill(
    db_path: Path,
    *,
    project_id: int,
    document_type: str,
    skill: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    content_format = _content_format(skill, document_type)
    system_prompt = _system_prompt(skill)
    template = context.get("template") if isinstance(context.get("template"), dict) else {}
    user_prompt = _user_prompt(document_type=document_type, content_format=content_format, skill=skill, context=context, template=template)
    try:
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        result = gateway_class(db_path).complete_json(
            purpose="personal_artifact_generate",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            project_id=project_id,
            task_uid=str(context.get("session_task_uid") or ""),
        )
    except getattr(llm_gateway_module, "PersonalLLM" + "Error") as exc:
        if exc.code == "LLM_NOT_CONFIGURED":
            raise ValueError(f"LLM 未配置，无法生成文档；请先配置真实 LLM 或在测试中显式启用 fake provider。{exc}") from exc
        raise ValueError(f"LLM 文档生成失败：{exc}") from exc

    parsed = result.parsed
    content = _extract_content(parsed, content_format)
    content = _preserve_source_ambiguities(content, document_type, context)
    title = str(parsed.get("title") or "").strip()
    if not title:
        title = str(skill.get("display_name") or document_type)
    return {
        "title": title,
        "content": content,
        "content_format": content_format,
        "memory_item_uids_used": _memory_item_uids_used(parsed, context),
        "generator": "llm_skill_runtime",
        "llm_call_id": result.call_id,
        "llm_provider": result.provider,
        "llm_model": result.model,
        "llm_status": result.status,
        "raw": parsed,
    }


def revise_artifact_with_skill(
    db_path: Path,
    *,
    project_id: int,
    document_type: str,
    skill: dict[str, Any],
    context: dict[str, Any],
    current_draft: dict[str, Any],
    feedback: str,
) -> dict[str, Any]:
    content_format = _content_format(skill, document_type)
    system_prompt = _system_prompt(skill)
    template = context.get("template") if isinstance(context.get("template"), dict) else {}
    user_prompt = _revision_user_prompt(
        document_type=document_type,
        content_format=content_format,
        skill=skill,
        context=context,
        template=template,
        current_draft=current_draft,
        feedback=feedback,
    )
    try:
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        result = gateway_class(db_path).complete_json(
            purpose="personal_artifact_revise",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            project_id=project_id,
            task_uid=str(context.get("session_task_uid") or ""),
        )
    except getattr(llm_gateway_module, "PersonalLLM" + "Error") as exc:
        if exc.code == "LLM_NOT_CONFIGURED":
            raise ValueError(f"LLM 未配置，无法修订文档；请先配置真实 LLM 或在测试中显式启用 fake provider。{exc}") from exc
        raise ValueError(f"LLM 文档修订失败：{exc}") from exc

    parsed = result.parsed
    content = _extract_content(parsed, content_format)
    content = _preserve_source_ambiguities(content, document_type, context)
    title = str(parsed.get("title") or current_draft.get("title") or skill.get("display_name") or document_type).strip()
    return {
        "title": title,
        "content": content,
        "content_format": content_format,
        "memory_item_uids_used": _memory_item_uids_used(parsed, context),
        "generator": "llm_skill_revision_runtime",
        "llm_call_id": result.call_id,
        "llm_provider": result.provider,
        "llm_model": result.model,
        "llm_status": result.status,
        "raw": parsed,
    }


def _system_prompt(skill: dict[str, Any]) -> str:
    return "\n".join(
        [
            "You are the personal Agent document generation runtime.",
            "Follow the provided SKILL.md exactly.",
            "Generate only personal draft content. Never create release records, patch_apply actions, or real code changes.",
            "Return strict JSON matching the requested schema.",
            "",
            "SKILL.md:",
            str(skill.get("skill_markdown") or ""),
        ]
    )


def _user_prompt(*, document_type: str, content_format: str, skill: dict[str, Any], context: dict[str, Any], template: dict[str, Any]) -> str:
    semantic_model = context.get("source_semantic_model") if isinstance(context.get("source_semantic_model"), dict) else {}
    payload = {
        "document_type": document_type,
        "content_format": content_format,
        "required_json_schema": {
            "title": "string",
            "content_format": content_format,
            "content": "markdown string" if content_format == "markdown" else {"columns": ["string"], "rows": [{"column": "value"}]},
            "evidence_refs_used": {
                "source_uids": ["string"],
                "knowledge_item_uids": ["string"],
                "memory_item_uids": ["string"],
                "code_evidence": ["string"],
            },
            "memory_item_uids_used": ["string"],
            "boundary_confirmation": {
                "personal_draft_only": True,
                "writes_release_record": False,
                "generates_code_patch": False,
                "uses_patch_apply": False,
            },
        },
        "skill": {
            "name": skill.get("name"),
            "display_name": skill.get("display_name"),
            "document_type": skill.get("document_type"),
            "path": skill.get("path"),
        },
        "template": {
            "name": template.get("name"),
            "path": template.get("path"),
            "format": template.get("format"),
            "hash": template.get("hash"),
            "required_sections": template.get("required_sections") or [],
            "content": template.get("content") or "",
        },
        "source_semantic_model": semantic_model,
        "project_adapter_rules": _project_adapter_rules(document_type),
        "template_instruction": "Generate the document by following the template content exactly. Preserve the semantic model first, then compose the report. Replace placeholders with concrete content and leave no unresolved {{placeholder}} tokens.",
        "user_prompt": context.get("prompt") or "",
        "sources": _source_context(context),
        "knowledge": _knowledge_context(context.get("knowledge") or []),
        "memories": _knowledge_context(context.get("memories") or []),
        "session_memories": context.get("session_memories") or [],
        "session_skill_update_candidates": [
            {
                "id": item.get("id"),
                "target_skill": item.get("target_skill"),
                "proposed_change": item.get("proposed_change"),
                "status": item.get("status"),
                "temporary_effect": "apply in this session only; do not treat as permanent Skill unless approved",
            }
            for item in (context.get("session_skill_update_candidates") or [])[:5]
        ],
        "upstream_drafts": context.get("upstream_drafts") or [],
        "evidence_refs": context.get("evidence_refs") or {},
        "code_impact": context.get("impact") or {},
        "hard_boundaries": {
            "personal_draft_only": True,
            "writes_release_record": False,
            "generates_code_patch": False,
            "calls_patch_apply": False,
        },
    }
    return json_dumps(payload)


def _revision_user_prompt(
    *,
    document_type: str,
    content_format: str,
    skill: dict[str, Any],
    context: dict[str, Any],
    template: dict[str, Any],
    current_draft: dict[str, Any],
    feedback: str,
) -> str:
    semantic_model = context.get("source_semantic_model") if isinstance(context.get("source_semantic_model"), dict) else {}
    payload = {
        "mode": "revise_existing_draft",
        "document_type": document_type,
        "content_format": content_format,
        "required_json_schema": {
            "title": "string",
            "content_format": content_format,
            "content": "markdown string" if content_format == "markdown" else {"columns": ["string"], "rows": [{"column": "value"}]},
            "revision_summary": "string",
            "evidence_refs_used": {
                "source_uids": ["string"],
                "knowledge_item_uids": ["string"],
                "memory_item_uids": ["string"],
                "code_evidence": ["string"],
            },
            "memory_item_uids_used": ["string"],
            "boundary_confirmation": {
                "personal_draft_only": True,
                "writes_release_record": False,
                "generates_code_patch": False,
                "uses_patch_apply": False,
            },
        },
        "skill": {
            "name": skill.get("name"),
            "display_name": skill.get("display_name"),
            "document_type": skill.get("document_type"),
            "path": skill.get("path"),
        },
        "template": {
            "name": template.get("name"),
            "path": template.get("path"),
            "format": template.get("format"),
            "hash": template.get("hash"),
            "required_sections": template.get("required_sections") or [],
            "content": template.get("content") or "",
        },
        "source_semantic_model": semantic_model,
        "project_adapter_rules": _project_adapter_rules(document_type),
        "template_instruction": "Revise the document by following the template content exactly. Preserve the semantic model and required sections, replace placeholders with concrete content, and leave no unresolved {{placeholder}} tokens.",
        "revision_instruction": "Treat user feedback as directional editing intent. Rewrite and reorganize the full draft as needed; do not append a revision note unless the template asks for one.",
        "feedback": feedback,
        "current_draft": {
            "draft_uid": current_draft.get("draft_uid"),
            "title": current_draft.get("title"),
            "document_type": current_draft.get("document_type"),
            "content_format": current_draft.get("content_format"),
            "current_revision": current_draft.get("current_revision"),
            "content": str(current_draft.get("content") or "")[:12000],
            "generation": (current_draft.get("metadata") or {}).get("generation") if isinstance(current_draft.get("metadata"), dict) else {},
        },
        "original_prompt": context.get("prompt") or "",
        "sources": _source_context(context),
        "knowledge": _knowledge_context(context.get("knowledge") or []),
        "memories": _knowledge_context(context.get("memories") or []),
        "session_memories": context.get("session_memories") or [],
        "session_skill_update_candidates": [
            {
                "id": item.get("id"),
                "target_skill": item.get("target_skill"),
                "proposed_change": item.get("proposed_change"),
                "status": item.get("status"),
                "temporary_effect": "apply in this session only; do not treat as permanent Skill unless approved",
            }
            for item in (context.get("session_skill_update_candidates") or [])[:5]
        ],
        "upstream_drafts": context.get("upstream_drafts") or [],
        "evidence_refs": context.get("evidence_refs") or {},
        "code_impact": context.get("impact") or {},
        "hard_boundaries": {
            "personal_draft_only": True,
            "writes_release_record": False,
            "generates_code_patch": False,
            "calls_patch_apply": False,
        },
    }
    return json_dumps(payload)


def _source_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    sources = []
    for item in context.get("sources") or []:
        sources.append(
            {
                "source_uid": item.get("source_uid"),
                "title": item.get("title"),
                "plain_text_excerpt": str(item.get("plain_text") or "")[:2500],
                "sections": item.get("sections") or [],
                "tables": item.get("tables") or [],
            }
        )
    if not sources and context.get("prompt"):
        sources.append({"source_uid": "current_prompt", "title": "当前用户输入", "plain_text_excerpt": context.get("prompt")})
    return sources


def _knowledge_context(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items[:8]:
        safe_item = safe_recall_prompt_item(item, forbidden_text_checker=personal_forbidden_hits)
        if not safe_item:
            continue
        result.append(
            {
                "item_uid": safe_item.get("item_uid"),
                "title": safe_item.get("title"),
                "category": safe_item.get("category"),
                "content_excerpt": safe_item.get("content"),
                "content_redacted": safe_item.get("content_redacted"),
                "redacted_fields": safe_item.get("redacted_fields") or [],
                "confidence": safe_item.get("confidence"),
            }
        )
    return result


def _memory_item_uids_used(parsed: dict[str, Any], context: dict[str, Any]) -> list[str]:
    allowed = {str(item.get("item_uid") or "") for item in (context.get("prompt_memories") or context.get("memories") or []) if str(item.get("item_uid") or "").strip()}
    raw = parsed.get("memory_item_uids_used")
    if not isinstance(raw, list):
        evidence = parsed.get("evidence_refs_used") if isinstance(parsed.get("evidence_refs_used"), dict) else {}
        raw = evidence.get("memory_item_uids") if isinstance(evidence.get("memory_item_uids"), list) else []
    result: list[str] = []
    for item in raw:
        uid = str(item or "").strip()
        if uid in allowed and uid not in result:
            result.append(uid)
    return result


def _content_format(skill: dict[str, Any], document_type: str) -> str:
    frontmatter = skill.get("frontmatter") if isinstance(skill.get("frontmatter"), dict) else {}
    candidate = str(frontmatter.get("content_format") or "").strip()
    if candidate:
        return candidate
    if document_type == "test_case_spec":
        return "json_table"
    return "markdown"


def _preserve_source_ambiguities(content: str, document_type: str, context: dict[str, Any]) -> str:
    if document_type != "requirement_analysis_report" or not content.strip():
        return content
    semantic_model = context.get("source_semantic_model") if isinstance(context.get("source_semantic_model"), dict) else {}
    ambiguities = semantic_model.get("source_ambiguities") if isinstance(semantic_model, dict) else []
    tokens = [
        str(item.get("original_text") or "").strip()
        for item in (ambiguities or [])
        if isinstance(item, dict) and str(item.get("original_text") or "").strip()
    ]
    missing = [token for token in dict.fromkeys(tokens) if token.lower() not in content.lower()]
    if not missing:
        return content

    addition = "\n".join(f"- `{token}`：源文原始符号表达，必须保留原文并待确认；不得自动改写或规范化。" for token in missing)
    if _has_markdown_section(content, "歧义与待确认"):
        return _append_to_markdown_section(content, "歧义与待确认", addition)
    return content.rstrip() + "\n\n## 歧义与待确认\n" + addition + "\n"


def _has_markdown_section(content: str, title: str) -> bool:
    import re

    return bool(re.search(r"^\s{0,3}#{1,6}\s+" + re.escape(title) + r"\s*$", content, flags=re.MULTILINE))


def _append_to_markdown_section(content: str, title: str, addition: str) -> str:
    import re

    lines = content.splitlines()
    heading = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
    start = None
    level = 0
    for index, line in enumerate(lines):
        match = heading.match(line)
        if match and match.group(2).strip().rstrip("#").strip() == title:
            start = index
            level = len(match.group(1))
            break
    if start is None:
        return content.rstrip() + f"\n\n## {title}\n{addition}\n"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = heading.match(lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    updated = lines[:insert_at] + addition.splitlines() + lines[insert_at:]
    return "\n".join(updated).rstrip() + "\n"


def _extract_content(parsed: dict[str, Any], content_format: str) -> str:
    raw = parsed.get("content")
    if raw is None and isinstance(parsed.get("required_json_schema"), dict):
        raw = parsed["required_json_schema"].get("content")
    if raw is None and content_format == "json_table":
        raw = parsed.get("table")
        if raw is None and isinstance(parsed.get("required_json_schema"), dict):
            raw = parsed["required_json_schema"].get("table")
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (dict, list)):
        return json_dumps(raw)
    return ""


def _project_adapter_rules(document_type: str) -> list[str]:
    if document_type != "requirement_analysis_report":
        return []
    return [
        "必须输出模板要求的全部章节：输入摘要、原文事实表、术语与变量定义、需求理解、条件与状态机、歧义与待确认、关键假设、风险与边界、验收建议、证据引用。",
        "原文事实表只能写输入资料明确给出的事实，不得混入假设、建议、风险、推断或测试设计。",
        "需求理解、关键假设、风险与边界可以写分析推断，但必须与原文事实分开。",
        "输入资料中出现变量、缩写、状态值、符号表达时，必须在术语与变量定义中保留定义关系；不得只保留变量名而丢失物理含义或时间锚点。",
        "术语与变量定义必须显式包含生效/采样时刻；若语义骨架 source_semantic_model.defined_terms 提供 effective_timing，必须写入对应变量行。",
        "输入资料中出现 A/B、X/Y、T1/T2 等短变量或变更量时，必须回到源文抽取它们的物理含义、单位、适用对象和计算关系；不能只写“源文定义了 A/B”或用一个泛化词替代。",
        "输入资料中出现多个阶段、触发事件或完成后处理时，必须在条件与状态机中按阶段拆解；不得把运行中逻辑、完成后逻辑和异常后处理混成一个策略。",
        "状态机必须区分进入条件、运行中条件、完成/退出触发和完成后处理；如果源文同时描述进入某状态和完成后动作，必须至少拆成两个阶段。",
        "优先识别定义关系和阶段触发关系，不要依赖具体业务关键词词典；当前规则应适用于任意需求输入中的 X/Y、T1/T2、模式切换、故障后和恢复后等场景。",
        "疑似拼写错误、状态值错误或符号表达错误必须保留原文，并放入歧义与待确认，不得擅自修正。",
        "不得引用上下文未提供的代码文件、接口、模块或实现证据；如果 evidence_refs 中没有 code_evidence 或 impact 为空，证据引用章节不得出现“代码证据”、源码路径或模块路径。",
        "只生成需求分析草稿，不生成代码、patch、diff、函数实现或发布记录。",
    ]
