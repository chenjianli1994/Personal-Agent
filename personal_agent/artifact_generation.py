from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from personal_agent.core.codebase.impact_analyzer import analyze_codebase_impact
from personal_agent.core.codebase.patch_planner import propose_patch
from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps

from .content_guard import assert_personal_content_clean, personal_forbidden_hits
from .artifact_quality import validate_generated_artifact
from .artifact_drafts import create_artifact_draft, get_artifact_draft, revise_artifact_draft_manual
from .knowledge_recall import billable_memory_item_uids, recall_knowledge, record_recall_feedback, safe_recall_prompt_item
from .knowledge_learning import pending_session_memory_candidates
from .source_semantic_model import build_source_semantic_model
from .skill_registry import ensure_default_document_skills, load_skill_for_document_type
from .skill_reflector import PersonalSkillReflector
from .skill_runtime import generate_artifact_with_skill, revise_artifact_with_skill
from .template_loader import load_default_template
from .skill_update_candidates import create_skill_update_candidate, list_skill_update_candidates


DOCUMENT_ARTIFACT_TYPES = {
    "requirement_analysis_report",
    "requirement_breakdown",
    "functional_spec",
    "detailed_design",
    "test_case_spec",
}

CODE_LINKED_ARTIFACT_TYPES = {"c_code_diff", "unit_test_code_or_diff"}


class DocumentQualityError(ValueError):
    def __init__(self, message: str, *, quality: dict[str, Any], document_type: str):
        super().__init__(message)
        self.quality = quality
        self.document_type = document_type


def propose_personal_artifact(
    db_path: Path,
    *,
    workspace: Path | None = None,
    project_id: int,
    prompt: str,
    document_type: str = "",
    source_uids: list[str] | None = None,
    session_task_uid: str = "",
    session_uid: str = "",
    make_active: bool = True,
) -> dict[str, Any]:
    document_type = _resolve_document_type(prompt, document_type)
    workspace_path = (workspace or db_path.parent).expanduser().resolve()
    ensure_default_document_skills(db_path, workspace=workspace_path, project_id=project_id)
    skill = load_skill_for_document_type(db_path, workspace=workspace_path, project_id=project_id, document_type=document_type)
    context = _generation_context(db_path, project_id=project_id, prompt=prompt, source_uids=source_uids, session_task_uid=session_task_uid, document_type=document_type)
    template = load_default_template(workspace=workspace_path, skill=skill)
    context["template"] = {**template.metadata(), "content": template.content}
    generated = generate_artifact_with_skill(db_path, project_id=project_id, document_type=document_type, skill=skill, context=context)
    assert_personal_content_clean(generated["content"], label="generated draft")
    quality = validate_generated_artifact(
        document_type=document_type,
        content_format=generated["content_format"],
        content=generated["content"],
        context=context,
        skill=skill,
        template=template.metadata(),
        llm_result=generated,
    )
    quality_passed = bool(quality["passed"])
    if not quality_passed:
        _record_quality_failure_skill_candidate(
            db_path,
            project_id=project_id,
            prompt=prompt,
            document_type=document_type,
            skill=skill,
            quality=quality,
            session_task_uid=session_task_uid,
        )
    draft = create_artifact_draft(
        db_path,
        project_id=project_id,
        document_type=document_type,
        title=generated.get("title") or _draft_title(document_type, prompt),
        content=generated["content"],
        content_format=generated["content_format"],
        source_uid=context["source_uids"][0] if context["source_uids"] else "",
        session_uid=session_uid or session_task_uid,
        metadata={
            "generation": {
                "phase": "skill_llm_document_generation",
                "prompt": prompt.strip(),
                "generator": generated["generator"],
                "skill": {
                    "skill_uid": skill["skill_uid"],
                    "name": skill["name"],
                    "display_name": skill["display_name"],
                    "version_uid": skill.get("active_version_uid") or "",
                    "version_index": skill.get("active_version_index"),
                    "path": skill["path"],
                },
                "template": template.metadata(),
                "llm": {
                    "call_id": generated["llm_call_id"],
                    "provider": generated["llm_provider"],
                    "model": generated["llm_model"],
                    "status": generated["llm_status"],
                },
                "quality": quality,
                "quality_gate_passed": quality_passed,
                "quality_gate_status": "passed" if quality_passed else "failed",
                "evidence_refs": context["evidence_refs"],
                "knowledge_refs": context["knowledge_refs"],
                "memory_refs": context["memory_refs"],
                "memory_item_uids_used": generated.get("memory_item_uids_used") or [],
                "boundaries": {
                    "personal_draft_only": True,
                    "writes_release_record": False,
                    "generates_code_patch": False,
                    "generates_unit_test_code": False,
                },
            }
        },
        make_active=make_active,
        status="active" if quality_passed else "quality_failed",
    )
    _record_generation_memory_feedback(
        db_path,
        memories=context["billable_prompt_memories"],
        quality_passed=quality_passed,
        memory_item_uids_used=generated.get("memory_item_uids_used") or [],
    )
    draft["generation"] = {
        "document_type": document_type,
        "content_format": generated["content_format"],
        "evidence_refs": context["evidence_refs"],
        "knowledge_refs": context["knowledge_refs"],
        "memory_refs": context["memory_refs"],
        "memory_item_uids_used": generated.get("memory_item_uids_used") or [],
        "skill": draft["metadata"]["generation"]["skill"],
        "template": draft["metadata"]["generation"]["template"],
        "llm": draft["metadata"]["generation"]["llm"],
        "quality": quality,
        "quality_gate_passed": quality_passed,
        "quality_gate_status": "passed" if quality_passed else "failed",
    }
    return draft


def revise_personal_artifact(
    db_path: Path,
    *,
    project_id: int,
    workspace: Path | None = None,
    draft_uid: str,
    feedback: str,
    session_task_uid: str = "",
    session_uid: str = "",
    make_active: bool = True,
) -> dict[str, Any]:
    feedback = feedback.strip()
    if not feedback:
        raise ValueError("feedback is required")
    draft = get_artifact_draft(db_path, project_id=project_id, draft_uid=draft_uid)
    workspace_path = (workspace or db_path.parent).expanduser().resolve()
    ensure_default_document_skills(db_path, workspace=workspace_path, project_id=project_id)
    document_type = _resolve_document_type_for_draft(draft)
    skill = load_skill_for_document_type(db_path, workspace=workspace_path, project_id=project_id, document_type=document_type)
    current_generation = draft.get("metadata", {}).get("generation") if isinstance(draft.get("metadata"), dict) else {}
    source_uids = [uid for uid in [str(draft.get("source_uid") or "")] if uid]
    context = _generation_context(
        db_path,
        project_id=project_id,
        prompt=feedback,
        source_uids=source_uids,
        session_task_uid=session_task_uid,
        document_type=document_type,
    )
    if isinstance(current_generation, dict):
        context["evidence_refs"] = current_generation.get("evidence_refs") if isinstance(current_generation.get("evidence_refs"), dict) else context["evidence_refs"]
    template = load_default_template(workspace=workspace_path, skill=skill)
    context["template"] = {**template.metadata(), "content": template.content}
    revised = revise_artifact_with_skill(
        db_path,
        project_id=project_id,
        document_type=document_type,
        skill=skill,
        context=context,
        current_draft=draft,
        feedback=feedback,
    )
    assert_personal_content_clean(revised["content"], label="revised draft")
    quality = validate_generated_artifact(
        document_type=document_type,
        content_format=revised["content_format"],
        content=revised["content"],
        context=context,
        skill=skill,
        template=template.metadata(),
        llm_result=revised,
    )
    quality_passed = bool(quality["passed"])
    if not quality_passed:
        _record_quality_failure_skill_candidate(
            db_path,
            project_id=project_id,
            prompt=feedback,
            document_type=document_type,
            skill=skill,
            quality=quality,
            session_task_uid=session_task_uid,
        )
    revision_metadata = {
        "generation": {
            "phase": "phase4_document_artifact_revision",
            "feedback": feedback,
            "previous_revision": draft["current_revision"],
            "skill": {
                "skill_uid": skill["skill_uid"],
                "name": skill["name"],
                "display_name": skill["display_name"],
                "version_uid": skill.get("active_version_uid") or "",
                "version_index": skill.get("active_version_index"),
                "path": skill["path"],
            },
            "template": template.metadata(),
            "llm": {
                "call_id": revised["llm_call_id"],
                "provider": revised["llm_provider"],
                "model": revised["llm_model"],
                "status": revised["llm_status"],
            },
            "quality": quality,
            "quality_gate_passed": quality_passed,
            "quality_gate_status": "passed" if quality_passed else "failed",
            "evidence_refs": context["evidence_refs"],
            "knowledge_refs": context["knowledge_refs"],
            "memory_refs": context["memory_refs"],
            "memory_item_uids_used": revised.get("memory_item_uids_used") or [],
            "boundaries": {
                "personal_draft_only": True,
                "writes_release_record": False,
                "generates_code_patch": False,
            },
        }
    }
    revised_draft = revise_artifact_draft_manual(
        db_path,
        project_id=project_id,
        draft_uid=draft_uid,
        content=revised["content"],
        metadata=revision_metadata,
        make_active=make_active,
        status="active" if quality_passed else "quality_failed",
    )
    _record_generation_memory_feedback(
        db_path,
        memories=context["billable_prompt_memories"],
        quality_passed=quality_passed,
        memory_item_uids_used=revised.get("memory_item_uids_used") or [],
    )
    return revised_draft


def propose_personal_code_patch(
    db_path: Path,
    *,
    project_id: int,
    prompt: str,
    target_symbol: str = "",
    target_file: str = "",
    directives: list[dict[str, Any]] | None = None,
    session_uid: str = "",
    make_active: bool = True,
) -> dict[str, Any]:
    patch_result = propose_patch(
        db_path,
        project_id,
        {
            "change_text": prompt,
            "target_symbol": target_symbol,
            "target_file": target_file,
            "directives": directives or [],
            "session_uid": session_uid,
            "dry_run": True,
        },
    )
    patch_text = str(patch_result.get("patch_text") or "").strip()
    if not patch_text:
        raise ValueError("; ".join(str(item) for item in patch_result.get("limitations") or []) or str(patch_result.get("error") or "no candidate diff generated"))
    draft = create_artifact_draft(
        db_path,
        project_id=project_id,
        document_type="c_code_diff",
        title="C 代码 Patch 草稿",
        content=patch_text,
        content_format="diff",
        session_uid=session_uid,
        metadata={
            "generation": {
                "phase": "phase5_code_patch_linkage",
                "prompt": prompt.strip(),
                "generator": "patch_propose",
                "patch_propose": patch_result,
                "boundaries": {
                    "personal_draft_only": True,
                    "writes_release_record": False,
                    "writes_real_code": False,
                    "uses_patch_propose": True,
                    "requires_confirmed_apply": True,
                },
            }
        },
        make_active=make_active,
    )
    draft["generation"] = {"document_type": "c_code_diff", "content_format": "diff", "patch_propose": patch_result}
    return draft


def propose_personal_unit_test_code(
    db_path: Path,
    *,
    project_id: int,
    prompt: str,
    source_uids: list[str] | None = None,
    session_task_uid: str = "",
    session_uid: str = "",
    make_active: bool = True,
) -> dict[str, Any]:
    context = _generation_context(db_path, project_id=project_id, prompt=prompt, source_uids=source_uids, session_task_uid=session_task_uid)
    content = _unit_test_code_or_diff(context)
    draft = create_artifact_draft(
        db_path,
        project_id=project_id,
        document_type="unit_test_code_or_diff",
        title="单元测试代码/Patch 草稿",
        content=content,
        content_format="diff",
        source_uid=context["source_uids"][0] if context["source_uids"] else "",
        session_uid=session_uid or session_task_uid,
        metadata={
            "generation": {
                "phase": "phase5_unit_test_code_linkage",
                "prompt": prompt.strip(),
                "generator": "unit_test_code_or_diff_generator",
                "evidence_refs": context["evidence_refs"],
                "impact": context["impact"],
                "boundaries": {
                    "personal_draft_only": True,
                    "writes_release_record": False,
                    "writes_real_code": False,
                    "requires_whitelisted_validation": True,
                },
            }
        },
        make_active=make_active,
    )
    draft["generation"] = {"document_type": "unit_test_code_or_diff", "content_format": "diff", "impact": context["impact"]}
    return draft


def active_draft_uid(db_path: Path, *, project_id: int) -> str:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT draft_uid FROM personal_drafts
            WHERE project_id=? AND status='active' AND is_active=1
            ORDER BY id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return str(row["draft_uid"]) if row is not None else ""


def _resolve_document_type(prompt: str, document_type: str) -> str:
    candidate = document_type.strip()
    if not candidate:
        candidate = _requested_document_type(prompt)
    if candidate not in DOCUMENT_ARTIFACT_TYPES:
        raise ValueError(f"unsupported document_type: {candidate}")
    return candidate


def _requested_document_type(prompt: str) -> str:
    text = prompt.replace(" ", "").lower()
    if "需求拆" in text or "需求分解" in text:
        return "requirement_breakdown"
    if "功能规范" in text or "功能规格" in text:
        return "functional_spec"
    if "详细设计" in text or "设计说明" in text:
        return "detailed_design"
    if "测试用例" in text or "测试规格" in text:
        return "test_case_spec"
    return "requirement_analysis_report"


def _resolve_document_type_for_draft(draft: dict[str, Any]) -> str:
    document_type = str(draft.get("document_type") or "").strip()
    if document_type not in DOCUMENT_ARTIFACT_TYPES:
        raise ValueError(f"unsupported document_type: {document_type}")
    return document_type


def _generation_context(
    db_path: Path,
    *,
    project_id: int,
    prompt: str,
    source_uids: list[str] | None,
    session_task_uid: str = "",
    document_type: str = "",
) -> dict[str, Any]:
    sources = _load_sources(db_path, project_id=project_id, source_uids=source_uids)
    knowledge = recall_knowledge(db_path, project_id=project_id, query=prompt, limit=5, exclude_category="memory_lesson")
    memories = recall_knowledge(db_path, project_id=project_id, query=prompt, limit=5, category="memory_lesson")
    prompt_memories = [safe_item for item in memories if (safe_item := safe_recall_prompt_item(item, forbidden_text_checker=personal_forbidden_hits))]
    billable_memory_uids = set(billable_memory_item_uids(prompt_memories))
    billable_prompt_memories = [item for item in prompt_memories if item.get("item_uid") in billable_memory_uids]
    session_memories = pending_session_memory_candidates(db_path, project_id=project_id, task_uid=session_task_uid, session_uid=session_task_uid)
    session_skill_candidates = [
        item
        for item in list_skill_update_candidates(db_path, project_id=project_id, status="candidate")
        if item.get("session_uid") == session_task_uid
    ] if session_task_uid else []
    source_semantic_model = (
        build_source_semantic_model(
            db_path,
            project_id=project_id,
            prompt=prompt,
            sources=sources,
            session_task_uid=session_task_uid,
        )
        if document_type == "requirement_analysis_report"
        else {"defined_terms": [], "state_phases": [], "control_branches": [], "open_questions": [], "llm": {}}
    )
    return {
        "db_path": db_path,
        "project_id": project_id,
        "prompt": prompt.strip(),
        "session_task_uid": session_task_uid,
        "sources": sources,
        "source_uids": [item["source_uid"] for item in sources],
        "knowledge_refs": [{"item_uid": item["item_uid"], "title": item["title"]} for item in knowledge],
        "memory_refs": [{"item_uid": item["item_uid"], "title": item["title"]} for item in prompt_memories],
        "session_memory_refs": [{"id": item["id"], "title": item["title"]} for item in session_memories],
        "knowledge": knowledge,
        "memories": memories,
        "prompt_memories": prompt_memories,
        "billable_prompt_memories": billable_prompt_memories,
        "session_memories": session_memories,
        "session_skill_update_candidates": session_skill_candidates,
        "source_semantic_model": source_semantic_model,
        "evidence_refs": {
            "active_source_uids": [item["source_uid"] for item in sources],
            "knowledge_item_uids": [item["item_uid"] for item in knowledge],
            "approved_memory_uids": [item["item_uid"] for item in prompt_memories],
            "session_memory_candidate_ids": [item["id"] for item in session_memories],
            "session_skill_update_candidate_ids": [item["id"] for item in session_skill_candidates],
        },
        "impact": _detailed_design_impact(db_path, project_id, prompt) if document_type == "detailed_design" else {},
    }


def _record_quality_failure_skill_candidate(
    db_path: Path,
    *,
    project_id: int,
    prompt: str,
    document_type: str,
    skill: dict[str, Any],
    quality: dict[str, Any],
    session_task_uid: str = "",
) -> None:
    try:
        reflection = PersonalSkillReflector(db_path, project_id).reflect(
            {
                "prompt": prompt,
                "session_uid": session_task_uid,
                "quality_failure": quality,
                "document_type": document_type,
                "target_skill": skill.get("name") or "",
                "allowed_change_scope": ["Instructions", "Output Contract", "template_rules"],
            }
        )
        if not reflection.get("has_skill_update_signal"):
            return
        target_skill = str(reflection.get("target_skill") or skill.get("name") or "").strip()
        proposed_change = str(reflection.get("proposed_change") or "").strip()
        if not target_skill or not proposed_change:
            return
        create_skill_update_candidate(
            db_path,
            project_id=project_id,
            target_skill=target_skill,
            reason=str(reflection.get("reason") or "Quality check failure suggests a reusable Skill rule."),
            proposed_change=proposed_change,
            risk=str(reflection.get("risk") or ""),
            evidence_refs={
                "quality": {
                    "document_type": document_type,
                    "score": quality.get("score"),
                    "blocking_failures": quality.get("blocking_failures") or [],
                },
                "reflection": {"confidence": reflection.get("confidence"), "change_type": reflection.get("change_type")},
            },
            session_uid=session_task_uid,
            source="quality_check_failure",
        )
    except Exception:
        return


def _load_sources(db_path: Path, *, project_id: int, source_uids: list[str] | None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        if source_uids:
            placeholders = ",".join("?" for _ in source_uids)
            rows = conn.execute(
                f"""
                SELECT source_uid, title, plain_text, sections_json, tables_json
                FROM personal_input_sources
                WHERE project_id=? AND status='active' AND source_uid IN ({placeholders})
                ORDER BY is_active DESC, id DESC
                """,
                (project_id, *source_uids),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT source_uid, title, plain_text, sections_json, tables_json
                FROM personal_input_sources
                WHERE project_id=? AND status='active' AND is_active=1
                ORDER BY id DESC
                """,
                (project_id,),
            ).fetchall()
    return [
        {
            "source_uid": row["source_uid"],
            "title": row["title"],
            "plain_text": row["plain_text"],
            "sections": _loads_json(row["sections_json"], []),
            "tables": _loads_json(row["tables_json"], []),
        }
        for row in rows
    ]


def _record_generation_memory_feedback(db_path: Path, *, memories: list[dict[str, Any]], quality_passed: bool, memory_item_uids_used: list[str]) -> None:
    billable_uids = set(billable_memory_item_uids(memories))
    helpful_uids = set(memory_item_uids_used if quality_passed else []) & billable_uids
    for item in [memory for memory in memories if str(memory.get("item_uid") or "").strip() in billable_uids]:
        item_uid = str(item.get("item_uid") or "").strip()
        if not item_uid:
            continue
        try:
            record_recall_feedback(db_path, item_uid=item_uid, event="use")
            if item_uid in helpful_uids:
                record_recall_feedback(db_path, item_uid=item_uid, event="helpful")
        except ValueError:
            continue


def _unit_test_code_or_diff(context: dict[str, Any]) -> str:
    impacted_tests = context.get("impact", {}).get("impacted_tests") or []
    target = str(impacted_tests[0].get("path") if impacted_tests else "tests/test_generated_candidate.c")
    body = [
        f"diff --git a/{target} b/{target}",
        "new file mode 100644",
        "index 0000000..1111111",
        "--- /dev/null",
        f"+++ b/{target}",
        "@@ -0,0 +1,28 @@",
        "+/* Candidate unit-test patch draft generated as personal artifact. */",
        "+#include <assert.h>",
        "+",
        "+void test_normal_path(void)",
        "+{",
        "+    /* TODO: bind to concrete interface from code impact evidence. */",
        "+    assert(1);",
        "+}",
        "+",
        "+void test_boundary_path(void)",
        "+{",
        "+    /* TODO: cover threshold or boundary condition from requirement. */",
        "+    assert(1);",
        "+}",
        "+",
        "+void test_exception_or_diagnostic_path(void)",
        "+{",
        "+    /* TODO: cover invalid input, diagnostic event, or fallback behavior. */",
        "+    assert(1);",
        "+}",
    ]
    return "\n".join(body)


def _detailed_design_impact(db_path: Path, project_id: int, prompt: str) -> dict[str, Any]:
    try:
        return analyze_codebase_impact(db_path, project_id, prompt, limit=8)
    except Exception as exc:
        return {"passed": False, "limitations": [str(exc)], "impacted_files": [], "symbols": []}


def _draft_title(document_type: str, prompt: str) -> str:
    labels = {
        "requirement_analysis_report": "需求分析报告",
        "requirement_breakdown": "需求拆解文件",
        "functional_spec": "功能规范说明",
        "detailed_design": "软件详细设计",
        "test_case_spec": "测试用例规格",
    }
    return labels.get(document_type, document_type)


def _source_excerpt(context: dict[str, Any]) -> str:
    sources = context.get("sources") or []
    if not sources:
        return f"- 当前对话：{context.get('prompt') or '无'}"
    lines = []
    for source in sources[:3]:
        text = str(source.get("plain_text") or "").strip().replace("\r\n", "\n")
        excerpt = text[:500] + ("..." if len(text) > 500 else "")
        lines.append(f"- {source.get('title')}（{source.get('source_uid')}）：{excerpt}")
    return "\n".join(lines)


def _bullet_from_sources(context: dict[str, Any], *, fallback: str) -> str:
    sources = context.get("sources") or []
    if not sources:
        return f"- {fallback}"
    bullets = []
    for source in sources[:3]:
        text = str(source.get("plain_text") or "").strip().splitlines()
        first = next((line.strip() for line in text if line.strip()), "")
        if first:
            bullets.append(f"- {first[:180]}")
    return "\n".join(bullets) if bullets else f"- {fallback}"


def _numbered_items(context: dict[str, Any], *, prefix: str) -> str:
    sources = context.get("sources") or []
    candidates: list[str] = []
    for source in sources:
        for line in str(source.get("plain_text") or "").splitlines():
            line = line.strip("- #\t ")
            if line:
                candidates.append(line)
            if len(candidates) >= 5:
                break
    if not candidates:
        candidates = [context.get("prompt") or "根据当前用户输入补充需求条目"]
    return "\n".join(f"{index}. {prefix}-{index:03d}：{item[:180]}" for index, item in enumerate(candidates[:5], start=1))


def _knowledge_section(context: dict[str, Any]) -> str:
    refs = context.get("knowledge_refs") or []
    memories = context.get("memory_refs") or []
    session_memories = context.get("session_memory_refs") or []
    if not refs and not memories and not session_memories:
        return "\n## 知识与经验引用\n- 暂无匹配的 active knowledge 或 approved memory。"
    lines = ["", "## 知识与经验引用"]
    lines.extend(f"- 知识：{item['title']}（{item['item_uid']}）" for item in refs)
    lines.extend(f"- 经验：{item['title']}（{item['item_uid']}）" for item in memories)
    lines.extend(f"- 本会话即时反馈：{item['title']}（candidate:{item['id']}，未批准为长期规则）" for item in session_memories)
    return "\n".join(lines)


def _behavior_rules_section(context: dict[str, Any]) -> str:
    rules: list[str] = []
    for item in context.get("session_memories") or []:
        lesson = str(item.get("lesson") or item.get("expected_behavior") or "").strip()
        if lesson:
            rules.append(f"- 本会话即时遵守：{lesson}")
    for item in context.get("memories") or []:
        content = str(item.get("content") or "").strip()
        if content:
            rules.append(f"- 长期经验：{content[:220]}")
    if not rules:
        return ""
    return "\n".join(["", "## 行为规则注入", *rules])


def _format_impact(impact: dict[str, Any]) -> str:
    limitations = impact.get("limitations") or []
    files = impact.get("impacted_files") or impact.get("affected_files") or []
    symbols = impact.get("symbols") or impact.get("symbol_refs") or []
    lines = [f"- impact_analyze.passed: {bool(impact.get('passed'))}"]
    if files:
        lines.append(f"- impacted_files: {', '.join(str(item) for item in files[:8])}")
    if symbols:
        lines.append(f"- symbols: {', '.join(str(item) for item in symbols[:8])}")
    if limitations:
        lines.append(f"- limitations: {'; '.join(str(item) for item in limitations[:5])}")
    return "\n".join(lines)


def _format_symbols(symbols: list[dict[str, Any]]) -> str:
    if not symbols:
        return "- no symbol_lookup candidates available"
    return "\n".join(
        f"- {item.get('kind', 'symbol')} {item.get('name', '')} @ {item.get('file_path') or item.get('path', '')}:{item.get('start_line', '')}"
        for item in symbols[:8]
    )


def _format_call_impacts(call_impacts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for impact in call_impacts[:5]:
        function = impact.get("function_name", "")
        callers = ", ".join(str(item.get("caller", "")) for item in (impact.get("callers") or [])[:5])
        callees = ", ".join(str(item.get("callee", "")) for item in (impact.get("callees") or [])[:5])
        lines.append(f"- {function}: callers=[{callers or 'none'}], callees=[{callees or 'none'}]")
    return "\n".join(lines) if lines else "- no call_graph_query evidence available"


def _format_include_impacts(include_impacts: list[dict[str, Any]]) -> str:
    lines = []
    for impact in include_impacts[:5]:
        affected = ", ".join(str(item) for item in (impact.get("affected_files") or [])[:8])
        lines.append(f"- {impact.get('path', '')}: affected_files=[{affected or 'none'}]")
    return "\n".join(lines) if lines else "- no include_impact_query evidence available"


def _format_macro_type_variable_impacts(impact: dict[str, Any]) -> str:
    macro_lines = [
        f"macro {item.get('macro_name', '')}: blocks={len(item.get('conditional_blocks') or [])}"
        for item in (impact.get("macro_impacts") or [])[:4]
    ]
    type_lines = [
        f"type {item.get('type_name', '')}: interfaces={len(item.get('interfaces') or [])}, files={len(item.get('referencing_files') or [])}"
        for item in (impact.get("type_impacts") or [])[:4]
    ]
    variable_lines = [
        f"variable {item.get('variable_name', '')}: references={len(item.get('references') or [])}"
        for item in (impact.get("variable_impacts") or [])[:4]
    ]
    lines = [*macro_lines, *type_lines, *variable_lines]
    return "\n".join(f"- {line}" for line in lines) if lines else "- no macro/type/variable evidence available"


def _loads_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default
