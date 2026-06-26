import {
  Alert,
  App,
  AutoComplete,
  Button,
  Checkbox,
  Divider,
  Drawer,
  Dropdown,
  Empty,
  Input,
  InputNumber,
  Layout,
  List,
  Modal,
  Progress,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload
} from "antd";
import { BookOutlined, BranchesOutlined, BulbOutlined, CheckCircleOutlined, CloudDownloadOutlined, CodeOutlined, CopyOutlined, DeleteOutlined, DiffOutlined, EditOutlined, ExperimentOutlined, FileDoneOutlined, FileProtectOutlined, FileTextOutlined, HistoryOutlined, MoreOutlined, PlayCircleOutlined, ReloadOutlined, RobotOutlined, SearchOutlined, ThunderboltOutlined, ToolOutlined, UploadOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { personalAgentApi } from "./api";
import { ApiError } from "../api/client";
import { ChatPanel } from "./ChatPanel";
import type { ComposerAttachment, LocalMessage } from "./ChatPanel";
import { DiffView } from "./DiffView";
import { MarkdownMessage } from "./MarkdownMessage";
import {
  ACCEPTED_UPLOAD_ACCEPT,
  ACCEPTED_UPLOAD_SET,
  EMPTY_CHAT_DESCRIPTION,
  MAX_COMPOSER_ATTACHMENTS,
  MAX_UPLOAD_SIZE,
  PENDING_MESSAGE,
  UPLOAD_HINT,
  friendlyUploadError,
  formatUploadLimitMb,
  toMessageAttachments,
} from "./constants";
import { useThemeMode } from "./theme";
import type {
  AgentLlmStatus,
  CodebaseIndexStreamEvent,
  PatchDirectiveInput,
  PendingStateKey,
  PendingVisualState,
  PersonalArtifactDraft,
  PersonalArtifactDraftManagementImpact,
  PersonalCodebaseConfig,
  PersonalDevTask,
  PersonalInputSource,
  PersonalInboxItem,
  PersonalKnowledgeItem,
  PersonalLearningCandidate,
  PersonalChatTurnResult,
  PersonalLlmConfig,
  PersonalLlmConfigInput,
  PersonalSession,
  PersonalSkill,
  PersonalSkillUpdateCandidate,
  PersonalToolResult
} from "./types";
import "../styles/personal-agent.css";

type DraftReviewTab = "preview" | "revise" | "versions" | "quality";
type DraftFilterMode = "all" | "session" | "task" | "unlinked" | "trash";
const NEW_SESSION_KEY = "__new_session__";

type ChatTurnVariables = {
  body: Parameters<typeof personalAgentApi.chatTurn>[0];
  sessionKey: string;
  signal: AbortSignal;
  restoreDraft?: string;
  clearAttachmentsOnSuccess?: boolean;
};

type TurnApplyOptions = Pick<ChatTurnVariables, "clearAttachmentsOnSuccess" | "sessionKey">;

function pendingGenerateLabel(intent?: string) {
  switch (intent) {
    case "answer_only":
      return "正在组织回答";
    case "analyze_input_source":
      return "正在分析输入材料";
    case "generate_document":
      return "正在生成文档草稿";
    case "revise_draft":
      return "正在修订当前草稿";
    case "propose_code_patch":
      return "正在生成代码修改候选";
    case "run_validation":
      return "正在准备验证执行";
    case "learn_feedback":
      return "正在处理学习反馈";
    default:
      return "正在生成回应";
  }
}

function buildPendingState(key: PendingStateKey, intent?: string): PendingVisualState {
  if (key === "generate") {
    return {
      key,
      intent,
      title: pendingGenerateLabel(intent),
    };
  }
  if (key === "route") {
    return { key, title: "正在理解意图…" };
  }
  if (key === "reflect") {
    return { key, title: "正在沉淀经验…" };
  }
  return { key: "initial", title: "正在思考" };
}

function createPendingAssistantMessage(createdAt: string): LocalMessage {
  return {
    role: "assistant",
    content: PENDING_MESSAGE,
    created_at: createdAt,
    pending: true,
    metadata: {
      pending_state: buildPendingState("initial"),
    },
  };
}

export function PersonalAgentApp() {
  const { message, modal } = App.useApp();
  const { mode } = useThemeMode();
  const showError = useMutationErrorHandler();
  const queryClient = useQueryClient();
  const [selectedSessionUid, setSelectedSessionUid] = useState<string>();
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [attachmentsUploading, setAttachmentsUploading] = useState(false);
  const [inputHistory, setInputHistory] = useState<string[]>([]);
  const [inputHistoryIndex, setInputHistoryIndex] = useState<number | null>(null);
  const [optimisticBySession, setOptimisticBySession] = useState<Record<string, LocalMessage[]>>({});
  const [localErrorBySession, setLocalErrorBySession] = useState<Record<string, string>>({});
  const [pendingChatSessionKey, setPendingChatSessionKey] = useState<string>();
  const [llmSettingsOpen, setLlmSettingsOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [draftsOpen, setDraftsOpen] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [learningOpen, setLearningOpen] = useState(false);
  const [codebaseOpen, setCodebaseOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [draftToOpenUid, setDraftToOpenUid] = useState<string>();
  const [draftPanelTab, setDraftPanelTab] = useState<"create" | "current">("create");
  const [draftFilterMode, setDraftFilterMode] = useState<DraftFilterMode>("session");
  const [draftFilterTaskUid, setDraftFilterTaskUid] = useState<string>();
  const [focusedTaskUid, setFocusedTaskUid] = useState<string>();
  const [renamingSession, setRenamingSession] = useState<PersonalSession>();
  const [renameTitle, setRenameTitle] = useState("");
  const [typewriterKey, setTypewriterKey] = useState<string>();
  const [animationVersion, setAnimationVersion] = useState(0);
  const hasAutoSelected = useRef(false);
  const chatAbortControllerRef = useRef<AbortController | null>(null);

  const contextQuery = useQuery({ queryKey: ["personal-context"], queryFn: personalAgentApi.context, retry: false });
  const llmConfigQuery = useQuery({ queryKey: ["personal-llm-config"], queryFn: personalAgentApi.llmConfig, retry: false, refetchInterval: 15000 });
  const llmStatusQuery = useQuery({ queryKey: ["personal-llm-status"], queryFn: personalAgentApi.llmStatus, retry: false, refetchInterval: 15000 });
  const codebaseConfigQuery = useQuery({ queryKey: ["personal-codebase-config"], queryFn: personalAgentApi.codebaseConfig, retry: false });
  const sessionsQuery = useQuery({ queryKey: ["personal-sessions"], queryFn: personalAgentApi.sessions, retry: false, refetchInterval: 10000 });
  const inboxBadgeQuery = useQuery({ queryKey: ["personal-inbox"], queryFn: personalAgentApi.inbox, retry: false, refetchInterval: 30000 });
  const selectedSessionQuery = useQuery({
    queryKey: ["personal-session", selectedSessionUid],
    queryFn: () => personalAgentApi.session(selectedSessionUid!),
    enabled: Boolean(selectedSessionUid),
    retry: false
  });
  const currentTaskQuery = useQuery({
    queryKey: ["personal-dev-task-current", selectedSessionUid],
    enabled: Boolean(selectedSessionUid),
    retry: false,
    refetchInterval: 10000,
    queryFn: async () => {
      const tasks = await personalAgentApi.devTaskList(selectedSessionUid);
      return pickCurrentTask(tasks);
    }
  });

  const saveLlmConfig = useMutation({
    mutationFn: personalAgentApi.saveLlmConfig,
    onSuccess: (result) => {
      queryClient.setQueryData(["personal-llm-config"], result);
      queryClient.setQueryData(["personal-llm-status"], result.status);
      message.success("LLM 配置已保存。");
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : String(error));
    }
  });

  const clearTypewriterAnimation = () => {
    setTypewriterKey(undefined);
    setAnimationVersion((value) => value + 1);
  };

  const applyTurnResult = (result: PersonalChatTurnResult, options: TurnApplyOptions) => {
    setSelectedSessionUid(result.session.session_uid);
    setOptimisticBySession((items) => omitKey(items, options.sessionKey));
    setLocalErrorBySession((items) => omitKey(items, options.sessionKey));
    setPendingChatSessionKey((value) => (value === options.sessionKey ? undefined : value));
    chatAbortControllerRef.current = null;
    if (options.clearAttachmentsOnSuccess) setAttachments([]);
    queryClient.setQueryData(["personal-session", result.session.session_uid], result.session);
    queryClient.invalidateQueries({ queryKey: ["personal-sessions"] });
    queryClient.invalidateQueries({ queryKey: ["personal-drafts"] });
    queryClient.invalidateQueries({ queryKey: ["personal-dev-task-current"] });
    const learningReflection = result.message?.metadata?.learning_reflection as { candidate_id?: number | null } | undefined;
    if (learningReflection?.candidate_id) {
      queryClient.invalidateQueries({ queryKey: ["personal-learning-summary"] });
      queryClient.invalidateQueries({ queryKey: ["personal-learning-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["personal-knowledge"] });
    }
    setTypewriterKey(result.message?.message_uid);
  };

  const chatTurn = useMutation({
    mutationFn: ({ body, signal }: ChatTurnVariables) => personalAgentApi.chatTurn(body, { signal }),
    onSuccess: (result, variables) => {
      applyTurnResult(result, variables);
    },
    onError: (error, variables) => {
      setPendingChatSessionKey((value) => (value === variables.sessionKey ? undefined : value));
      chatAbortControllerRef.current = null;
      if (isAbortError(error)) {
        if (variables.restoreDraft) setDraft(variables.restoreDraft);
        setOptimisticBySession((items) => omitKey(items, variables.sessionKey));
        setLocalErrorBySession((items) => ({ ...items, [variables.sessionKey]: "已停止本次回复。" }));
        return;
      }
      const text = error instanceof Error ? error.message : String(error);
      if (variables.restoreDraft) setDraft(variables.restoreDraft);
      setLocalErrorBySession((items) => ({ ...items, [variables.sessionKey]: text }));
      message.error(text);
      setOptimisticBySession((items) => ({
        ...items,
        [variables.sessionKey]: (items[variables.sessionKey] ?? []).map((item) =>
          item.pending ? { ...item, content: `发送失败：${text}`, pending: false } : item
        )
      }));
    }
  });
  const openDraftFile = useMutation({
    mutationFn: (draftUid: string) => personalAgentApi.openDraft(draftUid, {}),
    onSuccess: (result) => {
      message.success(`已打开 ${result.file_name}。`);
    },
    onError: showError
  });

  const renameSession = useMutation({
    mutationFn: ({ sessionUid, title }: { sessionUid: string; title: string }) => personalAgentApi.renameSession(sessionUid, { title }),
    onSuccess: (session) => {
      queryClient.setQueryData(["personal-session", session.session_uid], session);
      queryClient.setQueryData(["personal-sessions"], (items: PersonalSession[] | undefined) =>
        (items ?? []).map((item) => (item.session_uid === session.session_uid ? { ...item, title: session.title } : item))
      );
      setRenamingSession(undefined);
      setRenameTitle("");
    },
    onError: (error) => message.error(error instanceof Error ? error.message : String(error))
  });

  const deleteSession = useMutation({
    mutationFn: personalAgentApi.deleteSession,
    onSuccess: (result) => {
      const remaining = (sessionsQuery.data ?? []).filter((item) => item.session_uid !== result.session_uid);
      queryClient.setQueryData(["personal-sessions"], remaining);
      queryClient.removeQueries({ queryKey: ["personal-session", result.session_uid] });
      setOptimisticBySession((items) => omitKey(items, result.session_uid));
      setLocalErrorBySession((items) => omitKey(items, result.session_uid));
      if (pendingChatSessionKey === result.session_uid) chatAbortControllerRef.current?.abort();
      if (selectedSessionUid === result.session_uid) {
        setSelectedSessionUid(remaining[0]?.session_uid);
      }
    },
    onError: (error) => message.error(error instanceof Error ? error.message : String(error))
  });

  const deleteSessions = useMutation({
    mutationFn: async (sessionUids: string[]) => Promise.all(sessionUids.map((sessionUid) => personalAgentApi.deleteSession(sessionUid))),
    onSuccess: (_results, sessionUids) => {
      const deleted = new Set(sessionUids);
      const remaining = (sessionsQuery.data ?? []).filter((item) => !deleted.has(item.session_uid));
      queryClient.setQueryData(["personal-sessions"], remaining);
      sessionUids.forEach((sessionUid) => queryClient.removeQueries({ queryKey: ["personal-session", sessionUid] }));
      setOptimisticBySession((items) => omitKeys(items, sessionUids));
      setLocalErrorBySession((items) => omitKeys(items, sessionUids));
      if (pendingChatSessionKey && deleted.has(pendingChatSessionKey)) chatAbortControllerRef.current?.abort();
      if (selectedSessionUid && deleted.has(selectedSessionUid)) {
        setSelectedSessionUid(remaining[0]?.session_uid);
      }
    },
    onError: (error) => message.error(error instanceof Error ? error.message : String(error))
  });

  const sessions = sessionsQuery.data ?? [];
  const selectedSession = selectedSessionQuery.data ?? sessions.find((session) => session.session_uid === selectedSessionUid);
  const currentTask = currentTaskQuery.data as PersonalDevTask | undefined;
  const inboxBadge = inboxBadgeQuery.data ?? [];
  const learningBadgeCount = inboxBadge.filter((item) => item.kind === "learning_candidate" && item.status === "candidate").length;
  const skillsBadgeCount = inboxBadge.filter((item) => item.kind === "skill_update_candidate" && item.status === "candidate").length;
  const activeSessionKey = selectedSessionUid ?? NEW_SESSION_KEY;
  const optimistic = optimisticBySession[activeSessionKey] ?? [];
  const localError = localErrorBySession[activeSessionKey] ?? "";
  const sending = pendingChatSessionKey === activeSessionKey;
  const sendDisabled = Boolean(pendingChatSessionKey && pendingChatSessionKey !== activeSessionKey);

  useEffect(() => {
    if (!hasAutoSelected.current && !selectedSessionUid && sessions[0]) {
      hasAutoSelected.current = true;
      setSelectedSessionUid(sessions[0].session_uid);
    }
  }, [selectedSessionUid, sessions]);

  const addAttachments = (files: File[]) => {
    if (!files.length) return;
    const accepted: ComposerAttachment[] = [];
    const existingKeys = new Set(attachments.map((item) => `${item.file.name}:${item.file.size}`));
    const batchKeys = new Set<string>();
    for (const file of files) {
      const dedupeKey = `${file.name}:${file.size}`;
      if (existingKeys.has(dedupeKey) || batchKeys.has(dedupeKey)) {
        message.info(`已添加该文件：${file.name}`);
        continue;
      }
      const extension = file.name.split(".").pop()?.toLowerCase() || "";
      if (!ACCEPTED_UPLOAD_SET.has(extension)) {
        message.warning(`不支持的文件类型：${file.name}`);
        continue;
      }
      if (file.size > MAX_UPLOAD_SIZE) {
        message.warning(`文件过大：${file.name}（上限 ${formatUploadLimitMb()}MB）`);
        continue;
      }
      batchKeys.add(dedupeKey);
      accepted.push({ id: `${file.name}-${file.lastModified}-${file.size}-${crypto.randomUUID()}`, file, status: "ready" });
    }
    if (!accepted.length) return;
    setAttachments((items) => {
      const remaining = Math.max(0, MAX_COMPOSER_ATTACHMENTS - items.length);
      if (accepted.length > remaining) {
        message.warning(`一次最多添加 ${MAX_COMPOSER_ATTACHMENTS} 个文件。`);
      }
      return [...items, ...accepted.slice(0, remaining)];
    });
  };

  const removeAttachment = (id: string) => {
    setAttachments((items) => items.filter((item) => item.id !== id));
  };

  const updatePendingAssistant = (sessionKey: string, pendingState: PendingVisualState) => {
    setOptimisticBySession((items) => ({
      ...items,
      [sessionKey]: (items[sessionKey] ?? []).map((item) =>
        item.pending && item.role !== "user"
          ? {
            ...item,
            content: pendingState.title,
            metadata: {
              ...(item.metadata ?? {}),
              pending_state: pendingState,
            },
          }
          : item
      ),
    }));
  };

  const startChatTurn = ({
    content,
    sourceUids = [],
    optimisticMessages,
    restoreDraft,
    clearAttachmentsOnSuccess = false,
    sessionUidOverride,
  }: {
    content: string;
    sourceUids?: string[];
    optimisticMessages?: LocalMessage[];
    restoreDraft?: string;
    clearAttachmentsOnSuccess?: boolean;
    sessionUidOverride?: string | null;
  }) => {
    const targetSessionUid = sessionUidOverride ?? selectedSessionUid;
    const sessionKey = targetSessionUid ?? NEW_SESSION_KEY;
    if (pendingChatSessionKey || attachmentsUploading) return false;
    clearTypewriterAnimation();
    const controller = new AbortController();
    const body = { session_uid: targetSessionUid, content, source_uids: sourceUids };
    chatAbortControllerRef.current = controller;
    setPendingChatSessionKey(sessionKey);
    setLocalErrorBySession((items) => omitKey(items, sessionKey));
    if (optimisticMessages) {
      setOptimisticBySession((items) => ({ ...items, [sessionKey]: optimisticMessages }));
    }
    let receivedStreamEvent = false;
    let handledDone = false;
    let streamedSessionUid = "";
    const finishStreamFailure = (text: string, aborted = false) => {
      const errorSessionKey = streamedSessionUid || sessionKey;
      setPendingChatSessionKey((value) => (value === sessionKey ? undefined : value));
      chatAbortControllerRef.current = null;
      if (restoreDraft) setDraft(restoreDraft);
      setOptimisticBySession((items) => omitKey(items, sessionKey));
      setLocalErrorBySession((items) => ({ ...items, [errorSessionKey]: text }));
      if (streamedSessionUid) {
        setSelectedSessionUid(streamedSessionUid);
        queryClient.invalidateQueries({ queryKey: ["personal-session", streamedSessionUid] });
      }
      queryClient.invalidateQueries({ queryKey: ["personal-sessions"] });
      if (!aborted) message.error(text);
    };
    void personalAgentApi.chatTurnStream(
      body,
      (event) => {
        receivedStreamEvent = true;
        if (typeof event?.session_uid === "string" && event.session_uid) {
          streamedSessionUid = event.session_uid;
        }
        if (event?.stage === "route") {
          updatePendingAssistant(sessionKey, buildPendingState("route"));
          return;
        }
        if (event?.stage === "generate") {
          updatePendingAssistant(sessionKey, buildPendingState("generate", typeof event.intent === "string" ? event.intent : ""));
          return;
        }
        if (event?.stage === "reflect") {
          updatePendingAssistant(sessionKey, buildPendingState("reflect"));
          return;
        }
        if (event?.stage === "done") {
          handledDone = true;
          applyTurnResult(event.payload, { sessionKey, clearAttachmentsOnSuccess });
          return;
        }
        if (event?.stage === "error") {
          throw new Error(String(event.error || "stream failed"));
        }
      },
      { signal: controller.signal },
    ).catch((error) => {
      if (!receivedStreamEvent) {
        chatTurn.mutate({
          body,
          sessionKey,
          signal: controller.signal,
          restoreDraft,
          clearAttachmentsOnSuccess,
        });
        return;
      }
      if (isAbortError(error)) {
        finishStreamFailure("已停止本次回复。", true);
        return;
      }
      if (handledDone) return;
      finishStreamFailure(error instanceof Error ? error.message : String(error));
    });
    return true;
  };

  const cancelChatTurn = () => {
    if (!pendingChatSessionKey) return;
    chatAbortControllerRef.current?.abort();
  };

  const continueDevTaskFromPanel = (task: PersonalDevTask) => {
    if (pendingChatSessionKey || attachmentsUploading) return;
    setSelectedSessionUid(task.session_uid);
    setFocusedTaskUid(task.task_uid);
    startChatTurn({
      sessionUidOverride: task.session_uid,
      content: "继续",
      optimisticMessages: [createPendingAssistantMessage(new Date().toISOString())],
    });
  };

  const continueDevTaskFromDraft = (taskUid: string, sessionUid?: string) => {
    if (!taskUid || pendingChatSessionKey || attachmentsUploading) return;
    if (sessionUid) setSelectedSessionUid(sessionUid);
    setFocusedTaskUid(taskUid);
    startChatTurn({
      sessionUidOverride: sessionUid || selectedSessionUid,
      content: "继续",
      optimisticMessages: [createPendingAssistantMessage(new Date().toISOString())],
    });
  };

  const regenerate = () => {
    if (pendingChatSessionKey || attachmentsUploading) return;
    const messages = selectedSession?.messages ?? [];
    const lastUser = [...messages].reverse().find((item) => item.role === "user");
    if (!lastUser) return;
    clearTypewriterAnimation();
    const now = new Date().toISOString();
    const sourceUids = toMessageAttachments(lastUser.metadata?.attachments)
      .map((attachment) => attachment.source_uid || "")
      .filter(Boolean);
    startChatTurn({
      content: lastUser.content,
      sourceUids,
      optimisticMessages: [createPendingAssistantMessage(now)],
      restoreDraft: draft,
    });
  };

  const send = async () => {
    const content = draft.trim();
    if (!content) {
      if (attachments.length) message.warning("请输入对附件的分析指令。");
      return;
    }
    if (pendingChatSessionKey || attachmentsUploading) return;
    const now = new Date().toISOString();
    setInputHistory((items) => [content, ...items.filter((item) => item !== content)].slice(0, 50));
    setInputHistoryIndex(null);
    setDraft("");
    setLocalErrorBySession((items) => omitKey(items, activeSessionKey));
    clearTypewriterAnimation();
    let sourceUids: string[] = [];
    if (attachments.length) {
      setAttachmentsUploading(true);
      try {
        const results = await Promise.allSettled(attachments.map(async (attachment) => {
          if (attachment.status === "uploaded" && attachment.sourceUid) {
            return attachment.sourceUid;
          }
          setAttachments((items) => items.map((item) => (item.id === attachment.id ? { ...item, status: "uploading", error: undefined, progress: 0 } : item)));
          try {
            const source = await personalAgentApi.uploadSource(attachment.file, {
              make_active: false,
              onProgress: (progress) => {
                setAttachments((items) => items.map((item) => (item.id === attachment.id ? { ...item, status: "uploading", progress } : item)));
              },
            });
            setAttachments((items) => items.map((item) => (item.id === attachment.id ? { ...item, status: "uploaded", sourceUid: source.source_uid, progress: 100 } : item)));
            return source.source_uid;
          } catch (error) {
            const text = friendlyUploadError(error instanceof Error ? error.message : String(error), error);
            setAttachments((items) => items.map((item) => (item.id === attachment.id ? { ...item, status: "error", error: text, progress: undefined } : item)));
            throw new Error(text);
          }
        }));
        const rejected = results.find((result): result is PromiseRejectedResult => result.status === "rejected");
        if (rejected) {
          throw rejected.reason;
        }
        sourceUids = results
          .filter((result): result is PromiseFulfilledResult<string> => result.status === "fulfilled")
          .map((result) => result.value);
      } catch (error) {
        const text = friendlyUploadError(error instanceof Error ? error.message : String(error), error);
        setLocalErrorBySession((items) => ({ ...items, [activeSessionKey]: text }));
        message.error(text);
        setDraft(content);
        setAttachmentsUploading(false);
        return;
      }
      setAttachmentsUploading(false);
    }
    const optimisticAttachments = attachments.map((item) => ({
      source_uid: item.sourceUid || "",
      title: item.file.name.replace(/\.[^.]+$/, "") || item.file.name,
      source_type: item.file.name.split(".").pop()?.toLowerCase() || "file",
      original_name: item.file.name,
    }));
    startChatTurn({
      content,
      sourceUids,
      optimisticMessages: [
        { role: "user", content, created_at: now, metadata: optimisticAttachments.length ? { attachments: optimisticAttachments } : {} },
        createPendingAssistantMessage(now)
      ],
      restoreDraft: content,
      clearAttachmentsOnSuccess: true
    });
    queryClient.invalidateQueries({ queryKey: ["personal-sources"] });
  };

  const loading = contextQuery.isLoading || llmConfigQuery.isLoading || llmStatusQuery.isLoading || sessionsQuery.isLoading;
  const error = contextQuery.error ?? llmConfigQuery.error ?? llmStatusQuery.error ?? sessionsQuery.error;

  if (loading) {
    return <div className="personal-agent-boot"><Spin size="large" /></div>;
  }
  if (error) {
    return <Alert type="error" message="个人 Agent 启动失败" description={String(error)} />;
  }
  return (
    <Layout className="personal-agent-shell">
      <Layout.Sider width={300} theme={mode} className="personal-agent-sidebar">
        <Sidebar
          sessions={sessions}
          selectedSessionUid={selectedSessionUid}
          onSelect={(sessionUid) => {
            clearTypewriterAnimation();
            setSelectedSessionUid(sessionUid);
            setDraftFilterTaskUid(undefined);
            setDraftFilterMode("session");
          }}
          onRename={(session) => {
            setRenamingSession(session);
            setRenameTitle(session.title || "");
          }}
          onDelete={(session) => {
            modal.confirm({
              title: "删除会话",
              content: `确定删除“${session.title || "Agent 会话"}”？`,
              okText: "删除",
              okButtonProps: { danger: true },
              cancelText: "取消",
              onOk: () => deleteSession.mutate(session.session_uid)
            });
          }}
          onBulkDelete={(sessionUids, onDeleted) => {
            modal.confirm({
              title: "批量删除会话",
              content: `确定删除选中的 ${sessionUids.length} 个会话？`,
              okText: "删除",
              okButtonProps: { danger: true },
              cancelText: "取消",
              onOk: () => deleteSessions.mutateAsync(sessionUids).then(() => onDeleted())
            });
          }}
          bulkDeleting={deleteSessions.isPending}
          onNew={() => {
            clearTypewriterAnimation();
            setSelectedSessionUid(undefined);
            setDraftFilterTaskUid(undefined);
            setDraftFilterMode("all");
            setLocalErrorBySession((items) => omitKey(items, NEW_SESSION_KEY));
            setOptimisticBySession((items) => omitKey(items, NEW_SESSION_KEY));
          }}
        />
      </Layout.Sider>
      <Layout.Content className="personal-agent-main">
        <ChatPanel
          session={selectedSession}
          optimistic={optimistic}
          draft={draft}
          setDraft={setDraft}
          attachments={attachments}
          attachmentsUploading={attachmentsUploading}
          onAddAttachments={addAttachments}
          onRemoveAttachment={removeAttachment}
          inputHistory={inputHistory}
          inputHistoryIndex={inputHistoryIndex}
          setInputHistoryIndex={setInputHistoryIndex}
          onSend={send}
          onRetry={send}
          onRegenerate={regenerate}
          sending={sending}
          sendDisabled={sendDisabled}
          onCancelSend={cancelChatTurn}
          localError={localError}
          llmStatus={llmStatusQuery.data}
          currentTask={currentTask}
          onOpenLlmSettings={() => setLlmSettingsOpen(true)}
          onOpenSources={() => setSourcesOpen(true)}
          onOpenDrafts={(draftUid) => {
            setDraftToOpenUid(draftUid);
            setDraftPanelTab("current");
            setDraftFilterTaskUid(undefined);
            setDraftFilterMode(selectedSessionUid ? "session" : "all");
            setDraftsOpen(true);
          }}
          onOpenDraftFile={(draftUid) => openDraftFile.mutate(draftUid)}
          onOpenTasks={() => {
            setFocusedTaskUid(currentTask?.task_uid);
            setTasksOpen(true);
          }}
          onContinueTask={() => {
            if (!selectedSessionUid) return;
            startChatTurn({ content: "继续" });
          }}
          onOpenKnowledge={() => setKnowledgeOpen(true)}
          onOpenLearning={() => setLearningOpen(true)}
          onOpenCodebase={() => setCodebaseOpen(true)}
          onOpenSkills={() => setSkillsOpen(true)}
          learningBadgeCount={learningBadgeCount}
          skillsBadgeCount={skillsBadgeCount}
          typewriterKey={typewriterKey}
          animationVersion={animationVersion}
        />
      </Layout.Content>
      <Drawer title="输入材料" open={sourcesOpen} onClose={() => setSourcesOpen(false)} width={760}>
        <SourcesPanel />
      </Drawer>
      <Drawer
        title="当前草稿"
        open={draftsOpen}
        onClose={() => {
          setDraftsOpen(false);
          setDraftToOpenUid(undefined);
        }}
        width={860}
      >
        <ArtifactDraftsPanel
          openDraftUid={draftToOpenUid}
          activeTab={draftPanelTab}
          onTabChange={setDraftPanelTab}
          selectedSessionUid={selectedSessionUid}
          currentTask={currentTask}
          filterMode={draftFilterMode}
          onFilterModeChange={setDraftFilterMode}
          filterTaskUid={draftFilterTaskUid}
          onFilterTaskUidChange={setDraftFilterTaskUid}
          onOpenTask={(taskUid, sessionUid) => {
            if (sessionUid) setSelectedSessionUid(sessionUid);
            setFocusedTaskUid(taskUid);
            setTasksOpen(true);
          }}
          onContinueTask={(taskUid, sessionUid) => continueDevTaskFromDraft(taskUid, sessionUid)}
          onFocusTaskDrafts={(taskUid) => {
            setDraftFilterTaskUid(taskUid);
            setDraftFilterMode(taskUid ? "task" : selectedSessionUid ? "session" : "all");
          }}
        />
      </Drawer>
      <Drawer title="任务" open={tasksOpen} onClose={() => setTasksOpen(false)} width={760}>
        <DevTasksPanel
          selectedSessionUid={selectedSessionUid}
          currentTaskUid={focusedTaskUid || currentTask?.task_uid}
          onContinueTask={continueDevTaskFromPanel}
          onOpenDrafts={(draftUid, taskUid, sessionUid) => {
            setDraftToOpenUid(draftUid);
            setDraftPanelTab("current");
            setDraftFilterTaskUid(taskUid);
            setDraftFilterMode(taskUid ? "task" : sessionUid ? "session" : "all");
            setDraftsOpen(true);
          }}
        />
      </Drawer>
      <Drawer title="知识库" open={knowledgeOpen} onClose={() => setKnowledgeOpen(false)} width={820}>
        <KnowledgePanel />
      </Drawer>
      <Drawer title="学习经验" open={learningOpen} onClose={() => setLearningOpen(false)} width={820}>
        <ReadableLearningPanel selectedSessionUid={selectedSessionUid} />
      </Drawer>
      <Drawer title="LLM 设置" open={llmSettingsOpen} onClose={() => setLlmSettingsOpen(false)} width={420}>
        <LlmConfigPanel
          config={llmConfigQuery.data}
          status={llmStatusQuery.data}
          saving={saveLlmConfig.isPending}
          onSave={(input) => saveLlmConfig.mutate(input)}
        />
      </Drawer>
      <Drawer title="代码库" open={codebaseOpen} onClose={() => setCodebaseOpen(false)} width={760}>
        <CodebasePanel
          config={codebaseConfigQuery.data}
          onConfigChanged={() => queryClient.invalidateQueries({ queryKey: ["personal-codebase-config"] })}
        />
      </Drawer>
      <Drawer title="Skills" open={skillsOpen} onClose={() => setSkillsOpen(false)} width={920}>
        <SkillsPanel />
      </Drawer>
      <Modal
        title="重命名会话"
        open={Boolean(renamingSession)}
        okText="保存"
        cancelText="取消"
        confirmLoading={renameSession.isPending}
        onCancel={() => {
          setRenamingSession(undefined);
          setRenameTitle("");
        }}
        onOk={() => {
          const title = renameTitle.trim();
          if (!renamingSession || !title) return;
          renameSession.mutate({ sessionUid: renamingSession.session_uid, title });
        }}
      >
        <Input value={renameTitle} onChange={(event) => setRenameTitle(event.target.value)} autoFocus />
      </Modal>
    </Layout>
  );
}

function Sidebar({
  sessions,
  selectedSessionUid,
  onSelect,
  onRename,
  onDelete,
  onBulkDelete,
  bulkDeleting,
  onNew
}: {
  sessions: PersonalSession[];
  selectedSessionUid?: string;
  onSelect: (sessionUid: string) => void;
  onRename: (session: PersonalSession) => void;
  onDelete: (session: PersonalSession) => void;
  onBulkDelete: (sessionUids: string[], onDeleted: () => void) => void;
  bulkDeleting: boolean;
  onNew: () => void;
}) {
  const [bulkMode, setBulkMode] = useState(false);
  const [checkedSessionUids, setCheckedSessionUids] = useState<string[]>([]);
  const [sessionSearch, setSessionSearch] = useState("");
  const checkedSet = new Set(checkedSessionUids);
  const allChecked = sessions.length > 0 && checkedSessionUids.length === sessions.length;
  const partlyChecked = checkedSessionUids.length > 0 && checkedSessionUids.length < sessions.length;
  const searchValue = sessionSearch.trim().toLowerCase();
  const filteredSessions = searchValue
    ? sessions.filter((session) =>
        (session.title || "").toLowerCase().includes(searchValue) ||
        session.session_uid.toLowerCase().includes(searchValue))
    : sessions;
  const exitBulkMode = () => {
    setBulkMode(false);
    setCheckedSessionUids([]);
  };
  const toggleSession = (sessionUid: string, checked: boolean) => {
    setCheckedSessionUids((items) => (checked ? [...new Set([...items, sessionUid])] : items.filter((item) => item !== sessionUid)));
  };
  return (
    <div className="personal-sidebar-inner">
      <div className="personal-brand">
        <RobotOutlined />
        <div>
          <Typography.Text strong>Personal Agent</Typography.Text>
          <Typography.Text type="secondary">本地单人助手</Typography.Text>
        </div>
      </div>
      <Button type="primary" icon={<ThunderboltOutlined />} block onClick={onNew}>
        新会话
      </Button>
      <Input.Search
        allowClear
        placeholder="搜索会话"
        value={sessionSearch}
        onChange={(event) => setSessionSearch(event.target.value)}
        onSearch={setSessionSearch}
      />
      <div className="session-bulk-toolbar">
        {bulkMode ? (
          <>
            <Checkbox
              checked={allChecked}
              indeterminate={partlyChecked}
              disabled={!sessions.length || bulkDeleting}
              onChange={(event) => setCheckedSessionUids(event.target.checked ? sessions.map((item) => item.session_uid) : [])}
            >
              已选 {checkedSessionUids.length}
            </Checkbox>
            <Space size={6}>
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={bulkDeleting}
                disabled={!checkedSessionUids.length}
                onClick={() => {
                  onBulkDelete(checkedSessionUids, exitBulkMode);
                }}
              >
                删除
              </Button>
              <Button size="small" disabled={bulkDeleting} onClick={exitBulkMode}>
                取消
              </Button>
            </Space>
          </>
        ) : (
          <Button size="small" icon={<DeleteOutlined />} disabled={!sessions.length} onClick={() => setBulkMode(true)}>
            批量删除
          </Button>
        )}
      </div>
      <List
        className="personal-task-list"
        dataSource={filteredSessions}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={sessionSearch.trim() ? "无匹配会话" : "还没有会话"} /> }}
        renderItem={(item) => (
          <List.Item
            className={`personal-task-item ${item.session_uid === selectedSessionUid ? "active" : ""} ${bulkMode ? "bulk-mode" : ""}`}
            onClick={() => {
              if (bulkMode) {
                toggleSession(item.session_uid, !checkedSet.has(item.session_uid));
                return;
              }
              onSelect(item.session_uid);
            }}
            onDoubleClick={() => {
              if (!bulkMode) onRename(item);
            }}
          >
            <div className="personal-task-content">
              {bulkMode ? (
                <Checkbox
                  checked={checkedSet.has(item.session_uid)}
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => toggleSession(item.session_uid, event.target.checked)}
                />
              ) : null}
              <Space direction="vertical" size={2} className="personal-task-title">
                <Typography.Text strong ellipsis>{item.title || "Agent 会话"}</Typography.Text>
                <Typography.Text type="secondary" className="personal-small">{shortId(item.session_uid)}</Typography.Text>
              </Space>
              {bulkMode ? null : (
                <Dropdown
                  trigger={["click"]}
                  menu={{
                    items: [
                      { key: "rename", icon: <EditOutlined />, label: "重命名" },
                      { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true }
                    ],
                    onClick: ({ key, domEvent }) => {
                      domEvent.stopPropagation();
                      if (key === "rename") onRename(item);
                      if (key === "delete") onDelete(item);
                    }
                  }}
                >
                  <Button
                    type="text"
                    shape="circle"
                    icon={<MoreOutlined />}
                    onClick={(event) => event.stopPropagation()}
                  />
                </Dropdown>
              )}
            </div>
          </List.Item>
        )}
      />
    </div>
  );
}

function SourcesPanel() {
  const { message, modal } = App.useApp();
  const showError = useMutationErrorHandler();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [selectedSourceUid, setSelectedSourceUid] = useState<string>();
  const [failedUploadFile, setFailedUploadFile] = useState<File>();
  const [failedUploadError, setFailedUploadError] = useState("");

  const sourcesQuery = useQuery({ queryKey: ["personal-sources"], queryFn: personalAgentApi.sources, retry: false });
  const sources = sourcesQuery.data ?? [];
  const activeSource = sources.find((item) => item.is_active);
  const selectedSource = sources.find((item) => item.source_uid === selectedSourceUid) ?? activeSource ?? sources[0];
  const sourceDetailQuery = useQuery({
    queryKey: ["personal-source", selectedSource?.source_uid],
    queryFn: () => personalAgentApi.source(selectedSource!.source_uid),
    enabled: Boolean(selectedSource?.source_uid),
    retry: false
  });
  const sourceDetail = sourceDetailQuery.data ?? selectedSource;

  useEffect(() => {
    if (!selectedSourceUid && selectedSource?.source_uid) {
      setSelectedSourceUid(selectedSource.source_uid);
    }
  }, [selectedSource?.source_uid, selectedSourceUid]);

  const refreshSources = (source?: PersonalInputSource) => {
    queryClient.invalidateQueries({ queryKey: ["personal-sources"] });
    if (source?.source_uid) {
      setSelectedSourceUid(source.source_uid);
      queryClient.setQueryData(["personal-source", source.source_uid], source);
    }
  };

  const createText = useMutation({
    mutationFn: personalAgentApi.createTextSource,
    onSuccess: (source) => {
      setTitle("");
      setContent("");
      refreshSources(source);
      message.success("输入材料已保存。");
    },
    onError: showError
  });
  const uploadSource = useMutation({
    mutationFn: ({ file, onProgress }: { file: File; onProgress?: (percent: number) => void }) =>
      personalAgentApi.uploadSource(file, { make_active: true, onProgress }),
    onSuccess: (source) => {
      setFailedUploadFile(undefined);
      setFailedUploadError("");
      refreshSources(source);
      message.success("文件已解析。");
    },
    onError: (error, file) => {
      const text = friendlyUploadError(error instanceof Error ? error.message : String(error), error);
      setFailedUploadFile(file.file);
      setFailedUploadError(text);
      message.error(text);
    }
  });
  const activateSource = useMutation({
    mutationFn: personalAgentApi.activateSource,
    onSuccess: (source) => {
      refreshSources(source);
      message.success("已设为当前材料。");
    },
    onError: showError
  });
  const deleteSource = useMutation({
    mutationFn: personalAgentApi.deleteSource,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["personal-sources"] });
      queryClient.removeQueries({ queryKey: ["personal-source", result.source_uid] });
      if (selectedSourceUid === result.source_uid) {
        setSelectedSourceUid(result.active_source_uid || undefined);
      }
      message.success("输入材料已清除。");
    },
    onError: showError
  });

  return (
    <div className="sources-panel">
      <Tabs
        items={[
          {
            key: "text",
            label: <span><EditOutlined /> 粘贴文本</span>,
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="标题" />
                <Input.TextArea
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  rows={8}
                  placeholder="粘贴需求、会议纪要、设计约束或测试资料。"
                />
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  loading={createText.isPending}
                  disabled={!content.trim()}
                  onClick={() => createText.mutate({ title, content, make_active: true })}
                >
                  保存为当前材料
                </Button>
              </Space>
            )
          },
          {
            key: "upload",
            label: <span><UploadOutlined /> 上传文件</span>,
            children: (
              <Space direction="vertical" size={12} className="full-width">
                {failedUploadFile ? (
                  <Alert
                    type="error"
                    showIcon
                    message={failedUploadError || "文件上传失败"}
                    action={
                      <Button size="small" icon={<ReloadOutlined />} loading={uploadSource.isPending} onClick={() => uploadSource.mutate({ file: failedUploadFile })}>
                        重试
                      </Button>
                    }
                  />
                ) : null}
                <Upload.Dragger
                  multiple={false}
                  showUploadList={{ showRemoveIcon: false }}
                  accept={ACCEPTED_UPLOAD_ACCEPT}
                  beforeUpload={(file) => {
                    if (file.size > MAX_UPLOAD_SIZE) {
                      message.warning(`文件过大：${file.name}（上限 ${formatUploadLimitMb()}MB）`);
                      return Upload.LIST_IGNORE;
                    }
                    const extension = file.name.split(".").pop()?.toLowerCase() || "";
                    if (!ACCEPTED_UPLOAD_SET.has(extension)) {
                      message.warning(`不支持的文件类型：${file.name}`);
                      return Upload.LIST_IGNORE;
                    }
                    return true;
                  }}
                  customRequest={(options) => {
                    const file = options.file as File;
                    uploadSource.mutate({ file, onProgress: (percent) => options.onProgress?.({ percent }) }, {
                      onSuccess: () => options.onSuccess?.({}, file),
                      onError: (error) => options.onError?.(error as Error)
                    });
                  }}
                >
                  <p className="ant-upload-drag-icon"><UploadOutlined /></p>
                  <p className="ant-upload-text">拖入或选择输入材料</p>
                  <p className="ant-upload-hint">{UPLOAD_HINT}</p>
                </Upload.Dragger>
              </Space>
            )
          }
        ]}
      />
      <Divider />
      <div className="sources-layout">
        <div className="sources-list">
          <List
            loading={sourcesQuery.isLoading}
            dataSource={sources}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有输入材料" /> }}
            renderItem={(item) => (
              <List.Item
                className={`source-item ${item.source_uid === selectedSource?.source_uid ? "active" : ""}`}
                onClick={() => setSelectedSourceUid(item.source_uid)}
              >
                <Space direction="vertical" size={3} className="full-width source-item-content">
                  <div className="source-item-title">
                    <Space wrap>
                      <Typography.Text strong ellipsis>{item.title}</Typography.Text>
                      <Tag>{item.source_type}</Tag>
                      {item.is_active ? <Tag color="green">当前</Tag> : null}
                    </Space>
                    <Tooltip title="清除">
                      <Button
                        type="text"
                        danger
                        shape="circle"
                        icon={<DeleteOutlined />}
                        loading={deleteSource.isPending}
                        onClick={(event) => {
                          event.stopPropagation();
                          modal.confirm({
                            title: "清除输入材料",
                            content: `确定清除“${item.title}”？`,
                            okText: "清除",
                            okButtonProps: { danger: true },
                            cancelText: "取消",
                            onOk: () => deleteSource.mutate(item.source_uid)
                          });
                        }}
                      />
                    </Tooltip>
                  </div>
                  <Typography.Text type="secondary" className="personal-small">{item.preview || item.plain_text.slice(0, 80)}</Typography.Text>
                </Space>
              </List.Item>
            )}
          />
        </div>
        <div className="sources-preview">
          {!sourceDetail ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择材料查看解析结果" />
          ) : (
            <Space direction="vertical" size={10} className="full-width">
              <Space wrap>
                <Typography.Text strong>{sourceDetail.title}</Typography.Text>
                <Tag>{sourceDetail.source_type}</Tag>
                {sourceDetail.is_active ? <Tag color="green">当前材料</Tag> : null}
                {!sourceDetail.is_active ? (
                  <Button size="small" loading={activateSource.isPending} onClick={() => activateSource.mutate(sourceDetail.source_uid)}>
                    设为当前
                  </Button>
                ) : null}
              </Space>
              <Space wrap>
                <Tag>章节 {sourceDetail.sections?.length ?? 0}</Tag>
                <Tag>表格 {sourceDetail.tables?.length ?? 0}</Tag>
                {String(sourceDetail.metadata?.original_name || "") ? <Tag>{String(sourceDetail.metadata.original_name)}</Tag> : null}
              </Space>
              <pre className="source-preview-text">{sourceDetail.plain_text}</pre>
            </Space>
          )}
        </div>
      </div>
    </div>
  );
}

const documentTypeOptions = [
  { value: "requirement_analysis_report", label: "需求分析报告" },
  { value: "requirement_breakdown", label: "需求拆解文件" },
  { value: "functional_spec", label: "功能规范说明" },
  { value: "detailed_design", label: "软件详细设计" },
  { value: "test_case_spec", label: "测试用例规格" },
  { value: "c_code_diff", label: "C 代码 Patch" },
  { value: "unit_test_code_or_diff", label: "单元测试代码/Patch" }
];

const contentFormatOptions = [
  { value: "markdown", label: "Markdown" },
  { value: "json_table", label: "JSON 表格" },
  { value: "diff", label: "Diff" },
  { value: "text", label: "纯文本" }
];

function ArtifactDraftsPanel({
  openDraftUid,
  activeTab,
  onTabChange,
  selectedSessionUid,
  currentTask,
  filterMode,
  onFilterModeChange,
  filterTaskUid,
  onFilterTaskUidChange,
  onOpenTask,
  onContinueTask,
  onFocusTaskDrafts,
}: {
  openDraftUid?: string;
  activeTab: "create" | "current";
  onTabChange: (value: "create" | "current") => void;
  selectedSessionUid?: string;
  currentTask?: PersonalDevTask;
  filterMode: DraftFilterMode;
  onFilterModeChange: (value: DraftFilterMode) => void;
  filterTaskUid?: string;
  onFilterTaskUidChange: (value?: string) => void;
  onOpenTask?: (taskUid: string, sessionUid?: string) => void;
  onContinueTask?: (taskUid: string, sessionUid?: string) => void;
  onFocusTaskDrafts?: (taskUid: string) => void;
}) {
  const { message, modal } = App.useApp();
  const showError = useMutationErrorHandler();
  const queryClient = useQueryClient();
  const [draftTitle, setDraftTitle] = useState("");
  const [documentType, setDocumentType] = useState("requirement_analysis_report");
  const [contentFormat, setContentFormat] = useState("markdown");
  const [sourceUid, setSourceUid] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const [selectedDraftUid, setSelectedDraftUid] = useState<string>();
  const [revisionContent, setRevisionContent] = useState("");
  const [revisionFeedback, setRevisionFeedback] = useState("");
  const [reviewTab, setReviewTab] = useState<DraftReviewTab>("preview");
  const [selectedRevisionIndex, setSelectedRevisionIndex] = useState<number>();
  const [exportFormat, setExportFormat] = useState("");
  const [compareRevisionIndex, setCompareRevisionIndex] = useState<number>();
  const [manualDownload, setManualDownload] = useState<{ url: string; fileName: string } | null>(null);
  const [selectedDraftUids, setSelectedDraftUids] = useState<string[]>([]);
  const [draftUidsPendingRemoval, setDraftUidsPendingRemoval] = useState<string[]>([]);
  const draftTaskUid = filterMode === "task" ? filterTaskUid || "" : "";
  const draftSessionUid = filterMode === "session" || filterMode === "unlinked" ? selectedSessionUid || "" : "";
  const isTrashMode = filterMode === "trash";
  const draftStatusFilter = isTrashMode ? "deleted" : "active_like";

  const draftsQuery = useQuery({
    queryKey: ["personal-drafts", draftSessionUid, draftTaskUid, filterMode, draftStatusFilter],
    queryFn: () => personalAgentApi.draftList(draftSessionUid || undefined, draftTaskUid || undefined, draftStatusFilter),
    retry: false
  });
  const sourcesQuery = useQuery({ queryKey: ["personal-sources"], queryFn: personalAgentApi.sources, retry: false });
  const draftList = draftsQuery.data ?? [];
  const drafts = filterMode === "unlinked" ? draftList.filter((item) => !item.task_uid) : draftList;
  const taskGroups = buildDraftTaskGroups(drafts);
  const draftScopeSummary = buildDraftScopeSummary(drafts);
  const activeDraft = drafts.find((item) => item.is_active);
  const selectedDraftSummary = drafts.find((item) => item.draft_uid === selectedDraftUid) ?? activeDraft ?? drafts[0];
  const draftDetailQuery = useQuery({
    queryKey: ["personal-draft", selectedDraftSummary?.draft_uid, isTrashMode],
    queryFn: () => personalAgentApi.draftDetail(selectedDraftSummary!.draft_uid, isTrashMode),
    enabled: Boolean(selectedDraftSummary?.draft_uid) && !draftUidsPendingRemoval.includes(selectedDraftSummary?.draft_uid || ""),
    retry: false
  });
  const draftDetail = draftDetailQuery.data ?? selectedDraftSummary;
  const selectedRevision = draftDetail?.revisions?.find((item) => item.revision_index === selectedRevisionIndex);
  const previewContent = selectedRevision?.content ?? draftDetail?.content ?? "";
  const filteredTaskDraft = drafts.find((item) => item.task_uid === draftTaskUid)
    ?? (draftDetail?.task_uid === draftTaskUid ? draftDetail : undefined);

  useEffect(() => {
    if (!selectedDraftUid && selectedDraftSummary?.draft_uid) {
      setSelectedDraftUid(selectedDraftSummary.draft_uid);
    }
  }, [selectedDraftSummary?.draft_uid, selectedDraftUid]);

  useEffect(() => {
    if (openDraftUid) {
      setSelectedDraftUid(openDraftUid);
      setReviewTab("preview");
      setSelectedRevisionIndex(undefined);
      setCompareRevisionIndex(undefined);
      setManualDownload(null);
    }
  }, [openDraftUid]);

  useEffect(() => {
    if (filterMode === "task" && !filterTaskUid) {
      onFilterModeChange(selectedSessionUid ? "session" : "all");
    }
  }, [filterTaskUid, filterMode, onFilterModeChange, selectedSessionUid]);

  useEffect(() => {
    if (filterMode === "unlinked") {
      onFilterTaskUidChange(undefined);
    }
  }, [filterMode, onFilterTaskUidChange]);

  useEffect(() => {
    setSelectedDraftUids((items) => items.filter((draftUid) => drafts.some((draft) => draft.draft_uid === draftUid)));
  }, [drafts]);

  useEffect(() => {
    setDraftUidsPendingRemoval((items) => items.filter((draftUid) => drafts.some((draft) => draft.draft_uid === draftUid)));
  }, [drafts]);

  useEffect(() => {
    if (draftDetail?.content !== undefined) {
      setRevisionContent(draftDetail.content);
      setSelectedRevisionIndex(undefined);
      setCompareRevisionIndex(undefined);
      setExportFormat(defaultExportFormat(draftDetail));
      setManualDownload(null);
    }
  }, [draftDetail?.draft_uid, draftDetail?.current_revision, draftDetail?.content]);

  useEffect(() => {
    if (isTrashMode && reviewTab === "revise") {
      setReviewTab("preview");
    }
  }, [isTrashMode, reviewTab]);

  const refreshAllDraftLists = () => {
    queryClient.invalidateQueries({ queryKey: ["personal-drafts"] });
  };

  const refreshDrafts = (draft?: PersonalArtifactDraft) => {
    refreshAllDraftLists();
    if (draft?.draft_uid) {
      setSelectedDraftUid(draft.draft_uid);
      setReviewTab("preview");
      setSelectedRevisionIndex(undefined);
      setCompareRevisionIndex(undefined);
      queryClient.setQueryData(["personal-draft", draft.draft_uid], draft);
    }
  };

  const removeDraftFromVisibleCaches = (draftUid: string) => {
    queryClient.setQueriesData(
      { queryKey: ["personal-drafts"] },
      (cached: PersonalArtifactDraft[] | undefined) => (cached ?? []).filter((draft) => draft.draft_uid !== draftUid),
    );
  };

  const clearDraftSelection = (draftUid: string) => {
    setSelectedDraftUids((items) => items.filter((item) => item !== draftUid));
    if (selectedDraftUid === draftUid) {
      setSelectedDraftUid(undefined);
      setSelectedRevisionIndex(undefined);
      setCompareRevisionIndex(undefined);
      setManualDownload(null);
    }
  };

  const markDraftPendingRemoval = (draftUid: string, pending: boolean) => {
    setDraftUidsPendingRemoval((items) => {
      if (pending) {
        return items.includes(draftUid) ? items : [...items, draftUid];
      }
      return items.filter((item) => item !== draftUid);
    });
  };

  const createDraft = useMutation({
    mutationFn: personalAgentApi.createDraft,
    onSuccess: (draft) => {
      setDraftTitle("");
      setDraftContent("");
      setSourceUid("");
      refreshDrafts(draft);
      message.success("草稿 v1 已创建。");
    },
    onError: showError
  });
  const reviseDraft = useMutation({
    mutationFn: ({ draftUid, content }: { draftUid: string; content: string }) =>
      personalAgentApi.reviseDraftManual(draftUid, { content, make_active: true }),
    onSuccess: (draft) => {
      refreshDrafts(draft);
      message.success(`已保存 v${draft.current_revision}。`);
    },
    onError: showError
  });
  const reviseDraftNatural = useMutation({
    mutationFn: ({ draftUid, feedback }: { draftUid: string; feedback: string }) =>
      personalAgentApi.reviseDraft(draftUid, { feedback, make_active: true }),
    onSuccess: (draft) => {
      setRevisionFeedback("");
      refreshDrafts(draft);
      setReviewTab("versions");
      message.success(`已根据修订意见生成 v${draft.current_revision}。`);
    },
    onError: showError
  });
  const activateDraft = useMutation({
    mutationFn: personalAgentApi.activateDraft,
    onSuccess: (draft) => {
      refreshDrafts(draft);
      message.success("已设为会话当前草稿。");
    },
    onError: showError
  });
  const exportDraft = useMutation({
    mutationFn: ({ draftUid, format }: { draftUid: string; format: string }) =>
      personalAgentApi.exportDraft(draftUid, { format }),
    onSuccess: (result) => {
      const url = personalAgentApi.draftDownloadUrl(result.draft_uid, result.export_format);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.file_name;
      anchor.rel = "noopener noreferrer";
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      setManualDownload({ url, fileName: result.file_name });
      message.success(`已导出 ${result.file_name}。`);
    },
    onError: showError
  });
  const openDraftFile = useMutation({
    mutationFn: (draftUid: string) => personalAgentApi.openDraft(draftUid, {}),
    onSuccess: (result) => {
      message.success(`已打开 ${result.file_name}。`);
    },
    onError: showError
  });
  const regenerateDraft = useMutation({
    mutationFn: (draft: PersonalArtifactDraft) => {
      const prompt = `重新生成${documentLabel(draft.document_type)}：${draft.title}`;
      if (draft.document_type === "unit_test_code_or_diff") {
        return personalAgentApi.proposeUnitTestCodeDraft({
          prompt,
          session_uid: draft.session_uid,
          task_uid: draft.task_uid,
          source_uids: draft.source_uid ? [draft.source_uid] : []
        });
      }
      if (draft.document_type === "c_code_diff") {
        return personalAgentApi.createDraft({
          document_type: draft.document_type,
          title: `${draft.title} 重新生成`,
          content: draft.content || "",
          content_format: draft.content_format,
          source_uid: draft.source_uid,
          session_uid: draft.session_uid,
          task_uid: draft.task_uid,
          metadata: { regenerated_from: draft.draft_uid, boundary: "copied_diff_draft_without_apply" },
          make_active: true
        });
      }
      return personalAgentApi.proposeDocumentDraft({
        prompt,
        document_type: draft.document_type,
        session_uid: draft.session_uid,
        task_uid: draft.task_uid,
        source_uids: draft.source_uid ? [draft.source_uid] : []
      });
    },
    onSuccess: (draft) => {
      refreshDrafts(draft);
      message.success("已重新生成 draft。");
    },
    onError: showError
  });
  const restoreDraft = useMutation({
    mutationFn: (draftUid: string) => personalAgentApi.restoreDraft(draftUid),
    onSuccess: (result, draftUid) => {
      clearDraftSelection(draftUid);
      refreshAllDraftLists();
      message.success("已恢复草稿。");
      queryClient.setQueryData(["personal-draft", result.draft.draft_uid, false], result.draft);
    },
    onError: showError
  });
  const trashDraft = useMutation({
    mutationFn: ({ draftUid, confirmImpact }: { draftUid: string; confirmImpact?: boolean }) =>
      personalAgentApi.trashDraft(draftUid, { confirm_impact: confirmImpact, reason: "用户移入回收站" }),
    onSuccess: (_result, variables) => {
      removeDraftFromVisibleCaches(variables.draftUid);
      clearDraftSelection(variables.draftUid);
      markDraftPendingRemoval(variables.draftUid, false);
      refreshAllDraftLists();
      message.success("已移入回收站。");
    },
    onError: (error, variables) => {
      const apiError = error instanceof ApiError ? error : undefined;
      if (apiError?.status === 409) {
        return;
      }
      markDraftPendingRemoval(variables.draftUid, false);
      showError(error);
    }
  });
  const exportOptions = draftDetail ? exportFormatOptionsForDraft(draftDetail) : [];
  const compareRevision = draftDetail?.revisions?.find((item) => item.revision_index === compareRevisionIndex);
  const compareText = draftDetail && compareRevision ? buildLineDiff(compareRevision.content, draftDetail.content || "") : "";
  const previewIsDiff = draftDetail ? isDiffDraft(draftDetail) : false;
  const qualityFailures = draftDetail ? qualityFailureSummary(draftDetail) : [];
  const canContinueTask = draftDetail ? canContinueDraftTask(draftDetail) : false;
  const taskFilterLabel = filteredTaskDraft ? draftTaskDisplayCode(filteredTaskDraft) : shortId(draftTaskUid);
  const selectedEligibleTrashCount = selectedDraftUids.filter((draftUid) => {
    const draft = drafts.find((item) => item.draft_uid === draftUid);
    return Boolean(draft && canBulkTrashDraft(draft));
  }).length;

  const performTrashDraft = async (draft: PersonalArtifactDraft, confirmImpact = false) => {
    markDraftPendingRemoval(draft.draft_uid, true);
    try {
      await trashDraft.mutateAsync({ draftUid: draft.draft_uid, confirmImpact });
    } catch (error) {
      const apiError = error instanceof ApiError ? error : undefined;
      const detail = asRecord(apiError?.detail);
      const impact = asDraftManagementImpact(detail.impact);
      if (apiError?.status === 409 && detail.reason === "current_stage_candidate_requires_confirmation") {
        markDraftPendingRemoval(draft.draft_uid, false);
        modal.confirm({
          title: "确认移除当前采用候选？",
          okText: "仍然移入回收站",
          okButtonProps: { danger: true },
          cancelText: "取消",
          content: (
            <Space direction="vertical" size={8}>
              <Typography.Text>
                {formatDraftTrashImpactSummary(draft, impact)}
              </Typography.Text>
              <Typography.Text type="secondary">
                {formatDraftTrashImpactDetail(draft, impact)}
              </Typography.Text>
              {impact.was_session_active ? (
                <Typography.Text type="secondary">
                  这份草稿也是当前会话草稿，移入回收站后当前会话将不再指向它。
                </Typography.Text>
              ) : null}
            </Space>
          ),
          onOk: () => performTrashDraft(draft, true)
        });
        return;
      }
      markDraftPendingRemoval(draft.draft_uid, false);
      throw error;
    }
  };

  const confirmTrashDraft = (draft: PersonalArtifactDraft) => {
    modal.confirm({
      title: "移入回收站",
      okText: "移入回收站",
      okButtonProps: { danger: true },
      cancelText: "取消",
      content: (
        <Space direction="vertical" size={8}>
          <Typography.Text>这份草稿会从默认列表隐藏，可在回收站恢复，版本历史会保留。</Typography.Text>
          {draft.is_active ? (
            <Typography.Text type="secondary">
              它也是当前会话草稿，移入回收站后当前会话不再指向这份草稿。
            </Typography.Text>
          ) : null}
        </Space>
      ),
      onOk: () => performTrashDraft(draft)
    });
  };

  const confirmRestoreDraft = (draft: PersonalArtifactDraft) => {
    modal.confirm({
      title: "恢复草稿",
      okText: "恢复",
      cancelText: "取消",
      content: "恢复后这份草稿会回到默认列表，但不会自动设为会话当前草稿。",
      onOk: () => restoreDraft.mutateAsync(draft.draft_uid)
    });
  };

  const handleBatchTrash = () => {
    const candidates = drafts.filter((draft) => selectedDraftUids.includes(draft.draft_uid) && canBulkTrashDraft(draft));
    if (!candidates.length) return;
    modal.confirm({
      title: "批量移入回收站",
      okText: "移入回收站",
      okButtonProps: { danger: true },
      cancelText: "取消",
      content: `将把 ${candidates.length} 份历史候选或未关联草稿移入回收站。当前采用候选不会参与本次批量操作。`,
      onOk: async () => {
        for (const draft of candidates) {
          await trashDraft.mutateAsync({ draftUid: draft.draft_uid });
        }
        setSelectedDraftUids([]);
      }
    });
  };

  const selectDraft = (draftUid: string) => {
    setSelectedDraftUid(draftUid);
    setSelectedRevisionIndex(undefined);
    setReviewTab("preview");
  };

  const toggleDraftSelection = (draftUid: string, checked: boolean) => {
    setSelectedDraftUids((items) => {
      if (checked) {
        return items.includes(draftUid) ? items : [...items, draftUid];
      }
      return items.filter((item) => item !== draftUid);
    });
  };

  const buildDraftActionItems = (draft: PersonalArtifactDraft) => {
    if (isTrashMode) {
      return [
        {
          key: "restore",
          label: "恢复",
          icon: <ReloadOutlined />,
          onClick: () => confirmRestoreDraft(draft),
        },
      ];
    }
    return [
      {
        key: "open",
        label: "打开",
        icon: <FileTextOutlined />,
        onClick: () => openDraftFile.mutate(draft.draft_uid),
      },
      {
        key: "download",
        label: "下载",
        icon: <CloudDownloadOutlined />,
        onClick: () => exportDraft.mutate({ draftUid: draft.draft_uid, format: defaultExportFormat(draft) }),
      },
      {
        key: "activate",
        label: "设为会话当前草稿",
        icon: <PlayCircleOutlined />,
        disabled: draft.is_active,
        onClick: () => activateDraft.mutate(draft.draft_uid),
      },
      {
        key: "trash",
        label: "移入回收站",
        icon: <DeleteOutlined />,
        danger: true,
        onClick: () => confirmTrashDraft(draft),
      },
    ];
  };

  const renderDraftCard = (item: PersonalArtifactDraft) => (
    <div
      key={item.draft_uid}
      className={`artifact-item ${item.draft_uid === selectedDraftSummary?.draft_uid ? "active" : ""}`}
      onClick={() => selectDraft(item.draft_uid)}
    >
      <div className="artifact-draft-card">
        <div className="artifact-draft-card-header">
          <Space size={8} className="artifact-card-heading">
            {!isTrashMode && canBulkTrashDraft(item) ? (
              <Checkbox
                checked={selectedDraftUids.includes(item.draft_uid)}
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => toggleDraftSelection(item.draft_uid, event.target.checked)}
              />
            ) : null}
            <Typography.Text strong ellipsis title={item.title}>
              {item.title}
            </Typography.Text>
          </Space>
          <Space size={6}>
            <Tag color={draftTaskStateColor(item)}>
              {draftTaskStateLabel(item)}
            </Tag>
            <Dropdown
              trigger={["click"]}
              menu={{
                items: buildDraftActionItems(item),
                onClick: ({ key, domEvent }) => {
                  domEvent.stopPropagation();
                  const action = buildDraftActionItems(item).find((candidate) => candidate.key === key);
                  action?.onClick?.();
                },
              }}
            >
              <Button
                size="small"
                type="text"
                icon={<MoreOutlined />}
                onClick={(event) => event.stopPropagation()}
              />
            </Dropdown>
          </Space>
        </div>
        <Space size={4} wrap className="artifact-draft-meta">
          <Tag>{documentLabel(item.document_type)}</Tag>
          <Tag>{draftCandidateLabel(item)}</Tag>
        </Space>
        <Space size={4} wrap className="artifact-draft-status">
          {item.task_uid ? <Tag color="blue">{draftTaskDisplayCode(item)}</Tag> : <Tag>未关联草稿</Tag>}
          {item.status === "deleted" ? <Tag color="default">回收站</Tag> : null}
          {item.status === "quality_failed" ? <Tag color="red">质量未通过</Tag> : null}
          {item.lineage_stale ? <Tag color="orange">上游已更新</Tag> : null}
        </Space>
      </div>
    </div>
  );

  return (
    <div className="artifact-panel">
      <Tabs
        activeKey={activeTab}
        onChange={(key) => onTabChange(key as "create" | "current")}
        items={[
          {
            key: "create",
            label: <span><EditOutlined /> 新建草稿</span>,
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Input value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} placeholder="草稿标题" />
                <Space.Compact className="full-width">
                  <Select className="artifact-select" value={documentType} options={documentTypeOptions} onChange={setDocumentType} />
                  <Select className="artifact-format-select" value={contentFormat} options={contentFormatOptions} onChange={setContentFormat} />
                </Space.Compact>
                <Select
                  allowClear
                  value={sourceUid || undefined}
                  options={(sourcesQuery.data ?? []).map((item) => ({ value: item.source_uid, label: item.title }))}
                  onChange={(value) => setSourceUid(value || "")}
                  placeholder="关联输入材料，可选"
                />
                <Input.TextArea
                  value={draftContent}
                  onChange={(event) => setDraftContent(event.target.value)}
                  rows={9}
                  placeholder="手动输入草稿内容。"
                />
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  loading={createDraft.isPending}
                  disabled={!draftTitle.trim() || !draftContent.trim()}
                  onClick={() => createDraft.mutate({
                    document_type: documentType,
                    title: draftTitle,
                    content: draftContent,
                    content_format: contentFormat,
                    source_uid: sourceUid || undefined,
                    session_uid: selectedSessionUid,
                    task_uid: currentTask?.task_uid,
                    make_active: true
                  })}
                >
                  创建 draft v1
                </Button>
              </Space>
            )
          },
          {
            key: "current",
            label: <span><FileDoneOutlined /> 当前草稿</span>,
            children: (
              <div className="artifact-layout">
                <div className="artifact-list">
                  <Space direction="vertical" size={10} className="full-width">
                    <Select
                      value={filterMode}
                      onChange={(value) => onFilterModeChange(value)}
                      options={[
                        { value: "all", label: "全部" },
                        { value: "session", label: "当前会话", disabled: !selectedSessionUid },
                        { value: "task", label: "任务草稿", disabled: !filterTaskUid && !currentTask?.task_uid },
                        { value: "unlinked", label: "未关联草稿" },
                        { value: "trash", label: "回收站" },
                      ]}
                    />
                    <Typography.Text type="secondary" className="personal-small">
                      {filterMode === "task"
                        ? `仅显示 ${taskFilterLabel} 的草稿`
                        : filterMode === "trash"
                          ? "仅显示已移入回收站的草稿"
                        : filterMode === "unlinked"
                          ? "仅显示未关联草稿"
                        : filterMode === "session"
                          ? "仅显示当前会话草稿"
                          : "显示全部草稿"}
                    </Typography.Text>
                    {!isTrashMode && selectedEligibleTrashCount ? (
                      <Space size={8} wrap>
                        <Typography.Text className="personal-small">
                          已选 {selectedEligibleTrashCount} 份可批量移入回收站的草稿
                        </Typography.Text>
                        <Button size="small" danger onClick={handleBatchTrash}>
                          批量移入回收站
                        </Button>
                        <Button size="small" onClick={() => setSelectedDraftUids([])}>
                          清空选择
                        </Button>
                      </Space>
                    ) : null}
                    <div className="artifact-scope-summary">
                      <Typography.Text strong>当前范围：</Typography.Text>
                      <Typography.Text>
                        {draftScopeSummary}
                      </Typography.Text>
                    </div>
                    {taskGroups.length ? (
                      <div className="artifact-group-list">
                        {taskGroups.map((group) => (
                          <div key={group.key} className="artifact-task-group">
                            <div className="artifact-task-group-header">
                              <Space wrap size={6} className="full-width">
                                {group.isUnlinked ? <Tag>未关联草稿</Tag> : <Tag color="blue">{group.taskDisplayCode || shortId(group.taskUid)}</Tag>}
                                {filterMode === "all" && group.sessionUid ? (
                                  <Typography.Text type="secondary" className="personal-small">
                                    会话 {shortId(group.sessionUid)}
                                  </Typography.Text>
                                ) : null}
                                <Typography.Text strong ellipsis={{ tooltip: group.taskTitle || "未关联草稿" }}>
                                  {group.isUnlinked ? "未关联草稿" : `${group.taskDisplayCode || shortId(group.taskUid)} · 任务草稿线`}
                                </Typography.Text>
                                {group.taskStatus ? <Tag>{group.taskStatus}</Tag> : null}
                              </Space>
                              {!group.isUnlinked && group.taskTitle ? (
                                <Typography.Text ellipsis={{ tooltip: group.taskTitle }}>
                                  {group.taskTitle}
                                </Typography.Text>
                              ) : null}
                              <Typography.Text className="artifact-task-group-summary">
                                {taskGroupSummary(group)}
                              </Typography.Text>
                            </div>
                            {group.stages.map((stage) => (
                              <div key={stage.key} className="artifact-stage-group">
                                <Typography.Text type="secondary" className="artifact-stage-group-title">
                                  {group.isUnlinked ? `${documentLabel(stage.documentType)} · ${stage.drafts.length} 份草稿 · 仅版本语义` : stageGroupSummary(stage)}
                                </Typography.Text>
                                <div className="artifact-candidate-list">
                                  {stage.candidates.map((candidate) => (
                                    <div key={candidate.key} className="artifact-candidate-group">
                                      <Typography.Text type="secondary" className="artifact-candidate-label">
                                        {candidate.label}
                                      </Typography.Text>
                                      {renderDraftCard(candidate.draft)}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有草稿" />
                    )}
                  </Space>
                </div>
                <div className="artifact-preview">
                  {!draftDetail ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择草稿查看内容" />
                  ) : (
                    <Space direction="vertical" size={10} className="full-width">
                        <div className="artifact-detail-header">
                          <div className="artifact-detail-title">
                            <Typography.Title level={5}>{draftDetail.title}</Typography.Title>
                            <Space size={6} wrap>
                              <Tag>{documentLabel(draftDetail.document_type)}</Tag>
                            <Tag>{draftCandidateLabel(draftDetail)}</Tag>
                            {draftDetail.status === "quality_failed" ? <Tag color="red">质量未通过</Tag> : null}
                            {draftDetail.lineage_stale ? <Tag color="orange">上游已更新</Tag> : null}
                          </Space>
                        </div>
                        {!isTrashMode && !draftDetail.is_active ? (
                          <Tooltip title="只会把这份草稿设为当前会话下该文档类型的当前草稿，不会改变任务阶段当前采用候选。">
                            <Button
                              size="small"
                              loading={activateDraft.isPending}
                              onClick={() => activateDraft.mutate(draftDetail.draft_uid)}
                            >
                              设为会话当前草稿
                            </Button>
                          </Tooltip>
                        ) : null}
                      </div>
                      {draftDetail.task_uid ? (
                        <div className="artifact-task-context">
                          <Space direction="vertical" size={8} className="full-width">
                            <Space wrap size={6}>
                              <Tag color="blue">{draftTaskDisplayCode(draftDetail)}</Tag>
                              <Typography.Text strong>{draftDetail.task_title || "未命名任务"}</Typography.Text>
                              {draftDetail.task_status ? <Tag>{draftDetail.task_status}</Tag> : null}
                              {draftDetail.task_current_step ? <Tag>{documentLabel(draftDetail.task_current_step)}</Tag> : null}
                              <Tag>{draftCandidateLabel(draftDetail)}</Tag>
                              {draftDetail.is_stage_current_candidate ? <Tag color="green">当前采用候选</Tag> : <Tag>历史候选</Tag>}
                              {draftDetail.is_active ? <Tag color="processing">会话当前草稿</Tag> : null}
                            </Space>
                            <div className="artifact-relationship-grid">
                              {draftRelationshipFacts(draftDetail).map((fact) => (
                                <div key={fact.key} className="artifact-relationship-row">
                                  <Typography.Text type="secondary" className="artifact-relationship-label">
                                    {fact.label}
                                  </Typography.Text>
                                  <Typography.Text className="artifact-relationship-value">
                                    {fact.value}
                                  </Typography.Text>
                                </div>
                              ))}
                            </div>
                            <Space direction="vertical" size={4} className="full-width">
                              <Typography.Text>
                                {draftRelationshipSummary(draftDetail)}
                              </Typography.Text>
                              <Typography.Text type="secondary">
                                {draftRelationshipDetail(draftDetail)}
                              </Typography.Text>
                            </Space>
                            <Space wrap size={8}>
                              <Button size="small" onClick={() => onOpenTask?.(draftDetail.task_uid!, draftDetail.session_uid)}>
                                查看任务
                              </Button>
                              <Tooltip title={isTrashMode ? "回收站草稿不能直接继续推进任务" : canContinueTask ? undefined : "该任务已结束，不能继续推进"}>
                                <span>
                                  <Button
                                    size="small"
                                    type="primary"
                                    disabled={isTrashMode || !canContinueTask}
                                    onClick={() => onContinueTask?.(draftDetail.task_uid!, draftDetail.session_uid)}
                                  >
                                    继续推进任务
                                  </Button>
                                </span>
                              </Tooltip>
                              <Button
                                size="small"
                                onClick={() => {
                                  onFilterTaskUidChange(draftDetail.task_uid!);
                                  onFilterModeChange("task");
                                  onFocusTaskDrafts?.(draftDetail.task_uid!);
                                }}
                              >
                                只看此任务草稿
                              </Button>
                            </Space>
                          </Space>
                        </div>
                      ) : (
                        <Alert
                          type="info"
                          showIcon
                          message="未关联草稿"
                          description={(
                            <Space direction="vertical" size={8} className="full-width">
                              <div className="artifact-relationship-grid artifact-relationship-grid-compact">
                                {draftRelationshipFacts(draftDetail).map((fact) => (
                                  <div key={fact.key} className="artifact-relationship-row">
                                    <Typography.Text type="secondary" className="artifact-relationship-label">
                                      {fact.label}
                                    </Typography.Text>
                                    <Typography.Text className="artifact-relationship-value">
                                      {fact.value}
                                    </Typography.Text>
                                  </div>
                                ))}
                              </div>
                              <Typography.Text>{draftRelationshipSummary(draftDetail)}</Typography.Text>
                              <Typography.Text type="secondary">{draftRelationshipDetail(draftDetail)}</Typography.Text>
                            </Space>
                          )}
                        />
                      )}
                      <div className="artifact-toolbar">
                        <Space wrap size={8}>
                          {!isTrashMode ? (
                            <>
                              <span className="artifact-toolbar-label">导出格式</span>
                              <Select
                                className="artifact-export-select"
                                value={exportFormat || undefined}
                                options={exportOptions.map((item) => ({ value: item, label: item.toUpperCase() }))}
                                onChange={setExportFormat}
                              />
                              <Button
                                icon={<FileTextOutlined />}
                                loading={openDraftFile.isPending}
                                onClick={() => openDraftFile.mutate(draftDetail.draft_uid)}
                              >
                                打开
                              </Button>
                              <Button
                                icon={<CloudDownloadOutlined />}
                                loading={exportDraft.isPending}
                                disabled={!exportFormat}
                                onClick={() => exportDraft.mutate({ draftUid: draftDetail.draft_uid, format: exportFormat || defaultExportFormat(draftDetail) })}
                              >
                                下载
                              </Button>
                            </>
                          ) : null}
                          <Button
                            icon={<CopyOutlined />}
                            onClick={() => {
                              navigator.clipboard?.writeText(previewContent);
                              message.success("内容已复制。");
                            }}
                          >
                            复制
                          </Button>
                        </Space>
                        <Space wrap size={8}>
                          {isTrashMode ? (
                            <Button
                              icon={<ReloadOutlined />}
                              loading={restoreDraft.isPending}
                              onClick={() => confirmRestoreDraft(draftDetail)}
                            >
                              恢复草稿
                            </Button>
                          ) : (
                            <>
                              <Button danger icon={<DeleteOutlined />} loading={trashDraft.isPending} onClick={() => confirmTrashDraft(draftDetail)}>
                                移入回收站
                              </Button>
                              <Popconfirm
                                title="重新生成草稿"
                                description={regenerateDraftDescription(draftDetail)}
                                okText="重新生成"
                                cancelText="取消"
                                onConfirm={() => regenerateDraft.mutate(draftDetail)}
                              >
                                <Button
                                  icon={<ReloadOutlined />}
                                  loading={regenerateDraft.isPending}
                                >
                                  重新生成
                                </Button>
                              </Popconfirm>
                            </>
                          )}
                        </Space>
                      </div>
                      {!isTrashMode && manualDownload ? (
                        <Typography.Link href={manualDownload.url} download={manualDownload.fileName}>
                          手动下载 {manualDownload.fileName}
                        </Typography.Link>
                      ) : null}
                      {qualityFailures.length ? (
                        <Alert
                          type="error"
                          showIcon
                          message="质量未通过"
                          description={(
                            <Space direction="vertical" size={4}>
                              {qualityFailures.map((item) => (
                                <Typography.Text key={item}>{item}</Typography.Text>
                              ))}
                            </Space>
                          )}
                          action={(
                            <Space>
                              <Button size="small" onClick={() => setReviewTab("quality")}>
                                查看质量页
                              </Button>
                              <Button size="small" type="primary" onClick={() => setReviewTab("revise")}>
                                打开修订
                              </Button>
                            </Space>
                          )}
                        />
                      ) : null}
                      <Tabs
                        className="artifact-review-tabs"
                        activeKey={reviewTab}
                        onChange={(key) => setReviewTab(key as DraftReviewTab)}
                        items={[
                          {
                            key: "preview",
                            label: "预览",
                            children: (
                              <Space direction="vertical" size={8} className="full-width">
                                {selectedRevision ? (
                                  <Alert
                                    type="info"
                                    showIcon
                                    message={`正在预览历史版本 v${selectedRevision.revision_index}`}
                                    action={<Button size="small" onClick={() => setSelectedRevisionIndex(undefined)}>回到当前版本</Button>}
                                  />
                                ) : null}
                                {previewIsDiff ? (
                                  <DiffView text={previewContent} className="artifact-preview-text artifact-preview-text-large diff-view" />
                                ) : draftDetail && shouldRenderMarkdownDraft(draftDetail) ? (
                                  <div className="artifact-preview-rendered">
                                    <MarkdownMessage content={previewContent} />
                                  </div>
                                ) : (
                                  <pre className="artifact-preview-text artifact-preview-text-large">{previewContent}</pre>
                                )}
                              </Space>
                            )
                          },
                          ...(!isTrashMode ? [{
                            key: "revise",
                            label: "修订",
                            children: (
                              <Space direction="vertical" size={12} className="full-width">
                                <div className="artifact-review-card">
                                  <Space direction="vertical" size={8} className="full-width">
                                    <Typography.Text strong>按方向生成新版本</Typography.Text>
                                    <Typography.Text type="secondary" className="personal-small">
                                      写下方向性修改意见，Agent 会结合当前草稿、Skill、Template 和证据来源重新生成可审阅的新版本。
                                    </Typography.Text>
                                    <Input.TextArea
                                      value={revisionFeedback}
                                      onChange={(event) => setRevisionFeedback(event.target.value)}
                                      rows={5}
                                      placeholder="例如：整体更像功能规范，减少实现细节，补充边界条件和验收标准。"
                                    />
                                    <Button
                                      type="primary"
                                      icon={<CheckCircleOutlined />}
                                      loading={reviseDraftNatural.isPending}
                                      disabled={!revisionFeedback.trim()}
                                      onClick={() => reviseDraftNatural.mutate({ draftUid: draftDetail.draft_uid, feedback: revisionFeedback.trim() })}
                                    >
                                      生成修订版
                                    </Button>
                                  </Space>
                                </div>
                                <div className="artifact-review-card">
                                  <Space direction="vertical" size={8} className="full-width">
                                    <Typography.Text strong>手动修订</Typography.Text>
                                    <Input.TextArea
                                      value={revisionContent}
                                      onChange={(event) => setRevisionContent(event.target.value)}
                                      rows={8}
                                      className="artifact-editor"
                                    />
                                    <Button
                                      icon={<CheckCircleOutlined />}
                                      loading={reviseDraft.isPending}
                                      disabled={!revisionContent.trim() || revisionContent.trim() === (draftDetail.content || "").trim()}
                                      onClick={() => reviseDraft.mutate({ draftUid: draftDetail.draft_uid, content: revisionContent })}
                                    >
                                      保存为新版本
                                    </Button>
                                  </Space>
                                </div>
                              </Space>
                            )
                          }] : []),
                          {
                            key: "versions",
                            label: "版本",
                            children: (
                              <Space direction="vertical" size={12} className="full-width">
                                <Space direction="vertical" size={8} className="full-width">
                                  <Typography.Text strong><HistoryOutlined /> 版本历史</Typography.Text>
                                  <List
                                    size="small"
                                    dataSource={draftDetail.revisions ?? []}
                                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无版本记录" /> }}
                                    renderItem={(revision) => (
                                      <List.Item
                                        className={`artifact-revision-item ${revision.revision_index === selectedRevisionIndex ? "active" : ""}`}
                                        onClick={() => setSelectedRevisionIndex(revision.revision_index)}
                                      >
                                        <Space wrap>
                                          <Tag color={revision.revision_index === draftDetail.current_revision ? "green" : undefined}>
                                            v{revision.revision_index}
                                          </Tag>
                                          {revision.revision_index === draftDetail.current_revision ? <Tag>当前</Tag> : null}
                                          <Typography.Text type="secondary">{revision.created_at}</Typography.Text>
                                        </Space>
                                      </List.Item>
                                    )}
                                  />
                                </Space>
                                <Divider />
                                <Space direction="vertical" size={8} className="full-width">
                                  <Typography.Text strong><DiffOutlined /> 与当前版本对比</Typography.Text>
                                  <Select
                                    allowClear
                                    value={compareRevisionIndex}
                                    options={(draftDetail.revisions ?? [])
                                      .filter((revision) => revision.revision_index !== draftDetail.current_revision)
                                      .map((revision) => ({ value: revision.revision_index, label: `v${revision.revision_index} -> v${draftDetail.current_revision}` }))}
                                    onChange={setCompareRevisionIndex}
                                    placeholder="选择历史版本"
                                  />
                                  {compareText ? (
                                    <DiffView text={compareText} />
                                  ) : (
                                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个历史版本查看 diff" />
                                  )}
                                </Space>
                              </Space>
                            )
                          },
                          {
                            key: "quality",
                            label: "质量/证据",
                            children: <GenerationMetadataSummary draft={draftDetail} />
                          }
                        ]}
                      />
                    </Space>
                  )}
                </div>
              </div>
            )
          }
        ]}
      />
    </div>
  );
}

function KnowledgePanel() {
  const { message, modal } = App.useApp();
  const showError = useMutationErrorHandler();
  const queryClient = useQueryClient();
  const [selectedSourceUid, setSelectedSourceUid] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<PersonalKnowledgeItem[]>([]);

  const knowledgeQuery = useQuery({ queryKey: ["personal-knowledge"], queryFn: personalAgentApi.knowledge, retry: false });
  const sourcesQuery = useQuery({ queryKey: ["personal-sources"], queryFn: personalAgentApi.sources, retry: false });
  const items = knowledgeQuery.data?.items ?? [];

  useEffect(() => {
    const active = (sourcesQuery.data ?? []).find((item) => item.is_active) ?? sourcesQuery.data?.[0];
    if (!selectedSourceUid && active?.source_uid) setSelectedSourceUid(active.source_uid);
  }, [selectedSourceUid, sourcesQuery.data]);

  const importSource = useMutation({
    mutationFn: personalAgentApi.importSourceToKnowledge,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personal-knowledge"] });
      message.success("输入材料已导入知识库。");
    },
    onError: showError
  });
  const search = useMutation({
    mutationFn: personalAgentApi.searchKnowledge,
    onSuccess: setHits,
    onError: showError
  });
  const deprecate = useMutation({
    mutationFn: (knowledgeId: number) => personalAgentApi.deprecateKnowledge(knowledgeId, { reviewer: "local_user", comment: "personal knowledge deprecated" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personal-knowledge"] });
      message.success("知识条目已废弃。");
    },
    onError: showError
  });
  const visibleItems = hits.length ? hits : items;
  const handleDeprecate = (item: PersonalKnowledgeItem) => {
    modal.confirm({
      title: "废弃知识条目",
      content: "确定废弃这条知识条目吗？废弃后不再纳入知识召回。",
      okText: "废弃",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => deprecate.mutate(item.id),
    });
  };

  return (
    <Space direction="vertical" size={12} className="full-width">
      <Space.Compact className="full-width">
        <Select
          className="knowledge-source-select"
          value={selectedSourceUid || undefined}
          options={(sourcesQuery.data ?? []).map((item) => ({ value: item.source_uid, label: item.title }))}
          onChange={(value) => setSelectedSourceUid(value || "")}
          placeholder="选择输入材料"
        />
        <Button
          type="primary"
          icon={<BookOutlined />}
          loading={importSource.isPending}
          disabled={!selectedSourceUid}
          onClick={() => importSource.mutate({ source_uid: selectedSourceUid })}
        >
          导入
        </Button>
      </Space.Compact>
      {importSource.isPending ? (
        <Typography.Text type="secondary" className="personal-small">
          正在导入知识条目，请稍候。
        </Typography.Text>
      ) : null}
      <Input.Search
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onSearch={(value) => value.trim() && search.mutate({ query: value.trim(), limit: 8 })}
        enterButton="搜索"
        placeholder="搜索知识条目"
        loading={search.isPending}
      />
      <List
        loading={knowledgeQuery.isLoading}
        dataSource={visibleItems}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有知识条目" /> }}
        renderItem={(item) => (
          <List.Item
            actions={[
              item.status === "deprecated" ? <Tag key="deprecated">已废弃</Tag> : (
                <Button key="deprecate" size="small" danger loading={deprecate.isPending} onClick={() => handleDeprecate(item)}>
                  废弃
                </Button>
              )
            ]}
          >
            <Space direction="vertical" size={4} className="full-width">
              <Space wrap>
                <Typography.Text strong>{item.title}</Typography.Text>
                <Tag>{item.category}</Tag>
                <Tag>{item.source_type}</Tag>
                <Tag color={item.status === "active" ? "green" : "default"}>{item.status || "active"}</Tag>
              </Space>
              <Typography.Text type="secondary" className="personal-small">{item.source_ref}</Typography.Text>
              <Typography.Paragraph className="knowledge-snippet">{item.excerpt || item.content}</Typography.Paragraph>
            </Space>
          </List.Item>
        )}
      />
    </Space>
  );
}

function ReadableLearningPanel({ selectedSessionUid }: { selectedSessionUid?: string }) {
  const { message, modal } = App.useApp();
  const showError = useMutationErrorHandler();
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState("");
  const [correctedBehavior, setCorrectedBehavior] = useState("");

  const summaryQuery = useQuery({ queryKey: ["personal-learning-summary"], queryFn: personalAgentApi.learningSummary, retry: false });
  const inboxQuery = useQuery({ queryKey: ["personal-inbox"], queryFn: personalAgentApi.inbox, retry: false });
  const candidates = inboxQuery.data ?? [];
  const summary = summaryQuery.data;
  const pendingCount = learningStatusCount(summary, "candidate");
  const approvedCount = summary?.approved_lessons ?? learningStatusCount(summary, "approved");
  const rejectedCount = learningStatusCount(summary, "rejected");
  const feedbackScope = selectedSessionUid ? "session" : "project";

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["personal-learning-summary"] });
    queryClient.invalidateQueries({ queryKey: ["personal-learning-candidates"] });
    queryClient.invalidateQueries({ queryKey: ["personal-inbox"] });
    queryClient.invalidateQueries({ queryKey: ["personal-knowledge"] });
  };
  const createFeedback = useMutation({
    mutationFn: personalAgentApi.createLearningFeedback,
    onSuccess: () => {
      setFeedback("");
      setCorrectedBehavior("");
      refresh();
      message.success("学习候选已创建。");
    },
    onError: showError
  });
  const approve = useMutation({
    mutationFn: (candidateId: number) => personalAgentApi.approveLearningCandidate(candidateId, { reviewer: "local_user", comment: "personal learning approved" }),
    onSuccess: () => {
      refresh();
      message.success("经验已批准为长期规则。");
    },
    onError: showError
  });
  const reject = useMutation({
    mutationFn: (candidateId: number) => personalAgentApi.rejectLearningCandidate(candidateId, { reviewer: "local_user", comment: "personal learning rejected" }),
    onSuccess: () => {
      refresh();
      message.success("经验已拒绝。");
    },
    onError: showError
  });
  const dismiss = useMutation({
    mutationFn: (itemUid: string) => personalAgentApi.dismissMemoryLesson(itemUid, { reviewer: "local_user", comment: "personal memory dismissed" }),
    onSuccess: () => {
      refresh();
      message.success("记忆经验已撤销。");
    },
    onError: showError
  });

  const handleReject = (item: PersonalInboxItem) => {
    modal.confirm({
      title: "拒绝学习经验",
      content: "确定拒绝这条经验吗？拒绝后该候选将不再出现在待审批列表中。",
      okText: "拒绝",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => reject.mutate(item.id),
    });
  };

  return (
    <Space direction="vertical" size={12} className="full-width">
      <div className="learning-summary">
        <Space wrap>
          <Tag color="orange">待批准 {pendingCount}</Tag>
          <Tag color="green">已批准 {approvedCount}</Tag>
          <Tag color="red">已拒绝 {rejectedCount}</Tag>
          <Tag>回归用例 {summary?.regression_cases ?? 0}</Tag>
        </Space>
        <Typography.Text type="secondary" className="personal-small">
          Agent 会把对话中的长期偏好、纠错和工作方式要求先提炼成候选经验；批准后才会变成长期记忆。
        </Typography.Text>
      </div>
      <Space direction="vertical" size={8} className="full-width">
        <Input.TextArea
          value={feedback}
          onChange={(event) => setFeedback(event.target.value)}
          rows={3}
          placeholder="例如：以后功能规范不要写实现细节"
        />
        <Input
          value={correctedBehavior}
          onChange={(event) => setCorrectedBehavior(event.target.value)}
          placeholder="提炼后的规则，可留空"
        />
        <Button
          type="primary"
          icon={<BulbOutlined />}
          loading={createFeedback.isPending}
          disabled={!feedback.trim()}
          onClick={() => createFeedback.mutate({ feedback, corrected_behavior: correctedBehavior, session_uid: selectedSessionUid, scope: feedbackScope })}
        >
          生成学习候选
        </Button>
      </Space>
      <List
        loading={inboxQuery.isLoading}
        dataSource={candidates}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有学习候选" /> }}
        renderItem={(item: PersonalInboxItem) => (
          <List.Item
            actions={[
              item.kind === "learning_candidate" && item.status === "candidate" ? (
                <Button key="approve" size="small" type="primary" loading={approve.isPending} onClick={() => approve.mutate(item.id)}>
                  批准
                </Button>
              ) : null,
              item.kind === "learning_candidate" && item.status === "candidate" ? (
                <Button key="reject" size="small" danger loading={reject.isPending} onClick={() => handleReject(item)}>
                  拒绝
                </Button>
              ) : null,
              item.kind === "learning_candidate" && item.status === "approved" && item.item_uid ? (
                <Button key="dismiss" size="small" danger loading={dismiss.isPending} onClick={() => dismiss.mutate(item.item_uid!)}>
                  撤销
                </Button>
              ) : null
            ].filter(Boolean)}
          >
            <Space direction="vertical" size={4} className="full-width">
              <Space wrap>
                <Typography.Text strong>{item.kind === "skill_update_candidate" ? item.target_skill || "Skill 更新" : learningCandidateTitle(item)}</Typography.Text>
                <Tag color={item.status === "approved" ? "green" : item.status === "rejected" ? "red" : "orange"}>{learningStatusLabel(item.status || "")}</Tag>
                {item.kind ? <Tag>{item.kind === "skill_update_candidate" ? "Skill 候选" : "学习候选"}</Tag> : null}
                {"lesson_type" in item && item.lesson_type ? <Tag>{learningTypeLabel(item.lesson_type)}</Tag> : null}
                {item.created_at ? <Typography.Text type="secondary" className="personal-small">{item.created_at}</Typography.Text> : null}
              </Space>
              {item.kind === "skill_update_candidate" ? (
                <Typography.Text>建议修改：{readableLearningText(String(item.proposed_change || ""))}</Typography.Text>
              ) : (
                <>
                  <Typography.Text>提炼后的规则：{readableLearningText(item.lesson || item.expected_behavior)}</Typography.Text>
                  {item.anti_behavior ? <Typography.Text type="secondary">避免行为：{readableLearningText(item.anti_behavior)}</Typography.Text> : null}
                  <Typography.Text type="secondary" className="personal-small">
                    来源：{learningSourceLabel(item)}；用户原始反馈：{readableLearningText(String(item.evidence_refs?.feedback_text || item.validation_query || ""))}
                  </Typography.Text>
                </>
              )}
            </Space>
          </List.Item>
        )}
      />
    </Space>
  );
}

function learningStatusCount(summary: { memory?: { status: string; count: number }[] } | undefined, status: string): number {
  return summary?.memory?.find((item) => item.status === status)?.count ?? 0;
}

function learningStatusLabel(status: string): string {
  if (status === "candidate") return "待批准";
  if (status === "approved") return "已批准";
  if (status === "rejected") return "已拒绝";
  return status || "未知状态";
}

function learningTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    conversation_lesson: "对话经验",
    routing_lesson: "意图理解",
    tool_lesson: "工具使用",
    workflow_lesson: "流程经验",
    code_lesson: "代码经验",
    safety_lesson: "安全边界",
    style_preference: "表达偏好",
    correction: "纠错经验",
    workflow_preference: "工作方式",
    quality_bar: "质量要求",
  };
  return labels[type] ?? type;
}

function learningCandidateTitle(item: PersonalLearningCandidate | PersonalInboxItem): string {
  if (isUnreadableLearningText(item.title || "")) {
    return "待批准经验";
  }
  if (item.title && !item.title.includes("/")) {
    return item.title;
  }
  return `${learningTypeLabel(item.lesson_type || "") || "经验"} #${item.id}`;
}

function learningSourceLabel(item: PersonalLearningCandidate | PersonalInboxItem): string {
  const evidence = item.evidence_refs ?? {};
  const source = String(evidence.source || "");
  if (source === "personal_learning_reflect") return "对话自动提炼";
  if (source === "personal_unified_turn") return "对话反馈";
  if (source === "personal_learning_api") return "手动创建";
  return source || "未知来源";
}

function readableLearningText(value: string | undefined): string {
  const text = String(value || "").trim();
  if (!text) return "暂无内容";
  if (isUnreadableLearningText(text)) return "内容不可读，建议拒绝这条候选或重新用中文表达一次。";
  return text;
}

function isUnreadableLearningText(text: string): boolean {
  const value = String(text || "");
  if (!value.trim()) return false;
  const questionMarks = (value.match(/\?/g) || []).length;
  return questionMarks >= 4;
}

function GenerationMetadataSummary({ draft }: { draft: PersonalArtifactDraft }) {
  const generation = asRecord(draft.metadata?.generation);
  const skill = asRecord(generation.skill);
  const template = asRecord(generation.template);
  const llm = asRecord(generation.llm);
  const quality = asRecord(generation.quality);
  const evidenceRefs = asRecord(generation.evidence_refs);
  const checks = Array.isArray(quality.checks) ? quality.checks : [];
  const hasGeneration = Boolean(Object.keys(generation).length);
  const hasSkill = Boolean(Object.keys(skill).length);
  const hasTemplate = Boolean(Object.keys(template).length);
  const hasQuality = Boolean(Object.keys(quality).length);
  const hasEvidence = Boolean(Object.keys(evidenceRefs).length);
  const qualityPassed = quality.passed === undefined ? undefined : Boolean(quality.passed);

  if (!hasGeneration) {
    return (
      <Alert
        type="info"
        showIcon
        message="这份草稿没有生成审查 metadata"
        description="历史草稿或手动草稿可能没有 Skill、Template、质量检查 和 Evidence refs 信息。"
      />
    );
  }

  return (
    <Space direction="vertical" size={12} className="full-width generation-review">
      <div className="generation-review-grid">
        <div className="generation-review-card">
          <Space direction="vertical" size={6} className="full-width">
            <Typography.Text strong>Skill</Typography.Text>
            {hasSkill ? (
              <>
                <Space wrap>
                  {skill.name ? <Tag color="blue">{String(skill.name)}</Tag> : null}
                  {skill.version_index ? <Tag>v{String(skill.version_index)}</Tag> : null}
                  {skill.path ? <Tag>已记录路径</Tag> : null}
                </Space>
                {skill.path ? <Typography.Text type="secondary" className="personal-small" copyable>{String(skill.path)}</Typography.Text> : null}
              </>
            ) : (
              <Typography.Text type="secondary">未记录 Skill 信息。</Typography.Text>
            )}
          </Space>
        </div>
        <div className="generation-review-card">
          <Space direction="vertical" size={6} className="full-width">
            <Typography.Text strong>Template</Typography.Text>
            {hasTemplate ? (
              <>
                <Space wrap>
                  {template.name ? <Tag color="cyan">{String(template.name)}</Tag> : null}
                  {template.format ? <Tag>{String(template.format)}</Tag> : null}
                  {template.hash ? <Tag title={String(template.hash)}>hash {String(template.hash).slice(0, 12)}</Tag> : null}
                </Space>
                {template.path ? <Typography.Text type="secondary" className="personal-small" copyable>{String(template.path)}</Typography.Text> : null}
              </>
            ) : (
              <Typography.Text type="secondary">未记录模板信息，可能是旧草稿或手动草稿。</Typography.Text>
            )}
          </Space>
        </div>
      </div>
      <div className="generation-review-card">
        <Space direction="vertical" size={8} className="full-width">
          <Space wrap>
            <Typography.Text strong>质量检查</Typography.Text>
            {qualityPassed !== undefined ? (
              <Tag color={qualityPassed ? "green" : "red"}>{qualityPassed ? "通过" : "未通过"}</Tag>
            ) : (
              <Tag>未记录</Tag>
            )}
            {quality.policy ? <Tag>{String(quality.policy)}</Tag> : null}
          </Space>
          {hasQuality && checks.length ? (
            <List
              size="small"
              dataSource={checks}
              renderItem={(check) => {
                const item = asRecord(check);
                const passed = item.passed === undefined ? undefined : Boolean(item.passed);
                return (
                  <List.Item className="quality-check-item">
                    <Space direction="vertical" size={3} className="full-width">
                      <Space wrap>
                        <Tag color={passed === undefined ? undefined : passed ? "green" : "red"}>
                          {passed === undefined ? "未知" : passed ? "通过" : "失败"}
                        </Tag>
                        <Typography.Text strong>{String(item.name || item.check || "quality_check")}</Typography.Text>
                      </Space>
                      {item.message || item.detail ? (
                        <Typography.Text type="secondary" className="personal-small">
                          {String(item.message || item.detail)}
                        </Typography.Text>
                      ) : null}
                    </Space>
                  </List.Item>
                );
              }}
            />
          ) : (
            <Typography.Text type="secondary">没有记录逐项检查结果。</Typography.Text>
          )}
        </Space>
      </div>
      <div className="generation-review-card">
        <Space direction="vertical" size={8} className="full-width">
          <Space wrap>
            <Typography.Text strong>Evidence refs</Typography.Text>
            {hasEvidence ? <Tag color="geekblue">已记录</Tag> : <Tag>未记录</Tag>}
            {llm.provider ? <Tag>{String(llm.provider)} / {String(llm.model || "-")}</Tag> : null}
            {llm.call_id ? <Tag color="purple">call {String(llm.call_id)}</Tag> : null}
          </Space>
          {hasEvidence ? (
            <pre className="codebase-result">{JSON.stringify(evidenceRefs, null, 2)}</pre>
          ) : (
            <Typography.Text type="secondary">没有证据来源 metadata。旧草稿会安全跳过这一项。</Typography.Text>
          )}
        </Space>
      </div>
    </Space>
  );
}

function SkillsPanel() {
  const { message } = App.useApp();
  const showError = useMutationErrorHandler();
  const queryClient = useQueryClient();
  const [selectedSkillName, setSelectedSkillName] = useState<string>();

  const skillsQuery = useQuery({ queryKey: ["personal-skills"], queryFn: personalAgentApi.skills, retry: false });
  const skills = skillsQuery.data ?? [];
  const selectedSummary = skills.find((item) => item.name === selectedSkillName) ?? skills[0];
  const skillQuery = useQuery({
    queryKey: ["personal-skill", selectedSummary?.name],
    queryFn: () => personalAgentApi.skill(selectedSummary!.name),
    enabled: Boolean(selectedSummary?.name),
    retry: false
  });
  const versionsQuery = useQuery({
    queryKey: ["personal-skill-versions", selectedSummary?.name],
    queryFn: () => personalAgentApi.skillVersions(selectedSummary!.name),
    enabled: Boolean(selectedSummary?.name),
    retry: false
  });
  const updateCandidatesQuery = useQuery({ queryKey: ["personal-skill-update-candidates"], queryFn: personalAgentApi.skillUpdateCandidates, retry: false });
  const selectedSkill = skillQuery.data ?? selectedSummary;
  const versions = versionsQuery.data ?? [];
  const updateCandidates = updateCandidatesQuery.data ?? [];

  useEffect(() => {
    if (!selectedSkillName && selectedSummary?.name) setSelectedSkillName(selectedSummary.name);
  }, [selectedSkillName, selectedSummary?.name]);

  const evaluate = useMutation({
    mutationFn: personalAgentApi.evaluateSkill,
    onSuccess: (_result, skillName) => {
      queryClient.invalidateQueries({ queryKey: ["personal-skills"] });
      queryClient.invalidateQueries({ queryKey: ["personal-skill", skillName] });
      message.success("Skill 评测完成。");
    },
    onError: showError
  });
  const approveSkillCandidate = useMutation({
    mutationFn: (candidate: PersonalSkillUpdateCandidate) =>
      personalAgentApi.approveSkillUpdateCandidate(candidate.id, { reviewer: "local_user", comment: "approved from Skills panel" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personal-skill-update-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["personal-skills"] });
      queryClient.invalidateQueries({ queryKey: ["personal-skill-versions"] });
      message.success("Skill 修改候选已批准并激活新版本。");
    },
    onError: showError
  });
  const rejectSkillCandidate = useMutation({
    mutationFn: (candidate: PersonalSkillUpdateCandidate) =>
      personalAgentApi.rejectSkillUpdateCandidate(candidate.id, { reviewer: "local_user", comment: "rejected from Skills panel" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personal-skill-update-candidates"] });
      message.success("Skill 修改候选已驳回。");
    },
    onError: showError
  });

  return (
    <div className="skills-panel">
      {skillsQuery.error ? (
        <Alert
          type="error"
          showIcon
          message="Skills 加载失败"
          description={skillsQuery.error instanceof Error ? skillsQuery.error.message : String(skillsQuery.error)}
          action={<Button size="small" onClick={() => skillsQuery.refetch()}>刷新</Button>}
          className="personal-send-error"
        />
      ) : null}
      <div className="artifact-layout">
        <div className="artifact-list">
          <Space className="full-width" style={{ marginBottom: 8 }}>
            <Button icon={<ReloadOutlined />} onClick={() => skillsQuery.refetch()} loading={skillsQuery.isFetching}>
              刷新 Skills
            </Button>
          </Space>
          <List
            loading={skillsQuery.isLoading}
            dataSource={skills}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 skills" /> }}
            renderItem={(item) => (
              <List.Item
                className={`artifact-item ${item.name === selectedSkill?.name ? "active" : ""}`}
                onClick={() => setSelectedSkillName(item.name)}
              >
                <Space direction="vertical" size={3} className="full-width">
                  <Space wrap>
                    <Typography.Text strong>{item.display_name}</Typography.Text>
                        <Tag>{item.document_type}</Tag>
                    <Tag>v{item.active_version_index || 1}</Tag>
                  </Space>
                  <Typography.Text type="secondary" className="personal-small">{item.name}</Typography.Text>
                </Space>
              </List.Item>
            )}
          />
        </div>
        <div className="artifact-preview">
          {!selectedSkill ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个 skill" />
          ) : (
            <Tabs
              items={[
                {
                  key: "current",
                  label: "SKILL.md",
                  children: (
                    <Space direction="vertical" size={10} className="full-width">
                      <Space wrap>
                        <Typography.Text strong>{selectedSkill.display_name}</Typography.Text>
                        <Tag>{selectedSkill.name}</Tag>
                        <Tag>{selectedSkill.skill_kind}</Tag>
                        <Tag color={selectedSkill.exists ? "green" : "red"}>{selectedSkill.exists ? "file exists" : "missing file"}</Tag>
                      </Space>
                      <Typography.Text copyable className="personal-small">{selectedSkill.path}</Typography.Text>
                      <pre className="artifact-preview-text">{selectedSkill.skill_markdown || ""}</pre>
                    </Space>
                  )
                },
                {
                  key: "versions",
                  label: "版本",
                  children: (
                    <List
                      loading={versionsQuery.isLoading}
                      dataSource={versions}
                      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无版本" /> }}
                      renderItem={(item) => (
                        <List.Item>
                          <Space direction="vertical" size={4} className="full-width">
                            <Space wrap>
                              <Tag>v{item.version_index}</Tag>
                              <Tag>{item.status}</Tag>
                              <Typography.Text type="secondary">{item.created_by}</Typography.Text>
                              <Typography.Text type="secondary">{item.created_at}</Typography.Text>
                            </Space>
                            <Typography.Text className="personal-small">{item.version_uid}</Typography.Text>
                          </Space>
                        </List.Item>
                      )}
                    />
                  )
                },
                {
                  key: "candidates",
                  label: "候选更新",
                  children: (
                    <List
                      loading={updateCandidatesQuery.isLoading}
                      dataSource={updateCandidates}
                      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Skill 修改候选" /> }}
                      renderItem={(item) => (
                        <List.Item
                          actions={item.status === "candidate" ? [
                            <Button key="approve" size="small" type="primary" loading={approveSkillCandidate.isPending} onClick={() => approveSkillCandidate.mutate(item)}>批准</Button>,
                            <Button key="reject" size="small" danger loading={rejectSkillCandidate.isPending} onClick={() => rejectSkillCandidate.mutate(item)}>驳回</Button>
                          ] : []}
                        >
                          <Space direction="vertical" size={6} className="full-width">
                            <Space wrap>
                              <Tag color={item.status === "candidate" ? "orange" : item.status === "approved" ? "green" : "red"}>{item.status}</Tag>
                              <Tag>{item.target_skill}</Tag>
                              <Typography.Text type="secondary">{item.created_at}</Typography.Text>
                            </Space>
                            <Typography.Text strong>{item.reason || "Agent 提出的 Skill 小范围修改"}</Typography.Text>
                            <pre className="codebase-result">{item.proposed_change}</pre>
                            {item.risk ? <Typography.Text type="secondary">风险：{item.risk}</Typography.Text> : null}
                          </Space>
                        </List.Item>
                      )}
                    />
                  )
                },
                {
                  key: "eval",
                  label: "评测",
                  children: (
                    <Space direction="vertical" size={10} className="full-width">
                      <Button
                        type="primary"
                        icon={<ExperimentOutlined />}
                        loading={evaluate.isPending}
                        onClick={() => evaluate.mutate(selectedSkill.name)}
                      >
                        运行评测
                      </Button>
                      <List
                        dataSource={selectedSkill.eval_runs ?? []}
                        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无评测结果" /> }}
                        renderItem={(item) => (
                          <List.Item>
                            <Space direction="vertical" size={4} className="full-width">
                              <Space wrap>
                                <Tag color={item.status === "passed" ? "green" : "red"}>{item.status}</Tag>
                                <Tag>score {item.score}</Tag>
                                <Typography.Text type="secondary">{item.created_at}</Typography.Text>
                              </Space>
                              <pre className="codebase-result">{JSON.stringify(item.checks, null, 2)}</pre>
                            </Space>
                          </List.Item>
                        )}
                      />
                    </Space>
                  )
                }
              ]}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function DevTasksPanel({
  selectedSessionUid,
  currentTaskUid,
  onContinueTask,
  onOpenDrafts
}: {
  selectedSessionUid?: string;
  currentTaskUid?: string;
  onContinueTask?: (task: PersonalDevTask) => void;
  onOpenDrafts: (draftUid?: string, taskUid?: string, sessionUid?: string) => void;
}) {
  const [scope, setScope] = useState<"session" | "all">(selectedSessionUid ? "session" : "all");
  const sessionUid = scope === "session" ? selectedSessionUid || "" : "";
  const tasksQuery = useQuery({
    queryKey: ["personal-dev-tasks-panel", scope, sessionUid],
    queryFn: () => personalAgentApi.devTaskList(sessionUid || undefined),
    retry: false,
    refetchInterval: 10000
  });
  const tasks = tasksQuery.data ?? [];

  useEffect(() => {
    if (!selectedSessionUid && scope === "session") setScope("all");
  }, [scope, selectedSessionUid]);

  return (
    <Space direction="vertical" size={12} className="full-width">
      <Space wrap>
        <Select
          value={scope}
          onChange={setScope}
          options={[
            { value: "session", label: "当前 session", disabled: !selectedSessionUid },
            { value: "all", label: "全部任务" },
          ]}
        />
        <Typography.Text type="secondary" className="personal-small">
          {scope === "session" ? "只看当前会话的开发任务" : "显示所有会话的开发任务"}
        </Typography.Text>
      </Space>
      <List
        loading={tasksQuery.isLoading}
        dataSource={tasks}
        locale={{ emptyText: "当前还没有开发任务。发送开发任务类需求后，这里会出现 task 状态和阶段进度。" }}
        renderItem={(task) => {
          const focusStage = task.stages.find((stage) => stage.document_type === task.current_step) ?? task.stages.find((stage) => stage.effective_status !== "done");
          const doneCount = task.stages.filter((stage) => stage.effective_status === "done").length;
          return (
            <List.Item className={`dev-task-list-item ${task.task_uid === currentTaskUid ? "active" : ""}`}>
              <Space direction="vertical" size={8} className="full-width">
                <Space wrap>
                  <Typography.Text strong>{task.title}</Typography.Text>
                  <Tag color={task.status === "blocked" ? "volcano" : task.status === "completed" ? "green" : task.status === "archived" ? "default" : "blue"}>
                    {task.status}
                  </Tag>
                  <Tooltip title={task.task_uid}>
                    <Tag color="blue">{taskDisplayCode(task)}</Tag>
                  </Tooltip>
                  <Button
                    size="small"
                    type="text"
                    icon={<CopyOutlined />}
                    onClick={() => navigator.clipboard?.writeText(task.task_uid)}
                  />
                  <Tag>{doneCount}/{task.stages.length}</Tag>
                </Space>
                {scope === "all" ? (
                  <Typography.Text type="secondary" className="personal-small">
                    会话 {shortId(task.session_uid)}
                  </Typography.Text>
                ) : null}
                <Typography.Text type="secondary" className="personal-small">
                  {focusStage ? `当前/下一阶段：${documentLabel(focusStage.document_type)} · ${focusStage.effective_status}` : "阶段已完成"}
                </Typography.Text>
                {task.blocked_reason ? <Typography.Text type="danger" className="personal-small">阻塞：{task.blocked_reason}</Typography.Text> : null}
                <Space wrap>
                  {task.stages.map((stage) => (
                    <Tag
                      key={`${task.task_uid}-${stage.document_type}`}
                      color={stage.effective_status === "done" ? "green" : stage.effective_status === "needs_revision" ? "volcano" : "default"}
                      className={stage.draft_uid ? "clickable-tag" : ""}
                      onClick={() => stage.draft_uid && onOpenDrafts(stage.draft_uid, task.task_uid, task.session_uid)}
                    >
                      {documentLabel(stage.document_type)}:{stage.effective_status}
                    </Tag>
                  ))}
                </Space>
                {task.status === "blocked" ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="任务已阻塞"
                    description={task.blocked_reason || "任务当前无法继续推进。"}
                  />
                ) : task.status !== "completed" && task.status !== "archived" ? (
                  <Button
                    size="small"
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    onClick={() => onContinueTask?.(task)}
                  >
                    继续推进{task.next_action?.stage ? `（${documentLabel(task.next_action.stage)}）` : ""}
                  </Button>
                ) : null}
              </Space>
            </List.Item>
          );
        }}
      />
    </Space>
  );
}

function CodebasePanel({
  config,
  onConfigChanged
}: {
  config?: PersonalCodebaseConfig;
  onConfigChanged: () => void;
}) {
  const { message, modal } = App.useApp();
  const showError = useMutationErrorHandler();
  const [repoPath, setRepoPath] = useState("");
  const [buildCommand, setBuildCommand] = useState("");
  const [testCommand, setTestCommand] = useState("");
  const [staticCommand, setStaticCommand] = useState("");
  const [timeoutS, setTimeoutS] = useState(120);
  const [indexQuery, setIndexQuery] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [symbolName, setSymbolName] = useState("");
  const [includePath, setIncludePath] = useState("");
  const [functionName, setFunctionName] = useState("");
  const [macroName, setMacroName] = useState("");
  const [typeName, setTypeName] = useState("");
  const [variableName, setVariableName] = useState("");
  const [impactText, setImpactText] = useState("");
  const [patchChange, setPatchChange] = useState("");
  const [patchTargetFile, setPatchTargetFile] = useState("");
  const [patchTargetSymbol, setPatchTargetSymbol] = useState("");
  const [directiveFile, setDirectiveFile] = useState("");
  const [directiveFind, setDirectiveFind] = useState("");
  const [directiveReplace, setDirectiveReplace] = useState("");
  const [patchText, setPatchText] = useState("");
  const [lastResult, setLastResult] = useState<PersonalToolResult>();
  const indexAbortControllerRef = useRef<AbortController | null>(null);
  const [indexStream, setIndexStream] = useState({
    status: "idle" as "idle" | "started" | "running" | "done" | "error" | "cancelled",
    phase: "scan",
    scannedCount: 0,
    totalCount: null as number | null,
    estimatedTotalCount: null as number | null,
    message: "",
    error: "",
  });

  useEffect(() => {
    if (!config) return;
    setRepoPath(config.repo_path || "");
    setBuildCommand(config.build_command || "");
    setTestCommand(config.test_command || "");
    setStaticCommand(config.static_analysis_command || "");
    setTimeoutS(config.tool_timeout_s || 120);
  }, [config]);

  useEffect(() => () => indexAbortControllerRef.current?.abort(), []);

  const saveConfig = useMutation({
    mutationFn: personalAgentApi.saveCodebaseConfig,
    onSuccess: () => {
      message.success("代码库配置已保存。");
      onConfigChanged();
    },
    onError: showError
  });
  const runTool = <T,>(mutationFn: (input: T) => Promise<PersonalToolResult>) =>
    useMutation({
      mutationFn,
      onSuccess: (result) => {
        setLastResult(result);
        message.success(`${result.tool_name} 完成。`);
        const output = asRecord(result.output);
        if (typeof output.patch_text === "string") setPatchText(output.patch_text);
      },
      onError: showError
    });
  const search = runTool(personalAgentApi.codebaseSearch);
  const symbol = runTool(personalAgentApi.symbolLookup);
  const includeImpact = runTool(personalAgentApi.includeImpact);
  const callGraph = runTool(personalAgentApi.callGraph);
  const macroImpact = runTool(personalAgentApi.macroImpact);
  const typeUsage = runTool(personalAgentApi.typeUsage);
  const variableUsage = runTool(personalAgentApi.variableUsage);
  const impact = runTool(personalAgentApi.impactAnalyze);
  const propose = runTool(personalAgentApi.patchPropose);
  const validate = runTool(personalAgentApi.patchValidate);
  const applyPatchMutation = runTool(personalAgentApi.patchApply);
  const build = runTool((input: { command?: string; timeout_s?: number; confirmed?: boolean }) => personalAgentApi.validationRun("build", input));
  const tests = runTool((input: { command?: string; timeout_s?: number; confirmed?: boolean }) => personalAgentApi.validationRun("tests", input));
  const staticAnalysis = runTool((input: { command?: string; timeout_s?: number; confirmed?: boolean }) => personalAgentApi.validationRun("static-analysis", input));

  const directive: PatchDirectiveInput[] = directiveFile.trim() && directiveFind
    ? [{ file_path: directiveFile.trim(), find: directiveFind, replace: directiveReplace }]
    : [];
  const repoReady = Boolean(config?.repo_path || repoPath.trim());
  const indexStreaming = indexStream.status === "started" || indexStream.status === "running";
  const indexPercent = typeof indexStream.totalCount === "number" && indexStream.totalCount > 0
    ? Math.min(100, Math.round((Math.min(indexStream.scannedCount, indexStream.totalCount) / indexStream.totalCount) * 100))
    : undefined;
  const startIndexStream = () => {
    if (!repoReady || indexStreaming) return;
    const controller = new AbortController();
    let finished = false;
    indexAbortControllerRef.current = controller;
    setLastResult(undefined);
    setIndexStream({
      status: "started",
      phase: "scan",
      scannedCount: 0,
      totalCount: null,
      estimatedTotalCount: null,
      message: "开始扫描代码仓库……",
      error: "",
    });
    void personalAgentApi.codebaseIndexStream(
      { query: indexQuery, max_files: 320 },
      (event: CodebaseIndexStreamEvent) => {
        setIndexStream({
          status: event.event === "progress" ? "running" : event.event,
          phase: event.phase || "index",
          scannedCount: event.scanned_count ?? 0,
          totalCount: event.total_count ?? null,
          estimatedTotalCount: event.estimated_total_count ?? null,
          message: event.message || "",
          error: event.error || "",
        });
        if (event.event === "done" && event.result) {
          finished = true;
          setLastResult(event.result);
        }
      },
      { signal: controller.signal },
    ).then(() => {
      if (!finished) return;
      message.success("codebase_index 完成。");
      onConfigChanged();
    }).catch((error) => {
      if (isAbortError(error)) {
        setIndexStream((current) => ({
          ...current,
          status: "cancelled",
          message: current.message || "代码库索引已取消。",
          error: "",
        }));
        return;
      }
      const text = error instanceof Error ? error.message : String(error);
      setIndexStream((current) => ({
        ...current,
        status: "error",
        message: text,
        error: text,
      }));
      message.error(text);
    }).finally(() => {
      if (indexAbortControllerRef.current === controller) {
        indexAbortControllerRef.current = null;
      }
    });
  };
  const cancelIndexStream = () => {
    if (!indexAbortControllerRef.current) return;
    indexAbortControllerRef.current.abort(new DOMException("代码库索引已取消。", "AbortError"));
    setIndexStream((current) => ({
      ...current,
      status: "cancelled",
      message: "代码库索引已取消。",
      error: "",
    }));
  };
  const handlePatchApply = () => {
    modal.confirm({
      title: "确认应用 Patch",
      content: "此操作将直接修改本地源文件，不可自动回滚。确定要应用吗？",
      okText: "确认应用",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => applyPatchMutation.mutate({ patch_text: patchText, dry_run: false, confirmed: true, comment: "personal agent confirmed apply" }),
    });
  };
  const confirmRun = (command: string, label: string, mutateFn: () => void) => {
    modal.confirm({
      title: `确认执行${label}`,
      content: `将执行以下命令：\n${command}`,
      okText: "确认执行",
      cancelText: "取消",
      onOk: mutateFn,
    });
  };

  return (
    <div className="codebase-panel">
      <Tabs
        items={[
          {
            key: "config",
            label: <span><ToolOutlined /> 配置</span>,
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Input value={repoPath} onChange={(event) => setRepoPath(event.target.value)} placeholder="本地 C 代码库绝对路径" />
                <Input value={buildCommand} onChange={(event) => setBuildCommand(event.target.value)} placeholder="白名单构建命令，例如 python -m pytest" />
                <Input value={testCommand} onChange={(event) => setTestCommand(event.target.value)} placeholder="白名单测试命令" />
                <Input value={staticCommand} onChange={(event) => setStaticCommand(event.target.value)} placeholder="白名单静态分析命令" />
                <InputNumber className="full-width" min={1} max={1800} value={timeoutS} onChange={(value) => setTimeoutS(Number(value || 120))} addonAfter="秒" />
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  loading={saveConfig.isPending}
                  onClick={() => saveConfig.mutate({
                    repo_path: repoPath,
                    build_command: buildCommand,
                    test_command: testCommand,
                    static_analysis_command: staticCommand,
                    tool_timeout_s: timeoutS
                  })}
                >
                  保存配置
                </Button>
                <ConfigSummary config={config} />
              </Space>
            )
          },
          {
            key: "index",
            label: <span><SearchOutlined /> 索引</span>,
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Input value={indexQuery} onChange={(event) => setIndexQuery(event.target.value)} placeholder="索引时的相关需求关键词，可留空" />
                <Button type="primary" icon={<BranchesOutlined />} loading={indexStreaming} disabled={!repoReady || indexStreaming} onClick={startIndexStream}>
                  扫描/更新索引
                </Button>
                {indexStream.status !== "idle" ? (
                  <div className={`codebase-index-progress state-${indexStream.status}`}>
                    <div className="codebase-index-progress-header">
                      <div className="codebase-index-progress-meta">
                        <Typography.Text strong>{indexStream.message || "正在处理代码库索引……"}</Typography.Text>
                        <Typography.Text type="secondary" className="personal-small">
                          {formatCodebaseIndexProgress(indexStream)}
                        </Typography.Text>
                      </div>
                      {indexStreaming ? <Button size="small" onClick={cancelIndexStream}>取消</Button> : null}
                    </div>
                    {indexPercent !== undefined ? (
                      <Progress
                        percent={indexPercent}
                        size="small"
                        status={
                          indexStream.status === "done"
                            ? "success"
                            : indexStream.status === "error"
                              ? "exception"
                              : indexStream.status === "cancelled"
                                ? "normal"
                                : "active"
                        }
                      />
                    ) : (
                      <div className="codebase-indeterminate-progress" aria-hidden="true">
                        <span />
                      </div>
                    )}
                    {indexStream.error ? (
                      <Typography.Text type="danger" className="personal-small">
                        {indexStream.error}
                      </Typography.Text>
                    ) : null}
                  </div>
                ) : null}
                {false ? (
                  <Typography.Text type="secondary" className="personal-small">
                    正在扫描代码库……大型项目可能需要数十秒，请耐心等待。
                  </Typography.Text>
                ) : null}
                <Divider />
                <Input.Search value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onSearch={(value) => value.trim() && search.mutate({ query: value, limit: 10 })} enterButton="搜索代码" placeholder="关键词搜索代码片段和符号" loading={search.isPending} />
                <Input.Search value={symbolName} onChange={(event) => setSymbolName(event.target.value)} onSearch={(value) => value.trim() && symbol.mutate({ name: value, limit: 20 })} enterButton="查符号" placeholder="函数/宏/类型/变量名" loading={symbol.isPending} />
                <Divider />
                <Input.Search value={functionName} onChange={(event) => setFunctionName(event.target.value)} onSearch={(value) => value.trim() && callGraph.mutate({ function_name: value, limit: 20 })} enterButton="调用图" placeholder="函数调用者/被调用者" loading={callGraph.isPending} />
                <Input.Search value={includePath} onChange={(event) => setIncludePath(event.target.value)} onSearch={(value) => value.trim() && includeImpact.mutate({ path: value })} enterButton="Include 影响" placeholder="头文件路径或 include 名称" loading={includeImpact.isPending} />
                <Input.Search value={macroName} onChange={(event) => setMacroName(event.target.value)} onSearch={(value) => value.trim() && macroImpact.mutate({ macro_name: value, limit: 20 })} enterButton="宏影响" placeholder="宏/条件编译开关" loading={macroImpact.isPending} />
                <Input.Search value={typeName} onChange={(event) => setTypeName(event.target.value)} onSearch={(value) => value.trim() && typeUsage.mutate({ type_name: value, limit: 20 })} enterButton="类型使用" placeholder="typedef / struct / enum" loading={typeUsage.isPending} />
                <Input.Search value={variableName} onChange={(event) => setVariableName(event.target.value)} onSearch={(value) => value.trim() && variableUsage.mutate({ variable_name: value, limit: 20 })} enterButton="变量读写" placeholder="全局变量或 static 变量" loading={variableUsage.isPending} />
              </Space>
            )
          },
          {
            key: "impact",
            label: <span><FileProtectOutlined /> 影响</span>,
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Input.TextArea value={impactText} onChange={(event) => setImpactText(event.target.value)} rows={5} placeholder="输入需求变更或问题描述，生成只读影响分析。" />
                <Button type="primary" icon={<FileProtectOutlined />} loading={impact.isPending} disabled={!impactText.trim()} onClick={() => impact.mutate({ change_hint: impactText, limit: 10 })}>
                  分析影响范围
                </Button>
              </Space>
            )
          },
          {
            key: "patch",
            label: <span><CodeOutlined /> Patch</span>,
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Input.TextArea value={patchChange} onChange={(event) => setPatchChange(event.target.value)} rows={3} placeholder="修改意图" />
                <Input value={patchTargetFile} onChange={(event) => setPatchTargetFile(event.target.value)} placeholder="目标文件，可选" />
                <Input value={patchTargetSymbol} onChange={(event) => setPatchTargetSymbol(event.target.value)} placeholder="目标符号，可选" />
                <Divider />
                <Input value={directiveFile} onChange={(event) => setDirectiveFile(event.target.value)} placeholder="确定性替换文件，例如 src/foo.c" />
                <Input.TextArea value={directiveFind} onChange={(event) => setDirectiveFind(event.target.value)} rows={4} placeholder="查找原文" />
                <Input.TextArea value={directiveReplace} onChange={(event) => setDirectiveReplace(event.target.value)} rows={4} placeholder="替换为" />
                <Space wrap>
                  <Button
                    type="primary"
                    icon={<CodeOutlined />}
                    loading={propose.isPending}
                    disabled={!patchChange.trim() || directive.length === 0}
                    onClick={() => propose.mutate({
                      change_text: patchChange,
                      target_file: patchTargetFile,
                      target_symbol: patchTargetSymbol,
                      directives: directive
                    })}
                  >
                    生成候选 Patch
                  </Button>
                  <Button icon={<FileProtectOutlined />} loading={validate.isPending} disabled={!patchText.trim()} onClick={() => validate.mutate({ patch_text: patchText })}>
                    校验 Patch
                  </Button>
                  <Button icon={<CheckCircleOutlined />} loading={applyPatchMutation.isPending} disabled={!patchText.trim()} onClick={() => applyPatchMutation.mutate({ patch_text: patchText, dry_run: true })}>
                    Dry-run 应用
                  </Button>
                  <Button danger icon={<CheckCircleOutlined />} loading={applyPatchMutation.isPending} disabled={!patchText.trim()} onClick={handlePatchApply}>
                    确认应用
                  </Button>
                </Space>
                <Input.TextArea value={patchText} onChange={(event) => setPatchText(event.target.value)} rows={12} className="codebase-mono" placeholder="候选 unified diff 会显示在这里。" />
              </Space>
            )
          },
          {
            key: "validation",
            label: <span><ExperimentOutlined /> 验证</span>,
            children: (
              <Space direction="vertical" size={12} className="full-width">
                <Alert type="info" showIcon message="只会执行配置页保存的精确白名单命令，且不会通过 shell 执行。" />
                <Space wrap>
                  <Button icon={<PlayCircleOutlined />} loading={build.isPending} disabled={!buildCommand.trim()} onClick={() => confirmRun(buildCommand, "构建", () => build.mutate({ command: buildCommand, timeout_s: timeoutS, confirmed: true }))}>
                    运行构建
                  </Button>
                  <Button icon={<PlayCircleOutlined />} loading={tests.isPending} disabled={!testCommand.trim()} onClick={() => confirmRun(testCommand, "测试", () => tests.mutate({ command: testCommand, timeout_s: timeoutS, confirmed: true }))}>
                    运行测试
                  </Button>
                  <Button icon={<PlayCircleOutlined />} loading={staticAnalysis.isPending} disabled={!staticCommand.trim()} onClick={() => confirmRun(staticCommand, "静态分析", () => staticAnalysis.mutate({ command: staticCommand, timeout_s: timeoutS, confirmed: true }))}>
                    静态分析
                  </Button>
                </Space>
              </Space>
            )
          }
        ]}
      />
      <Divider />
      <ResultBlock result={lastResult} />
    </div>
  );
}

function ConfigSummary({ config }: { config?: PersonalCodebaseConfig }) {
  const repo = asRecord(config?.repository);
  return (
    <div className="codebase-summary">
      <Tag color={config?.repo_path ? "green" : "orange"}>{config?.repo_path ? "已配置" : "未配置"}</Tag>
      {config?.repo_path ? <Typography.Text copyable>{config.repo_path}</Typography.Text> : null}
      {repo.last_indexed_at ? <Typography.Text type="secondary">最近索引：{String(repo.last_indexed_at)}</Typography.Text> : null}
    </div>
  );
}

function ResultBlock({ result }: { result?: PersonalToolResult }) {
  if (!result) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="工具结果会显示在这里。" />;
  }
  const ok = result.status === "ok";
  return (
    <Space direction="vertical" size={8} className="full-width">
      <Space wrap>
        <Tag color={ok ? "green" : "red"}>{result.tool_name}</Tag>
        <Tag>{result.status}</Tag>
        {result.risk_level ? <Tag>{result.risk_level}</Tag> : null}
      </Space>
      <pre className="codebase-result">{JSON.stringify(result.output, null, 2)}</pre>
    </Space>
  );
}

function formatCodebaseIndexProgress(state: {
  phase: string;
  scannedCount: number;
  totalCount: number | null;
  estimatedTotalCount: number | null;
  status: string;
}) {
  if (typeof state.totalCount === "number" && state.totalCount > 0) {
    return `${state.phase} · ${state.scannedCount}/${state.totalCount}`;
  }
  if (typeof state.estimatedTotalCount === "number" && state.estimatedTotalCount > 0) {
    return `${state.phase} · 已处理 ${state.scannedCount}，预计总量 ${state.estimatedTotalCount}`;
  }
  if (state.status === "cancelled") return `${state.phase} · 已取消`;
  return `${state.phase} · 已处理 ${state.scannedCount}，总量估算中`;
}

function LlmConfigPanel({
  config,
  status,
  saving,
  onSave
}: {
  config?: PersonalLlmConfig;
  status?: AgentLlmStatus;
  saving: boolean;
  onSave: (input: PersonalLlmConfigInput) => void;
}) {
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const runtime = status ?? config?.status;
  const options = config?.available_providers ?? [];
  const selectedOption = options.find((item) => item.value === provider);
  const modelOptions = (selectedOption?.model_options?.length ? selectedOption.model_options : selectedOption?.default_model ? [selectedOption.default_model] : [])
    .map((item) => ({ value: item }));
  const isConfigured = Boolean(runtime?.configured);

  useEffect(() => {
    if (!config) return;
    setProvider(config.provider || "deepseek");
    setModel(config.model || config.available_providers.find((item) => item.value === config.provider)?.default_model || "");
    setApiKey("");
  }, [config]);

  return (
    <Space direction="vertical" size={12} className="full-width">
      <Space wrap>
        <Tag color={isConfigured ? "green" : "red"}>
          {isConfigured ? "真实 LLM 已接入" : "真实 LLM 未配置"}
        </Tag>
        <Tag>{String(runtime?.provider || "-")} / {String(runtime?.model || "-")}</Tag>
      </Space>
      {runtime?.error ? <Alert type="warning" showIcon message={runtime.error} /> : null}
      <Select
        className="full-width"
        popupClassName="llm-select-popup"
        style={{ width: "100%" }}
        popupMatchSelectWidth={false}
        value={provider}
        options={options.map((item) => ({ value: item.value, label: item.label }))}
        onChange={(value) => {
          const next = options.find((item) => item.value === value);
          setProvider(value);
          setModel(next?.default_model || "");
        }}
      />
      <AutoComplete
        className="full-width"
        popupClassName="llm-select-popup"
        style={{ width: "100%" }}
        value={model}
        options={modelOptions}
        onChange={setModel}
        placeholder={selectedOption?.default_model ? `选择或输入模型，例如 ${selectedOption.default_model}` : "选择或输入模型"}
        filterOption={false}
      />
      <Input.Password value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="API Key，留空则保留已保存密钥" />
      <Button
        type="primary"
        block
        loading={saving}
        disabled={!provider || !model.trim()}
        onClick={() => onSave({ provider, model, api_key: apiKey, clear_other_provider_keys: true })}
      >
        保存
      </Button>
      <Typography.Text type="secondary" className="personal-small">
        {config?.api_key_name ? `${config.api_key_name}: ${config.api_key_configured ? "已保存" : "未保存"}` : ""}
      </Typography.Text>
    </Space>
  );
}

function shortId(value: string) {
  return value.length > 18 ? `${value.slice(0, 10)}...${value.slice(-6)}` : value;
}

type DraftCandidateGroup = {
  key: string;
  label: string;
  draft: PersonalArtifactDraft;
};

type DraftStageGroup = {
  key: string;
  documentType: string;
  stageIndex: number | null;
  drafts: PersonalArtifactDraft[];
  candidates: DraftCandidateGroup[];
  currentCandidate?: PersonalArtifactDraft;
};

type DraftTaskGroup = {
  key: string;
  taskUid: string;
  isUnlinked: boolean;
  taskDisplayCode: string;
  taskTitle: string;
  taskStatus: string;
  taskDisplayScope: string;
  taskSessionDisplayIndex: number | null;
  sessionUid: string;
  drafts: PersonalArtifactDraft[];
  stages: DraftStageGroup[];
};

type DraftScopeSummary = {
  taskLineCount: number;
  taskDraftCount: number;
  unlinkedDraftCount: number;
};

type DraftRelationshipFact = {
  key: string;
  label: string;
  value: string;
};

function taskDisplayCode(task?: Pick<PersonalDevTask, "display_code" | "task_uid">) {
  return task?.display_code || shortId(task?.task_uid || "");
}

function draftTaskDisplayCode(draft: Pick<PersonalArtifactDraft, "task_display_code" | "task_uid">) {
  return draft.task_display_code || (draft.task_uid ? shortId(draft.task_uid) : "");
}

function draftCandidateLabel(draft: Pick<PersonalArtifactDraft, "task_uid" | "candidate_index" | "stage_candidate_count" | "current_revision">) {
  if (!draft.task_uid || !draft.candidate_index) return `版本 v${draft.current_revision}`;
  const total = draft.stage_candidate_count || draft.candidate_index;
  return `候选 ${draft.candidate_index}/${total} · 版本 v${draft.current_revision}`;
}

function buildDraftScopeSummary(drafts: PersonalArtifactDraft[]) {
  const summary: DraftScopeSummary = {
    taskLineCount: 0,
    taskDraftCount: 0,
    unlinkedDraftCount: 0,
  };
  const taskLineKeys = new Set<string>();
  for (const draft of drafts) {
    if (draft.task_uid) {
      summary.taskDraftCount += 1;
      taskLineKeys.add(`${draft.session_uid || "__no_session__"}:${draft.task_uid}`);
    } else {
      summary.unlinkedDraftCount += 1;
    }
  }
  summary.taskLineCount = taskLineKeys.size;
  return `${summary.taskLineCount} 条任务线，${summary.taskDraftCount} 份任务候选草稿，${summary.unlinkedDraftCount} 份未关联草稿。候选表示同一阶段的不同生成结果；版本表示单份草稿的内部修订。`;
}

function draftTaskStateLabel(draft: PersonalArtifactDraft) {
  if (draft.status === "deleted") {
    return "回收站草稿";
  }
  if (!draft.task_uid) {
    return draft.is_active ? "会话当前草稿" : "未关联草稿";
  }
  if (draft.is_stage_current_candidate) {
    return draft.status === "quality_failed" ? "当前采用候选 · 质量未通过" : "当前采用候选";
  }
  return "历史候选";
}

function draftTaskStateColor(draft: PersonalArtifactDraft) {
  if (draft.status === "deleted") {
    return "default";
  }
  if (!draft.task_uid) {
    return draft.is_active ? "processing" : "default";
  }
  if (draft.is_stage_current_candidate) {
    return draft.status === "quality_failed" ? "red" : "green";
  }
  return "default";
}

function stageGroupSummary(stage: DraftStageGroup) {
  const parts = [`${stage.candidates.length} 个候选`];
  if (stage.currentCandidate?.candidate_index) {
    parts.push(`当前采用候选 ${stage.currentCandidate.candidate_index}`);
  }
  if (stage.currentCandidate?.status === "quality_failed") {
    parts.push("质量未通过");
  }
  return `${documentLabel(stage.documentType)} · ${parts.join(" · ")}`;
}

function taskGroupSummary(group: DraftTaskGroup) {
  if (group.isUnlinked) {
    return `这些草稿不属于任何任务线，不会参与任务阶段候选比较。`;
  }
  const currentStage = group.drafts.find((draft) => draft.task_current_step)?.task_current_step || group.stages[0]?.documentType || "";
  const parts = [group.taskStatus || "状态待确认"];
  parts.push(currentStage ? `当前阶段：${documentLabel(currentStage)}` : "当前阶段待确认");
  return parts.join(" · ");
}

function draftRelationshipSummary(draft: PersonalArtifactDraft) {
  if (!draft.task_uid) {
    return `这是一份未关联草稿 / 版本 v${draft.current_revision}。`;
  }
  const taskCode = draftTaskDisplayCode(draft);
  const stageLabel = documentLabel(draft.document_type);
  const candidateLabel = draft.candidate_index && draft.stage_candidate_count
    ? `候选 ${draft.candidate_index}/${draft.stage_candidate_count}`
    : "候选待确认";
  return `这份文档属于 ${taskCode} / ${stageLabel} / ${candidateLabel} / 版本 v${draft.current_revision}。`;
}

function draftRelationshipDetail(draft: PersonalArtifactDraft) {
  if (!draft.task_uid) {
    return draft.is_active
      ? "它不属于任何任务线，不会出现在任务列表，也不会参与任务阶段候选比较。它当前也是当前会话下该文档类型的当前草稿，仍可查看、导出和修订。"
      : "它不属于任何任务线，不会出现在任务列表，也不会参与任务阶段候选比较。仍可查看、导出和修订。";
  }
  const sessionHint = draft.is_active
    ? "它同时也是当前会话下该文档类型的当前草稿，但这不等同于任务阶段当前采用候选。"
    : "";
  if (draft.is_stage_current_candidate) {
    return draft.status === "quality_failed"
      ? `当前任务正在采用这份候选继续推进，但它尚未通过质量检查。${sessionHint}`.trim()
      : `当前任务正在采用这份候选继续推进。${sessionHint}`.trim();
  }
  return `这是一份历史候选，可用于对比，但当前任务阶段并未采用它继续推进。${sessionHint}`.trim();
}

function draftRelationshipFacts(draft: PersonalArtifactDraft): DraftRelationshipFact[] {
  if (!draft.task_uid) {
    return [
      { key: "identity", label: "草稿身份", value: "未关联草稿" },
      { key: "version", label: "版本", value: `v${draft.current_revision}` },
      { key: "task", label: "任务线", value: "不属于任何任务线" },
      { key: "candidate", label: "候选关系", value: "不参与任务阶段候选比较" },
      { key: "session", label: "会话状态", value: draft.is_active ? "会话当前草稿" : "独立草稿" },
    ];
  }
  return [
    { key: "task", label: "任务线", value: `${draftTaskDisplayCode(draft)} · ${draft.task_title || "未命名任务"}` },
    { key: "stage", label: "阶段", value: documentLabel(draft.document_type) },
    {
      key: "candidate",
      label: "候选关系",
      value: `${draftCandidateLabel(draft)} · ${draft.is_stage_current_candidate ? "当前采用候选" : "历史候选"}`
    },
    { key: "version", label: "版本", value: `v${draft.current_revision}` },
    { key: "quality", label: "质量状态", value: draft.status === "quality_failed" ? "质量未通过" : "当前无失败标记" },
    { key: "session", label: "会话状态", value: draft.is_active ? "会话当前草稿" : "不是会话当前草稿" },
  ];
}

function buildDraftTaskGroups(drafts: PersonalArtifactDraft[]): DraftTaskGroup[] {
  const taskOrder: string[] = [];
  const taskMap = new Map<string, DraftTaskGroup>();
  for (const draft of drafts) {
    const taskKey = draft.task_uid ? `${draft.session_uid || "__no_session__"}:${draft.task_uid}` : "unassigned";
    if (!taskMap.has(taskKey)) {
      taskOrder.push(taskKey);
      taskMap.set(taskKey, {
        key: taskKey,
        taskUid: draft.task_uid || "",
        isUnlinked: !draft.task_uid,
        taskDisplayCode: draftTaskDisplayCode(draft),
        taskTitle: draft.task_title || (!draft.task_uid ? "未关联草稿" : ""),
        taskStatus: draft.task_status || "",
        taskDisplayScope: draft.task_display_scope || "",
        taskSessionDisplayIndex: draft.task_session_display_index ?? null,
        sessionUid: draft.task_uid ? draft.session_uid || "" : "",
        drafts: [],
        stages: [],
      });
    }
    taskMap.get(taskKey)!.drafts.push(draft);
  }
  return taskOrder.map((taskKey) => {
    const taskGroup = taskMap.get(taskKey)!;
    const stageOrder: string[] = [];
    const stageMap = new Map<string, DraftStageGroup>();
    for (const draft of taskGroup.drafts) {
      const stageKey = draft.document_type;
      if (!stageMap.has(stageKey)) {
        stageOrder.push(stageKey);
        stageMap.set(stageKey, {
          key: `${taskGroup.key}:${stageKey}`,
          documentType: draft.document_type,
          stageIndex: draft.stage_index ?? null,
          drafts: [],
          candidates: [],
        });
      }
      stageMap.get(stageKey)!.drafts.push(draft);
    }
    return {
      ...taskGroup,
      drafts: [...taskGroup.drafts].sort((left, right) => {
        const leftCandidate = left.candidate_index ?? Number.MAX_SAFE_INTEGER;
        const rightCandidate = right.candidate_index ?? Number.MAX_SAFE_INTEGER;
        if (leftCandidate !== rightCandidate) return leftCandidate - rightCandidate;
        return (left.id ?? 0) - (right.id ?? 0);
      }),
      stages: stageOrder
        .map((stageKey) => {
          const group = stageMap.get(stageKey)!;
          const sortedDrafts = [...group.drafts].sort((left, right) => {
            const leftCandidate = left.candidate_index ?? Number.MAX_SAFE_INTEGER;
            const rightCandidate = right.candidate_index ?? Number.MAX_SAFE_INTEGER;
            if (leftCandidate !== rightCandidate) return leftCandidate - rightCandidate;
            return (left.id ?? 0) - (right.id ?? 0);
          });
          return {
            ...group,
            drafts: sortedDrafts,
            currentCandidate: sortedDrafts.find((draft) => draft.is_stage_current_candidate),
            candidates: sortedDrafts.map((draft) => ({
              key: `${group.key}:${draft.draft_uid}`,
              label: draftCandidateLabel(draft),
              draft,
            })),
          };
        })
        .sort((left, right) => {
          const leftIndex = left.stageIndex ?? Number.MAX_SAFE_INTEGER;
          const rightIndex = right.stageIndex ?? Number.MAX_SAFE_INTEGER;
          if (leftIndex !== rightIndex) return leftIndex - rightIndex;
          return left.documentType.localeCompare(right.documentType);
        }),
    };
  }).sort((left, right) => {
    if (left.isUnlinked !== right.isUnlinked) return left.isUnlinked ? 1 : -1;
    const leftIndex = left.taskSessionDisplayIndex ?? Number.MAX_SAFE_INTEGER;
    const rightIndex = right.taskSessionDisplayIndex ?? Number.MAX_SAFE_INTEGER;
    if (leftIndex !== rightIndex) return leftIndex - rightIndex;
    if (left.sessionUid !== right.sessionUid) return left.sessionUid.localeCompare(right.sessionUid);
    return left.taskUid.localeCompare(right.taskUid);
  });
}

function documentLabel(value: string) {
  return documentTypeOptions.find((item) => item.value === value)?.label || value;
}

function exportFormatOptionsForDraft(draft: PersonalArtifactDraft) {
  if (draft.content_format === "diff" || ["c_code_diff", "unit_test_code_or_diff"].includes(draft.document_type)) return ["diff"];
  if (draft.content_format === "json_table" || draft.document_type === "test_case_spec") return ["xlsx"];
  return ["md", "docx"];
}

function defaultExportFormat(draft: PersonalArtifactDraft) {
  return exportFormatOptionsForDraft(draft)[0] || "md";
}

function isDiffDraft(draft: PersonalArtifactDraft) {
  return draft.content_format === "diff" || ["c_code_diff", "unit_test_code_or_diff"].includes(draft.document_type);
}

function shouldRenderMarkdownDraft(draft: PersonalArtifactDraft) {
  if (isDiffDraft(draft)) return false;
  if (draft.content_format === "markdown") return true;
  return [
    "requirement_analysis_report",
    "requirement_breakdown",
    "functional_spec",
    "detailed_design",
  ].includes(draft.document_type);
}

function regenerateDraftDescription(draft: PersonalArtifactDraft) {
  if (draft.document_type === "c_code_diff") {
    return "将基于当前代码 Patch 草稿创建新的候选草稿，不会应用到项目文件。确认继续？";
  }
  return "将创建新的草稿候选版本，不会直接修改项目文件。确认继续？";
}

function canContinueDraftTask(draft: Pick<PersonalArtifactDraft, "task_uid" | "task_status">) {
  if (!draft.task_uid) return false;
  return draft.task_status === "active" || draft.task_status === "blocked";
}

function qualityFailureSummary(draft: PersonalArtifactDraft): string[] {
  if (draft.status !== "quality_failed") return [];

  const generation = asRecord(draft.metadata?.generation);
  const quality = asRecord(generation.quality);
  if (!Object.keys(quality).length) return [];

  const blocking = Array.isArray(quality.blocking_failures) ? quality.blocking_failures : [];
  const blockingTexts = blocking
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  if (blockingTexts.length) return blockingTexts.slice(0, 2);

  const checks = Array.isArray(quality.checks) ? quality.checks : [];
  const failedChecks = checks
    .map((check) => asRecord(check))
    .filter((check) => check.passed === false)
    .map((check) => String(check.message || check.detail || check.name || check.check || "").trim())
    .filter(Boolean);
  if (failedChecks.length) return failedChecks.slice(0, 2);

  return ["质量检查未通过，请查看质量页。"];
}

function canBulkTrashDraft(draft: PersonalArtifactDraft) {
  if (draft.status === "deleted") return false;
  if (!draft.task_uid) return true;
  return !draft.is_stage_current_candidate;
}

function formatDraftTrashImpactSummary(draft: PersonalArtifactDraft, impact: PersonalArtifactDraftManagementImpact) {
  const taskCode = draft.task_uid ? draftTaskDisplayCode(draft) : "未关联草稿";
  const stageLabel = documentLabel(impact.affected_document_type || draft.document_type);
  return `这会把 ${taskCode} 的 ${stageLabel} 当前采用候选移入回收站。`;
}

function formatDraftTrashImpactDetail(draft: PersonalArtifactDraft, impact: PersonalArtifactDraftManagementImpact) {
  if (impact.fallback_draft_uid) {
    const fallbackLabel = impact.fallback_candidate_index ? `候选 ${impact.fallback_candidate_index}` : "其他候选";
    const fallbackTitle = impact.fallback_title ? `“${impact.fallback_title}”` : "该候选";
    return `确认后系统会改用 ${fallbackLabel} ${fallbackTitle} 继续作为该阶段的当前采用候选。`;
  }
  return `确认后，这个任务阶段将暂时没有当前采用候选，需要后续重新生成或恢复草稿。`;
}

function asDraftManagementImpact(value: unknown): PersonalArtifactDraftManagementImpact {
  const record = asRecord(value);
  return {
    was_session_active: Boolean(record.was_session_active),
    was_stage_current_candidate: Boolean(record.was_stage_current_candidate),
    fallback_draft_uid: typeof record.fallback_draft_uid === "string" ? record.fallback_draft_uid : "",
    fallback_candidate_index: typeof record.fallback_candidate_index === "number" ? record.fallback_candidate_index : null,
    fallback_title: typeof record.fallback_title === "string" ? record.fallback_title : "",
    affected_task_uid: typeof record.affected_task_uid === "string" ? record.affected_task_uid : "",
    affected_document_type: typeof record.affected_document_type === "string" ? record.affected_document_type : "",
    affected_session_uid: typeof record.affected_session_uid === "string" ? record.affected_session_uid : "",
  };
}


function buildLineDiff(before: string, after: string) {
  const oldLines = before.split(/\r?\n/);
  const newLines = after.split(/\r?\n/);
  const max = Math.max(oldLines.length, newLines.length);
  const rows: string[] = [];
  for (let index = 0; index < max; index += 1) {
    const left = oldLines[index] ?? "";
    const right = newLines[index] ?? "";
    if (left === right) {
      rows.push(`  ${right}`);
    } else {
      if (left) rows.push(`- ${left}`);
      if (right) rows.push(`+ ${right}`);
    }
  }
  return rows.join("\n");
}

function useMutationErrorHandler() {
  const { message } = App.useApp();
  return (error: unknown) => {
    message.error(error instanceof Error ? error.message : String(error));
  };
}

function pickCurrentTask(tasks: PersonalDevTask[]): PersonalDevTask | undefined {
  return tasks.find((task) => task.status === "active")
    ?? tasks.find((task) => task.status === "blocked")
    ?? tasks.find((task) => task.status === "completed")
    ?? tasks[0];
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === "AbortError";
}

function omitKey<T>(items: Record<string, T>, key: string): Record<string, T> {
  if (!(key in items)) return items;
  const { [key]: _removed, ...rest } = items;
  return rest;
}

function omitKeys<T>(items: Record<string, T>, keys: string[]): Record<string, T> {
  const remove = new Set(keys);
  return Object.fromEntries(Object.entries(items).filter(([key]) => !remove.has(key)));
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
