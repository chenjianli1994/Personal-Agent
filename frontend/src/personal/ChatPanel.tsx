import { App, Alert, Badge, Button, Collapse, Dropdown, Empty, Input, Space, Tag, Tooltip, Typography } from "antd";
import {
  ApiOutlined,
  BookOutlined,
  BulbFilled,
  BulbOutlined,
  CodeOutlined,
  CopyOutlined,
  FileDoneOutlined,
  FileProtectOutlined,
  FileTextOutlined,
  HistoryOutlined,
  MoreOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  UploadOutlined,
  UserOutlined,
} from "@ant-design/icons";
import type { DragEvent, KeyboardEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { MarkdownMessage } from "./MarkdownMessage";
import {
  ACCEPTED_UPLOAD_ACCEPT,
  COMPOSER_PLACEHOLDER,
  EMPTY_CHAT_DESCRIPTION,
  MAX_COMPOSER_ATTACHMENTS,
  PENDING_MESSAGE,
  QUICK_START_PROMPTS,
  toMessageAttachments,
} from "./constants";
import { useThemeMode } from "./theme";
import type {
  AgentLlmStatus,
  PendingVisualState,
  PersonalDevTask,
  PersonalDevTaskStage,
  PersonalMessage,
  PersonalRecallProvenance,
  PersonalSession,
} from "./types";
import { useTypewriter } from "./useTypewriter";

export type LocalMessage = PersonalMessage & { pending?: boolean };

export type ComposerAttachment = {
  id: string;
  file: File;
  status: "ready" | "uploading" | "uploaded" | "error";
  sourceUid?: string;
  error?: string;
  progress?: number;
};

export function ChatPanel({
  session,
  optimistic,
  draft,
  setDraft,
  attachments,
  attachmentsUploading,
  onAddAttachments,
  onRemoveAttachment,
  inputHistory,
  inputHistoryIndex,
  setInputHistoryIndex,
  onSend,
  onRetry,
  onRegenerate,
  sending,
  sendDisabled,
  onCancelSend,
  localError,
  llmStatus,
  currentTask,
  onOpenLlmSettings,
  onOpenSources,
  onOpenDrafts,
  onOpenDraftFile,
  onOpenTasks,
  onContinueTask,
  onOpenKnowledge,
  onOpenLearning,
  onOpenCodebase,
  onOpenSkills,
  learningBadgeCount,
  skillsBadgeCount,
  typewriterKey,
  animationVersion,
}: {
  session?: PersonalSession;
  optimistic: LocalMessage[];
  draft: string;
  setDraft: (value: string) => void;
  attachments: ComposerAttachment[];
  attachmentsUploading: boolean;
  onAddAttachments: (files: File[]) => void;
  onRemoveAttachment: (id: string) => void;
  inputHistory: string[];
  inputHistoryIndex: number | null;
  setInputHistoryIndex: (value: number | null) => void;
  onSend: () => void;
  onRetry?: () => void;
  onRegenerate?: () => void;
  sending: boolean;
  sendDisabled?: boolean;
  onCancelSend?: () => void;
  localError: string;
  llmStatus?: AgentLlmStatus;
  currentTask?: PersonalDevTask;
  onOpenLlmSettings: () => void;
  onOpenSources: () => void;
  onOpenDrafts: (draftUid?: string) => void;
  onOpenDraftFile: (draftUid: string) => void;
  onOpenTasks: () => void;
  onContinueTask: () => void;
  onOpenKnowledge: () => void;
  onOpenLearning: () => void;
  onOpenCodebase: () => void;
  onOpenSkills: () => void;
  learningBadgeCount?: number;
  skillsBadgeCount?: number;
  typewriterKey?: string;
  animationVersion: number;
}) {
  const { mode, toggle } = useThemeMode();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textAreaRef = useRef<{ focus: () => void; blur: () => void } | null>(null);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const messages = useMemo(() => [...(session?.messages ?? []), ...optimistic], [optimistic, session?.messages]);
  const canChat = Boolean(llmStatus?.configured || llmStatus?.provider === "fake");
  const moreBadgeCount = (learningBadgeCount ?? 0) + (skillsBadgeCount ?? 0);
  const lastAssistantIndex = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role !== "user" && !message.pending) return index;
    }
    return -1;
  }, [messages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, session?.session_uid, currentTask?.task_uid, currentTask?.updated_at]);

  useEffect(() => {
    const timer = setTimeout(() => textAreaRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, [session?.session_uid]);

  useEffect(() => {
    const handler = (event: globalThis.KeyboardEvent) => {
      const target = document.activeElement;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
      if (!event.ctrlKey && !event.metaKey) return;
      const mappings: Record<string, () => void> = {
        "1": onOpenSources,
        "2": () => onOpenDrafts(),
        "3": onOpenKnowledge,
        "4": onOpenLlmSettings,
      };
      const action = mappings[event.key];
      if (!action) return;
      event.preventDefault();
      action();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onOpenDrafts, onOpenKnowledge, onOpenLlmSettings, onOpenSources]);

  const handleDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDraggingFiles(false);
    onAddAttachments(Array.from(event.dataTransfer.files || []));
  };

  const handleDragOver = (event: DragEvent<HTMLElement>) => {
    if (event.dataTransfer.types.includes("Files")) {
      event.preventDefault();
      setDraggingFiles(true);
    }
  };

  const withMenuBadge = (label: string, count?: number) => (
    <Space size={6}>
      <span>{label}</span>
      <Badge count={count} size="small" />
    </Space>
  );

  const moreItems = [
    { key: "knowledge", label: "知识库", icon: <BookOutlined />, onClick: onOpenKnowledge },
    { key: "learning", label: "学习经验", icon: <BulbOutlined />, onClick: onOpenLearning },
    { key: "codebase", label: "代码库", icon: <CodeOutlined />, onClick: onOpenCodebase },
    { key: "skills", label: "Skills", icon: <FileProtectOutlined />, onClick: onOpenSkills },
    { key: "llm", label: "LLM 设置", icon: <ApiOutlined />, onClick: onOpenLlmSettings },
  ];
  const menuItems = moreItems.map((item) => ({
    key: item.key,
    label:
      item.key === "learning"
        ? withMenuBadge("学习经验", learningBadgeCount)
        : item.key === "skills"
          ? withMenuBadge("Skills", skillsBadgeCount)
          : item.label,
    icon: item.icon,
    onClick: item.onClick,
  }));

  return (
    <section
      className={`personal-chat ${draggingFiles ? "dragging-files" : ""}`}
      onDragEnter={handleDragOver}
      onDragOver={handleDragOver}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDraggingFiles(false);
      }}
      onDrop={handleDrop}
    >
      <header className="personal-chat-header">
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">对话</Typography.Text>
          <Typography.Title level={4}>{session?.title || "新的会话"}</Typography.Title>
        </Space>
        <Space className="personal-chat-tools" size={8}>
          <LlmBadge status={llmStatus} />
          <Button aria-label="输入材料" icon={<UploadOutlined />} onClick={onOpenSources}>
            材料
          </Button>
          <Button aria-label="当前草稿" icon={<FileDoneOutlined />} onClick={() => onOpenDrafts()}>
            草稿
          </Button>
          <Button aria-label="任务" icon={<HistoryOutlined />} onClick={onOpenTasks}>
            任务
          </Button>
          <Dropdown
            menu={{
              items: menuItems,
            }}
            trigger={["click"]}
          >
            <Badge count={moreBadgeCount} size="small" offset={[-2, 2]}>
              <Button aria-label="更多" icon={<MoreOutlined />}>
                更多
              </Button>
            </Badge>
          </Dropdown>
          <Tooltip title={mode === "dark" ? "切到亮色" : "切到暗色"}>
            <Button
              aria-label="切换主题"
              shape="circle"
              icon={mode === "dark" ? <BulbFilled /> : <BulbOutlined />}
              onClick={toggle}
            />
          </Tooltip>
        </Space>
      </header>
      <div className="personal-chat-messages" ref={scrollRef}>
        {currentTask ? <CurrentTaskBar task={currentTask} onOpenDrafts={onOpenDrafts} onContinueTask={onContinueTask} /> : null}
        {!canChat ? (
          <div className="llm-guide-card">
            <Typography.Title level={4}>配置 LLM 以开始</Typography.Title>
            <Typography.Text type="secondary">
              PersonalAgent 需要接入大语言模型才能工作。请先配置 DeepSeek 或其他提供商的 API Key。
            </Typography.Text>
            <Button type="primary" icon={<ApiOutlined />} onClick={onOpenLlmSettings}>
              打开 LLM 设置
            </Button>
          </div>
        ) : !messages.length ? (
          <Space direction="vertical" size={12} className="personal-chat-empty">
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={EMPTY_CHAT_DESCRIPTION} />
            <Space wrap style={{ justifyContent: "center" }}>
              {QUICK_START_PROMPTS.map((prompt) => (
                <Tag className="clickable-tag" color="blue" key={prompt} onClick={() => setDraft(prompt)}>
                  {prompt}
                </Tag>
              ))}
            </Space>
          </Space>
        ) : (
          messages.map((item, index) => (
            <Bubble
              key={item.message_uid || `${item.role}-${item.created_at || index}-${index}`}
              item={item}
              onOpenDrafts={onOpenDrafts}
              onOpenDraftFile={onOpenDraftFile}
              isLastAssistant={index === lastAssistantIndex}
              onRegenerate={onRegenerate}
              typewriterKey={typewriterKey}
              animationVersion={animationVersion}
            />
          ))
        )}
      </div>
      {localError ? (
        <Alert
          type="error"
          showIcon
          message={localError}
          className="personal-send-error"
          action={onRetry ? <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>重试</Button> : undefined}
        />
      ) : null}
      <footer className="personal-composer">
        <div className="personal-composer-input">
          {attachments.length ? (
            <div className="composer-attachments">
              {attachments.map((item) => (
                <Tag
                  key={item.id}
                  closable={!attachmentsUploading && item.status !== "uploading"}
                  onClose={(event) => {
                    event.preventDefault();
                    onRemoveAttachment(item.id);
                  }}
                  color={item.status === "error" ? "red" : item.status === "uploaded" ? "green" : item.status === "uploading" ? "blue" : "default"}
                  className="composer-attachment-tag"
                >
                  <FileTextOutlined /> {item.file.name}
                  {item.status === "uploading"
                    ? item.progress ? ` 上传中 ${item.progress}%` : " 上传中"
                    : item.status === "uploaded"
                      ? " 已解析"
                      : item.status === "error"
                        ? ` 失败: ${item.error || ""}`
                        : " 待发送"}
                </Tag>
              ))}
            </div>
          ) : null}
          <div className="composer-text-shell">
            <Tooltip title="添加文件">
              <Button
                aria-label="添加文件"
                type="text"
                shape="circle"
                className="composer-inline-upload"
                icon={<UploadOutlined />}
                disabled={sending || attachmentsUploading || attachments.length >= MAX_COMPOSER_ATTACHMENTS}
                onClick={() => fileInputRef.current?.click()}
              />
            </Tooltip>
            <Input.TextArea
              ref={textAreaRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onPaste={(event) => {
                const files = Array.from(event.clipboardData.files || []);
                if (!files.length) return;
                event.preventDefault();
                onAddAttachments(files);
              }}
              onKeyDown={(event: KeyboardEvent<HTMLTextAreaElement>) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  onSend();
                  return;
                }
                if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                  const caretAtStart = event.currentTarget.selectionStart === 0 && event.currentTarget.selectionEnd === 0;
                  const caretAtEnd = event.currentTarget.selectionStart === draft.length && event.currentTarget.selectionEnd === draft.length;
                  if (!caretAtStart && !caretAtEnd && draft.trim()) return;
                  if (!inputHistory.length) return;
                  event.preventDefault();
                  const nextIndex = resolveHistoryIndex({
                    key: event.key,
                    current: inputHistoryIndex,
                    historyLength: inputHistory.length,
                  });
                  setInputHistoryIndex(nextIndex);
                  setDraft(nextIndex === null ? "" : inputHistory[nextIndex] ?? "");
                }
              }}
              autoSize={{ minRows: 3, maxRows: 8 }}
              placeholder={COMPOSER_PLACEHOLDER}
              className="composer-inline-textarea"
            />
          </div>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED_UPLOAD_ACCEPT}
          className="composer-file-input"
          onChange={(event) => {
            onAddAttachments(Array.from(event.target.files || []));
            event.target.value = "";
          }}
        />
        <Tooltip title={sending ? "停止本次回复" : !canChat ? "请先配置 LLM" : sendDisabled ? "其他会话正在回复" : "发送"}>
          <Button
            aria-label={sending ? "停止回复" : "发送消息"}
            type="primary"
            danger={sending}
            shape="circle"
            icon={sending ? <StopOutlined /> : <SendOutlined />}
            loading={attachmentsUploading}
            disabled={attachmentsUploading || sendDisabled || !canChat || (!sending && !draft.trim() && !attachments.length)}
            onClick={sending ? onCancelSend : onSend}
          />
        </Tooltip>
      </footer>
    </section>
  );
}

function resolveHistoryIndex({
  key,
  current,
  historyLength,
}: {
  key: "ArrowUp" | "ArrowDown";
  current: number | null;
  historyLength: number;
}): number | null {
  if (!historyLength) return null;
  if (key === "ArrowUp") {
    if (current === null) return 0;
    return Math.min(current + 1, historyLength - 1);
  }
  if (current === null) return null;
  const next = current - 1;
  return next >= 0 ? next : null;
}

function LlmBadge({ status }: { status?: AgentLlmStatus }) {
  const isReal = Boolean(status?.configured);
  const text = isReal ? `${status?.provider || "-"} / ${status?.model || "-"}` : "LLM 未配置";
  return <Tag className="llm-badge" color={isReal ? "green" : "red"}>{text}</Tag>;
}

function CurrentTaskBar({
  task,
  onOpenDrafts,
  onContinueTask,
}: {
  task: PersonalDevTask;
  onOpenDrafts: (draftUid?: string) => void;
  onContinueTask: () => void;
}) {
  const lastAction = asRecord(task.last_action);
  const focusStage = task.stages.find((stage) => stage.document_type === task.current_step) ?? task.stages.find((stage) => stage.effective_status !== "done");
  const validationEntries = Object.values(task.validation_summary ?? {});
  return (
    <div className={`current-task-bar ${task.status === "blocked" ? "is-blocked" : ""}`}>
      <div className="current-task-main">
        <Space wrap>
          <Typography.Text strong>{task.title}</Typography.Text>
          <Tag color={task.status === "blocked" ? "volcano" : task.status === "completed" ? "green" : "blue"}>{task.status}</Tag>
          <Tag>{shortTaskId(task.task_uid)}</Tag>
          {focusStage?.draft_uid ? (
            <Button type="link" size="small" className="current-task-stage-link" onClick={() => onOpenDrafts(focusStage.draft_uid)}>
              {documentLabel(focusStage.document_type)}
            </Button>
          ) : null}
          {task.next_action?.stage ? <Tag>下一步：{documentLabel(task.next_action.stage)}</Tag> : null}
        </Space>
        <div className="current-task-stage-list">
          {task.stages.map((stage) => (
            <TaskStageTag key={`${task.task_uid}-${stage.document_type}`} stage={stage} onOpenDrafts={onOpenDrafts} />
          ))}
        </div>
      </div>
      <div className="current-task-side">
        {task.blocked_reason ? <Typography.Text type="danger">阻塞：{task.blocked_reason}</Typography.Text> : null}
        {lastAction.type || lastAction.status ? (
          <Typography.Text type="secondary" className="personal-small">
            最近动作：{String(lastAction.type || "unknown")} / {String(lastAction.status || task.status)}
          </Typography.Text>
        ) : null}
        {validationEntries.length ? (
          <Space wrap className="current-task-validation">
            {validationEntries.map((entry) => (
              <Tag key={`${entry.kind}-${entry.invocation_uid || entry.recorded_at || entry.category}`} color={validationTagColor(entry.category)}>
                {entry.kind}:{entry.category}
              </Tag>
            ))}
          </Space>
        ) : null}
        <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={onContinueTask} disabled={task.status === "completed"}>
          继续推进
        </Button>
      </div>
    </div>
  );
}

function TaskStageTag({
  stage,
  onOpenDrafts,
}: {
  stage: PersonalDevTaskStage;
  onOpenDrafts: (draftUid?: string) => void;
}) {
  const clickable = Boolean(stage.draft_uid);
  return (
    <button
      type="button"
      className={`current-task-stage-tag is-${stage.effective_status}${clickable ? " clickable" : ""}`}
      disabled={!clickable}
      onClick={() => clickable && onOpenDrafts(stage.draft_uid)}
    >
      <span>{documentLabel(stage.document_type)}</span>
      <span>{stage.effective_status}</span>
    </button>
  );
}

function Bubble({
  item,
  onOpenDrafts,
  onOpenDraftFile,
  isLastAssistant,
  onRegenerate,
  typewriterKey,
  animationVersion,
}: {
  item: LocalMessage;
  onOpenDrafts: (draftUid?: string) => void;
  onOpenDraftFile: (draftUid: string) => void;
  isLastAssistant: boolean;
  onRegenerate?: () => void;
  typewriterKey?: string;
  animationVersion: number;
}) {
  const { message } = App.useApp();
  const isUser = item.role === "user";
  const draft = asRecord(item.metadata?.draft);
  const draftUid = typeof draft.draft_uid === "string" ? draft.draft_uid : "";
  const devTask = asRecord(item.metadata?.dev_task);
  const devTaskUid = typeof devTask.task_uid === "string" ? devTask.task_uid : "";
  const devTaskStatus = typeof devTask.status === "string" ? devTask.status : "";
  const devTaskNextAction = asRecord(devTask.next_action);
  const devTaskStage = typeof devTaskNextAction.stage === "string" ? devTaskNextAction.stage : "";
  const diagnostics = collectMessageDiagnostics(item.metadata);
  const attachments = toMessageAttachments(item.metadata?.attachments);
  const provenance = recallProvenance(item.metadata?.recall_provenance);
  const content = displayMessageContent(item.content, isUser);
  const animate = !isUser && !item.pending && item.message_uid === typewriterKey;
  const { shown, done, skip } = useTypewriter(content, animate);
  const animationVersionRef = useRef(animationVersion);

  useEffect(() => {
    if (animationVersionRef.current === animationVersion) return;
    animationVersionRef.current = animationVersion;
    if (animate) skip();
  }, [animate, animationVersion, skip]);

  return (
    <div className={`personal-bubble-row ${isUser ? "user" : "assistant"}`}>
      <div className="personal-avatar">{isUser ? <UserOutlined /> : <RobotOutlined />}</div>
      <div className={`personal-bubble ${isUser ? "user" : "assistant"} ${item.pending ? "pending" : ""}`}>
        {isUser && attachments.length ? (
          <div className="message-attachments">
            {attachments.map((attachment, index) => (
              <div className="message-attachment-card" key={`${attachment.source_uid || attachment.original_name || attachment.title}-${index}`}>
                <FileTextOutlined />
                <div>
                  <Typography.Text strong>{attachment.original_name || attachment.title || "文件"}</Typography.Text>
                  <Typography.Text type="secondary" className="personal-small">文件</Typography.Text>
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {item.pending ? (
          <PendingIndicator
            startedAt={item.created_at}
            stage={item.content}
            pendingState={asPendingVisualState(item.metadata?.pending_state)}
          />
        ) : isUser ? (
          <div className="personal-bubble-content">{content}</div>
        ) : (
          <div className="typewriter-shell">
            <MarkdownMessage content={shown} />
            {animate && !done ? <span className="tw-caret" /> : null}
          </div>
        )}
        {!isUser && provenance.length ? (
          <div className="message-attachments">
            {provenance.map((entry) => (
              <div className="message-attachment-card" key={`${entry.kind}-${entry.uid}`}>
                <BookOutlined />
                <div>
                  <Typography.Text strong>{entry.title || entry.uid}</Typography.Text>
                  <Typography.Text type="secondary" className="personal-small">{entry.kind === "memory" ? "记忆来源" : "知识来源"}</Typography.Text>
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {!isUser && devTaskUid ? (
          <div className="message-attachments">
            <div className="message-attachment-card">
              <FileProtectOutlined />
              <div>
                <Typography.Text strong>开发任务 {devTaskStatus || "active"}</Typography.Text>
                <Typography.Text type="secondary" className="personal-small">
                  {devTaskStage ? `下一步：${documentLabel(devTaskStage)}` : "任务已记录到 agent_tasks"}
                </Typography.Text>
              </div>
            </div>
          </div>
        ) : null}
        {!isUser && draftUid ? (
          <Space wrap className="bubble-draft-actions">
            <Button size="small" icon={<FileDoneOutlined />} className="bubble-draft-link" onClick={() => onOpenDraftFile(draftUid)}>
              打开草稿
            </Button>
            <Button size="small" onClick={() => onOpenDrafts(draftUid)}>
              在草稿箱查看
            </Button>
          </Space>
        ) : null}
        {!isUser && diagnostics.length ? <MessageDiagnostics diagnostics={diagnostics} /> : null}
        {!isUser && !item.pending ? (
          <Space className="bubble-actions" size={4}>
            <Tooltip title="复制">
              <Button
                aria-label="复制消息"
                type="text"
                size="small"
                icon={<CopyOutlined />}
                onClick={() => {
                  navigator.clipboard?.writeText(content);
                  message.success("已复制");
                }}
              />
            </Tooltip>
            {animate && !done ? (
              <Button type="text" size="small" onClick={skip}>
                跳过动画
              </Button>
            ) : null}
            {isLastAssistant && onRegenerate ? (
              <Tooltip title="重新生成">
                <Button aria-label="重新生成" type="text" size="small" icon={<ReloadOutlined />} onClick={onRegenerate} />
              </Tooltip>
            ) : null}
          </Space>
        ) : null}
      </div>
    </div>
  );
}

function PendingIndicator({
  startedAt,
  stage,
  pendingState,
}: {
  startedAt?: string;
  stage?: string;
  pendingState?: PendingVisualState;
}) {
  const [seconds, setSeconds] = useState(0);
  const title = pendingState?.title || (stage && stage !== PENDING_MESSAGE ? stage : "正在思考");
  const hint = pendingHint(pendingState, seconds);

  useEffect(() => {
    const base = startedAt ? Date.parse(startedAt) : Date.now();
    const timer = window.setInterval(() => {
      setSeconds(Math.max(0, Math.round((Date.now() - base) / 1000)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  return (
    <div className="pending-indicator">
      <span className="pending-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <div className="pending-copy">
        <Typography.Text className="pending-title">{title}</Typography.Text>
        <Typography.Text type="secondary" className="personal-small pending-hint">
          {seconds ? `${hint} · ${seconds}s` : hint}
        </Typography.Text>
      </div>
    </div>
  );
}

function pendingHint(state: PendingVisualState | undefined, seconds: number) {
  if (seconds >= 45) return "仍在等待模型返回，请保持页面打开";
  if (seconds >= 15) return "复杂任务可能需要更久";
  if (state?.key === "route") return "正在选择合适的处理路径";
  if (state?.key === "reflect") return "正在检查是否需要记录可复用经验";
  if (state?.key === "generate") {
    switch (state.intent) {
      case "analyze_input_source":
        return "已完成：理解意图 · 正在读取并整理材料";
      case "generate_document":
        return "已完成：理解意图 · 正在生成结构化草稿";
      case "revise_draft":
        return "已完成：理解意图 · 正在按当前反馈修订草稿";
      case "propose_code_patch":
        return "已完成：理解意图 · 正在组织代码修改方案";
      case "run_validation":
        return "已完成：理解意图 · 正在准备验证步骤";
      case "learn_feedback":
        return "已完成：理解意图 · 正在整理可复用经验";
      case "answer_only":
      default:
        return "已完成：理解意图 · 等待模型返回";
    }
  }
  return "已完成：理解意图 · 等待模型返回";
}

function asPendingVisualState(value: unknown): PendingVisualState | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const key = typeof (value as Record<string, unknown>).key === "string" ? String((value as Record<string, unknown>).key) : "";
  const title = typeof (value as Record<string, unknown>).title === "string" ? String((value as Record<string, unknown>).title) : "";
  const intent = typeof (value as Record<string, unknown>).intent === "string" ? String((value as Record<string, unknown>).intent) : undefined;
  if (!title || !["initial", "route", "generate", "reflect"].includes(key)) return undefined;
  return {
    key: key as PendingVisualState["key"],
    title,
    intent,
  };
}

function MessageDiagnostics({
  diagnostics,
}: {
  diagnostics: Array<{ key: string; title: string; lines: string[] }>;
}) {
  return (
    <Collapse
      size="small"
      ghost
      className="message-diagnostics"
      items={[
        {
          key: "diagnostics",
          label: "诊断",
          children: (
            <div className="message-diagnostics-body">
              {diagnostics.map((item) => (
                <div key={item.key} className="message-diagnostic-item">
                  <Typography.Text strong>{item.title}</Typography.Text>
                  {item.lines.map((line, index) => (
                    <Typography.Text key={`${item.key}-${index}`} type="secondary" className="personal-small">
                      {line}
                    </Typography.Text>
                  ))}
                </div>
              ))}
            </div>
          ),
        },
      ]}
    />
  );
}

function recallProvenance(value: unknown): PersonalRecallProvenance[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    .map((item) => ({
      uid: typeof item.uid === "string" ? item.uid : "",
      title: typeof item.title === "string" ? item.title : "",
      kind: typeof item.kind === "string" ? item.kind : "",
    }))
    .filter((item) => item.uid);
}

function displayMessageContent(content: string, isUser: boolean) {
  if (isUser) return content;
  return content
    .replace(/[，,]?\s*draft_uid=[A-Za-z0-9_-]+(?=[。,.]|$)/g, "")
    .replace(/\s+([。,.])/g, "$1");
}

function collectMessageDiagnostics(metadata: Record<string, unknown> | undefined): Array<{ key: string; title: string; lines: string[] }> {
  const result: Array<{ key: string; title: string; lines: string[] }> = [];
  if (!metadata) return result;
  const devTask = asRecord(metadata.dev_task);
  const lastAction = asRecord(devTask.last_action);
  const policy = asRecord(lastAction.policy);
  const innerPolicy = asRecord(policy.policy);
  const route = asRecord(metadata.intent_route);
  const routeLlm = asRecord(route.llm);
  const validationSummary = asRecord(devTask.validation_summary);
  const toolResult = asRecord(metadata.tool_result);

  if (Object.keys(policy).length || Object.keys(innerPolicy).length) {
    const lines = compactLines([
      boolLine("policy", innerPolicy.allowed),
      textLine("reason", innerPolicy.reason || policy.reason),
    ]);
    if (lines.length) result.push({ key: "policy", title: "策略", lines });
  }
  if (metadata.fallback || String(route.router_source || "") === "fallback" || String(routeLlm.status || "").toLowerCase() === "failed") {
    const lines = compactLines([
      textLine("provider", routeLlm.provider),
      textLine("model", routeLlm.model),
      textLine("call_id", routeLlm.call_id),
      textLine("error", routeLlm.error),
    ]);
    if (lines.length) result.push({ key: "llm-fallback", title: "LLM fallback", lines });
  }
  if (Object.keys(validationSummary).length) {
    const lines = Object.values(validationSummary).flatMap((value) => {
      const entry = asRecord(value);
      return compactLines([
        `${String(entry.kind || "validation")}: ${String(entry.category || entry.status || "unknown")}`,
        textLine("command", entry.command_kind),
        textLine("call_id", entry.invocation_uid),
      ]);
    });
    if (lines.length) result.push({ key: "validation", title: "验证摘要", lines });
  }
  if (Object.keys(toolResult).length && (toolResult.error || String(toolResult.status || "") === "failed" || String(toolResult.status || "") === "rejected")) {
    const lines = compactLines([
      textLine("tool", toolResult.tool_name || toolResult.tool),
      textLine("status", toolResult.status),
      textLine("error", toolResult.error),
      textLine("call_id", toolResult.invocation_uid),
    ]);
    if (lines.length) result.push({ key: "tool", title: "工具错误", lines });
  }
  return result;
}

function compactLines(lines: Array<string | null>): string[] {
  return lines.filter((line): line is string => Boolean(line && line.trim()));
}

function textLine(label: string, value: unknown): string | null {
  const text = typeof value === "string" ? value.trim() : value === undefined || value === null ? "" : String(value);
  return text ? `${label}: ${text}` : null;
}

function boolLine(label: string, value: unknown): string | null {
  return typeof value === "boolean" ? `${label}: ${value ? "allowed" : "blocked"}` : null;
}

function validationTagColor(category: string): string {
  if (category === "passed") return "green";
  if (category === "timeout") return "gold";
  if (category === "config") return "orange";
  if (category === "code_logic" || category === "test_expectation") return "volcano";
  return "default";
}

function shortTaskId(value: string): string {
  return value.length > 14 ? value.slice(-8) : value;
}

function documentLabel(value: string): string {
  const labels: Record<string, string> = {
    requirement_analysis_report: "需求分析",
    requirement_breakdown: "需求拆解",
    functional_spec: "功能规格",
    detailed_design: "详细设计",
    test_case_spec: "测试用例",
    unit_test_code_or_diff: "单测代码",
    c_code_diff: "代码补丁",
  };
  return labels[value] ?? value;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
