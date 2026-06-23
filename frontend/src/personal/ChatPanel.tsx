import { Alert, Button, Collapse, Empty, Input, Space, Tag, Tooltip, Typography } from "antd";
import {
  ApiOutlined,
  BookOutlined,
  BulbOutlined,
  CodeOutlined,
  FileDoneOutlined,
  FileProtectOutlined,
  FileTextOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  UploadOutlined,
  UserOutlined
} from "@ant-design/icons";
import type { DragEvent, KeyboardEvent } from "react";
import { useEffect, useRef, useState } from "react";
import type {
  AgentLlmStatus,
  PersonalDevTask,
  PersonalDevTaskStage,
  PersonalMessage,
  PersonalRecallProvenance,
  PersonalSession
} from "./types";

export type LocalMessage = PersonalMessage & { pending?: boolean };

export type ComposerAttachment = {
  id: string;
  file: File;
  status: "ready" | "uploading" | "uploaded" | "error";
  sourceUid?: string;
  error?: string;
};

type MessageAttachment = {
  source_uid?: string;
  title?: string;
  source_type?: string;
  original_name?: string;
};

const composerAccept = ".txt,.md,.docx,.pdf,.xlsx,.xlsm";
export const composerAcceptedExtensions = new Set(["txt", "md", "docx", "pdf", "xlsx", "xlsm"]);
export const maxComposerAttachments = 5;

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
  onOpenSkills
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
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const messages = [...(session?.messages ?? []), ...optimistic];

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, session?.session_uid, currentTask?.task_uid, currentTask?.updated_at]);

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
        <Space wrap className="personal-chat-tools">
          <LlmBadge status={llmStatus} />
          <Tooltip title="输入材料">
            <Button shape="circle" icon={<UploadOutlined />} onClick={onOpenSources} />
          </Tooltip>
          <Tooltip title="当前草稿">
            <Button shape="circle" icon={<FileDoneOutlined />} onClick={() => onOpenDrafts()} />
          </Tooltip>
          <Tooltip title="任务">
            <Button shape="circle" icon={<HistoryOutlined />} onClick={onOpenTasks} />
          </Tooltip>
          <Tooltip title="知识库">
            <Button shape="circle" icon={<BookOutlined />} onClick={onOpenKnowledge} />
          </Tooltip>
          <Tooltip title="学习经验">
            <Button shape="circle" icon={<BulbOutlined />} onClick={onOpenLearning} />
          </Tooltip>
          <Tooltip title="代码库">
            <Button shape="circle" icon={<CodeOutlined />} onClick={onOpenCodebase} />
          </Tooltip>
          <Tooltip title="Skills">
            <Button shape="circle" icon={<FileProtectOutlined />} onClick={onOpenSkills} />
          </Tooltip>
          <Tooltip title="LLM 设置">
            <Button shape="circle" icon={<ApiOutlined />} onClick={onOpenLlmSettings} />
          </Tooltip>
        </Space>
      </header>
      <div className="personal-chat-messages" ref={scrollRef}>
        {currentTask ? <CurrentTaskBar task={currentTask} onOpenDrafts={onOpenDrafts} onContinueTask={onContinueTask} /> : null}
        {!messages.length ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="输入问题或任务，Agent 会给出回应。" />
        ) : (
          messages.map((item, index) => (
            <Bubble
              key={`${item.role}-${item.created_at || index}-${index}`}
              item={item}
              onOpenDrafts={onOpenDrafts}
              onOpenDraftFile={onOpenDraftFile}
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
                  {item.status === "uploading" ? " 上传中" : item.status === "uploaded" ? " 已解析" : item.status === "error" ? ` 失败: ${item.error || ""}` : " 待发送"}
                </Tag>
              ))}
            </div>
          ) : null}
          <div className="composer-text-shell">
            <Tooltip title="添加文件">
              <Button
                type="text"
                shape="circle"
                className="composer-inline-upload"
                icon={<UploadOutlined />}
                disabled={sending || attachmentsUploading || attachments.length >= maxComposerAttachments}
                onClick={() => fileInputRef.current?.click()}
              />
            </Tooltip>
            <Input.TextArea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
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
              placeholder="随便问点什么"
              className="composer-inline-textarea"
            />
          </div>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={composerAccept}
          className="composer-file-input"
          onChange={(event) => {
            onAddAttachments(Array.from(event.target.files || []));
            event.target.value = "";
          }}
        />
        <Tooltip title={sending ? "停止本次回复" : sendDisabled ? "其他会话正在回复" : "发送"}>
          <Button
            type="primary"
            danger={sending}
            shape="circle"
            icon={sending ? <StopOutlined /> : <SendOutlined />}
            loading={attachmentsUploading}
            disabled={attachmentsUploading || sendDisabled || (!sending && !draft.trim() && !attachments.length)}
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
  return <Tag color={isReal ? "green" : "red"}>{text}</Tag>;
}

function CurrentTaskBar({
  task,
  onOpenDrafts,
  onContinueTask
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
  onOpenDrafts
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
  onOpenDraftFile
}: {
  item: LocalMessage;
  onOpenDrafts: (draftUid?: string) => void;
  onOpenDraftFile: (draftUid: string) => void;
}) {
  const isUser = item.role === "user";
  const draft = asRecord(item.metadata?.draft);
  const draftUid = typeof draft.draft_uid === "string" ? draft.draft_uid : "";
  const devTask = asRecord(item.metadata?.dev_task);
  const devTaskUid = typeof devTask.task_uid === "string" ? devTask.task_uid : "";
  const devTaskStatus = typeof devTask.status === "string" ? devTask.status : "";
  const devTaskNextAction = asRecord(devTask.next_action);
  const devTaskStage = typeof devTaskNextAction.stage === "string" ? devTaskNextAction.stage : "";
  const diagnostics = collectMessageDiagnostics(item.metadata);
  const attachments = messageAttachments(item.metadata?.attachments);
  const provenance = recallProvenance(item.metadata?.recall_provenance);

  return (
    <div className={`personal-bubble-row ${isUser ? "user" : "assistant"}`}>
      <div className="personal-avatar">{isUser ? <UserOutlined /> : <RobotOutlined />}</div>
      <div className={`personal-bubble ${isUser ? "user" : "assistant"} ${item.pending ? "pending" : ""}`}>
        <Typography.Text strong>{isUser ? "你" : "Agent"}</Typography.Text>
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
        <div className="personal-bubble-content">{displayMessageContent(item.content, isUser)}</div>
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
      </div>
    </div>
  );
}

function MessageDiagnostics({
  diagnostics
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
          )
        }
      ]}
    />
  );
}

function messageAttachments(value: unknown): MessageAttachment[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    .map((item) => ({
      source_uid: typeof item.source_uid === "string" ? item.source_uid : "",
      title: typeof item.title === "string" ? item.title : "",
      source_type: typeof item.source_type === "string" ? item.source_type : "",
      original_name: typeof item.original_name === "string" ? item.original_name : "",
    }));
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
