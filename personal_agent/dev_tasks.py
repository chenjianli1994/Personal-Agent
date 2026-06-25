from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.codebase.controlled_patch import validate_patch
from personal_agent.core.codebase.impact_analyzer import analyze_codebase_impact
from personal_agent.core.codebase.patch_planner import propose_patch
from personal_agent.core.codebase.retriever import symbol_lookup
from personal_agent.core.codebase.index_store import latest_repository
from personal_agent.core.database import connect
from personal_agent.core.services_min import create_memory_candidate
from personal_agent.core.utils import json_dumps, utc_now

from .artifact_drafts import DOCUMENT_LINEAGE_ORDER
from .artifact_generation import propose_personal_artifact
from .context_builder import PersonalContextBuilder
from .dev_task_trace import DevTaskTraceService
from .knowledge_recall import recall_knowledge
from .policy_guard import apply_personal_policy


DEV_TASK_TYPE = "personal_dev_document_pipeline_v3"
ACTIVE_LIKE_STATUSES = ("active", "blocked")
CONTINUE_PROMPTS = {"继续", "下一步", "按计划", "继续推进"}


def dev_task_stage_order() -> list[str]:
    return list(DOCUMENT_LINEAGE_ORDER[: DOCUMENT_LINEAGE_ORDER.index("test_case_spec") + 1])


class DevTaskOrchestrator:
    def __init__(self, db_path: Path, *, workspace: Path, project_id: int):
        self.db_path = db_path
        self.workspace = workspace
        self.project_id = project_id
        self.context_builder = PersonalContextBuilder(db_path, project_id)
        self.trace_service = DevTaskTraceService(db_path, project_id=project_id)

    def start(self, *, session_uid: str, prompt: str, source_uids: list[str] | None = None) -> dict[str, Any]:
        session_uid = session_uid.strip()
        prompt = prompt.strip()
        task_source_uids = list(dict.fromkeys(uid.strip() for uid in (source_uids or []) if uid.strip()))
        if not session_uid:
            raise ValueError("session_uid is required")
        if not prompt:
            raise ValueError("prompt is required")
        self._require_session(session_uid)
        task_uid = f"task_{uuid4().hex}"
        now = utc_now()
        plan = {
            "stage_order": dev_task_stage_order(),
            "source_uids": task_source_uids,
            "last_action": {"type": "start", "status": "created", "at": now},
            "blocked_reason": "",
            "validation_summary": {},
        }
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE agent_tasks
                SET status='archived', updated_at=?, completed_at=CASE WHEN completed_at='' THEN ? ELSE completed_at END
                WHERE project_id=? AND session_uid=? AND task_type=? AND status IN ('active', 'blocked')
                """,
                (now, now, self.project_id, session_uid, DEV_TASK_TYPE),
            )
            conn.execute(
                """
                INSERT INTO agent_tasks(
                    task_uid, project_id, session_uid, title, task_type, status, user_prompt,
                    normalized_intent_json, constraints_json, plan_json, current_step,
                    source_run_id, agent_run_id, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, '', '', '', '', ?, ?)
                """,
                (
                    task_uid,
                    self.project_id,
                    session_uid,
                    _task_title(prompt),
                    DEV_TASK_TYPE,
                    prompt,
                    json_dumps({"intent": "dev_task_pipeline_v3"}),
                    json_dumps({"single_step_continue": True, "auto_validation": False}),
                    json_dumps(plan),
                    now,
                    now,
                ),
            )
        return self._advance_one(task_uid, action_type="start")

    def continue_task(self, *, task_uid: str) -> dict[str, Any]:
        row = self._task_row(task_uid.strip())
        if str(row["status"] or "") not in ACTIVE_LIKE_STATUSES:
            raise ValueError("dev task is not active")
        return self._advance_one(task_uid.strip(), action_type="continue")

    def get(self, task_uid: str) -> dict[str, Any]:
        task_uid = task_uid.strip()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM agent_tasks WHERE project_id=? AND task_uid=? AND task_type=?",
                (self.project_id, task_uid, DEV_TASK_TYPE),
            ).fetchone()
            if row is None:
                raise ValueError("dev task not found")
            display_metadata = self._task_display_metadata_for_rows(conn, [row]).get(task_uid)
        return self._task_payload(row, display_metadata=display_metadata)

    def trace(self, task_uid: str) -> dict[str, Any]:
        return self.trace_service.trace_for_task(task_uid.strip())

    def rebuild_trace(self, task_uid: str) -> dict[str, Any]:
        return self.trace_service.rebuild_for_task(task_uid.strip())

    def propose_patch_candidate(self, *, task_uid: str, prompt: str) -> dict[str, Any]:
        row = self._task_row(task_uid.strip())
        context = self._context_for_task(row)
        requirement_id = self.trace_service.active_requirement_id_for_task(task_uid)
        latest_failure = self._latest_failed_validation(task_uid)
        gate = self._patch_evidence_gate(
            task_uid=task_uid,
            requirement_id=requirement_id,
            context=context,
            failure_category=str(latest_failure.get("category") or ""),
            validation_kind=str(latest_failure.get("kind") or ""),
            modified_files=self._draft_target_files(self._latest_patch_candidate(task_uid=task_uid, requirement_id=requirement_id)),
        )
        if not gate["passed"]:
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=gate["reason"], trace_refs=gate["trace_refs"])
        try:
            directives_result = self._generate_patch_directives(
                task_uid=task_uid,
                requirement_id=requirement_id,
                prompt=prompt,
                evidence_pack=gate["evidence_pack"],
            )
        except ValueError as exc:
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=str(exc), trace_refs=gate["trace_refs"])
        directives = directives_result["directives"]
        try:
            self._validate_patch_directives(
                directives=directives,
                allowed_files=gate["allowed_files"],
                allowed_symbols=gate["allowed_symbols"],
            )
        except ValueError as exc:
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=str(exc), trace_refs=gate["trace_refs"])
        patch_result = propose_patch(
            self.db_path,
            self.project_id,
            {
                "change_text": prompt.strip(),
                "session_uid": str(row["session_uid"] or ""),
                "requirement_id": requirement_id,
                "target_symbol": directives_result["target_symbols"][0] if directives_result["target_symbols"] else "",
                "target_file": directives_result["target_files"][0] if directives_result["target_files"] else "",
                "directives": directives,
                "dry_run": False,
            },
        )
        if not bool(patch_result.get("passed")):
            reason = "; ".join(str(item) for item in patch_result.get("limitations") or []) or str(patch_result.get("error") or "patch propose failed")
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=reason, trace_refs=gate["trace_refs"])
        artifact = patch_result.get("artifact") if isinstance(patch_result.get("artifact"), dict) else {}
        draft_uid = str(artifact.get("draft_uid") or "")
        if not draft_uid:
            raise ValueError("patch propose did not return candidate draft")
        validation_result = validate_patch(self.db_path, self.project_id, {"draft_uid": draft_uid})
        if not bool(validation_result.get("passed")):
            reason = "; ".join(str(item) for item in validation_result.get("limitations") or []) or "patch validation failed"
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=reason, trace_refs=gate["trace_refs"])
        self._attach_patch_candidate_metadata(
            draft_uid=draft_uid,
            task_uid=task_uid,
            requirement_id=requirement_id,
            directives_result=directives_result,
            trace_refs=gate["trace_refs"],
            validation_result=validation_result,
        )
        self.trace_service.write_patch_candidate_trace(task_uid=task_uid, requirement_id=requirement_id, draft_uid=draft_uid)
        return {
            "status": "ok",
            "task_uid": task_uid,
            "requirement_id": requirement_id,
            "candidate_only": True,
            "applied": False,
            "draft_uid": draft_uid,
            "trace_refs": gate["trace_refs"],
            "directives": directives,
            "change_summary": directives_result["change_summary"],
            "target_files": directives_result["target_files"],
            "target_symbols": directives_result["target_symbols"],
            "risk_notes": directives_result["risk_notes"],
            "validation_plan": directives_result["validation_plan"],
            "validation_result": validation_result,
        }

    def propose_repair_candidate(self, *, task_uid: str, prompt: str) -> dict[str, Any]:
        row = self._task_row(task_uid.strip())
        prompt = prompt.strip()
        if not prompt:
            return self._blocked_patch_response(task_uid=task_uid, requirement_id="", reason="repair prompt is required", trace_refs=[])
        latest_failure = self._latest_failed_validation(task_uid)
        if not latest_failure:
            return self._blocked_patch_response(task_uid=task_uid, requirement_id="", reason="no failed validation summary is available for repair", trace_refs=[])
        requirement_id = str(latest_failure.get("requirement_id") or self.trace_service.active_requirement_id_for_task(task_uid))
        parent_patch = self._latest_patch_candidate(task_uid=task_uid, requirement_id=requirement_id)
        if not parent_patch:
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason="no patch candidate draft is available for repair", trace_refs=latest_failure.get("trace_refs") or [])
        existing_repair = self._existing_repair_candidate(validation_invocation_uid=str(latest_failure.get("invocation_uid") or ""))
        if existing_repair:
            return self._blocked_patch_response(
                task_uid=task_uid,
                requirement_id=requirement_id,
                reason="repair candidate already exists for this validation failure",
                trace_refs=latest_failure.get("trace_refs") or [],
            )
        context = self._context_for_task(row)
        gate = self._patch_evidence_gate(
            task_uid=task_uid,
            requirement_id=requirement_id,
            context=context,
            failure_category=str(latest_failure.get("category") or ""),
            validation_kind=str(latest_failure.get("kind") or ""),
            modified_files=self._draft_target_files(parent_patch),
        )
        if not gate["passed"]:
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=gate["reason"], trace_refs=gate["trace_refs"])
        directives_result = self._generate_repair_directives(
            task_uid=task_uid,
            requirement_id=requirement_id,
            prompt=prompt,
            parent_patch=parent_patch,
            latest_failure=latest_failure,
            evidence_pack=gate["evidence_pack"],
        )
        directives = directives_result["directives"]
        try:
            self._validate_patch_directives(
                directives=directives,
                allowed_files=gate["allowed_files"],
                allowed_symbols=gate["allowed_symbols"],
            )
        except ValueError as exc:
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=str(exc), trace_refs=gate["trace_refs"])
        patch_result = propose_patch(
            self.db_path,
            self.project_id,
            {
                "change_text": prompt,
                "session_uid": str(row["session_uid"] or ""),
                "requirement_id": requirement_id,
                "target_symbol": directives_result["target_symbols"][0] if directives_result["target_symbols"] else "",
                "target_file": directives_result["target_files"][0] if directives_result["target_files"] else "",
                "directives": directives,
                "dry_run": False,
            },
        )
        if not bool(patch_result.get("passed")):
            reason = "; ".join(str(item) for item in patch_result.get("limitations") or []) or str(patch_result.get("error") or "repair patch propose failed")
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=reason, trace_refs=gate["trace_refs"])
        artifact = patch_result.get("artifact") if isinstance(patch_result.get("artifact"), dict) else {}
        draft_uid = str(artifact.get("draft_uid") or "")
        if not draft_uid:
            raise ValueError("repair patch propose did not return candidate draft")
        validation_result = validate_patch(self.db_path, self.project_id, {"draft_uid": draft_uid})
        if not bool(validation_result.get("passed")):
            reason = "; ".join(str(item) for item in validation_result.get("limitations") or []) or "repair patch validation failed"
            return self._blocked_patch_response(task_uid=task_uid, requirement_id=requirement_id, reason=reason, trace_refs=gate["trace_refs"])
        repair_attempt_index = 1
        self._attach_repair_candidate_metadata(
            draft_uid=draft_uid,
            task_uid=task_uid,
            requirement_id=requirement_id,
            parent_patch_draft_uid=str(parent_patch.get("draft_uid") or ""),
            latest_failure=latest_failure,
            directives_result=directives_result,
            trace_refs=gate["trace_refs"],
            validation_result=validation_result,
            repair_attempt_index=repair_attempt_index,
        )
        self.trace_service.write_patch_candidate_trace(task_uid=task_uid, requirement_id=requirement_id, draft_uid=draft_uid)
        return {
            "status": "ok",
            "task_uid": task_uid,
            "requirement_id": requirement_id,
            "candidate_only": True,
            "applied": False,
            "draft_uid": draft_uid,
            "trace_refs": gate["trace_refs"],
            "parent_patch_draft_uid": str(parent_patch.get("draft_uid") or ""),
            "repair_of_validation_uid": str(latest_failure.get("invocation_uid") or ""),
            "repair_attempt_index": repair_attempt_index,
            "failure_category": str(latest_failure.get("category") or ""),
            "directives": directives,
            "change_summary": directives_result["change_summary"],
            "target_files": directives_result["target_files"],
            "target_symbols": directives_result["target_symbols"],
            "risk_notes": directives_result["risk_notes"],
            "validation_plan": directives_result["validation_plan"],
            "validation_result": validation_result,
        }

    def list(self, *, session_uid: str = "", status: str = "") -> list[dict[str, Any]]:
        where = ["project_id=?", "task_type=?"]
        params: list[Any] = [self.project_id, DEV_TASK_TYPE]
        if session_uid.strip():
            where.append("session_uid=?")
            params.append(session_uid.strip())
        if status.strip():
            where.append("status=?")
            params.append(status.strip())
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM agent_tasks WHERE " + " AND ".join(where) + " ORDER BY id DESC",
                params,
            ).fetchall()
            display_metadata = self._task_display_metadata_for_rows(conn, rows)
        return [self._task_payload(row, display_metadata=display_metadata.get(str(row["task_uid"]))) for row in rows]

    def active_task_for_session(self, session_uid: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM agent_tasks
                WHERE project_id=? AND session_uid=? AND task_type=? AND status IN ('active', 'blocked')
                ORDER BY id DESC LIMIT 1
                """,
                (self.project_id, session_uid, DEV_TASK_TYPE),
            ).fetchone()
            display_metadata = self._task_display_metadata_for_rows(conn, [row]).get(str(row["task_uid"])) if row else None
        return self._task_payload(row, display_metadata=display_metadata) if row else None

    def record_validation(self, *, task_uid: str, kind: str, result: dict[str, Any]) -> dict[str, Any] | None:
        task_uid = task_uid.strip()
        if not task_uid:
            return None
        row = self._task_row(task_uid)
        plan = _loads_json(row["plan_json"], {})
        summary = dict(plan.get("validation_summary") or {})
        entry = validation_summary(kind=kind, result=result)
        entry["requirement_id"] = self.trace_service.active_requirement_id_for_task(task_uid)
        entry["trace_refs"] = [
            item["target_ref"]
            for item in self.trace_service.trace_for_task(task_uid).get("trace_links", [])
            if item.get("status") == "active" and item.get("requirement_id") == entry["requirement_id"]
        ]
        latest_candidate = self._latest_patch_candidate(task_uid=task_uid, requirement_id=entry["requirement_id"])
        entry["solved_failure_lessons"] = self._recall_solved_failure_lessons(
            requirement_id=entry["requirement_id"],
            failure_category=str(entry.get("category") or ""),
            validation_kind=kind,
            trace_refs=entry["trace_refs"],
            modified_files=self._draft_target_files(latest_candidate),
        )
        summary[kind] = entry
        plan["validation_summary"] = summary
        plan["last_action"] = {"type": "validation", "status": entry["status"], "kind": kind, "at": utc_now()}
        self._update_plan(task_uid, plan, status=str(row["status"] or "active"))
        self.trace_service.rebuild_for_task(task_uid)
        if entry["status"] == "passed":
            self._create_solved_failure_learning_candidate(task_uid=task_uid, validation_entry=entry)
        return self.get(task_uid)

    def _advance_one(self, task_uid: str, *, action_type: str) -> dict[str, Any]:
        row = self._task_row(task_uid)
        payload = self._task_payload(row)
        stages = payload["stages"]
        revision_stage = next((stage for stage in stages if stage["effective_status"] == "needs_revision"), None)
        if revision_stage:
            return self._block(row, f"{revision_stage['document_type']} needs revision", revision_stage, action_type)
        pending_stage = next((stage for stage in stages if stage["effective_status"] == "pending"), None)
        if pending_stage is None:
            return self._complete(row, action_type)
        stage_index = int(pending_stage["index"])
        blockers = [stage for stage in stages[:stage_index] if stage["effective_status"] != "done"]
        if blockers:
            return self._block(row, f"{pending_stage['document_type']} is waiting for predecessors", blockers[0], action_type)
        if pending_stage["document_type"] == "detailed_design" and not self._has_code_index():
            return self._block(row, "detailed_design requires an indexed codebase", pending_stage, action_type)

        stage_context = self._context_for_task(row)
        policy = self._policy_for_stage(stage_context, pending_stage["document_type"])
        if not bool((policy.get("policy") or {}).get("allowed", True)):
            reason = str((policy.get("policy") or {}).get("reason") or "policy_guard blocked generation")
            return self._block(row, reason, pending_stage, action_type, policy=policy)

        draft = propose_personal_artifact(
            self.db_path,
            workspace=self.workspace,
            project_id=self.project_id,
            prompt=str(row["user_prompt"] or ""),
            document_type=pending_stage["document_type"],
            source_uids=stage_context["active_source_uids"],
            session_uid=str(row["session_uid"] or ""),
            task_uid=str(row["task_uid"] or ""),
            make_active=True,
        )
        plan = _loads_json(row["plan_json"], {})
        quality = (draft.get("generation") or {}).get("quality") if isinstance(draft.get("generation"), dict) else {}
        failed_reason = ""
        if draft.get("status") == "quality_failed":
            failures = quality.get("blocking_failures") if isinstance(quality, dict) else []
            failed_reason = "; ".join(str(item) for item in (failures or [])) or "quality gate failed"
        plan["blocked_reason"] = failed_reason
        plan["last_action"] = {
            "type": action_type,
            "status": "quality_failed" if failed_reason else "generated",
            "stage": pending_stage["document_type"],
            "draft_uid": draft.get("draft_uid", ""),
            "reason": failed_reason,
            "at": utc_now(),
        }
        status = "blocked" if failed_reason else "active"
        self._update_plan(str(row["task_uid"]), plan, status=status, current_step=pending_stage["document_type"], error_message=failed_reason)
        self.trace_service.rebuild_for_task(str(row["task_uid"]))
        return self.get(str(row["task_uid"]))

    def _context_for_task(self, row: Any) -> dict[str, Any]:
        plan = _loads_json(row["plan_json"], {})
        source_uids = [str(uid).strip() for uid in (plan.get("source_uids") or []) if str(uid).strip()]
        kwargs: dict[str, Any] = {"session_uid": str(row["session_uid"] or ""), "prompt": str(row["user_prompt"] or "")}
        if source_uids:
            kwargs["source_uids"] = source_uids
        return self.context_builder.build(**kwargs)

    def _policy_for_stage(self, context: dict[str, Any], document_type: str) -> dict[str, Any]:
        route = {
            "intent": "generate_document",
            "confidence": 1.0,
            "target_document_type": document_type,
            "creates_draft": True,
            "writes_project_files": False,
            "router_source": "dev_task_orchestrator",
        }
        return apply_personal_policy(route, context)

    def _task_payload(self, row: Any, *, display_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = _loads_json(row["plan_json"], {})
        stages = self._effective_stages(str(row["task_uid"]))
        next_action = self._next_action(stages, str(plan.get("blocked_reason") or ""))
        trace = self.trace_service.trace_for_task(str(row["task_uid"]))
        metadata = display_metadata or {}
        return {
            "id": row["id"],
            "task_uid": row["task_uid"],
            "project_id": row["project_id"],
            "session_uid": row["session_uid"],
            "title": row["title"],
            "task_type": row["task_type"],
            "status": row["status"],
            "user_prompt": row["user_prompt"],
            "current_step": row["current_step"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "plan": plan,
            "stages": stages,
            "next_action": next_action,
            "blocked_reason": str(plan.get("blocked_reason") or row["error_message"] or ""),
            "last_action": plan.get("last_action") or {},
            "validation_summary": plan.get("validation_summary") or {},
            "requirements": trace["requirements"],
            "trace_summary": trace["trace_summary"],
            "display_code": metadata.get("display_code", ""),
            "session_display_index": metadata.get("session_display_index"),
            "display_scope": metadata.get("display_scope", ""),
        }

    def _effective_stages(self, task_uid: str) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM personal_drafts
                WHERE project_id=? AND task_uid=? AND status IN ('active', 'quality_failed')
                ORDER BY is_active DESC, id DESC
                """,
                (self.project_id, task_uid),
            ).fetchall()
        latest_by_type: dict[str, Any] = {}
        for row in rows:
            latest_by_type.setdefault(str(row["document_type"]), row)
        stages: list[dict[str, Any]] = []
        for index, document_type in enumerate(dev_task_stage_order()):
            draft = latest_by_type.get(document_type)
            if draft is None:
                status = "pending"
                draft_uid = ""
                lineage_stale = False
                draft_status = ""
            else:
                draft_uid = str(draft["draft_uid"])
                draft_status = str(draft["status"])
                lineage_stale = bool(draft["lineage_stale"])
                status = "needs_revision" if draft_status == "quality_failed" or lineage_stale else "done"
            stages.append(
                {
                    "index": index,
                    "document_type": document_type,
                    "effective_status": status,
                    "draft_uid": draft_uid,
                    "draft_status": draft_status,
                    "lineage_stale": lineage_stale,
                }
            )
        return stages

    def _next_action(self, stages: list[dict[str, Any]], blocked_reason: str) -> dict[str, Any]:
        for stage in stages:
            if stage["effective_status"] == "needs_revision":
                return {"action": "revise_draft", "stage": stage["document_type"], "reason": blocked_reason or "stage needs revision"}
        for stage in stages:
            if stage["effective_status"] == "pending":
                return {"action": "continue", "stage": stage["document_type"], "reason": blocked_reason}
        return {"action": "completed", "stage": "", "reason": ""}

    def _block(self, row: Any, reason: str, stage: dict[str, Any], action_type: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = _loads_json(row["plan_json"], {})
        plan["blocked_reason"] = reason
        plan["last_action"] = {
            "type": action_type,
            "status": "blocked",
            "stage": stage.get("document_type", ""),
            "reason": reason,
            "policy": policy or {},
            "at": utc_now(),
        }
        self._update_plan(str(row["task_uid"]), plan, status="blocked", current_step=str(stage.get("document_type") or ""), error_message=reason)
        return self.get(str(row["task_uid"]))

    def _complete(self, row: Any, action_type: str) -> dict[str, Any]:
        plan = _loads_json(row["plan_json"], {})
        plan["blocked_reason"] = ""
        plan["last_action"] = {"type": action_type, "status": "completed", "at": utc_now()}
        self._update_plan(str(row["task_uid"]), plan, status="completed", current_step="", error_message="", completed=True)
        return self.get(str(row["task_uid"]))

    def _update_plan(
        self,
        task_uid: str,
        plan: dict[str, Any],
        *,
        status: str,
        current_step: str | None = None,
        error_message: str | None = None,
        completed: bool = False,
    ) -> None:
        now = utc_now()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE agent_tasks
                SET plan_json=?, status=?, current_step=COALESCE(?, current_step),
                    error_message=COALESCE(?, error_message), updated_at=?,
                    completed_at=CASE WHEN ? THEN ? ELSE completed_at END
                WHERE project_id=? AND task_uid=?
                """,
                (
                    json_dumps(plan),
                    status,
                    current_step,
                    error_message,
                    now,
                    1 if completed else 0,
                    now,
                    self.project_id,
                    task_uid,
                ),
            )

    def _task_row(self, task_uid: str) -> Any:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM agent_tasks WHERE project_id=? AND task_uid=? AND task_type=?",
                (self.project_id, task_uid, DEV_TASK_TYPE),
            ).fetchone()
        if row is None:
            raise ValueError("dev task not found")
        return row

    def _task_display_metadata_for_rows(self, conn: Any, rows: list[Any]) -> dict[str, dict[str, Any]]:
        metadata_by_uid: dict[str, dict[str, Any]] = {}
        if not rows:
            return metadata_by_uid
        task_uids = {str(row["task_uid"] or "").strip() for row in rows if str(row["task_uid"] or "").strip()}
        if not task_uids:
            return metadata_by_uid
        session_uids = {str(row["session_uid"] or "").strip() for row in rows if str(row["session_uid"] or "").strip()}
        for session_uid in session_uids:
            indexes = self._task_display_indexes(conn, session_uid=session_uid)
            for task_uid, index in indexes.items():
                if task_uid not in task_uids:
                    continue
                metadata_by_uid[task_uid] = {
                    "display_code": f"T{index}" if index else "",
                    "session_display_index": index,
                    "display_scope": "session",
                }
        if any(not str(row["session_uid"] or "").strip() for row in rows):
            fallback_indexes = self._task_display_indexes(conn, session_uid="")
            for task_uid, index in fallback_indexes.items():
                if task_uid not in task_uids or task_uid in metadata_by_uid:
                    continue
                metadata_by_uid[task_uid] = {
                    "display_code": f"T{index}" if index else "",
                    "session_display_index": index,
                    "display_scope": "project_fallback",
                }
        return metadata_by_uid

    def _task_display_indexes(self, conn: Any, *, session_uid: str) -> dict[str, int]:
        if session_uid.strip():
            rows = conn.execute(
                """
                SELECT task_uid
                FROM agent_tasks
                WHERE project_id=? AND session_uid=? AND task_type=?
                ORDER BY id ASC
                """,
                (self.project_id, session_uid.strip(), DEV_TASK_TYPE),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT task_uid
                FROM agent_tasks
                WHERE project_id=? AND task_type=?
                ORDER BY id ASC
                """,
                (self.project_id, DEV_TASK_TYPE),
            ).fetchall()
        return {str(item["task_uid"] or "").strip(): index + 1 for index, item in enumerate(rows) if str(item["task_uid"] or "").strip()}

    def _require_session(self, session_uid: str) -> None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM personal_sessions WHERE session_uid=? AND status='active'",
                (session_uid,),
            ).fetchone()
        if row is None:
            raise ValueError("session not found")

    def _has_code_index(self) -> bool:
        repo = latest_repository(self.db_path, self.project_id)
        if not repo:
            return False
        with connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM code_files WHERE repository_id=?", (repo["id"],)).fetchone()[0]
        return int(count or 0) > 0

    def _patch_evidence_gate(
        self,
        *,
        task_uid: str,
        requirement_id: str,
        context: dict[str, Any],
        failure_category: str = "",
        validation_kind: str = "",
        modified_files: list[str] | None = None,
    ) -> dict[str, Any]:
        if not requirement_id:
            return {"passed": False, "reason": "active dev task has no active canonical requirement_id", "trace_refs": [], "evidence_pack": {}, "allowed_files": set(), "allowed_symbols": set()}
        if not self._has_code_index():
            return {"passed": False, "reason": "active dev task requires indexed codebase before code patch propose", "trace_refs": [], "evidence_pack": {}, "allowed_files": set(), "allowed_symbols": set()}

        trace = self.trace_service.trace_for_task(task_uid)
        active_links = [item for item in trace["trace_links"] if item["status"] == "active" and item["requirement_id"] == requirement_id]
        code_file_refs = [item for item in active_links if item["link_type"] == "requirement_to_code_file"]
        code_symbol_refs = [item for item in active_links if item["link_type"] == "requirement_to_code_symbol"]
        if not code_file_refs and not code_symbol_refs:
            return {
                "passed": False,
                "reason": "active canonical requirement_id must have trace-linked code file or code symbol evidence",
                "trace_refs": [item["target_ref"] for item in active_links],
                "evidence_pack": {},
                "allowed_files": set(),
                "allowed_symbols": set(),
            }

        detailed_design = next((item for item in context.get("upstream_drafts") or [] if item.get("document_type") == "detailed_design"), None)
        impact = analyze_codebase_impact(self.db_path, self.project_id, str(context.get("prompt") or ""), limit=8)
        if not detailed_design and not impact.get("affected_files"):
            return {
                "passed": False,
                "reason": "detailed_design or explicit code impact evidence is required before code patch propose",
                "trace_refs": [item["target_ref"] for item in active_links],
                "evidence_pack": {},
                "allowed_files": set(),
                "allowed_symbols": set(),
            }
        allowed_files = {item["target_ref"][10:] for item in code_file_refs if str(item.get("target_ref", "")).startswith("code_file:")}
        allowed_symbols = {item["target_ref"][12:] for item in code_symbol_refs if str(item.get("target_ref", "")).startswith("code_symbol:")}
        trace_refs = [item["target_ref"] for item in active_links]
        return {
            "passed": True,
            "reason": "",
            "trace_refs": trace_refs,
            "allowed_files": allowed_files,
            "allowed_symbols": allowed_symbols,
            "evidence_pack": {
                "task_uid": task_uid,
                "requirement_id": requirement_id,
                "prompt": str(context.get("prompt") or ""),
                "trace_refs": trace_refs,
                "allowed_files": sorted(allowed_files),
                "allowed_symbols": sorted(allowed_symbols),
                "impact": impact,
                "detailed_design": detailed_design or {},
                "solved_failure_lessons": self._recall_solved_failure_lessons(
                    requirement_id=requirement_id,
                    failure_category=failure_category,
                    validation_kind=validation_kind,
                    trace_refs=trace_refs,
                    modified_files=modified_files or sorted(allowed_files),
                ),
            },
        }

    def _generate_patch_directives(self, *, task_uid: str, requirement_id: str, prompt: str, evidence_pack: dict[str, Any]) -> dict[str, Any]:
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        try:
            result = gateway_class(self.db_path).complete_json(
                purpose="personal_dev_task_patch_directives",
                system_prompt="\n".join(
                    [
                        "You are the PersonalAgent LLM directives patch propose runtime.",
                        "Return strict JSON only.",
                        "Do not emit patch text. Emit directives only.",
                        "Each directive must target one allowed evidence-backed file.",
                        "Each directive find snippet must match exactly one location in the target file.",
                        "Do not rewrite entire files.",
                    ]
                ),
                user_prompt=json_dumps(
                    {
                        "task_uid": task_uid,
                        "requirement_id": requirement_id,
                        "prompt": prompt.strip(),
                        "required_json_schema": {
                            "change_summary": "string",
                            "target_files": ["string"],
                            "target_symbols": ["string"],
                            "directives": [
                                {
                                    "file_path": "string",
                                    "find": "string",
                                    "replace": "string",
                                    "description": "string",
                                }
                            ],
                            "risk_notes": ["string"],
                            "validation_plan": ["string"],
                        },
                        "evidence_pack": evidence_pack,
                    }
                ),
                project_id=self.project_id,
                task_uid=task_uid,
            )
        except getattr(llm_gateway_module, "PersonalLLM" + "Error") as exc:
            raise ValueError(f"LLM directives patch propose failed: {exc}") from exc
        parsed = result.parsed if isinstance(result.parsed, dict) else {}
        directives = parsed.get("directives") if isinstance(parsed.get("directives"), list) else []
        if not directives:
            raise ValueError("LLM directives patch propose returned no directives")
        return {
            "change_summary": str(parsed.get("change_summary") or "").strip(),
            "target_files": [str(item).strip() for item in (parsed.get("target_files") or []) if str(item).strip()],
            "target_symbols": [str(item).strip() for item in (parsed.get("target_symbols") or []) if str(item).strip()],
            "directives": [
                {
                    "file_path": str(item.get("file_path") or "").strip(),
                    "find": str(item.get("find") or ""),
                    "replace": str(item.get("replace") or ""),
                    "description": str(item.get("description") or "").strip(),
                }
                for item in directives
                if isinstance(item, dict)
            ],
            "risk_notes": [str(item).strip() for item in (parsed.get("risk_notes") or []) if str(item).strip()],
            "validation_plan": [str(item).strip() for item in (parsed.get("validation_plan") or []) if str(item).strip()],
        }

    def _generate_repair_directives(
        self,
        *,
        task_uid: str,
        requirement_id: str,
        prompt: str,
        parent_patch: dict[str, Any],
        latest_failure: dict[str, Any],
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        gateway_class = getattr(llm_gateway_module, "PersonalLLMGateway")
        try:
            result = gateway_class(self.db_path).complete_json(
                purpose="personal_dev_task_patch_repair_directives",
                system_prompt="\n".join(
                    [
                        "You are the PersonalAgent validation failure repair candidate runtime.",
                        "Return strict JSON only.",
                        "Do not emit patch text. Emit directives only.",
                        "Base failure attribution only on command kind, returncode, timeout/config category, and provided evidence.",
                        "Do not infer root cause from stdout or stderr patterns.",
                        "Generate one repair candidate only.",
                    ]
                ),
                user_prompt=json_dumps(
                    {
                        "task_uid": task_uid,
                        "requirement_id": requirement_id,
                        "prompt": prompt,
                        "required_json_schema": {
                            "change_summary": "string",
                            "target_files": ["string"],
                            "target_symbols": ["string"],
                            "directives": [
                                {
                                    "file_path": "string",
                                    "find": "string",
                                    "replace": "string",
                                    "description": "string",
                                }
                            ],
                            "risk_notes": ["string"],
                            "validation_plan": ["string"],
                        },
                        "parent_patch_candidate": parent_patch,
                        "validation_failure": latest_failure,
                        "evidence_pack": evidence_pack,
                    }
                ),
                project_id=self.project_id,
                task_uid=task_uid,
            )
        except getattr(llm_gateway_module, "PersonalLLM" + "Error") as exc:
            raise ValueError(f"LLM repair directives failed: {exc}") from exc
        parsed = result.parsed if isinstance(result.parsed, dict) else {}
        directives = parsed.get("directives") if isinstance(parsed.get("directives"), list) else []
        if not directives:
            raise ValueError("LLM repair directives returned no directives")
        return {
            "change_summary": str(parsed.get("change_summary") or "").strip(),
            "target_files": [str(item).strip() for item in (parsed.get("target_files") or []) if str(item).strip()],
            "target_symbols": [str(item).strip() for item in (parsed.get("target_symbols") or []) if str(item).strip()],
            "directives": [
                {
                    "file_path": str(item.get("file_path") or "").strip(),
                    "find": str(item.get("find") or ""),
                    "replace": str(item.get("replace") or ""),
                    "description": str(item.get("description") or "").strip(),
                }
                for item in directives
                if isinstance(item, dict)
            ],
            "risk_notes": [str(item).strip() for item in (parsed.get("risk_notes") or []) if str(item).strip()],
            "validation_plan": [str(item).strip() for item in (parsed.get("validation_plan") or []) if str(item).strip()],
        }

    def _validate_patch_directives(self, *, directives: list[dict[str, Any]], allowed_files: set[str], allowed_symbols: set[str]) -> None:
        repo = latest_repository(self.db_path, self.project_id)
        if not repo:
            raise ValueError("code repository is not indexed and code_repo_path is not configured")
        root = Path(str(repo["root_path"])).resolve()
        if not directives:
            raise ValueError("LLM directives patch propose returned no directives")
        for item in directives:
            file_path = str(item.get("file_path") or "").replace("\\", "/").strip()
            find_text = str(item.get("find") or "")
            replace_text = str(item.get("replace") or "")
            if not file_path or not find_text:
                raise ValueError("patch directives require non-empty file_path and find")
            if allowed_files and file_path not in allowed_files:
                raise ValueError(f"patch directive target is outside evidence pack: {file_path}")
            target = (root / file_path).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"patch directive target escapes code repository: {file_path}")
            if not target.exists() or not target.is_file():
                raise ValueError(f"patch directive target file does not exist: {file_path}")
            original = target.read_text(encoding="utf-8", errors="replace")
            occurrences = original.count(find_text)
            if occurrences != 1:
                raise ValueError(f"patch directive find must match exactly once in {file_path}")
            if replace_text == original:
                raise ValueError(f"patch directive replace attempts to rewrite entire file: {file_path}")
        for symbol_ref in allowed_symbols:
            file_path, _, symbol_name = symbol_ref.partition("#")
            if not file_path or not symbol_name:
                continue
            lookup = symbol_lookup(self.db_path, self.project_id, symbol_name, limit=20)
            matches = lookup.get("matches", []) if lookup.get("passed") else []
            matching_entries = [
                item for item in matches if str(item.get("file_path") or "") == file_path and str(item.get("name") or "") == symbol_name
            ]
            unique_spans = {
                (str(item.get("file_path") or ""), str(item.get("name") or ""), int(item.get("start_line") or 0), int(item.get("end_line") or 0))
                for item in matching_entries
            }
            if len(unique_spans) != 1:
                raise ValueError(f"trace-linked code symbol is not uniquely indexed: {symbol_ref}")

    def _attach_patch_candidate_metadata(
        self,
        *,
        draft_uid: str,
        task_uid: str,
        requirement_id: str,
        directives_result: dict[str, Any],
        trace_refs: list[str],
        validation_result: dict[str, Any],
    ) -> None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM personal_drafts WHERE project_id=? AND draft_uid=? AND document_type='c_code_diff'",
                (self.project_id, draft_uid),
            ).fetchone()
            if row is None:
                raise ValueError("candidate patch draft was not found")
            metadata = _loads_json(row["metadata_json"], {})
            generation = metadata.get("generation") if isinstance(metadata.get("generation"), dict) else {}
            generation["task_uid"] = task_uid
            generation["requirement_id"] = requirement_id
            generation["candidate_only"] = True
            generation["applied"] = False
            generation["llm_directives_patch_propose"] = {
                "change_summary": directives_result["change_summary"],
                "target_files": directives_result["target_files"],
                "target_symbols": directives_result["target_symbols"],
                "directives": directives_result["directives"],
                "risk_notes": directives_result["risk_notes"],
                "validation_plan": directives_result["validation_plan"],
                "validation_result": validation_result,
                "trace_refs": trace_refs,
            }
            metadata["generation"] = generation
            conn.execute(
                "UPDATE personal_drafts SET task_uid=?, metadata_json=?, updated_at=? WHERE project_id=? AND draft_uid=?",
                (task_uid, json_dumps(metadata), utc_now(), self.project_id, draft_uid),
            )

    def _attach_repair_candidate_metadata(
        self,
        *,
        draft_uid: str,
        task_uid: str,
        requirement_id: str,
        parent_patch_draft_uid: str,
        latest_failure: dict[str, Any],
        directives_result: dict[str, Any],
        trace_refs: list[str],
        validation_result: dict[str, Any],
        repair_attempt_index: int,
    ) -> None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM personal_drafts WHERE project_id=? AND draft_uid=? AND document_type='c_code_diff'",
                (self.project_id, draft_uid),
            ).fetchone()
            if row is None:
                raise ValueError("repair candidate draft was not found")
            metadata = _loads_json(row["metadata_json"], {})
            generation = metadata.get("generation") if isinstance(metadata.get("generation"), dict) else {}
            generation["task_uid"] = task_uid
            generation["requirement_id"] = requirement_id
            generation["candidate_only"] = True
            generation["applied"] = False
            generation["repair_candidate"] = {
                "parent_patch_draft_uid": parent_patch_draft_uid,
                "repair_of_validation_uid": str(latest_failure.get("invocation_uid") or ""),
                "repair_attempt_index": repair_attempt_index,
                "failure_category": str(latest_failure.get("category") or ""),
                "change_summary": directives_result["change_summary"],
                "target_files": directives_result["target_files"],
                "target_symbols": directives_result["target_symbols"],
                "directives": directives_result["directives"],
                "risk_notes": directives_result["risk_notes"],
                "validation_plan": directives_result["validation_plan"],
                "validation_result": validation_result,
                "trace_refs": trace_refs,
            }
            metadata["generation"] = generation
            conn.execute(
                "UPDATE personal_drafts SET task_uid=?, metadata_json=?, updated_at=? WHERE project_id=? AND draft_uid=?",
                (task_uid, json_dumps(metadata), utc_now(), self.project_id, draft_uid),
            )

    def _latest_failed_validation(self, task_uid: str) -> dict[str, Any]:
        row = self._task_row(task_uid)
        plan = _loads_json(row["plan_json"], {})
        summary = plan.get("validation_summary") if isinstance(plan.get("validation_summary"), dict) else {}
        failures = [item for item in summary.values() if isinstance(item, dict) and str(item.get("status") or "") == "failed"]
        if not failures:
            return {}
        failures.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
        return failures[0]

    def _latest_patch_candidate(self, *, task_uid: str, requirement_id: str) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT d.*, r.content AS current_content
                FROM personal_drafts d
                JOIN personal_draft_revisions r
                  ON r.draft_uid=d.draft_uid AND r.revision_index=d.current_revision
                WHERE d.project_id=? AND d.task_uid=? AND d.document_type='c_code_diff' AND d.status IN ('active', 'quality_failed')
                ORDER BY d.id DESC
                """,
                (self.project_id, task_uid),
            ).fetchall()
        for row in rows:
            metadata = _loads_json(row["metadata_json"], {})
            generation = metadata.get("generation") if isinstance(metadata.get("generation"), dict) else {}
            if str(generation.get("requirement_id") or "") != requirement_id:
                continue
            if generation.get("repair_candidate"):
                continue
            return {
                "draft_uid": str(row["draft_uid"] or ""),
                "content": str(row["current_content"] or ""),
                "metadata": metadata,
            }
        return {}

    def _existing_repair_candidate(self, *, validation_invocation_uid: str) -> dict[str, Any]:
        if not validation_invocation_uid:
            return {}
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT draft_uid, metadata_json
                FROM personal_drafts
                WHERE project_id=? AND document_type='c_code_diff' AND status IN ('active', 'quality_failed')
                ORDER BY id DESC
                """,
                (self.project_id,),
            ).fetchall()
        for row in rows:
            metadata = _loads_json(row["metadata_json"], {})
            generation = metadata.get("generation") if isinstance(metadata.get("generation"), dict) else {}
            repair = generation.get("repair_candidate") if isinstance(generation.get("repair_candidate"), dict) else {}
            if str(repair.get("repair_of_validation_uid") or "") == validation_invocation_uid:
                return {"draft_uid": str(row["draft_uid"] or ""), "metadata": metadata}
        return {}

    def _blocked_patch_response(self, *, task_uid: str, requirement_id: str, reason: str, trace_refs: list[str]) -> dict[str, Any]:
        return {
            "status": "blocked",
            "task_uid": task_uid,
            "requirement_id": requirement_id,
            "candidate_only": True,
            "applied": False,
            "blocked_reason": reason,
            "trace_refs": trace_refs,
        }

    def _recall_solved_failure_lessons(
        self,
        *,
        requirement_id: str,
        failure_category: str,
        validation_kind: str,
        trace_refs: list[str],
        modified_files: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        files = [str(ref)[10:] for ref in trace_refs if str(ref).startswith("code_file:")]
        symbols = [str(ref)[12:] for ref in trace_refs if str(ref).startswith("code_symbol:")]
        query_parts = [requirement_id, failure_category, validation_kind, *(modified_files or []), *files, *symbols]
        query = " ".join(part for part in query_parts if str(part).strip()).strip()
        if not query:
            return []
        recalled = recall_knowledge(
            self.db_path,
            project_id=self.project_id,
            query=query,
            limit=3,
            category="memory_lesson",
        )
        return [
            {
                "item_uid": str(item.get("item_uid") or ""),
                "title": str(item.get("title") or ""),
                "content": str(item.get("content") or "")[:400],
            }
            for item in recalled[:3]
        ]

    def _create_solved_failure_learning_candidate(self, *, task_uid: str, validation_entry: dict[str, Any]) -> dict[str, Any] | None:
        validation_invocation_uid = str(validation_entry.get("invocation_uid") or "")
        if not validation_invocation_uid:
            return None
        repair = self._existing_repair_candidate(validation_invocation_uid=validation_invocation_uid)
        if not repair:
            return None
        if self._existing_learning_candidate(validation_invocation_uid=validation_invocation_uid):
            return None
        requirement_id = str(validation_entry.get("requirement_id") or "")
        trace_refs = list(validation_entry.get("trace_refs") or [])
        failure_category = self._repair_failure_category(repair) or str(validation_entry.get("category") or "")
        validation_kind = str(validation_entry.get("kind") or "")
        modified_files = self._draft_target_files(repair)
        code_symbols = [str(ref)[12:] for ref in trace_refs if str(ref).startswith("code_symbol:")]
        trigger_context = {
            "task_uid": task_uid,
            "requirement_id": requirement_id,
            "validation_kind": validation_kind,
            "validation_invocation_uid": validation_invocation_uid,
            "modified_files": modified_files,
            "code_symbols": code_symbols,
        }
        evidence_refs = {
            "task_uid": task_uid,
            "requirement_id": requirement_id,
            "validation_invocation_uid": validation_invocation_uid,
            "repair_patch_draft_uid": str(repair.get("draft_uid") or ""),
            "trace_refs": trace_refs,
            "failure_mode": failure_category or "solved_failure",
            "failure_signature": validation_invocation_uid,
            "failure_category": failure_category or "solved_failure",
            "trigger_context": trigger_context,
            "modified_files": modified_files,
            "code_symbols": code_symbols,
            "validation_kind": validation_kind,
            "solved_failure_learning": True,
            "immediate_session": False,
        }
        problem_lines = [
            f"failure_signature: {validation_invocation_uid}",
            f"failure_category: {failure_category or 'solved_failure'}",
            f"trigger_context: {json_dumps(trigger_context)}",
            "wrong_behavior: the prior patch candidate introduced or preserved behavior that failed validation under this requirement.",
        ]
        lesson_lines = [
            "corrected_behavior: the repair candidate aligned the changed files and symbols with the requirement evidence, and the next validation passed.",
            f"applicability: requirement_id={requirement_id}; validation_kind={validation_kind}; modified_files={', '.join(modified_files) if modified_files else 'unknown'}; code_symbols={', '.join(code_symbols) if code_symbols else 'unknown'}",
            "counterexamples: do not reuse this lesson for unrelated requirements, different failure categories, or environment-only failures such as config and timeout.",
            f"evidence_refs: {json_dumps(evidence_refs)}",
        ]
        payload = SimpleNamespace(
            project_id=self.project_id,
            title=f"Solved failure lesson {validation_invocation_uid} {failure_category or validation_kind}".strip(),
            problem="\n".join(problem_lines),
            lesson="\n".join(lesson_lines),
            evidence_refs=evidence_refs,
            source_decision_uid="",
            lesson_type="code_lesson",
            expected_behavior="Prefer the repaired behavior when the same requirement, failure category, modified files, and code symbols recur.",
            anti_behavior="Do not reuse the previously failing patch strategy when the same failure signature or requirement evidence recurs.",
            validation_query=" ".join(
                part
                for part in [requirement_id, failure_category, validation_kind, validation_invocation_uid, *modified_files, *code_symbols]
                if part
            ),
            scope="project",
            failure_type=failure_category or "solved_failure",
            applicability={
                "requirement_id": requirement_id,
                "failure_signature": validation_invocation_uid,
                "failure_category": failure_category or "solved_failure",
                "trigger_context": trigger_context,
                "validation_kind": validation_kind,
                "modified_files": modified_files,
                "code_symbols": code_symbols,
            },
            counterexamples=[
                "Do not use this lesson when failure category is config or timeout.",
                "Do not use this lesson when the modified files or symbols differ from the traced requirement evidence.",
            ],
            regression_case_uid="",
            expires_at="",
            superseded_by="",
        )
        return create_memory_candidate(self.db_path, payload)

    def _existing_learning_candidate(self, *, validation_invocation_uid: str) -> dict[str, Any]:
        if not validation_invocation_uid:
            return {}
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_candidates WHERE project_id=? AND status='candidate' ORDER BY id DESC",
                (self.project_id,),
            ).fetchall()
        for row in rows:
            evidence = _loads_json(row["evidence_refs_json"], {})
            if str(evidence.get("validation_invocation_uid") or "") == validation_invocation_uid:
                return dict(row)
        return {}

    def _draft_target_files(self, draft: dict[str, Any]) -> list[str]:
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        generation = metadata.get("generation") if isinstance(metadata.get("generation"), dict) else {}
        repair = generation.get("repair_candidate") if isinstance(generation.get("repair_candidate"), dict) else {}
        patch = generation.get("llm_directives_patch_propose") if isinstance(generation.get("llm_directives_patch_propose"), dict) else {}
        raw = repair.get("target_files") if isinstance(repair.get("target_files"), list) else patch.get("target_files")
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    def _repair_failure_category(self, draft: dict[str, Any]) -> str:
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        generation = metadata.get("generation") if isinstance(metadata.get("generation"), dict) else {}
        repair = generation.get("repair_candidate") if isinstance(generation.get("repair_candidate"), dict) else {}
        return str(repair.get("failure_category") or "").strip()


def is_continue_prompt(prompt: str) -> bool:
    return "".join(prompt.strip().split()) in CONTINUE_PROMPTS


def validation_summary(*, kind: str, result: dict[str, Any]) -> dict[str, Any]:
    output = result.get("output") if isinstance(result.get("output"), dict) else {}
    category = "unknown"
    status = str(result.get("status") or "")
    returncode = output.get("returncode")
    limitations = [str(item).lower() for item in (output.get("limitations") or [])]
    error = str(result.get("error") or "").lower()
    command_kind = str(output.get("command_kind") or _kind_to_command(kind))
    if any("timed out" in item for item in limitations) or returncode == -1:
        category = "timeout"
    elif status == "rejected" or "not configured" in error or "allowlist" in error or returncode is None:
        category = "config"
    elif command_kind in {"run_build", "run_static_analysis"} and int(returncode or 0) != 0:
        category = "code_logic"
    elif command_kind == "run_tests" and int(returncode or 0) != 0:
        category = "test_expectation"
    elif bool(output.get("passed")):
        category = "passed"
    return {
        "kind": kind,
        "command_kind": command_kind,
        "status": "passed" if bool(output.get("passed")) else "failed",
        "category": category,
        "returncode": returncode,
        "timeout": category == "timeout",
        "configured": category != "config",
        "invocation_uid": result.get("invocation_uid", ""),
        "recorded_at": utc_now(),
    }


def _kind_to_command(kind: str) -> str:
    return {"build": "run_build", "tests": "run_tests", "static-analysis": "run_static_analysis"}.get(kind, "")


def _task_title(prompt: str) -> str:
    return (prompt.strip().replace("\n", " ")[:40] or "Dev task pipeline")


def _loads_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default
