import type { ChatMessage, ChatStreamError } from "./types";

const STREAM_ERROR_TITLES: Record<string, string> = {
  authentication: "模型服务鉴权失败",
  rate_limit: "模型服务限流或额度不足",
  model_not_found: "模型配置不可用",
  empty_response: "模型未返回有效内容",
  retrieval_error: "知识库检索服务不可用",
  generation_error: "回答生成异常",
};

export function messageStatusLabel(message: ChatMessage) {
  if (message.status === "streaming") return "正在生成";
  if (message.status === "stopped") return "已停止，未写入会话历史";
  if (message.status === "error") {
    return message.error?.partial
      ? "已保留部分正文，未写入会话历史"
      : "生成失败，未写入会话历史";
  }
  if (message.meta?.is_kb_busy) return "知识库正在更新，本次未执行检索。";
  if (message.meta?.is_reject) return "严格知识库模式：未找到可引用资料。";
  if (message.meta?.is_kb_stale) return "知识库已更新，此回答未写入缓存。";
  return null;
}

export function messageSources(message: ChatMessage) {
  if (
    message.role !== "assistant"
    || message.meta?.is_kb !== true
    || message.meta.is_reject
    || message.meta.is_kb_busy
    || message.meta.is_kb_stale
  ) {
    return [];
  }
  return message.meta?.sources?.filter((source) => source.trim().length > 0) ?? [];
}

export function streamErrorTitle(error: ChatStreamError) {
  return STREAM_ERROR_TITLES[error.code] ?? "回答失败";
}
