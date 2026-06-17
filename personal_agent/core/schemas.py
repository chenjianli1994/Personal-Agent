from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    code: str
    name: str
    description: str = ""


class AdminProjectCreateRequest(BaseModel):
    code: str
    name: str
    description: str = ""
    project_admin_user_uid: str = ""
    project_admin_display_name: str = ""
    project_admin_email: str = ""
    project_admin_temporary_password: str = ""


class ProjectAdminAssignRequest(BaseModel):
    user_uid: str
    display_name: str = ""
    email: str = ""
    temporary_password: str = ""


class ProjectMemberUpsertRequest(BaseModel):
    user_uid: str
    display_name: str = ""
    email: str = ""
    role_code: str
    status: str = "active"
    temporary_password: str = ""


class LoginRequest(BaseModel):
    user_uid: str
    password: str


class BootstrapAdminRequest(BaseModel):
    user_uid: str
    display_name: str = ""
    email: str = ""
    password: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


class ResponsibilityEntryRequest(BaseModel):
    process_code: str = ""
    artifact_type: str = ""
    owner_user_uid: str = ""
    developer_user_uid: str = ""
    tester_user_uid: str = ""
    reviewer_user_uid: str = ""
    approver_user_uid: str = ""
    backup_user_uid: str = ""
    status: str = "active"


class ResponsibilityMatrixReplaceRequest(BaseModel):
    entries: list[ResponsibilityEntryRequest] = Field(default_factory=list)


class ProjectInputUpsertRequest(BaseModel):
    input_key: str
    label: str
    category: str
    value: str
    status: str = "active"


class RequirementCreateRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str
    title: str
    description: str = ""
    trigger: str = ""
    expected_behavior: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk_level: str = ""
    status: str = "draft"
    expected_version: int | None = None


class ProjectContext(BaseModel):
    id: int | None = None
    code: str = ""
    name: str = ""
    description: str = ""
    status: str = ""
    product_domain: str = ""
    code_repository: str = ""
    aspice_scope: list[str] = Field(default_factory=list)
    template_scope: list[str] = Field(default_factory=list)
    knowledge_scope: list[str] = Field(default_factory=list)
    current_baseline: str = ""


class RequirementContext(BaseModel):
    id: int | None = None
    project_id: int | None = None
    requirement_id: str = ""
    title: str = ""
    description: str = ""
    trigger: str = ""
    expected_behavior: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    upstream_source: str = ""
    risk_level: str = ""
    status: str = ""
    related_processes: list[str] = Field(default_factory=list)
    current_process_status: str = ""


class RequiredProductState(BaseModel):
    artifact_type: str
    label: str
    status: str = "missing"


class ProcessState(BaseModel):
    code: str
    name: str
    objective: str = ""
    status: str = "missing"
    required_products: list[RequiredProductState] = Field(default_factory=list)
    missing_products: list[RequiredProductState] = Field(default_factory=list)
    artifact_count: int = 0
    approved_count: int = 0
    required_count: int = 0
    gate_status: str = "not_run"
    review_status: str = "not_started"
    trace_status: str = "missing"
    trace_link_count: int = 0
    gap_count: int = 0
    next_action: str = ""


class EvidenceGraphNode(BaseModel):
    kind: str
    id: int | str
    name: str = ""
    artifact_type: str = ""
    process_area_code: str = ""
    status: str = ""
    source_agent_run_id: str = ""


class EvidenceGraphLink(BaseModel):
    kind: str = "trace"
    id: int | None = None
    link_type: str = ""
    target_ref: str = ""
    source_agent_run_id: str = ""


class EvidenceGraph(BaseModel):
    nodes: list[EvidenceGraphNode] = Field(default_factory=list)
    links: list[EvidenceGraphLink] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class ProjectSemanticContext(BaseModel):
    version: str
    project: ProjectContext | None = None
    requirements: list[RequirementContext] = Field(default_factory=list)
    selected_requirement: RequirementContext | None = None
    process_states: list[ProcessState] = Field(default_factory=list)
    evidence_graph: EvidenceGraph = Field(default_factory=EvidenceGraph)
    learning: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)


class RequirementImportRequest(BaseModel):
    project_id: int | None = None
    source_name: str = "manual_import"
    format: str = "json"
    content: str


class KnowledgeItemCreateRequest(BaseModel):
    project_id: int | None = None
    title: str
    category: str = "template"
    source_type: str = "manual"
    source_ref: str = ""
    content: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.7
    status: str = "active"


class KnowledgeDocumentImportRequest(BaseModel):
    project_id: int | None = None
    title: str
    category: str = "reference"
    source_type: str = "manual"
    source_ref: str = ""
    content: str
    tags: list[str] = Field(default_factory=list)
    process_codes: list[str] = Field(default_factory=list)
    status: str = "active"
    import_batch_id: str = ""
    source_owner: str = ""
    source_trust_level: str = "internal"
    source_version: str = ""
    applicable_project: str = ""
    applicable_process: list[str] = Field(default_factory=list)
    applicable_domain: str = ""
    approval_status: str | None = None
    expires_at: str = ""
    supersedes: str = ""
    material_type: str = ""
    code_refs: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeSearchRequest(BaseModel):
    project_id: int | None = None
    query: str
    limit: int = 5
    category: str | None = None
    source_type: str | None = None
    process_code: str | None = None
    status: str | None = None


class KnowledgeDirectoryImportRequest(BaseModel):
    project_id: int | None = None
    root_path: str
    auto_build_style_profile: bool = True


class CodeStyleProfileBuildRequest(BaseModel):
    project_id: int | None = None


class MemoryCandidateCreateRequest(BaseModel):
    project_id: int | None = None
    title: str
    problem: str
    lesson: str
    evidence_refs: dict[str, Any] = Field(default_factory=dict)
    scope: str = "project"
    failure_type: str = "runtime"
    applicability: dict[str, Any] = Field(default_factory=dict)
    counterexamples: list[Any] = Field(default_factory=list)
    regression_case_uid: str = ""
    expires_at: str = ""
    superseded_by: str = ""


class LearningFeedbackRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str = ""
    source: str = "manual"
    failure_mode: str
    user_feedback: str
    root_cause: str = ""
    corrected_behavior: str
    evidence_refs: dict[str, Any] = Field(default_factory=dict)
    add_to_regression: bool = True


class MemoryCandidateReviewRequest(BaseModel):
    reviewer: str = "local_user"
    comment: str = ""


class ReviewDecisionRequest(BaseModel):
    reviewer: str = "local_user"
    comment: str = ""


class ReviewParticipantRequest(BaseModel):
    user_uid: str
    role: str = "reviewer"
    required: bool = True


class ReviewIssueCreateRequest(BaseModel):
    target_type: str = ""
    target_id: int | None = None
    target_ref: str = ""
    title: str
    description: str = ""
    severity: str = "medium"
    assignee_user_uid: str = ""
    source_gate_finding_id: int | None = None


class ReviewCommentCreateRequest(BaseModel):
    target_type: str = ""
    target_ref: str = ""
    comment: str


class ReviewIssueCloseRequest(BaseModel):
    comment: str = ""
    evidence_refs: list[Any] = Field(default_factory=list)


class GateFindingReviewIssueRequest(BaseModel):
    review_task_id: int | None = None


class TodoCompleteRequest(BaseModel):
    comment: str = ""


class NotificationPreferenceRequest(BaseModel):
    channel: str = "in_app"
    enabled: bool = True


class AspiceConfigRequest(BaseModel):
    project_id: int | None = None
    aspice_version: str = "4.0"
    process_scope: list[str] = Field(default_factory=list)
    capability_level_target: int = 2
    tailoring_rules: dict[str, Any] = Field(default_factory=dict)
    template_library_scope: str = "company_and_project"


class TemplateLibraryCreateRequest(BaseModel):
    project_id: int | None = None
    name: str
    scope: str = "company"
    version: str = "1.0"


class ProcessAssetCreateRequest(BaseModel):
    project_id: int | None = None
    library_id: int | None = None
    process_code: str = ""
    asset_type: str = "template"
    title: str
    content: str = ""
    version: str = "1.0"
    supersedes_id: int | None = None


class ProcessAssetApproveRequest(BaseModel):
    comment: str = ""


class ChangeImpactRequest(BaseModel):
    project_id: int | None = None
    source_type: str
    source_ref: str = ""


class AspiceExportRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str | None = None


class BaselineDiffRequest(BaseModel):
    project_id: int
    from_baseline_id: int | None = None
    to_baseline_id: int | None = None


class WaiverCreateRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str = ""
    process_code: str = ""
    title: str
    reason: str = ""
    expires_at: str = ""
    evidence_refs: list[Any] = Field(default_factory=list)


class CodeIntegrationPlanRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str = ""
    task_uid: str = ""
    code_repo_path: str = ""
    build_command: str = ""
    test_command: str = ""


class CodeIntegrationVerifyRequest(BaseModel):
    build_command: str = ""
    test_command: str = ""
    static_analysis_command: str = ""
    lint_command: str = ""
    coverage_command: str = ""
    ci_log: str = ""


class CodeIntegrationCiLogRequest(BaseModel):
    source: str = "manual"
    content: str = ""


class CodeIntegrationApplyRequest(BaseModel):
    reviewer: str = "local_user"
    comment: str = ""
    create_commit: bool = False
    commit_message: str = ""
    create_branch: bool = True
    allow_dirty: bool = False


class AgentTaskCreateRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str = "THM-SWE-006"
    task_type: str = "swe_main_chain"
    prompt: str
    title: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


class AgentMessageRequest(BaseModel):
    content: str


class AgentUnifiedTurnRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str = ""
    task_uid: str = ""
    content: str
    command: dict[str, Any] = Field(default_factory=dict)
    conversation_focus: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


class AgentTaskReviewRequest(BaseModel):
    reviewer: str = "local_user"
    comment: str = ""


class AgentTaskControlRequest(BaseModel):
    reason: str = ""
    assignee_user_uid: str = ""
    title: str = ""
    description: str = ""


class AgentTaskRerunRequest(BaseModel):
    prompt: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


class AgentProcessActionRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str | None = None
    prompt: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)


class AgentAutonomousRunRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str = ""
    task_uid: str = ""
    user_goal: str = "继续推进当前需求"
    execute: bool = True


class AgentProblemSolvingRunRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str = ""
    task_uid: str = ""
    user_goal: str = "继续推进当前需求"
    execute: bool = True


class AgentR4ClosureRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str = "THM-SWE-006"
    task_uid: str = ""
    user_goal: str = "补齐完整 ASPICE 主 V 后半段闭环"
    execute: bool = True


class SemanticGateRunRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str | None = None


class PlatformBackupRequest(BaseModel):
    label: str = ""


class LlmAdminConfigRequest(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    api_key: str = ""
    restart_backend: bool = False
    clear_other_provider_keys: bool = False


class EvidencePackageExportRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str | None = None


class Stage6PreflightClearanceRequest(BaseModel):
    project_id: int | None = None
    requirement_id: str | None = None
    reviewer: str = "operations_owner"
    comment: str = "阶段 6 前置清绿批处理。"
    run_r4_closure: bool = True


class AgentDecisionFeedbackRequest(BaseModel):
    decision_uid: str
    failure_mode: str
    user_feedback: str
    root_cause: str = ""
    corrected_behavior: str
    add_to_regression: bool = True


class AgentFailureCaseCreateRequest(BaseModel):
    failure_uid: str = ""
    project_id: int | None = None
    task_uid: str = ""
    trace_uid: str = ""
    failure_type: str = "runtime"
    symptom: str = ""
    expected_behavior: str = ""
    actual_behavior: str = ""
    evidence_refs: dict[str, Any] = Field(default_factory=dict)


class AgentStrategyCandidateCreateRequest(BaseModel):
    strategy_uid: str = ""
    source_failure_uid: str = ""
    strategy_type: str = "policy_patch"
    target_policy_key: str = ""
    target_prompt_key: str = ""
    proposal: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "medium"


class AgentStrategyPromotionRequest(BaseModel):
    reviewer: str = "local_user"
    rationale: str = ""


class LifecycleTransitionRequest(BaseModel):
    object_type: str
    object_id: int
    to_status: str
    reason: str = ""
    evidence_refs: list[Any] = Field(default_factory=list)
    expected_version: int | None = None


class ObjectLockAcquireRequest(BaseModel):
    object_type: str
    object_id: int
    reason: str = ""
    ttl_seconds: int = 900
    expected_version: int | None = None


class ObjectLockReleaseRequest(BaseModel):
    lock_token: str


class ObjectVersionCheckRequest(BaseModel):
    object_type: str
    object_id: int
    expected_version: int
