from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from personal_agent.core.codebase.index_store import latest_repository
from personal_agent.core.database import connect
from personal_agent.core.llm_admin import read_personal_llm_admin_config, save_personal_llm_admin_config
from personal_agent.core.schemas import LlmAdminConfigRequest
from personal_agent.core.tool_registry import TypedToolExecutor
from personal_agent.core.utils import utc_now

from .artifact_drafts import (
    activate_artifact_draft,
    create_artifact_draft,
    get_artifact_content,
    get_artifact_draft,
    list_artifact_drafts,
    revise_artifact_draft_manual,
)
from .artifact_generation import (
    propose_personal_artifact,
    propose_personal_code_patch,
    propose_personal_unit_test_code,
    revise_personal_artifact,
)
from .artifact_export import export_personal_artifact, open_personal_artifact, resolve_personal_artifact_download
from .bootstrap import PersonalAgentContext
from .dev_tasks import DevTaskOrchestrator
from .runtime import PersonalRuntime, PersonalRuntimeError
from .input_documents import (
    activate_input_source,
    create_input_source,
    delete_input_source,
    get_input_source,
    list_input_sources,
    parse_text_source,
    parse_uploaded_source,
)
from .knowledge_learning import (
    approve_personal_candidate,
    dismiss_personal_memory_lesson,
    deprecate_personal_knowledge,
    import_source_to_knowledge,
    list_personal_knowledge,
    personal_inbox,
    personal_learning_candidates,
    personal_learning_summary,
    record_personal_feedback,
    reject_personal_candidate,
    search_personal_knowledge,
)
from .knowledge_recall import consolidate_memory_lessons
from .skill_registry import (
    evaluate_personal_skill,
    get_personal_skill,
    list_personal_skill_versions,
    list_personal_skills,
)
from .skill_update_candidates import (
    approve_skill_update_candidate,
    list_skill_update_candidates,
    reject_skill_update_candidate,
)


class PersonalSessionRenameRequest(BaseModel):
    title: str


class PersonalChatTurnRequest(BaseModel):
    content: str
    session_uid: str = ""
    task_uid: str = ""
    source_uids: list[str] = Field(default_factory=list)


class PersonalCodebaseConfigRequest(BaseModel):
    repo_path: str
    build_command: str = ""
    test_command: str = ""
    static_analysis_command: str = ""
    tool_timeout_s: int = 120


class PersonalCodebaseIndexRequest(BaseModel):
    repo_path: str = ""
    query: str = ""
    max_files: int = 320
    batch_size: int = 0
    skip_dirs: list[str] = []


class PersonalCodebaseSearchRequest(BaseModel):
    query: str
    limit: int = 8


class PersonalSymbolLookupRequest(BaseModel):
    name: str
    kind: str = ""
    limit: int = 20


class PersonalIncludeImpactRequest(BaseModel):
    path: str


class PersonalCallGraphRequest(BaseModel):
    function_name: str
    limit: int = 20


class PersonalMacroImpactRequest(BaseModel):
    macro_name: str
    limit: int = 20


class PersonalTypeUsageRequest(BaseModel):
    type_name: str
    limit: int = 20


class PersonalVariableUsageRequest(BaseModel):
    variable_name: str
    limit: int = 20


class PersonalImpactRequest(BaseModel):
    change_hint: str
    limit: int = 8


class PersonalPatchDirective(BaseModel):
    file_path: str
    find: str
    replace: str
    description: str = ""


class PersonalPatchProposeRequest(BaseModel):
    change_text: str
    session_uid: str = ""
    target_symbol: str = ""
    target_file: str = ""
    directives: list[PersonalPatchDirective] = []
    dry_run: bool = False


class PersonalPatchTextRequest(BaseModel):
    patch_text: str = ""
    draft_uid: str = ""
    artifact_id: int | None = None


class PersonalPatchApplyRequest(PersonalPatchTextRequest):
    dry_run: bool = True
    confirmed: bool = False
    reviewer: str = "local_user"
    comment: str = ""


class PersonalValidationRunRequest(BaseModel):
    command: str = ""
    timeout_s: int = 120
    confirmed: bool = False
    task_uid: str = ""
    session_uid: str = ""


class PersonalSourceTextRequest(BaseModel):
    title: str = ""
    content: str
    make_active: bool = True


class PersonalArtifactDraftCreateRequest(BaseModel):
    document_type: str = ""
    session_uid: str = ""
    task_uid: str = ""
    title: str
    content: str
    content_format: str = "markdown"
    source_uid: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    make_active: bool = True


class PersonalArtifactDraftReviseRequest(BaseModel):
    session_uid: str = ""
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    make_active: bool = True


class PersonalArtifactProposeRequest(BaseModel):
    prompt: str
    session_uid: str = ""
    task_uid: str = ""
    document_type: str = ""
    source_uids: list[str] = Field(default_factory=list)
    make_active: bool = True


class PersonalArtifactReviseRequest(BaseModel):
    session_uid: str = ""
    feedback: str
    make_active: bool = True


class PersonalArtifactCodePatchRequest(BaseModel):
    prompt: str
    session_uid: str = ""
    task_uid: str = ""
    target_symbol: str = ""
    target_file: str = ""
    directives: list[PersonalPatchDirective] = []
    make_active: bool = True


class PersonalArtifactUnitTestRequest(BaseModel):
    prompt: str
    session_uid: str = ""
    task_uid: str = ""
    source_uids: list[str] = Field(default_factory=list)
    make_active: bool = True


class PersonalDevTaskStartRequest(BaseModel):
    session_uid: str
    prompt: str
    source_uids: list[str] = Field(default_factory=list)


class PersonalDevTaskContinueRequest(BaseModel):
    task_uid: str


class PersonalArtifactExportRequest(BaseModel):
    format: str = ""
    revision_index: int | None = None


class PersonalKnowledgeImportSourceRequest(BaseModel):
    source_uid: str


class PersonalKnowledgeSearchRequest(BaseModel):
    query: str
    limit: int = 8


class PersonalKnowledgeDeprecateRequest(BaseModel):
    reviewer: str = "local_user"
    comment: str = ""


class PersonalLearningFeedbackRequest(BaseModel):
    feedback: str
    session_uid: str = ""
    corrected_behavior: str = ""
    scope: str = "project"
    add_to_regression: bool = False


class PersonalLearningReviewRequest(BaseModel):
    reviewer: str = "local_user"
    comment: str = ""


class PersonalSkillUpdateCandidateReviewRequest(BaseModel):
    reviewer: str = "local_user"
    comment: str = ""


def register_personal_agent_routes(
    app: FastAPI,
    *,
    context: PersonalAgentContext,
    runtime: PersonalRuntime,
) -> None:
    dev_tasks = DevTaskOrchestrator(context.db_path, workspace=context.workspace, project_id=context.project_id)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "mode": "personal_agent", **context.to_dict()}

    @app.get("/api/personal/context")
    def personal_context() -> dict[str, Any]:
        return context.to_dict()

    @app.get("/api/personal/capabilities")
    def personal_capabilities() -> dict[str, Any]:
        return context.capabilities.to_dict()

    @app.get("/api/personal/sessions")
    def personal_sessions() -> list[dict[str, Any]]:
        _require_capability(context, "session_management")
        return runtime.list_sessions()

    @app.get("/api/personal/sessions/{session_uid}")
    def personal_session(session_uid: str) -> dict[str, Any]:
        _require_capability(context, "session_management")
        try:
            return runtime.get_session(session_uid)
        except PersonalRuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/personal/sessions/{session_uid}/title")
    def personal_session_rename(session_uid: str, req: PersonalSessionRenameRequest) -> dict[str, Any]:
        _require_capability(context, "session_management")
        try:
            return runtime.rename_session(session_uid, req.title)
        except PersonalRuntimeError as exc:
            status = 400 if "required" in str(exc) else 404
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.delete("/api/personal/sessions/{session_uid}")
    def personal_session_delete(session_uid: str) -> dict[str, Any]:
        _require_capability(context, "session_management")
        try:
            return runtime.delete_session(session_uid)
        except PersonalRuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personal/chat/turn")
    def personal_chat_turn(req: PersonalChatTurnRequest) -> dict[str, Any]:
        _require_capability(context, "chat")
        try:
            return runtime.turn(content=req.content, session_uid=req.session_uid, source_uids=req.source_uids)
        except PersonalRuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/personal/dev-tasks/start")
    def personal_dev_task_start(req: PersonalDevTaskStartRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_generation")
        try:
            return dev_tasks.start(session_uid=req.session_uid, prompt=req.prompt, source_uids=req.source_uids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/personal/dev-tasks/continue")
    def personal_dev_task_continue(req: PersonalDevTaskContinueRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_generation")
        try:
            return dev_tasks.continue_task(task_uid=req.task_uid)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/personal/dev-tasks/{task_uid}")
    def personal_dev_task_get(task_uid: str) -> dict[str, Any]:
        try:
            return dev_tasks.get(task_uid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/personal/dev-tasks")
    def personal_dev_task_list(session_uid: str = "", status: str = "") -> list[dict[str, Any]]:
        return dev_tasks.list(session_uid=session_uid, status=status)

    @app.post("/api/agent/" + "unified" + "-turn")
    def personal_unified_turn(req: PersonalChatTurnRequest) -> dict[str, Any]:
        _require_capability(context, "chat")
        try:
            result = runtime.turn(content=req.content, session_uid=req.session_uid or req.task_uid, source_uids=req.source_uids)
        except PersonalRuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        message = result["message"]
        session = result["session"]
        message_metadata = message.get("metadata") or {}
        learning = message_metadata.get("learning_reflection") or {}
        draft = message_metadata.get("draft") or {}
        personal_intent = {
            "intent": (message_metadata.get("intent_route") or {}).get("intent") or "",
            "learning_candidate_id": learning.get("candidate_id") or (message_metadata.get("learning_candidate") or {}).get("id"),
            "created_draft_uids": [draft.get("draft_uid")] if draft.get("draft_uid") else [],
            "active_draft_uid": draft.get("draft_uid") or session.get("active_draft_uid") or "",
            "learning_reflection": learning,
        }
        mode = "personal_phase6_learning" if personal_intent["learning_candidate_id"] else (
            "personal_phase4_artifact" if personal_intent["created_draft_uids"] else "personal_chat"
        )
        return {
            "mode": mode,
            "task": {"task_uid": session["session_uid"], "messages": session.get("messages") or []},
            "message": message,
            "metadata": {"personal_intent": personal_intent},
        }

    @app.get("/api/personal/llm-status")
    def personal_llm_status() -> dict[str, Any]:
        return runtime.llm_status()

    @app.post("/api/personal/sources/text")
    def personal_source_text(req: PersonalSourceTextRequest) -> dict[str, Any]:
        _require_capability(context, "input_sources")
        try:
            parsed = parse_text_source(req.content, title=req.title or "文本输入")
            return create_input_source(
                context.db_path,
                project_id=context.project_id,
                parsed=parsed,
                make_active=req.make_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/personal/sources/upload")
    async def personal_source_upload(
        file: UploadFile = File(...),
        title: str = Form(default=""),
        make_active: bool = Form(default=True),
    ) -> dict[str, Any]:
        _require_capability(context, "input_sources")
        try:
            parsed = parse_uploaded_source(file.filename or "", await file.read())
            if title.strip():
                parsed = _replace_parsed_title(parsed, title.strip())
            return create_input_source(
                context.db_path,
                project_id=context.project_id,
                parsed=parsed,
                make_active=make_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/personal/sources")
    def personal_sources() -> list[dict[str, Any]]:
        _require_capability(context, "input_sources")
        return list_input_sources(context.db_path, project_id=context.project_id)

    @app.get("/api/personal/sources/{source_uid}")
    def personal_source(source_uid: str) -> dict[str, Any]:
        _require_capability(context, "input_sources")
        try:
            return get_input_source(context.db_path, project_id=context.project_id, source_uid=source_uid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personal/sources/{source_uid}/activate")
    def personal_source_activate(source_uid: str) -> dict[str, Any]:
        _require_capability(context, "input_sources")
        try:
            return activate_input_source(context.db_path, project_id=context.project_id, source_uid=source_uid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/personal/sources/{source_uid}")
    def personal_source_delete(source_uid: str) -> dict[str, Any]:
        _require_capability(context, "input_sources")
        try:
            return delete_input_source(context.db_path, project_id=context.project_id, source_uid=source_uid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personal/drafts")
    def personal_artifact_draft_create(req: PersonalArtifactDraftCreateRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_drafts")
        try:
            return create_artifact_draft(
                context.db_path,
                project_id=context.project_id,
                document_type=req.document_type,
                title=req.title,
                content=req.content,
                content_format=req.content_format,
                source_uid=req.source_uid,
                session_uid=req.session_uid,
                task_uid=req.task_uid,
                metadata=req.metadata,
                make_active=req.make_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/personal/drafts")
    def personal_artifact_drafts(session_uid: str | None = None) -> list[dict[str, Any]]:
        _require_capability(context, "artifact_drafts")
        return list_artifact_drafts(context.db_path, project_id=context.project_id, session_uid=session_uid)

    @app.post("/api/personal/documents/propose")
    def personal_artifact_propose(req: PersonalArtifactProposeRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_generation")
        try:
            return propose_personal_artifact(
                context.db_path,
                workspace=context.workspace,
                project_id=context.project_id,
                prompt=req.prompt,
                session_uid=req.session_uid,
                task_uid=req.task_uid,
                document_type=req.document_type,
                source_uids=req.source_uids,
                make_active=req.make_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/personal/artifacts/propose")
    def personal_artifact_propose_alias(req: dict[str, Any]) -> dict[str, Any]:
        _require_capability(context, "artifact_generation")
        try:
            result = propose_personal_artifact(
                context.db_path,
                workspace=context.workspace,
                project_id=context.project_id,
                prompt=str(req.get("prompt") or ""),
                session_uid=str(req.get("session_uid") or ""),
                task_uid=str(req.get("task_uid") or ""),
                document_type=str(req.get("document_type") or req.get("artifact" + "_type") or ""),
                source_uids=[str(item) for item in (req.get("source_uids") or [])],
                make_active=bool(req.get("make_active", True)),
            )
            result.setdefault("artifact" + "_type", result.get("document_type", ""))
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/personal/skills")
    def personal_skills() -> list[dict[str, Any]]:
        _require_capability(context, "skills")
        return list_personal_skills(context.db_path, workspace=context.workspace, project_id=context.project_id)

    @app.get("/api/personal/skills/update-candidates")
    def personal_skill_update_candidates(status: str = "") -> list[dict[str, Any]]:
        _require_capability(context, "skills")
        return list_skill_update_candidates(context.db_path, project_id=context.project_id, status=status)

    @app.post("/api/personal/skills/update-candidates/{candidate_id}/approve")
    def personal_skill_update_candidate_approve(candidate_id: int, req: PersonalSkillUpdateCandidateReviewRequest) -> dict[str, Any]:
        _require_capability(context, "skills")
        try:
            return approve_skill_update_candidate(
                context.db_path,
                project_id=context.project_id,
                candidate_id=candidate_id,
                reviewer=req.reviewer,
                comment=req.comment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)) from exc

    @app.post("/api/personal/skills/update-candidates/{candidate_id}/reject")
    def personal_skill_update_candidate_reject(candidate_id: int, req: PersonalSkillUpdateCandidateReviewRequest) -> dict[str, Any]:
        _require_capability(context, "skills")
        try:
            return reject_skill_update_candidate(
                context.db_path,
                project_id=context.project_id,
                candidate_id=candidate_id,
                reviewer=req.reviewer,
                comment=req.comment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)) from exc

    @app.get("/api/personal/skills/{skill_name}")
    def personal_skill(skill_name: str) -> dict[str, Any]:
        _require_capability(context, "skills")
        try:
            return get_personal_skill(context.db_path, workspace=context.workspace, project_id=context.project_id, skill_name=skill_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/personal/skills/{skill_name}/versions")
    def personal_skill_versions(skill_name: str) -> list[dict[str, Any]]:
        _require_capability(context, "skills")
        try:
            return list_personal_skill_versions(context.db_path, project_id=context.project_id, skill_name=skill_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personal/skills/{skill_name}/evaluate")
    def personal_skill_evaluate(skill_name: str) -> dict[str, Any]:
        _require_capability(context, "skills")
        try:
            return evaluate_personal_skill(context.db_path, workspace=context.workspace, project_id=context.project_id, skill_name=skill_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personal/drafts/code-patch")
    def personal_artifact_code_patch(req: PersonalArtifactCodePatchRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_generation")
        _require_capability(context, "patch_candidate")
        try:
            return propose_personal_code_patch(
                context.db_path,
                project_id=context.project_id,
                prompt=req.prompt,
                session_uid=req.session_uid,
                task_uid=req.task_uid,
                target_symbol=req.target_symbol,
                target_file=req.target_file,
                directives=[_model_to_dict(item) for item in req.directives],
                make_active=req.make_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/personal/drafts/unit-test-code")
    def personal_artifact_unit_test_code(req: PersonalArtifactUnitTestRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_generation")
        try:
            return propose_personal_unit_test_code(
                context.db_path,
                project_id=context.project_id,
                prompt=req.prompt,
                session_uid=req.session_uid,
                task_uid=req.task_uid,
                source_uids=req.source_uids,
                make_active=req.make_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/personal/artifacts/code-patch")
    def personal_artifact_code_patch_alias(req: PersonalArtifactCodePatchRequest) -> dict[str, Any]:
        result = personal_artifact_code_patch(req)
        result.setdefault("artifact" + "_type", result.get("document_type", "c_code_diff"))
        return result

    @app.post("/api/personal/artifacts/unit-test-code")
    def personal_artifact_unit_test_code_alias(req: PersonalArtifactUnitTestRequest) -> dict[str, Any]:
        result = personal_artifact_unit_test_code(req)
        result.setdefault("artifact" + "_type", result.get("document_type", "unit_test_code_or_diff"))
        return result

    @app.post("/api/personal/artifacts/drafts")
    def personal_artifact_draft_create_alias(req: dict[str, Any]) -> dict[str, Any]:
        _require_capability(context, "artifact_drafts")
        try:
            return create_artifact_draft(
                context.db_path,
                project_id=context.project_id,
                document_type=str(req.get("document_type") or req.get("artifact" + "_type") or ""),
                session_uid=str(req.get("session_uid") or ""),
                task_uid=str(req.get("task_uid") or ""),
                title=str(req.get("title") or ""),
                content=str(req.get("content") or ""),
                content_format=str(req.get("content_format") or "markdown"),
                source_uid=str(req.get("source_uid") or ""),
                metadata=req.get("metadata") if isinstance(req.get("metadata"), dict) else {},
                make_active=bool(req.get("make_active", True)),
            )
        except ValueError as exc:
            detail = str(exc).replace("document_type", "artifact" + "_type")
            raise HTTPException(status_code=400, detail=detail) from exc

    @app.get("/api/personal/artifacts/drafts")
    def personal_artifact_drafts_alias(session_uid: str | None = None) -> list[dict[str, Any]]:
        _require_capability(context, "artifact_drafts")
        return list_artifact_drafts(context.db_path, project_id=context.project_id, session_uid=session_uid)

    @app.get("/api/personal/drafts/{draft_uid}")
    def personal_artifact_draft(draft_uid: str) -> dict[str, Any]:
        _require_capability(context, "artifact_drafts")
        try:
            return get_artifact_draft(context.db_path, project_id=context.project_id, draft_uid=draft_uid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/personal/artifacts/{draft_uid}")
    def personal_artifact_draft_alias(draft_uid: str) -> dict[str, Any]:
        result = personal_artifact_draft(draft_uid)
        result.setdefault("artifact" + "_type", result.get("document_type", ""))
        return result

    @app.get("/api/personal/drafts/{draft_uid}/content")
    def personal_artifact_draft_content(draft_uid: str, revision_index: int | None = None) -> dict[str, Any]:
        _require_capability(context, "artifact_drafts")
        try:
            return get_artifact_content(
                context.db_path,
                project_id=context.project_id,
                draft_uid=draft_uid,
                revision_index=revision_index,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/personal/artifacts/{draft_uid}/content")
    def personal_artifact_draft_content_alias(draft_uid: str, revision_index: int | None = None) -> dict[str, Any]:
        return personal_artifact_draft_content(draft_uid, revision_index)

    @app.post("/api/personal/drafts/{draft_uid}/revise-manual")
    def personal_artifact_draft_revise_manual(draft_uid: str, req: PersonalArtifactDraftReviseRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_drafts")
        try:
            return revise_artifact_draft_manual(
                context.db_path,
                project_id=context.project_id,
                draft_uid=draft_uid,
                content=req.content,
                metadata=req.metadata,
                make_active=req.make_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 400, detail=str(exc)) from exc

    @app.post("/api/personal/artifacts/{draft_uid}/revise-manual")
    def personal_artifact_draft_revise_manual_alias(draft_uid: str, req: PersonalArtifactDraftReviseRequest) -> dict[str, Any]:
        return personal_artifact_draft_revise_manual(draft_uid, req)

    @app.post("/api/personal/drafts/{draft_uid}/revise")
    def personal_artifact_revise(draft_uid: str, req: PersonalArtifactReviseRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_generation")
        try:
            return revise_personal_artifact(
                context.db_path,
                project_id=context.project_id,
                workspace=context.workspace,
                draft_uid=draft_uid,
                feedback=req.feedback,
                session_uid=req.session_uid,
                make_active=req.make_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 400, detail=str(exc)) from exc

    @app.post("/api/personal/drafts/{draft_uid}/activate")
    def personal_artifact_draft_activate(draft_uid: str) -> dict[str, Any]:
        _require_capability(context, "artifact_drafts")
        try:
            return activate_artifact_draft(context.db_path, project_id=context.project_id, draft_uid=draft_uid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personal/artifacts/{draft_uid}/activate")
    def personal_artifact_draft_activate_alias(draft_uid: str) -> dict[str, Any]:
        return personal_artifact_draft_activate(draft_uid)

    @app.post("/api/personal/drafts/{draft_uid}/export")
    def personal_artifact_export(draft_uid: str, req: PersonalArtifactExportRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_export")
        try:
            return export_personal_artifact(
                context.db_path,
                workspace=context.workspace,
                project_id=context.project_id,
                draft_uid=draft_uid,
                export_format=req.format,
                revision_index=req.revision_index,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400 if "not supported" in str(exc) else 404 if "not found" in str(exc) else 400, detail=str(exc)) from exc

    @app.post("/api/personal/artifacts/{draft_uid}/export")
    def personal_artifact_export_alias(draft_uid: str, req: PersonalArtifactExportRequest) -> dict[str, Any]:
        return personal_artifact_export(draft_uid, req)

    @app.post("/api/personal/drafts/{draft_uid}/open")
    def personal_artifact_open(draft_uid: str, req: PersonalArtifactExportRequest) -> dict[str, Any]:
        _require_capability(context, "artifact_export")
        try:
            return open_personal_artifact(
                context.db_path,
                workspace=context.workspace,
                project_id=context.project_id,
                draft_uid=draft_uid,
                export_format=req.format,
                revision_index=req.revision_index,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400 if "not supported" in str(exc) else 404 if "not found" in str(exc) else 400, detail=str(exc)) from exc

    @app.post("/api/personal/artifacts/{draft_uid}/open")
    def personal_artifact_open_alias(draft_uid: str, req: PersonalArtifactExportRequest) -> dict[str, Any]:
        return personal_artifact_open(draft_uid, req)

    @app.get("/api/personal/drafts/{draft_uid}/download")
    def personal_artifact_download(draft_uid: str, format: str = "") -> FileResponse:
        _require_capability(context, "artifact_export")
        try:
            download = resolve_personal_artifact_download(
                context.db_path,
                workspace=context.workspace,
                project_id=context.project_id,
                draft_uid=draft_uid,
                export_format=format,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400 if "not supported" in str(exc) else 404 if "not found" in str(exc) else 400, detail=str(exc)) from exc
        return FileResponse(
            download["file_path"],
            media_type=download["media_type"],
            filename=download["file_name"],
        )

    @app.get("/api/personal/artifacts/{draft_uid}/download")
    def personal_artifact_download_alias(draft_uid: str, format: str = "") -> FileResponse:
        return personal_artifact_download(draft_uid, format)

    @app.get("/api/personal/knowledge")
    def personal_knowledge(limit: int = 100) -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        return list_personal_knowledge(context.db_path, project_id=context.project_id, limit=limit)

    @app.post("/api/personal/knowledge/import-source")
    def personal_knowledge_import_source(req: PersonalKnowledgeImportSourceRequest) -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        _require_capability(context, "input_sources")
        try:
            return import_source_to_knowledge(context.db_path, project_id=context.project_id, source_uid=req.source_uid)
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 400, detail=str(exc)) from exc

    @app.post("/api/personal/knowledge/search")
    def personal_knowledge_search(req: PersonalKnowledgeSearchRequest) -> list[dict[str, Any]]:
        _require_capability(context, "knowledge_learning")
        try:
            return search_personal_knowledge(context.db_path, project_id=context.project_id, query=req.query, limit=req.limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/personal/knowledge/{knowledge_id}/deprecate")
    def personal_knowledge_deprecate(knowledge_id: int, req: PersonalKnowledgeDeprecateRequest) -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        try:
            return deprecate_personal_knowledge(
                context.db_path,
                project_id=context.project_id,
                knowledge_id=knowledge_id,
                reviewer=req.reviewer,
                comment=req.comment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personal/memory/consolidate")
    def personal_memory_consolidate() -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        return consolidate_memory_lessons(context.db_path, project_id=context.project_id)

    @app.get("/api/personal/learning/summary")
    def personal_learning_summary_route() -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        return personal_learning_summary(context.db_path, project_id=context.project_id)

    @app.get("/api/personal/learning/candidates")
    def personal_learning_candidates_route(limit: int = 100) -> list[dict[str, Any]]:
        _require_capability(context, "knowledge_learning")
        return personal_learning_candidates(context.db_path, project_id=context.project_id, limit=limit)

    @app.get("/api/personal/inbox")
    def personal_inbox_route(limit: int = 100) -> list[dict[str, Any]]:
        _require_capability(context, "knowledge_learning")
        return personal_inbox(context.db_path, project_id=context.project_id, limit=limit)

    @app.post("/api/personal/learning/feedback")
    def personal_learning_feedback(req: PersonalLearningFeedbackRequest) -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        try:
            return record_personal_feedback(
                context.db_path,
                project_id=context.project_id,
                feedback=req.feedback,
                session_uid=req.session_uid,
                source="personal_learning_api",
                corrected_behavior=req.corrected_behavior,
                scope=req.scope,
                add_to_regression=req.add_to_regression,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/personal/learning/candidates/{candidate_id}/approve")
    def personal_learning_candidate_approve(candidate_id: int, req: PersonalLearningReviewRequest) -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        try:
            return approve_personal_candidate(
                context.db_path,
                project_id=context.project_id,
                candidate_id=candidate_id,
                reviewer=req.reviewer,
                comment=req.comment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)) from exc

    @app.post("/api/personal/learning/candidates/{candidate_id}/reject")
    def personal_learning_candidate_reject(candidate_id: int, req: PersonalLearningReviewRequest) -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        try:
            return reject_personal_candidate(
                context.db_path,
                project_id=context.project_id,
                candidate_id=candidate_id,
                reviewer=req.reviewer,
                comment=req.comment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personal/learning/{item_uid}/dismiss")
    def personal_learning_dismiss(item_uid: str, req: PersonalLearningReviewRequest) -> dict[str, Any]:
        _require_capability(context, "knowledge_learning")
        try:
            return dismiss_personal_memory_lesson(
                context.db_path,
                project_id=context.project_id,
                item_uid=item_uid,
                reviewer=req.reviewer,
                comment=req.comment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/personal/llm-config")
    def personal_llm_config() -> dict[str, Any]:
        _require_capability(context, "llm_config")
        result = read_personal_llm_admin_config(context.db_path, context.env_path)
        result["restart_supported"] = False
        result["restart_required"] = False
        return result

    @app.put("/api/personal/llm-config")
    def personal_save_llm_config(req: LlmAdminConfigRequest) -> dict[str, Any]:
        _require_capability(context, "llm_config")
        try:
            result = save_personal_llm_admin_config(
                db_path=context.db_path,
                provider=req.provider,
                model=req.model,
                api_key=req.api_key,
                env_path=context.env_path,
                clear_other_provider_keys=req.clear_other_provider_keys,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["restart_supported"] = False
        result["restart_required"] = False
        return result

    @app.get("/api/personal/codebase/config")
    def personal_codebase_config() -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _personal_codebase_config(context)

    @app.put("/api/personal/codebase/config")
    def personal_save_codebase_config(req: PersonalCodebaseConfigRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        repo = _resolve_local_repo(req.repo_path)
        if not repo.exists() or not repo.is_dir():
            raise HTTPException(status_code=400, detail=f"代码库路径不存在或不是目录：{repo}")
        _upsert_project_input(context, "code_repo_path", "C 代码仓库路径", "code", str(repo))
        _upsert_project_input(context, "personal_build_command", "个人构建命令", "toolchain", req.build_command.strip())
        _upsert_project_input(context, "personal_test_command", "个人测试命令", "toolchain", req.test_command.strip())
        _upsert_project_input(context, "personal_static_analysis_command", "个人静态分析命令", "toolchain", req.static_analysis_command.strip())
        _upsert_project_input(context, "personal_tool_timeout_s", "个人工具超时秒数", "toolchain", str(max(1, req.tool_timeout_s)))
        return _personal_codebase_config(context)

    @app.post("/api/personal/codebase/index")
    def personal_codebase_index(req: PersonalCodebaseIndexRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(
            context,
            "codebase_index",
            {
                "repo_path": req.repo_path,
                "query": req.query,
                "max_files": req.max_files,
                "batch_size": req.batch_size,
                "skip_dirs": req.skip_dirs,
            },
        )

    @app.post("/api/personal/codebase/search")
    def personal_codebase_search(req: PersonalCodebaseSearchRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "codebase_search", {"query": req.query, "limit": req.limit})

    @app.post("/api/personal/codebase/symbols")
    def personal_symbol_lookup(req: PersonalSymbolLookupRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "symbol_lookup", {"name": req.name, "kind": req.kind, "limit": req.limit})

    @app.post("/api/personal/codebase/include-impact")
    def personal_include_impact(req: PersonalIncludeImpactRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "include_impact_query", {"path": req.path})

    @app.post("/api/personal/codebase/call-graph")
    def personal_call_graph(req: PersonalCallGraphRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "call_graph_query", {"function_name": req.function_name, "limit": req.limit})

    @app.post("/api/personal/codebase/macro-impact")
    def personal_macro_impact(req: PersonalMacroImpactRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "macro_impact_query", {"macro_name": req.macro_name, "limit": req.limit})

    @app.post("/api/personal/codebase/type-usage")
    def personal_type_usage(req: PersonalTypeUsageRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "type_usage_query", {"type_name": req.type_name, "limit": req.limit})

    @app.post("/api/personal/codebase/variable-usage")
    def personal_variable_usage(req: PersonalVariableUsageRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "variable_usage_query", {"variable_name": req.variable_name, "limit": req.limit})

    @app.post("/api/personal/codebase/impact")
    def personal_impact_analyze(req: PersonalImpactRequest) -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "impact_analyze", {"change_hint": req.change_hint, "limit": req.limit})

    @app.post("/api/personal/codebase/style")
    def personal_style_profile() -> dict[str, Any]:
        _require_capability(context, "codebase")
        return _invoke_personal_tool(context, "style_profile_read", {})

    @app.post("/api/personal/patch/propose")
    def personal_patch_propose(req: PersonalPatchProposeRequest) -> dict[str, Any]:
        _require_capability(context, "patch_candidate")
        return _invoke_personal_tool(
            context,
            "patch_propose",
            {
                "change_text": req.change_text,
                "session_uid": req.session_uid,
                "target_symbol": req.target_symbol,
                "target_file": req.target_file,
                "directives": [_model_to_dict(item) for item in req.directives],
                "dry_run": req.dry_run,
            },
            dry_run=req.dry_run,
        )

    @app.post("/api/personal/patch/validate")
    def personal_patch_validate(req: PersonalPatchTextRequest) -> dict[str, Any]:
        _require_capability(context, "patch_candidate")
        return _invoke_personal_tool(
            context,
            "patch_validate",
            {"patch_text": req.patch_text, "draft_uid": req.draft_uid, "artifact_id": req.artifact_id},
        )

    @app.post("/api/personal/patch/apply")
    def personal_patch_apply(req: PersonalPatchApplyRequest) -> dict[str, Any]:
        _require_capability(context, "patch_apply")
        return _invoke_personal_tool(
            context,
            "patch_apply",
            {
                "patch_text": req.patch_text,
                "draft_uid": req.draft_uid,
                "artifact_id": req.artifact_id,
                "dry_run": req.dry_run,
                "reviewer": req.reviewer,
                "comment": req.comment,
            },
            confirmed=req.confirmed,
            dry_run=req.dry_run,
        )

    @app.post("/api/personal/validation/{kind}")
    def personal_validation_run(kind: str, req: PersonalValidationRunRequest) -> dict[str, Any]:
        _require_capability(context, "validation")
        tool_name = {"build": "run_build", "tests": "run_tests", "static-analysis": "run_static_analysis"}.get(kind)
        if not tool_name:
            raise HTTPException(status_code=404, detail="unknown validation kind")
        validation_task_uid = req.task_uid
        if not validation_task_uid and req.session_uid:
            active = dev_tasks.active_task_for_session(req.session_uid)
            validation_task_uid = str((active or {}).get("task_uid") or "")
        return _invoke_personal_tool(
            context,
            tool_name,
            {"command": req.command, "timeout_s": req.timeout_s},
            confirmed=req.confirmed,
            task_uid=validation_task_uid,
            validation_kind=kind,
        )

def _require_capability(context: PersonalAgentContext, name: str) -> None:
    if not context.capabilities.enabled(name):
        raise HTTPException(status_code=403, detail=f"personal capability disabled: {name}")


def _replace_parsed_title(parsed: Any, title: str) -> Any:
    if hasattr(parsed, "__class__"):
        return parsed.__class__(
            source_type=parsed.source_type,
            title=title,
            plain_text=parsed.plain_text,
            tables=parsed.tables,
            sections=parsed.sections,
            metadata=parsed.metadata,
        )
    return parsed


def _personal_codebase_config(context: PersonalAgentContext) -> dict[str, Any]:
    with connect(context.db_path) as conn:
        rows = conn.execute(
            "SELECT input_key, value FROM project_inputs WHERE project_id=? AND status='active'",
            (context.project_id,),
        ).fetchall()
    inputs = {str(row["input_key"]): str(row["value"]) for row in rows}
    repo = latest_repository(context.db_path, context.project_id)
    return {
        "repo_path": inputs.get("code_repo_path", ""),
        "build_command": inputs.get("personal_build_command", ""),
        "test_command": inputs.get("personal_test_command", ""),
        "static_analysis_command": inputs.get("personal_static_analysis_command", ""),
        "tool_timeout_s": int(inputs.get("personal_tool_timeout_s") or 120),
        "repository": repo or {},
    }


def _resolve_local_repo(value: str) -> Path:
    raw = value.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="repo_path is required")
    candidate = Path(raw).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (Path.cwd() / candidate).resolve()


def _upsert_project_input(context: PersonalAgentContext, input_key: str, label: str, category: str, value: str) -> None:
    now = utc_now()
    with connect(context.db_path) as conn:
        conn.execute(
            """
            INSERT INTO project_inputs(project_id, input_key, label, category, value, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(project_id, input_key) DO UPDATE SET
                label=excluded.label,
                category=excluded.category,
                value=excluded.value,
                status='active',
                updated_at=excluded.updated_at
            """,
            (context.project_id, input_key, label, category, value, now, now),
        )


def _invoke_personal_tool(
    context: PersonalAgentContext,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    confirmed: bool = False,
    dry_run: bool = False,
    task_uid: str = "",
    validation_kind: str = "",
) -> dict[str, Any]:
    payload = {
        **tool_input,
        "project_id": context.project_id,
        "requirement" + "_id": "",
    }
    legacy_key = "requirement" + "_id"
    result = TypedToolExecutor(context.db_path).invoke(
        tool_name,
        payload,
        project_id=context.project_id,
        **{legacy_key: str(payload.get(legacy_key) or "")},
        task_uid=task_uid,
        caller="conversation",
        confirmed=confirmed,
        dry_run=dry_run,
    )
    if validation_kind:
        try:
            task = DevTaskOrchestrator(context.db_path, workspace=context.workspace, project_id=context.project_id).record_validation(
                task_uid=task_uid,
                kind=validation_kind,
                result=result,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)) from exc
        if task:
            result["dev_task"] = task
    if result["status"] in {"failed", "rejected"}:
        status = 400 if result["status"] == "rejected" else 500
        raise HTTPException(status_code=status, detail=result.get("error") or f"{tool_name} failed")
    return result


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
