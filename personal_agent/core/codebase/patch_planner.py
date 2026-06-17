from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..code_style import ensure_code_style_profile, evaluate_patch_style
from ..database import connect
from ..knowledge_base import search_knowledge
from ..utils import json_dumps, utc_now
from .impact_analyzer import analyze_codebase_impact
from .index_store import resolve_repo_path
from .retriever import symbol_lookup
from .schemas import ChangeRequest, PatchDirective, PatchPlan


def propose_patch(db_path: Path, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    request = _change_request(project_id, payload)
    repo = resolve_repo_path(db_path, project_id, "")
    if not repo:
        return _failed("project input code_repo_path is not configured")
    if not repo.exists() or not repo.is_dir():
        return _failed(f"code repository does not exist: {repo}")

    impact = analyze_codebase_impact(db_path, project_id, request.change_text, limit=int(payload.get("limit") or 8))
    symbol = symbol_lookup(db_path, project_id, request.target_symbol, limit=5) if request.target_symbol else {"passed": False, "matches": []}
    style_profile = ensure_code_style_profile(db_path, project_id)
    knowledge_rules = _knowledge_rules(db_path, project_id, request)
    plan = _build_patch_plan(request, impact, symbol, style_profile, knowledge_rules)
    patch_text = _candidate_diff(repo, request)
    if not patch_text.strip():
        return {
            "passed": False,
            "error": "no candidate diff could be generated from patch directives or target hints",
            "change_request": _request_payload(request),
            "patch_plan": _plan_payload(plan),
            "impact_analysis": impact,
            "style_profile": _compact_style_profile(style_profile),
            "knowledge_rules": knowledge_rules,
            "limitations": ["patch_propose requires explicit directives for stage-3 deterministic candidate generation"],
            "evidence_refs": plan.evidence_refs,
        }

    style_result = evaluate_patch_style(style_profile, patch_text)
    dry_run = bool(payload.get("dry_run"))
    artifact = {} if dry_run else _write_candidate_patch_artifact(db_path, request, patch_text, plan)
    return {
        "passed": True,
        "dry_run": dry_run,
        "change_request": _request_payload(request),
        "patch_plan": _plan_payload(plan),
        "patch_text": patch_text,
        "artifact": artifact,
        "artifact_type": "c_code_diff",
        "impact_analysis": impact,
        "style_profile": _compact_style_profile(style_profile),
        "style_result": style_result,
        "knowledge_rules": knowledge_rules,
        "integration_contract": {
            "candidate_artifact_type": "c_code_diff",
            "consumer": "personal_drafts",
            "real_project_write": False,
            "candidate_artifact_written": not dry_run,
            "apply_policy": "candidate diff only; verification, review, approval, and apply remain explicit personal actions",
        },
        "risk_boundary": "write_candidate_only",
        "limitations": _unique(
            [
                "candidate diff is generated from explicit deterministic directives, not from free-form code synthesis",
                *impact.get("limitations", []),
                *impact.get("unresolved_items", []),
            ]
        ),
        "evidence_refs": {
            **plan.evidence_refs,
            "draft_uids": [] if dry_run else [artifact["draft_uid"]],
            "artifact_files": [] if dry_run else [artifact["file_path"]],
        },
    }


def _change_request(project_id: int, payload: dict[str, Any]) -> ChangeRequest:
    directives = []
    for item in payload.get("directives") or []:
        directives.append(
            PatchDirective(
                file_path=str(item.get("file_path") or ""),
                find=str(item.get("find") or ""),
                replace=str(item.get("replace") or ""),
                description=str(item.get("description") or ""),
            )
        )
    return ChangeRequest(
        project_id=project_id,
        requirement_id=str(payload.get("requirement_id") or ""),
        change_text=str(payload.get("change_text") or payload.get("change_hint") or ""),
        target_symbol=str(payload.get("target_symbol") or ""),
        target_file=str(payload.get("target_file") or ""),
        directives=directives,
    )


def _build_patch_plan(
    request: ChangeRequest,
    impact: dict[str, Any],
    symbol: dict[str, Any],
    style_profile: dict[str, Any],
    knowledge_rules: list[dict[str, Any]],
) -> PatchPlan:
    directive_files = [item.file_path for item in request.directives if item.file_path]
    symbol_matches = symbol.get("matches", []) if symbol.get("passed") else []
    symbol_files = [item.get("file_path", "") for item in symbol_matches]
    modified_files = _unique([request.target_file, *directive_files, *symbol_files, *impact.get("affected_files", [])])[:12]
    modified_functions = _unique(
        [
            request.target_symbol,
            *[item.get("name", "") for item in symbol_matches if item.get("kind") == "function"],
            *[item.get("name", "") for item in impact.get("candidate_functions", [])],
        ]
    )
    interface_change = any(path.endswith((".h", ".hpp", ".hh")) for path in modified_files)
    impacted_tests = [item.get("path", "") for item in impact.get("impacted_tests", []) if item.get("path")]
    tests_need_update = bool(impacted_tests) or "test" in request.change_text.lower()
    risks = _unique(
        [
            *impact.get("risk_notes", []),
            *impact.get("unresolved_items", []),
            *([] if style_profile.get("sample_count") else ["code_style_profile has no project samples; candidate style confidence is limited"]),
            *([] if knowledge_rules else ["no directly matched knowledge-base rule was found for this change request"]),
        ]
    )
    return PatchPlan(
        plan_uid="patch_plan_" + uuid4().hex[:12],
        target_requirement=request.requirement_id,
        modified_files=modified_files,
        modified_functions=modified_functions,
        interface_change=interface_change,
        tests_need_update=tests_need_update,
        aspice_trace_impact={
            "requirement_id": request.requirement_id,
            "process_area": "SWE.3",
            "work_products": ["c_code_diff", "unit_test_diff" if tests_need_update else "test_suggestion"],
            "trace_policy": "candidate patch must be verified and reviewed before baseline",
        },
        risk_points=risks,
        test_suggestions=_test_suggestions(impact, impacted_tests),
        evidence_refs={
            "code_files": modified_files,
            "code_symbols": modified_functions,
            "knowledge_item_ids": [item.get("id") for item in knowledge_rules if item.get("id")],
            "style_profile_id": style_profile.get("id", ""),
        },
    )


def _candidate_diff(repo: Path, request: ChangeRequest) -> str:
    patches = []
    for directive in request.directives:
        if not directive.file_path or not directive.find:
            continue
        target = (repo / directive.file_path).resolve()
        root = repo.resolve()
        if target != root and root not in target.parents:
            raise ValueError("patch directive target must stay within configured code_repo_path")
        if not target.exists() or not target.is_file():
            continue
        original = target.read_text(encoding="utf-8", errors="replace")
        if directive.find not in original:
            continue
        updated = original.replace(directive.find, directive.replace, 1)
        if updated == original:
            continue
        rel = target.relative_to(repo).as_posix()
        patches.append(
            "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile=f"a/{rel}",
                    tofile=f"b/{rel}",
                )
            )
        )
    return "\n".join(patches)


def _write_candidate_patch_artifact(db_path: Path, request: ChangeRequest, patch_text: str, plan: PatchPlan) -> dict[str, Any]:
    now = utc_now()
    draft_uid = "draft_" + now.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "") + "_" + uuid4().hex[:8]
    revision_uid = "rev_" + uuid4().hex[:16]
    out_dir = (db_path.parent / "agent_runs" / draft_uid).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    patch_path = out_dir / "candidate.patch"
    plan_path = out_dir / "patch_plan.json"
    patch_path.write_text(patch_text, encoding="utf-8")
    plan_path.write_text(json_dumps(_plan_payload(plan)), encoding="utf-8")
    metadata = {
        "generation": {
            "phase": "phase5_code_patch_linkage",
            "generator": "patch_propose",
            "patch_propose": {
                "plan_uid": plan.plan_uid,
                "change_request": _request_payload(request),
                "patch_plan": _plan_payload(plan),
                "file_path": str(patch_path),
                "plan_path": str(plan_path),
            },
            "boundaries": {
                "personal_draft_only": True,
                "writes_release_record": False,
                "writes_real_code": False,
                "uses_patch_propose": True,
                "requires_confirmed_apply": True,
            },
        }
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE personal_drafts SET is_active=0 WHERE project_id=?
            """,
            (request.project_id,),
        )
        conn.execute(
            """
            INSERT INTO personal_drafts(
                draft_uid, project_id, source_uid, document_type, title, content_format,
                current_revision, status, is_active, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, '', 'c_code_diff', ?, 'diff', 1, 'active', 1, ?, ?, ?)
            """,
            (
                draft_uid,
                request.project_id,
                "Personal Code Patch Candidate",
                json_dumps(metadata),
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO personal_draft_revisions(
                revision_uid, draft_uid, project_id, revision_index, content, metadata_json, created_at
            )
            VALUES (?, ?, ?, 1, ?, ?, ?)
            """,
            (revision_uid, draft_uid, request.project_id, patch_text, json_dumps(metadata), now),
        )
        conn.execute(
            """
            INSERT INTO audit_events(project_id, event_type, message, payload_json, created_at)
            VALUES (?, 'PATCH_PROPOSED', ?, ?, ?)
            """,
            (
                request.project_id,
                f"Generated personal draft patch for {request.requirement_id}",
                json_dumps({"draft_uid": draft_uid, "plan_uid": plan.plan_uid, "modified_files": plan.modified_files, "patch_file": str(patch_path)}),
                now,
            ),
        )
    return {
        "draft_uid": draft_uid,
        "name": "Personal Code Patch Candidate",
        "file_path": str(patch_path),
        "plan_path": str(plan_path),
        "status": "active",
        "document_type": "c_code_diff",
        "artifact_type": "c_code_diff",
    }


def _knowledge_rules(db_path: Path, project_id: int, request: ChangeRequest) -> list[dict[str, Any]]:
    query = " ".join([request.change_text, request.target_symbol, "SWE.3 C code patch rule"])
    rules = search_knowledge(db_path, query, project_id=project_id, limit=5)
    return [
        {
            "id": item.get("id"),
            "title": item.get("title", ""),
            "category": item.get("category", ""),
            "source_ref": item.get("source_ref", ""),
            "excerpt": item.get("excerpt", ""),
        }
        for item in rules
    ]


def _test_suggestions(impact: dict[str, Any], impacted_tests: list[str]) -> dict[str, list[str]]:
    candidate_functions = [item.get("name", "") for item in impact.get("candidate_functions", []) if item.get("name")]
    return {
        "related_unit_tests": impacted_tests,
        "new_boundary_tests": [f"Add boundary coverage around {name}" for name in candidate_functions[:5]],
        "regression_tests": ["diagnostic", "communication", "state_machine", "NVM"],
    }


def _request_payload(request: ChangeRequest) -> dict[str, Any]:
    return {
        "project_id": request.project_id,
        "requirement_id": request.requirement_id,
        "change_text": request.change_text,
        "target_symbol": request.target_symbol,
        "target_file": request.target_file,
        "directives": [
            {"file_path": item.file_path, "find": item.find, "replace": item.replace, "description": item.description}
            for item in request.directives
        ],
    }


def _plan_payload(plan: PatchPlan) -> dict[str, Any]:
    return {
        "plan_uid": plan.plan_uid,
        "target_requirement": plan.target_requirement,
        "modified_files": plan.modified_files,
        "modified_functions": plan.modified_functions,
        "interface_change": plan.interface_change,
        "tests_need_update": plan.tests_need_update,
        "aspice_trace_impact": plan.aspice_trace_impact,
        "risk_points": plan.risk_points,
        "test_suggestions": plan.test_suggestions,
        "evidence_refs": plan.evidence_refs,
    }


def _compact_style_profile(profile: dict[str, Any]) -> dict[str, Any]:
    data = profile.get("profile") if isinstance(profile.get("profile"), dict) else {}
    return {
        "id": profile.get("id", ""),
        "sample_count": profile.get("sample_count", 0),
        "confidence": profile.get("confidence", 0),
        "rules": data.get("rules", [])[:8],
        "naming": data.get("naming", {}),
        "indentation": data.get("indentation", {}),
    }


def _failed(error: str) -> dict[str, Any]:
    return {"passed": False, "error": error, "limitations": [error], "evidence_refs": {}}


def _unique(items: list[Any]) -> list[Any]:
    result = []
    for item in items:
        if item in {None, ""}:
            continue
        if item not in result:
            result.append(item)
    return result
