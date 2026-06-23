from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_agent.core.codebase.index_store import latest_repository
from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps, utc_now

from .artifact_drafts import DOCUMENT_LINEAGE_ORDER
from .artifact_generation import propose_personal_artifact
from .context_builder import PersonalContextBuilder
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
        row = self._task_row(task_uid.strip())
        return self._task_payload(row)

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
        return [self._task_payload(row) for row in rows]

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
        return self._task_payload(row) if row else None

    def record_validation(self, *, task_uid: str, kind: str, result: dict[str, Any]) -> dict[str, Any] | None:
        task_uid = task_uid.strip()
        if not task_uid:
            return None
        row = self._task_row(task_uid)
        plan = _loads_json(row["plan_json"], {})
        summary = dict(plan.get("validation_summary") or {})
        entry = validation_summary(kind=kind, result=result)
        summary[kind] = entry
        plan["validation_summary"] = summary
        plan["last_action"] = {"type": "validation", "status": entry["status"], "kind": kind, "at": utc_now()}
        self._update_plan(task_uid, plan, status=str(row["status"] or "active"))
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

    def _task_payload(self, row: Any) -> dict[str, Any]:
        plan = _loads_json(row["plan_json"], {})
        stages = self._effective_stages(str(row["task_uid"]))
        next_action = self._next_action(stages, str(plan.get("blocked_reason") or ""))
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
