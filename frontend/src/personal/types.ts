export type PersonalCapabilityFlags = {
  chat: boolean;
  llm_config: boolean;
  session_management: boolean;
  codebase: boolean;
  patch_candidate: boolean;
  patch_apply: boolean;
  validation: boolean;
  input_sources: boolean;
  artifact_drafts: boolean;
  artifact_generation: boolean;
  knowledge_learning: boolean;
  artifact_export: boolean;
  skills: boolean;
};

export type PersonalCapabilities = {
  flags: PersonalCapabilityFlags;
  groups: Record<string, Record<string, boolean>>;
  descriptions: Record<string, string>;
  config_path: string;
  configured: boolean;
};

export type PersonalContext = {
  db_path: string;
  workspace: string;
  env_path: string;
  capabilities: PersonalCapabilities;
  project_id: number;
  project_code: string;
  project_name: string;
  workspace_uid: string;
};

export type PersonalMessage = {
  id?: number;
  message_uid?: string;
  session_uid?: string;
  role: string;
  content: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
  pending?: boolean;
};

export type PersonalRecallProvenance = {
  uid: string;
  title: string;
  kind: string;
};

export type PersonalSessionEvent = {
  id?: number;
  event_uid?: string;
  session_uid?: string;
  event_type: string;
  title?: string;
  payload?: Record<string, unknown>;
  created_at?: string;
};

export type PersonalSession = {
  session_uid: string;
  title?: string;
  status?: string;
  active_source_uid?: string;
  active_draft_uid?: string;
  current_requirement_summary?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  messages?: PersonalMessage[];
  events?: PersonalSessionEvent[];
};

export type PersonalSessionRenameInput = {
  title: string;
};

export type PersonalChatTurnInput = {
  session_uid?: string;
  content: string;
  source_uids?: string[];
};

export type PersonalChatTurnResult = {
  session: PersonalSession;
  message?: PersonalMessage;
};

export type AgentLlmStatus = {
  configured: boolean;
  provider?: string;
  model?: string;
  error?: string;
  configured_source?: string;
  last_call_status?: string;
  last_call_purpose?: string;
  last_call_at?: string;
};

export type LlmProviderOption = {
  value: string;
  label: string;
  default_model: string;
  model_options?: string[];
};

export type PersonalLlmConfig = {
  provider: string;
  model: string;
  api_key_name: string;
  api_key_configured: boolean;
  env_file: string;
  available_providers: LlmProviderOption[];
  status: AgentLlmStatus;
  restart_supported: boolean;
  restart_required?: boolean;
};

export type PersonalLlmConfigInput = {
  provider: string;
  model: string;
  api_key?: string;
  clear_other_provider_keys?: boolean;
};

export type PersonalInputSource = {
  id?: number;
  source_uid: string;
  project_id?: number;
  source_type: string;
  title: string;
  plain_text: string;
  tables: Array<Record<string, unknown>>;
  sections: Array<Record<string, unknown>>;
  metadata: Record<string, unknown>;
  status?: string;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
  preview?: string;
};

export type PersonalSourceTextInput = {
  title?: string;
  content: string;
  make_active?: boolean;
};

export type PersonalArtifactRevision = {
  id?: number;
  revision_uid: string;
  draft_uid: string;
  project_id?: number;
  revision_index: number;
  content: string;
  metadata: Record<string, unknown>;
  created_at?: string;
  preview?: string;
};

export type PersonalArtifactDraft = {
  id?: number;
  draft_uid: string;
  project_id?: number;
  source_uid?: string;
  session_uid?: string;
  document_type: string;
  title: string;
  content_format: string;
  current_revision: number;
  revision_count: number;
  derived_from_draft_uid?: string;
  lineage_stale?: boolean;
  status?: string;
  is_active: boolean;
  metadata: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  preview?: string;
  content?: string;
  revisions?: PersonalArtifactRevision[];
};

export type PersonalSkillEval = {
  id?: number;
  eval_uid: string;
  skill_uid: string;
  version_uid?: string;
  project_id?: number;
  status: string;
  score: number;
  checks: Array<Record<string, unknown>>;
  created_at?: string;
};

export type PersonalSkill = {
  id?: number;
  skill_uid: string;
  project_id?: number;
  name: string;
  display_name: string;
  skill_kind: string;
  document_type: string;
  description: string;
  status: string;
  active_version_uid?: string;
  active_version_index?: number;
  path: string;
  exists: boolean;
  frontmatter?: Record<string, unknown>;
  skill_markdown?: string;
  eval_runs?: PersonalSkillEval[];
  created_at?: string;
  updated_at?: string;
};

export type PersonalSkillVersion = {
  id?: number;
  version_uid: string;
  skill_uid: string;
  project_id?: number;
  version_index: number;
  skill_markdown: string;
  metadata: Record<string, unknown>;
  status: string;
  created_by: string;
  created_at?: string;
  activated_at?: string;
};

export type PersonalSkillUpdateCandidate = {
  id: number;
  candidate_uid: string;
  project_id?: number;
  target_skill: string;
  reason: string;
  proposed_change: string;
  risk?: string;
  evidence_refs?: Record<string, unknown>;
  status: string;
  source?: string;
  session_uid?: string;
  reviewed_by?: string;
  review_comment?: string;
  created_at?: string;
  updated_at?: string;
  reviewed_at?: string;
};

export type PersonalArtifactDraftCreateInput = {
  document_type: string;
  session_uid?: string;
  title: string;
  content: string;
  content_format?: string;
  source_uid?: string;
  metadata?: Record<string, unknown>;
  make_active?: boolean;
};

export type PersonalArtifactDraftReviseInput = {
  session_uid?: string;
  content: string;
  metadata?: Record<string, unknown>;
  make_active?: boolean;
};

export type PersonalArtifactProposeInput = {
  prompt: string;
  session_uid?: string;
  document_type?: string;
  source_uids?: string[];
  make_active?: boolean;
};

export type PersonalArtifactNaturalReviseInput = {
  session_uid?: string;
  feedback: string;
  make_active?: boolean;
};

export type PersonalArtifactCodePatchInput = {
  prompt: string;
  session_uid?: string;
  target_symbol?: string;
  target_file?: string;
  directives?: PatchDirectiveInput[];
  make_active?: boolean;
};

export type PersonalArtifactUnitTestCodeInput = {
  prompt: string;
  session_uid?: string;
  source_uids?: string[];
  make_active?: boolean;
};

export type PersonalArtifactContent = {
  draft_uid: string;
  title: string;
  document_type: string;
  content_format: string;
  current_revision: number;
  revision: PersonalArtifactRevision;
  content: string;
};

export type PersonalArtifactExportInput = {
  format?: string;
  revision_index?: number;
};

export type PersonalArtifactExport = {
  status: string;
  draft_uid: string;
  document_type: string;
  content_format: string;
  revision_index: number;
  export_format: string;
  file_name: string;
  file_path: string;
  download_url: string;
  boundaries?: Record<string, unknown>;
};

export type PersonalCodebaseConfig = {
  repo_path: string;
  build_command: string;
  test_command: string;
  static_analysis_command: string;
  tool_timeout_s: number;
  repository?: Record<string, unknown>;
};

export type PersonalCodebaseConfigInput = {
  repo_path: string;
  build_command?: string;
  test_command?: string;
  static_analysis_command?: string;
  tool_timeout_s?: number;
};

export type PersonalToolResult<T = Record<string, unknown>> = {
  invocation_uid: string;
  tool: string;
  tool_name: string;
  status: string;
  error?: string;
  output: T;
  permission_snapshot?: Record<string, unknown>;
  side_effect_level?: string;
  risk_level?: string;
  dry_run?: boolean;
  confirmation?: string;
};

export type CodebaseIndexInput = {
  repo_path?: string;
  query?: string;
  max_files?: number;
  batch_size?: number;
  skip_dirs?: string[];
};

export type CodebaseSearchInput = {
  query: string;
  limit?: number;
};

export type SymbolLookupInput = {
  name: string;
  kind?: string;
  limit?: number;
};

export type IncludeImpactInput = {
  path: string;
};

export type CallGraphInput = {
  function_name: string;
  limit?: number;
};

export type MacroImpactInput = {
  macro_name: string;
  limit?: number;
};

export type TypeUsageInput = {
  type_name: string;
  limit?: number;
};

export type VariableUsageInput = {
  variable_name: string;
  limit?: number;
};

export type ImpactAnalyzeInput = {
  change_hint: string;
  limit?: number;
};

export type PatchDirectiveInput = {
  file_path: string;
  find: string;
  replace: string;
  description?: string;
};

export type PatchProposeInput = {
  change_text: string;
  session_uid?: string;
  target_symbol?: string;
  target_file?: string;
  directives: PatchDirectiveInput[];
  dry_run?: boolean;
};

export type PatchTextInput = {
  patch_text?: string;
  artifact_id?: number | null;
};

export type PatchApplyInput = PatchTextInput & {
  dry_run?: boolean;
  confirmed?: boolean;
  reviewer?: string;
  comment?: string;
};

export type ValidationRunInput = {
  command?: string;
  timeout_s?: number;
  confirmed?: boolean;
};

export type PersonalKnowledgeItem = {
  id: number;
  item_uid: string;
  project_id?: number;
  title: string;
  category: string;
  source_type: string;
  source_ref: string;
  content: string;
  tags?: string[];
  confidence?: number;
  status?: string;
  created_at?: string;
  updated_at?: string;
  excerpt?: string;
  score?: number;
};

export type PersonalKnowledgeSummary = {
  summary: Record<string, unknown>;
  items: PersonalKnowledgeItem[];
};

export type PersonalKnowledgeImportSourceInput = {
  source_uid: string;
};

export type PersonalKnowledgeSearchInput = {
  query: string;
  limit?: number;
};

export type PersonalLearningCandidate = {
  id: number;
  project_id?: number;
  title: string;
  problem: string;
  lesson: string;
  status: string;
  item_uid?: string;
  evidence_refs?: Record<string, unknown>;
  lesson_type?: string;
  expected_behavior?: string;
  anti_behavior?: string;
  validation_query?: string;
  created_at?: string;
  updated_at?: string;
  immediate_rule?: string;
};

export type PersonalLearningSummary = {
  memory?: { status: string; count: number }[];
  approved_lessons?: number;
  regression_cases?: number;
  recent?: PersonalLearningCandidate[];
};

export type PersonalLearningFeedbackInput = {
  feedback: string;
  session_uid?: string;
  corrected_behavior?: string;
  scope?: string;
  add_to_regression?: boolean;
};

export type PersonalLearningReviewInput = {
  reviewer?: string;
  comment?: string;
};

export type PersonalInboxItem = {
  kind: "learning_candidate" | "skill_update_candidate";
  id: number;
  item_uid?: string;
  created_at?: string;
  updated_at?: string;
  status?: string;
  title?: string;
  problem?: string;
  lesson?: string;
  evidence_refs?: Record<string, unknown>;
  lesson_type?: string;
  expected_behavior?: string;
  anti_behavior?: string;
  validation_query?: string;
  target_skill?: string;
  proposed_change?: string;
};
