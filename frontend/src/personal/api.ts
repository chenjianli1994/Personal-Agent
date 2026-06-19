import { apiDelete, apiGet, apiPost, apiPostForm, apiPut } from "../api/client";
import type {
  AgentLlmStatus,
  CodebaseIndexInput,
  CodebaseSearchInput,
  CallGraphInput,
  ImpactAnalyzeInput,
  IncludeImpactInput,
  MacroImpactInput,
  PatchApplyInput,
  PatchProposeInput,
  PatchTextInput,
  PersonalArtifactContent,
  PersonalArtifactDraft,
  PersonalArtifactDraftCreateInput,
  PersonalArtifactCodePatchInput,
  PersonalArtifactExport,
  PersonalArtifactExportInput,
  PersonalArtifactNaturalReviseInput,
  PersonalArtifactProposeInput,
  PersonalArtifactDraftReviseInput,
  PersonalArtifactUnitTestCodeInput,
  PersonalCodebaseConfig,
  PersonalCodebaseConfigInput,
  PersonalCapabilities,
  PersonalChatTurnInput,
  PersonalChatTurnResult,
  PersonalInputSource,
  PersonalKnowledgeImportSourceInput,
  PersonalKnowledgeItem,
  PersonalKnowledgeSearchInput,
  PersonalKnowledgeSummary,
  PersonalInboxItem,
  PersonalLearningCandidate,
  PersonalLearningFeedbackInput,
  PersonalLearningReviewInput,
  PersonalLearningSummary,
  PersonalLlmConfig,
  PersonalLlmConfigInput,
  PersonalSession,
  PersonalSessionRenameInput,
  PersonalSkill,
  PersonalSkillEval,
  PersonalSkillUpdateCandidate,
  PersonalSkillVersion,
  PersonalSourceTextInput,
  PersonalContext,
  PersonalToolResult,
  SymbolLookupInput,
  TypeUsageInput,
  VariableUsageInput,
  ValidationRunInput
} from "./types";

function params(path: string, input: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  Object.entries(input).forEach(([key, value]) => {
    if (value !== undefined && value !== "") search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export const personalAgentApi = {
  context: () => apiGet<PersonalContext>("/api/personal/context"),
  capabilities: () => apiGet<PersonalCapabilities>("/api/personal/capabilities"),
  sources: () => apiGet<PersonalInputSource[]>("/api/personal/sources"),
  source: (sourceUid: string) => apiGet<PersonalInputSource>(`/api/personal/sources/${encodeURIComponent(sourceUid)}`),
  createTextSource: (body: PersonalSourceTextInput) => apiPost<PersonalSourceTextInput, PersonalInputSource>("/api/personal/sources/text", body),
  uploadSource: (file: File, input?: { title?: string; make_active?: boolean }) => {
    const body = new FormData();
    body.append("file", file);
    if (input?.title) body.append("title", input.title);
    body.append("make_active", String(input?.make_active ?? true));
    return apiPostForm<PersonalInputSource>("/api/personal/sources/upload", body);
  },
  activateSource: (sourceUid: string) => apiPost<undefined, PersonalInputSource>(`/api/personal/sources/${encodeURIComponent(sourceUid)}/activate`),
  deleteSource: (sourceUid: string) => apiDelete<{ status: string; source_uid: string; active_source_uid: string }>(`/api/personal/sources/${encodeURIComponent(sourceUid)}`),
  skills: () => apiGet<PersonalSkill[]>("/api/personal/skills"),
  skill: (skillName: string) => apiGet<PersonalSkill>(`/api/personal/skills/${encodeURIComponent(skillName)}`),
  skillVersions: (skillName: string) => apiGet<PersonalSkillVersion[]>(`/api/personal/skills/${encodeURIComponent(skillName)}/versions`),
  evaluateSkill: (skillName: string) => apiPost<undefined, PersonalSkillEval>(`/api/personal/skills/${encodeURIComponent(skillName)}/evaluate`),
  skillUpdateCandidates: () => apiGet<PersonalSkillUpdateCandidate[]>("/api/personal/skills/update-candidates"),
  approveSkillUpdateCandidate: (candidateId: number, body: PersonalLearningReviewInput) =>
    apiPost<PersonalLearningReviewInput, PersonalSkillUpdateCandidate>(`/api/personal/skills/update-candidates/${candidateId}/approve`, body),
  rejectSkillUpdateCandidate: (candidateId: number, body: PersonalLearningReviewInput) =>
    apiPost<PersonalLearningReviewInput, PersonalSkillUpdateCandidate>(`/api/personal/skills/update-candidates/${candidateId}/reject`, body),
  draftList: (sessionUid?: string) => apiGet<PersonalArtifactDraft[]>(params("/api/personal/drafts", { session_uid: sessionUid })),
  draftDetail: (draftUid: string) => apiGet<PersonalArtifactDraft>(`/api/personal/drafts/${encodeURIComponent(draftUid)}`),
  draftContent: (draftUid: string, revisionIndex?: number) =>
    apiGet<PersonalArtifactContent>(params(`/api/personal/drafts/${encodeURIComponent(draftUid)}/content`, { revision_index: revisionIndex })),
  createDraft: (body: PersonalArtifactDraftCreateInput) =>
    apiPost<PersonalArtifactDraftCreateInput, PersonalArtifactDraft>("/api/personal/drafts", body),
  proposeDocumentDraft: (body: PersonalArtifactProposeInput) =>
    apiPost<PersonalArtifactProposeInput, PersonalArtifactDraft>("/api/personal/documents/propose", body),
  proposeCodePatchDraft: (body: PersonalArtifactCodePatchInput) =>
    apiPost<PersonalArtifactCodePatchInput, PersonalArtifactDraft>("/api/personal/drafts/code-patch", body),
  proposeUnitTestCodeDraft: (body: PersonalArtifactUnitTestCodeInput) =>
    apiPost<PersonalArtifactUnitTestCodeInput, PersonalArtifactDraft>("/api/personal/drafts/unit-test-code", body),
  reviseDraft: (draftUid: string, body: PersonalArtifactNaturalReviseInput) =>
    apiPost<PersonalArtifactNaturalReviseInput, PersonalArtifactDraft>(`/api/personal/drafts/${encodeURIComponent(draftUid)}/revise`, body),
  reviseDraftManual: (draftUid: string, body: PersonalArtifactDraftReviseInput) =>
    apiPost<PersonalArtifactDraftReviseInput, PersonalArtifactDraft>(`/api/personal/drafts/${encodeURIComponent(draftUid)}/revise-manual`, body),
  activateDraft: (draftUid: string) =>
    apiPost<undefined, PersonalArtifactDraft>(`/api/personal/drafts/${encodeURIComponent(draftUid)}/activate`),
  exportDraft: (draftUid: string, body: PersonalArtifactExportInput) =>
    apiPost<PersonalArtifactExportInput, PersonalArtifactExport>(`/api/personal/drafts/${encodeURIComponent(draftUid)}/export`, body),
  draftDownloadUrl: (draftUid: string, format?: string) =>
    params(`/api/personal/drafts/${encodeURIComponent(draftUid)}/download`, { format }),
  llmConfig: () => apiGet<PersonalLlmConfig>("/api/personal/llm-config"),
  saveLlmConfig: (body: PersonalLlmConfigInput) => apiPut<PersonalLlmConfigInput, PersonalLlmConfig>("/api/personal/llm-config", body),
  llmStatus: () => apiGet<AgentLlmStatus>("/api/personal/llm-status"),
  sessions: () => apiGet<PersonalSession[]>("/api/personal/sessions"),
  session: (sessionUid: string) => apiGet<PersonalSession>(`/api/personal/sessions/${encodeURIComponent(sessionUid)}`),
  renameSession: (sessionUid: string, body: PersonalSessionRenameInput) =>
    apiPut<PersonalSessionRenameInput, PersonalSession>(`/api/personal/sessions/${encodeURIComponent(sessionUid)}/title`, body),
  deleteSession: (sessionUid: string) => apiDelete<{ status: string; session_uid: string }>(`/api/personal/sessions/${encodeURIComponent(sessionUid)}`),
  chatTurn: (body: PersonalChatTurnInput) => apiPost<PersonalChatTurnInput, PersonalChatTurnResult>("/api/personal/chat/turn", body),
  codebaseConfig: () => apiGet<PersonalCodebaseConfig>("/api/personal/codebase/config"),
  saveCodebaseConfig: (body: PersonalCodebaseConfigInput) => apiPut<PersonalCodebaseConfigInput, PersonalCodebaseConfig>("/api/personal/codebase/config", body),
  codebaseIndex: (body: CodebaseIndexInput) => apiPost<CodebaseIndexInput, PersonalToolResult>("/api/personal/codebase/index", body),
  codebaseSearch: (body: CodebaseSearchInput) => apiPost<CodebaseSearchInput, PersonalToolResult>("/api/personal/codebase/search", body),
  symbolLookup: (body: SymbolLookupInput) => apiPost<SymbolLookupInput, PersonalToolResult>("/api/personal/codebase/symbols", body),
  includeImpact: (body: IncludeImpactInput) => apiPost<IncludeImpactInput, PersonalToolResult>("/api/personal/codebase/include-impact", body),
  callGraph: (body: CallGraphInput) => apiPost<CallGraphInput, PersonalToolResult>("/api/personal/codebase/call-graph", body),
  macroImpact: (body: MacroImpactInput) => apiPost<MacroImpactInput, PersonalToolResult>("/api/personal/codebase/macro-impact", body),
  typeUsage: (body: TypeUsageInput) => apiPost<TypeUsageInput, PersonalToolResult>("/api/personal/codebase/type-usage", body),
  variableUsage: (body: VariableUsageInput) => apiPost<VariableUsageInput, PersonalToolResult>("/api/personal/codebase/variable-usage", body),
  impactAnalyze: (body: ImpactAnalyzeInput) => apiPost<ImpactAnalyzeInput, PersonalToolResult>("/api/personal/codebase/impact", body),
  styleProfile: () => apiPost<undefined, PersonalToolResult>("/api/personal/codebase/style"),
  patchPropose: (body: PatchProposeInput) => apiPost<PatchProposeInput, PersonalToolResult>("/api/personal/patch/propose", body),
  patchValidate: (body: PatchTextInput) => apiPost<PatchTextInput, PersonalToolResult>("/api/personal/patch/validate", body),
  patchApply: (body: PatchApplyInput) => apiPost<PatchApplyInput, PersonalToolResult>("/api/personal/patch/apply", body),
  validationRun: (kind: "build" | "tests" | "static-analysis", body: ValidationRunInput) =>
    apiPost<ValidationRunInput, PersonalToolResult>(`/api/personal/validation/${kind}`, body),
  knowledge: () => apiGet<PersonalKnowledgeSummary>("/api/personal/knowledge"),
  importSourceToKnowledge: (body: PersonalKnowledgeImportSourceInput) =>
    apiPost<PersonalKnowledgeImportSourceInput, Record<string, unknown>>("/api/personal/knowledge/import-source", body),
  searchKnowledge: (body: PersonalKnowledgeSearchInput) =>
    apiPost<PersonalKnowledgeSearchInput, PersonalKnowledgeItem[]>("/api/personal/knowledge/search", body),
  deprecateKnowledge: (knowledgeId: number, body: PersonalLearningReviewInput) =>
    apiPost<PersonalLearningReviewInput, PersonalKnowledgeItem>(`/api/personal/knowledge/${knowledgeId}/deprecate`, body),
  learningSummary: () => apiGet<PersonalLearningSummary>("/api/personal/learning/summary"),
  learningCandidates: () => apiGet<PersonalLearningCandidate[]>("/api/personal/learning/candidates"),
  inbox: () => apiGet<PersonalInboxItem[]>("/api/personal/inbox"),
  createLearningFeedback: (body: PersonalLearningFeedbackInput) =>
    apiPost<PersonalLearningFeedbackInput, PersonalLearningCandidate>("/api/personal/learning/feedback", body),
  approveLearningCandidate: (candidateId: number, body: PersonalLearningReviewInput) =>
    apiPost<PersonalLearningReviewInput, PersonalLearningCandidate>(`/api/personal/learning/candidates/${candidateId}/approve`, body),
  rejectLearningCandidate: (candidateId: number, body: PersonalLearningReviewInput) =>
    apiPost<PersonalLearningReviewInput, PersonalLearningCandidate>(`/api/personal/learning/candidates/${candidateId}/reject`, body),
  dismissMemoryLesson: (itemUid: string, body: PersonalLearningReviewInput) =>
    apiPost<PersonalLearningReviewInput, PersonalKnowledgeItem>(`/api/personal/learning/${encodeURIComponent(itemUid)}/dismiss`, body)
};
