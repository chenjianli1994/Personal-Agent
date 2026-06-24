import { ApiError } from "../api/client";

export const ACCEPTED_UPLOAD_EXTENSIONS = ["txt", "md", "docx", "pdf", "xlsx", "xlsm"] as const;
export const ACCEPTED_UPLOAD_ACCEPT = ACCEPTED_UPLOAD_EXTENSIONS.map((extension) => `.${extension}`).join(",");
export const ACCEPTED_UPLOAD_SET = new Set<string>(ACCEPTED_UPLOAD_EXTENSIONS);
export const MAX_UPLOAD_SIZE = 20 * 1024 * 1024;
export const MAX_COMPOSER_ATTACHMENTS = 5;
export const COMPOSER_PLACEHOLDER = "输入问题或任务，回车发送，Shift+Enter 换行";
export const EMPTY_CHAT_DESCRIPTION = "输入问题或任务，Agent 会给出回应。";
export const PENDING_MESSAGE = "Agent 正在思考。";
export const UPLOAD_HINT = `支持 ${ACCEPTED_UPLOAD_EXTENSIONS.join("、")}，单文件 ≤ ${Math.round(MAX_UPLOAD_SIZE / 1024 / 1024)}MB`;

export const QUICK_START_PROMPTS = [
  "帮我写一份技术方案",
  "分析这个项目的代码结构",
  "把这个需求拆解为开发任务",
] as const;

export type MessageAttachment = {
  source_uid?: string;
  title?: string;
  source_type?: string;
  original_name?: string;
};

export function formatUploadLimitMb() {
  return Math.round(MAX_UPLOAD_SIZE / 1024 / 1024);
}

export function friendlyUploadError(raw: string, error?: unknown): string {
  const apiError = error instanceof ApiError ? error : undefined;
  const text = (raw || apiError?.message || "").trim();
  const normalized = text.toLowerCase();
  if (apiError?.status === 413 || normalized.includes("too large") || normalized.includes("content too large")) {
    return `文件过大（上限 ${formatUploadLimitMb()}MB）`;
  }
  if (normalized.includes("unsupported file type") || normalized.includes("please save as .docx")) {
    return `该文件类型不支持，请上传 ${ACCEPTED_UPLOAD_EXTENSIONS.join("/")}`;
  }
  if (normalized.includes("uploaded file is empty") || normalized.includes("empty") || normalized.includes("no readable content")) {
    return "文件内容为空或无法读取";
  }
  if (normalized.includes("is required to parse")) {
    return "服务器缺少解析该格式的组件，请联系维护者";
  }
  return text || "上传失败";
}

export function toMessageAttachments(value: unknown): MessageAttachment[] {
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
