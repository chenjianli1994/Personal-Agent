import {
  Alert,
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
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message
} from "antd";
import { BookOutlined, BranchesOutlined, BulbOutlined, CheckCircleOutlined, CloudDownloadOutlined, CodeOutlined, CopyOutlined, DeleteOutlined, DiffOutlined, EditOutlined, ExperimentOutlined, FileDoneOutlined, FileProtectOutlined, FileTextOutlined, HistoryOutlined, MoreOutlined, PlayCircleOutlined, ReloadOutlined, RobotOutlined, SearchOutlined, ThunderboltOutlined, ToolOutlined, UploadOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { personalAgentApi } from "./api";
import { ChatPanel, composerAcceptedExtensions, maxComposerAttachments } from "./ChatPanel";
import type { ComposerAttachment, LocalMessage } from "./ChatPanel";
import type {
  AgentLlmStatus,
  PatchDirectiveInput,
  PersonalArtifactDraft,
  PersonalCodebaseConfig,
  PersonalInputSource,
  PersonalInboxItem,
  PersonalKnowledgeItem,
  PersonalLearningCandidate,
  PersonalLlmConfig,
  PersonalLlmConfigInput,
  PersonalSession,
  PersonalSkill,
  PersonalSkillUpdateCandidate,
  PersonalToolResult
} from "./types";
import "../styles/personal-agent.css";

type DraftReviewTab = "preview" | "revise" | "versions" | "quality";

export function PersonalAgentApp() {
  const queryClient = useQueryClient();
  const [selectedSessionUid, setSelectedSessionUid] = useState<string>();
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [attachmentsUploading, setAttachmentsUploading] = useState(false);
  const [inputHistory, setInputHistory] = useState<string[]>([]);
  const [inputHistoryIndex, setInputHistoryIndex] = useState<number | null>(null);
  const [optimistic, setOptimistic] = useState<LocalMessage[]>([]);
  const [localError, setLocalError] = useState("");
  const [llmSettingsOpen, setLlmSettingsOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [draftsOpen, setDraftsOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [learningOpen, setLearningOpen] = useState(false);
  const [codebaseOpen, setCodebaseOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [draftToOpenUid, setDraftToOpenUid] = useState<string>();
  const [draftPanelTab, setDraftPanelTab] = useState<"create" | "current">("create");
  const [renamingSession, setRenamingSession] = useState<PersonalSession>();
  const [renameTitle, setRenameTitle] = useState("");
  const hasAutoSelected = useRef(false);

  const contextQuery = useQuery({ queryKey: ["personal-context"], queryFn: personalAgentApi.context, retry: false });
  const llmConfigQuery = useQuery({ queryKey: ["personal-llm-config"], queryFn: personalAgentApi.llmConfig, retry: false, refetchInterval: 15000 });
  const llmStatusQuery = useQuery({ queryKey: ["personal-llm-status"], queryFn: personalAgentApi.llmStatus, retry: false, refetchInterval: 15000 });
  const codebaseConfigQuery = useQuery({ queryKey: ["personal-codebase-config"], queryFn: personalAgentApi.codebaseConfig, retry: false });
  const sessionsQuery = useQuery({ queryKey: ["personal-sessions"], queryFn: personalAgentApi.sessions, retry: false, refetchInterval: 10000 });
  const selectedSessionQuery = useQuery({
    queryKey: ["personal-session", selectedSessionUid],
    queryFn: () => personalAgentApi.session(selectedSessionUid!),
    enabled: Boolean(selectedSessionUid),
    retry: false
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

  const chatTurn = useMutation({
    mutationFn: personalAgentApi.chatTurn,
    onSuccess: (result) => {
      setSelectedSessionUid(result.session.session_uid);
      setOptimistic([]);
      setLocalError("");
      queryClient.setQueryData(["personal-session", result.session.session_uid], result.session);
      queryClient.invalidateQueries({ queryKey: ["personal-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["personal-drafts"] });
      const learningReflection = result.message?.metadata?.learning_reflection as { candidate_id?: number | null } | undefined;
      if (learningReflection?.candidate_id) {
        queryClient.invalidateQueries({ queryKey: ["personal-learning-summary"] });
        queryClient.invalidateQueries({ queryKey: ["personal-learning-candidates"] });
        queryClient.invalidateQueries({ queryKey: ["personal-knowledge"] });
      }
    },
    onError: (error) => {
      const text = error instanceof Error ? error.message : String(error);
      setLocalError(text);
      message.error(text);
      setOptimistic((items) => items.map((item) => (item.pending ? { ...item, content: `发送失败：${text}`, pending: false } : item)));
    }
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
      if (selectedSessionUid === result.session_uid) {
        setSelectedSessionUid(remaining[0]?.session_uid);
        setOptimistic([]);
        setLocalError("");
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
      if (selectedSessionUid && deleted.has(selectedSessionUid)) {
        setSelectedSessionUid(remaining[0]?.session_uid);
        setOptimistic([]);
        setLocalError("");
      }
    },
    onError: (error) => message.error(error instanceof Error ? error.message : String(error))
  });

  const sessions = sessionsQuery.data ?? [];
  const selectedSession = selectedSessionQuery.data ?? sessions.find((session) => session.session_uid === selectedSessionUid);

  useEffect(() => {
    if (!hasAutoSelected.current && !selectedSessionUid && sessions[0]) {
      hasAutoSelected.current = true;
      setSelectedSessionUid(sessions[0].session_uid);
    }
  }, [selectedSessionUid, sessions]);

  const addAttachments = (files: File[]) => {
    if (!files.length) return;
    const accepted: ComposerAttachment[] = [];
    for (const file of files) {
      const extension = file.name.split(".").pop()?.toLowerCase() || "";
      if (!composerAcceptedExtensions.has(extension)) {
        message.warning(`不支持的文件类型：${file.name}`);
        continue;
      }
      accepted.push({ id: `${file.name}-${file.lastModified}-${file.size}-${crypto.randomUUID()}`, file, status: "ready" });
    }
    if (!accepted.length) return;
    setAttachments((items) => {
      const remaining = Math.max(0, maxComposerAttachments - items.length);
      if (accepted.length > remaining) {
        message.warning(`一次最多添加 ${maxComposerAttachments} 个文件。`);
      }
      return [...items, ...accepted.slice(0, remaining)];
    });
  };

  const removeAttachment = (id: string) => {
    setAttachments((items) => items.filter((item) => item.id !== id));
  };

  const send = async () => {
    const content = draft.trim();
    if (!content) {
      if (attachments.length) message.warning("请输入对附件的分析指令。");
      return;
    }
    if (chatTurn.isPending || attachmentsUploading) return;
    const now = new Date().toISOString();
    setInputHistory((items) => [content, ...items.filter((item) => item !== content)].slice(0, 50));
    setInputHistoryIndex(null);
    setDraft("");
    setLocalError("");
    let sourceUids: string[] = [];
    if (attachments.length) {
      setAttachmentsUploading(true);
      try {
        const uploaded: string[] = [];
        for (const attachment of attachments) {
          if (attachment.status === "uploaded" && attachment.sourceUid) {
            uploaded.push(attachment.sourceUid);
            continue;
          }
          setAttachments((items) => items.map((item) => (item.id === attachment.id ? { ...item, status: "uploading", error: undefined } : item)));
          const source = await personalAgentApi.uploadSource(attachment.file, { make_active: false });
          uploaded.push(source.source_uid);
          setAttachments((items) => items.map((item) => (item.id === attachment.id ? { ...item, status: "uploaded", sourceUid: source.source_uid } : item)));
        }
        sourceUids = uploaded;
      } catch (error) {
        const text = error instanceof Error ? error.message : String(error);
        setLocalError(text);
        message.error(text);
        setAttachments((items) => items.map((item) => (item.status === "uploading" ? { ...item, status: "error", error: text } : item)));
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
    setOptimistic([
      { role: "user", content, created_at: now, metadata: optimisticAttachments.length ? { attachments: optimisticAttachments } : {} },
      { role: "assistant", content: "Agent 正在思考。", created_at: now, pending: true }
    ]);
    chatTurn.mutate(
      { session_uid: selectedSessionUid, content, source_uids: sourceUids },
      {
        onError: () => {
          setDraft(content);
        },
        onSuccess: () => {
          setAttachments([]);
          queryClient.invalidateQueries({ queryKey: ["personal-sources"] });
        }
      }
    );
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
      <Layout.Sider width={300} theme="light" className="personal-agent-sidebar">
        <Sidebar
          sessions={sessions}
          selectedSessionUid={selectedSessionUid}
          onSelect={setSelectedSessionUid}
          onRename={(session) => {
            setRenamingSession(session);
            setRenameTitle(session.title || "");
          }}
          onDelete={(session) => {
            Modal.confirm({
              title: "删除会话",
              content: `确定删除“${session.title || "Agent 会话"}”？`,
              okText: "删除",
              okButtonProps: { danger: true },
              cancelText: "取消",
              onOk: () => deleteSession.mutate(session.session_uid)
            });
          }}
          onBulkDelete={(sessionUids, onDeleted) => {
            Modal.confirm({
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
            setSelectedSessionUid(undefined);
            setOptimistic([]);
            setLocalError("");
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
          sending={chatTurn.isPending}
          localError={localError}
          llmStatus={llmStatusQuery.data}
          onOpenLlmSettings={() => setLlmSettingsOpen(true)}
          onOpenSources={() => setSourcesOpen(true)}
          onOpenDrafts={(draftUid) => {
            setDraftToOpenUid(draftUid);
            setDraftPanelTab("current");
            setDraftsOpen(true);
          }}
          onOpenKnowledge={() => setKnowledgeOpen(true)}
          onOpenLearning={() => setLearningOpen(true)}
          onOpenCodebase={() => setCodebaseOpen(true)}
          onOpenSkills={() => setSkillsOpen(true)}
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
  const checkedSet = new Set(checkedSessionUids);
  const allChecked = sessions.length > 0 && checkedSessionUids.length === sessions.length;
  const partlyChecked = checkedSessionUids.length > 0 && checkedSessionUids.length < sessions.length;
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
        dataSource={sessions}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有会话" /> }}
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
    onError: showMutationError
  });
  const uploadSource = useMutation({
    mutationFn: (file: File) => personalAgentApi.uploadSource(file, { make_active: true }),
    onSuccess: (source) => {
      setFailedUploadFile(undefined);
      setFailedUploadError("");
      refreshSources(source);
      message.success("文件已解析。");
    },
    onError: (error, file) => {
      setFailedUploadFile(file);
      setFailedUploadError(error instanceof Error ? error.message : String(error));
      showMutationError(error);
    }
  });
  const activateSource = useMutation({
    mutationFn: personalAgentApi.activateSource,
    onSuccess: (source) => {
      refreshSources(source);
      message.success("已设为当前材料。");
    },
    onError: showMutationError
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
    onError: showMutationError
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
                      <Button size="small" icon={<ReloadOutlined />} loading={uploadSource.isPending} onClick={() => uploadSource.mutate(failedUploadFile)}>
                        重试
                      </Button>
                    }
                  />
                ) : null}
                <Upload.Dragger
                  multiple={false}
                  showUploadList={false}
                  accept=".txt,.md,.docx,.pdf,.xlsx,.xlsm"
                  customRequest={(options) => {
                    const file = options.file as File;
                    uploadSource.mutate(file, {
                      onSuccess: () => options.onSuccess?.({}, file),
                      onError: (error) => options.onError?.(error as Error)
                    });
                  }}
                >
                  <p className="ant-upload-drag-icon"><UploadOutlined /></p>
                  <p className="ant-upload-text">拖入或选择输入材料</p>
                  <p className="ant-upload-hint">支持 txt、md、docx、pdf、xlsx、xlsm</p>
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
                          Modal.confirm({
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
  selectedSessionUid
}: {
  openDraftUid?: string;
  activeTab: "create" | "current";
  onTabChange: (value: "create" | "current") => void;
  selectedSessionUid?: string;
}) {
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

  const draftsQuery = useQuery({
    queryKey: ["personal-drafts", selectedSessionUid || ""],
    queryFn: () => personalAgentApi.draftList(selectedSessionUid),
    retry: false
  });
  const sourcesQuery = useQuery({ queryKey: ["personal-sources"], queryFn: personalAgentApi.sources, retry: false });
  const drafts = draftsQuery.data ?? [];
  const activeDraft = drafts.find((item) => item.is_active);
  const selectedDraftSummary = drafts.find((item) => item.draft_uid === selectedDraftUid) ?? activeDraft ?? drafts[0];
  const draftDetailQuery = useQuery({
    queryKey: ["personal-draft", selectedDraftSummary?.draft_uid],
    queryFn: () => personalAgentApi.draftDetail(selectedDraftSummary!.draft_uid),
    enabled: Boolean(selectedDraftSummary?.draft_uid),
    retry: false
  });
  const draftDetail = draftDetailQuery.data ?? selectedDraftSummary;
  const selectedRevision = draftDetail?.revisions?.find((item) => item.revision_index === selectedRevisionIndex);
  const previewContent = selectedRevision?.content ?? draftDetail?.content ?? "";

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
    }
  }, [openDraftUid]);

  useEffect(() => {
    if (draftDetail?.content !== undefined) {
      setRevisionContent(draftDetail.content);
      setSelectedRevisionIndex(undefined);
      setCompareRevisionIndex(undefined);
      setExportFormat(defaultExportFormat(draftDetail));
    }
  }, [draftDetail?.draft_uid, draftDetail?.current_revision, draftDetail?.content]);

  const refreshDrafts = (draft?: PersonalArtifactDraft) => {
    queryClient.invalidateQueries({ queryKey: ["personal-drafts"] });
    if (draft?.draft_uid) {
      setSelectedDraftUid(draft.draft_uid);
      setReviewTab("preview");
      setSelectedRevisionIndex(undefined);
      setCompareRevisionIndex(undefined);
      queryClient.setQueryData(["personal-draft", draft.draft_uid], draft);
    }
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
    onError: showMutationError
  });
  const reviseDraft = useMutation({
    mutationFn: ({ draftUid, content }: { draftUid: string; content: string }) =>
      personalAgentApi.reviseDraftManual(draftUid, { content, make_active: true }),
    onSuccess: (draft) => {
      refreshDrafts(draft);
      message.success(`已保存 v${draft.current_revision}。`);
    },
    onError: showMutationError
  });
  const reviseDraftNatural = useMutation({
    mutationFn: ({ draftUid, feedback }: { draftUid: string; feedback: string }) =>
      personalAgentApi.reviseDraft(draftUid, { feedback, make_active: true }),
    onSuccess: (draft) => {
      setRevisionFeedback("");
      refreshDrafts(draft);
      message.success(`已根据修订意见生成 v${draft.current_revision}。`);
    },
    onError: showMutationError
  });
  const activateDraft = useMutation({
    mutationFn: personalAgentApi.activateDraft,
    onSuccess: (draft) => {
      refreshDrafts(draft);
      message.success("已设为当前草稿。");
    },
    onError: showMutationError
  });
  const exportDraft = useMutation({
    mutationFn: ({ draftUid, format }: { draftUid: string; format: string }) =>
      personalAgentApi.exportDraft(draftUid, { format }),
    onSuccess: (result) => {
      message.success(`已导出 ${result.file_name}。`);
      window.open(personalAgentApi.draftDownloadUrl(result.draft_uid, result.export_format), "_blank", "noopener,noreferrer");
    },
    onError: showMutationError
  });
  const regenerateDraft = useMutation({
    mutationFn: (draft: PersonalArtifactDraft) => {
      const prompt = `重新生成${documentLabel(draft.document_type)}：${draft.title}`;
      if (draft.document_type === "unit_test_code_or_diff") {
        return personalAgentApi.proposeUnitTestCodeDraft({ prompt, source_uids: draft.source_uid ? [draft.source_uid] : [] });
      }
      if (draft.document_type === "c_code_diff") {
        return personalAgentApi.createDraft({
          document_type: draft.document_type,
          title: `${draft.title} 重新生成`,
          content: draft.content || "",
          content_format: draft.content_format,
          source_uid: draft.source_uid,
          metadata: { regenerated_from: draft.draft_uid, boundary: "copied_diff_draft_without_apply" },
          make_active: true
        });
      }
      return personalAgentApi.proposeDocumentDraft({ prompt, document_type: draft.document_type, source_uids: draft.source_uid ? [draft.source_uid] : [] });
    },
    onSuccess: (draft) => {
      refreshDrafts(draft);
      message.success("已重新生成 draft。");
    },
    onError: showMutationError
  });
  const exportOptions = draftDetail ? exportFormatOptionsForDraft(draftDetail) : [];
  const compareRevision = draftDetail?.revisions?.find((item) => item.revision_index === compareRevisionIndex);
  const compareText = draftDetail && compareRevision ? buildLineDiff(compareRevision.content, draftDetail.content || "") : "";

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
                  <List
                    loading={draftsQuery.isLoading}
                    dataSource={drafts}
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有草稿" /> }}
                    renderItem={(item) => (
                      <List.Item
                        className={`artifact-item ${item.draft_uid === selectedDraftSummary?.draft_uid ? "active" : ""}`}
                        onClick={() => {
                          setSelectedDraftUid(item.draft_uid);
                          setSelectedRevisionIndex(undefined);
                          setReviewTab("preview");
                        }}
                      >
                        <div className="artifact-title-picker">
                          <Typography.Text strong ellipsis title={item.title}>{item.title}</Typography.Text>
                          <Space size={4} wrap={false}>
                            <Tag>v{item.current_revision}</Tag>
                            {item.status === "quality_failed" ? <Tag color="red">质量未通过</Tag> : null}
                            {item.is_active ? <Tag color="green">当前</Tag> : null}
                          </Space>
                        </div>
                      </List.Item>
                    )}
                  />
                </div>
                <div className="artifact-preview">
                  {!draftDetail ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择草稿查看内容" />
                  ) : (
                    <Space direction="vertical" size={10} className="full-width">
                      <Space wrap>
                        <Typography.Text strong>{draftDetail.title}</Typography.Text>
                        <Tag>{documentLabel(draftDetail.document_type)}</Tag>
                        <Tag>{draftDetail.content_format}</Tag>
                        <Tag>当前 v{draftDetail.current_revision}</Tag>
                        {draftDetail.status === "quality_failed" ? <Tag color="red">质量未通过</Tag> : null}
                        {draftDetail.is_active ? <Tag color="green">active draft</Tag> : (
                          <Button size="small" loading={activateDraft.isPending} onClick={() => activateDraft.mutate(draftDetail.draft_uid)}>
                            设为当前
                          </Button>
                        )}
                      </Space>
                      <Space wrap>
                        <Select
                          className="artifact-export-select"
                          value={exportFormat || undefined}
                          options={exportOptions.map((item) => ({ value: item, label: item.toUpperCase() }))}
                          onChange={setExportFormat}
                        />
                        <Button
                          icon={<CloudDownloadOutlined />}
                          loading={exportDraft.isPending}
                          disabled={!exportFormat}
                          onClick={() => exportDraft.mutate({ draftUid: draftDetail.draft_uid, format: exportFormat || defaultExportFormat(draftDetail) })}
                        >
                          下载
                        </Button>
                        <Button
                          icon={<CopyOutlined />}
                          onClick={() => {
                            navigator.clipboard?.writeText(previewContent);
                            message.success("内容已复制。");
                          }}
                        >
                          复制
                        </Button>
                        <Button
                          icon={<ReloadOutlined />}
                          loading={regenerateDraft.isPending}
                          onClick={() => regenerateDraft.mutate(draftDetail)}
                        >
                          重新生成
                        </Button>
                      </Space>
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
                                <div className="artifact-inline-revise">
                                  <Input.TextArea
                                    value={revisionFeedback}
                                    onChange={(event) => setRevisionFeedback(event.target.value)}
                                    rows={3}
                                    placeholder="输入方向性修改意见，例如：整体更像功能规范，减少实现细节，补充边界条件和验收标准。"
                                  />
                                  <Button
                                    type="primary"
                                    icon={<CheckCircleOutlined />}
                                    loading={reviseDraftNatural.isPending}
                                    disabled={!revisionFeedback.trim()}
                                    onClick={() => reviseDraftNatural.mutate({ draftUid: draftDetail.draft_uid, feedback: revisionFeedback.trim() })}
                                  >
                                    按方向修订
                                  </Button>
                                </div>
                                <pre className="artifact-preview-text artifact-preview-text-large">{previewContent}</pre>
                              </Space>
                            )
                          },
                          {
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
                          },
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
                                    <pre className="artifact-diff-text">{compareText}</pre>
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
    onError: showMutationError
  });
  const search = useMutation({
    mutationFn: personalAgentApi.searchKnowledge,
    onSuccess: setHits,
    onError: showMutationError
  });
  const deprecate = useMutation({
    mutationFn: (knowledgeId: number) => personalAgentApi.deprecateKnowledge(knowledgeId, { reviewer: "local_user", comment: "personal knowledge deprecated" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personal-knowledge"] });
      message.success("知识条目已废弃。");
    },
    onError: showMutationError
  });
  const visibleItems = hits.length ? hits : items;

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
                <Button key="deprecate" size="small" danger loading={deprecate.isPending} onClick={() => deprecate.mutate(item.id)}>
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
    onError: showMutationError
  });
  const approve = useMutation({
    mutationFn: (candidateId: number) => personalAgentApi.approveLearningCandidate(candidateId, { reviewer: "local_user", comment: "personal learning approved" }),
    onSuccess: () => {
      refresh();
      message.success("经验已批准为长期规则。");
    },
    onError: showMutationError
  });
  const reject = useMutation({
    mutationFn: (candidateId: number) => personalAgentApi.rejectLearningCandidate(candidateId, { reviewer: "local_user", comment: "personal learning rejected" }),
    onSuccess: () => {
      refresh();
      message.success("经验已拒绝。");
    },
    onError: showMutationError
  });
  const dismiss = useMutation({
    mutationFn: (itemUid: string) => personalAgentApi.dismissMemoryLesson(itemUid, { reviewer: "local_user", comment: "personal memory dismissed" }),
    onSuccess: () => {
      refresh();
      message.success("记忆经验已撤销。");
    },
    onError: showMutationError
  });

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
                <Button key="reject" size="small" danger loading={reject.isPending} onClick={() => reject.mutate(item.id)}>
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
    onError: showMutationError
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
    onError: showMutationError
  });
  const rejectSkillCandidate = useMutation({
    mutationFn: (candidate: PersonalSkillUpdateCandidate) =>
      personalAgentApi.rejectSkillUpdateCandidate(candidate.id, { reviewer: "local_user", comment: "rejected from Skills panel" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personal-skill-update-candidates"] });
      message.success("Skill 修改候选已驳回。");
    },
    onError: showMutationError
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

function CodebasePanel({
  config,
  onConfigChanged
}: {
  config?: PersonalCodebaseConfig;
  onConfigChanged: () => void;
}) {
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

  useEffect(() => {
    if (!config) return;
    setRepoPath(config.repo_path || "");
    setBuildCommand(config.build_command || "");
    setTestCommand(config.test_command || "");
    setStaticCommand(config.static_analysis_command || "");
    setTimeoutS(config.tool_timeout_s || 120);
  }, [config]);

  const saveConfig = useMutation({
    mutationFn: personalAgentApi.saveCodebaseConfig,
    onSuccess: () => {
      message.success("代码库配置已保存。");
      onConfigChanged();
    },
    onError: showMutationError
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
      onError: showMutationError
    });
  const index = runTool(personalAgentApi.codebaseIndex);
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
                <Button type="primary" icon={<BranchesOutlined />} loading={index.isPending} disabled={!repoReady} onClick={() => index.mutate({ query: indexQuery, max_files: 320 })}>
                  扫描/更新索引
                </Button>
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
                  <Button danger icon={<CheckCircleOutlined />} loading={applyPatchMutation.isPending} disabled={!patchText.trim()} onClick={() => applyPatchMutation.mutate({ patch_text: patchText, dry_run: false, confirmed: true, comment: "personal agent confirmed apply" })}>
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
                  <Button icon={<PlayCircleOutlined />} loading={build.isPending} disabled={!buildCommand.trim()} onClick={() => build.mutate({ command: buildCommand, timeout_s: timeoutS, confirmed: true })}>
                    运行构建
                  </Button>
                  <Button icon={<PlayCircleOutlined />} loading={tests.isPending} disabled={!testCommand.trim()} onClick={() => tests.mutate({ command: testCommand, timeout_s: timeoutS, confirmed: true })}>
                    运行测试
                  </Button>
                  <Button icon={<PlayCircleOutlined />} loading={staticAnalysis.isPending} disabled={!staticCommand.trim()} onClick={() => staticAnalysis.mutate({ command: staticCommand, timeout_s: timeoutS, confirmed: true })}>
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
  const isFake = runtime?.fake_provider || runtime?.provider === "fake";
  const isConfigured = Boolean(runtime?.configured && !isFake);

  useEffect(() => {
    if (!config) return;
    setProvider(config.provider || "deepseek");
    setModel(config.model || config.available_providers.find((item) => item.value === config.provider)?.default_model || "");
    setApiKey("");
  }, [config]);

  return (
    <Space direction="vertical" size={12} className="full-width">
      <Space wrap>
        <Tag color={isFake ? "orange" : isConfigured ? "green" : "red"}>
          {isFake ? "测试 Fake" : isConfigured ? "真实 LLM 已接入" : "真实 LLM 未配置"}
        </Tag>
        <Tag>{String(runtime?.provider || "-")} / {String(runtime?.model || "-")}</Tag>
      </Space>
      {runtime?.error ? <Alert type="warning" showIcon message={runtime.error} /> : null}
      <Select
        value={provider}
        options={options.map((item) => ({ value: item.value, label: item.label }))}
        onChange={(value) => {
          const next = options.find((item) => item.value === value);
          setProvider(value);
          setModel(next?.default_model || "");
        }}
      />
      <Input value={model} onChange={(event) => setModel(event.target.value)} placeholder={selectedOption?.default_model || "model"} />
      <Input.Password value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="API Key，留空则保留已保存密钥" />
      <Button
        type="primary"
        block
        loading={saving}
        disabled={!provider || provider === "fake" || !model.trim()}
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

function showMutationError(error: unknown) {
  message.error(error instanceof Error ? error.message : String(error));
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
