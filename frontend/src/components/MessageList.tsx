import { useEffect, useRef, useState } from "react";
import { messageActions, messageSources, messageStatusLabel, streamErrorTitle } from "../chatPresentation";
import { useSettings } from "../store/settings";
import type { ChatAction, ChatMessage, FeedbackReason, FeedbackVerdict } from "../types";

interface MessageListProps {
  messages: ChatMessage[];
  isStreaming?: boolean;
  onAction?: (message: ChatMessage, action: ChatAction) => void;
  onFeedback?: (message: ChatMessage, verdict: FeedbackVerdict, reason?: FeedbackReason) => void;
  showDebug?: boolean;
}

const ACTION_LABELS: Record<ChatAction, string> = {
  general: "改用通用办公回答",
  draft: "基于资料起草",
};

const FEEDBACK_REASON_OPTIONS: Array<{ value: FeedbackReason; label: string }> = [
  { value: "not_accurate", label: "内容不准确" },
  { value: "missing_source", label: "缺少依据" },
  { value: "wrong_source", label: "引用不相关" },
  { value: "refusal_unexpected", label: "不应拒答" },
  { value: "latency", label: "响应过慢" },
  { value: "irrelevant", label: "回答不相关" },
  { value: "unsafe", label: "内容不安全" },
  { value: "other", label: "其他" },
];

function FeedbackControls({
  message,
  onFeedback,
}: {
  message: ChatMessage;
  onFeedback: (message: ChatMessage, verdict: FeedbackVerdict, reason?: FeedbackReason) => void;
}) {
  const [reason, setReason] = useState<FeedbackReason | "">("");
  const feedback = message.feedback;
  const isSubmitting = feedback?.status === "submitting";
  const isSubmitted = feedback?.status === "submitted";

  if (isSubmitted) {
    return <p className="mt-2 text-xs text-gray-500">反馈已记录。</p>;
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2" aria-label="回答反馈">
      <button
        type="button"
        title="有帮助"
        aria-label="有帮助"
        disabled={isSubmitting}
        onClick={() => onFeedback(message, "up")}
        className="border border-gray-300 px-2 py-1 text-sm hover:bg-gray-100 disabled:cursor-not-allowed disabled:text-gray-400"
      >
        👍
      </button>
      <select
        aria-label="不满意的原因"
        disabled={isSubmitting}
        value={reason}
        onChange={(event) => setReason(event.target.value as FeedbackReason | "")}
        className="border border-gray-300 bg-white px-2 py-1 text-xs disabled:cursor-not-allowed"
      >
        <option value="">选择踩的原因</option>
        {FEEDBACK_REASON_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
      <button
        type="button"
        title="需要改进"
        aria-label="需要改进"
        disabled={isSubmitting || !reason}
        onClick={() => {
          if (reason) {
            onFeedback(message, "down", reason);
          }
        }}
        className="border border-gray-300 px-2 py-1 text-sm hover:bg-gray-100 disabled:cursor-not-allowed disabled:text-gray-400"
      >
        👎
      </button>
      {feedback?.status === "error" && feedback.error && (
        <p className="text-xs text-red-700">{feedback.error}</p>
      )}
    </div>
  );
}

export default function MessageList({
  messages,
  isStreaming = false,
  onAction,
  onFeedback,
  showDebug: showDebugOverride,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const storedShowDebug = useSettings((state) => state.showDebug);
  const showDebug = showDebugOverride ?? storedShowDebug;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-4 text-center text-sm text-gray-500">
        请直接输入与企业知识库相关的问题。
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
        {messages.map((message) => {
          const isUser = message.role === "user";
          const status = messageStatusLabel(message);
          const sources = messageSources(message);
          const actions = messageActions(message);
          const feedbackEligible = !isUser
            && !message.action
            && message.status === "complete"
            && message.meta?.is_kb === true
            && message.meta?.is_reject !== true
            && message.meta?.feedback_eligible === true;
          const statusClass = message.status === "error" ? "text-red-700" : "text-amber-700";
          return (
            <article key={message.id} className={isUser ? "self-end max-w-[85%]" : "max-w-[85%]"}>
              <p className="mb-1 text-xs font-medium text-gray-500">{isUser ? "你" : "知识库助手"}</p>
              {message.action === "general" && (
                <p className="mb-2 border-l-2 border-amber-500 bg-amber-50 px-2 py-1 text-xs text-amber-800">
                  通用办公回答，未依据企业知识库。
                </p>
              )}
              {message.action === "draft" && (
                <p className="mb-2 border-l-2 border-blue-500 bg-blue-50 px-2 py-1 text-xs text-blue-800">
                  基于当前资料起草。
                </p>
              )}
              <div
                className={
                  "whitespace-pre-wrap border px-3 py-2 text-sm leading-6 " +
                  (isUser
                    ? "border-blue-700 bg-blue-700 text-white"
                    : "border-gray-200 bg-white text-gray-900")
                }
              >
                {message.content || (message.status === "streaming" ? "正在连接..." : "回答未完成。")}
              </div>
              {status && <p className={`mt-1 text-xs ${statusClass}`}>{status}</p>}
              {message.error && (
                <p className="mt-1 text-xs text-red-700">
                  {streamErrorTitle(message.error)}：{message.error.message}
                </p>
              )}
              {sources.length > 0 && (
                <details className="mt-2 border-t border-gray-100 pt-2 text-xs text-gray-600">
                  <summary className="cursor-pointer text-gray-700">引用来源 ({sources.length})</summary>
                  <ol className="mt-2 list-decimal space-y-1 pl-4">
                    {sources.map((source, index) => <li key={`${message.id}-${index}`}>{source}</li>)}
                  </ol>
                </details>
              )}
              {message.meta?.citation_validation?.valid === false && (
                <p className="mt-1 text-xs text-amber-700">
                  回答包含无法对应本次资料的引用标识，建议复核。
                </p>
              )}
              {showDebug && message.meta?.thought && (
                <details className="mt-2 border-t border-gray-100 pt-2 text-xs text-gray-600">
                  <summary className="cursor-pointer text-gray-700">回答依据</summary>
                  <p className="mt-2 whitespace-pre-wrap leading-5">{message.meta.thought}</p>
                </details>
              )}
              {actions.length > 0 && onAction && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {actions.map((action) => (
                    <button
                      key={action}
                      type="button"
                      disabled={isStreaming}
                      onClick={() => onAction(message, action)}
                      className="border border-blue-200 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-400"
                    >
                      {ACTION_LABELS[action]}
                    </button>
                  ))}
                </div>
              )}
              {feedbackEligible && onFeedback && (
                <FeedbackControls message={message} onFeedback={onFeedback} />
              )}
            </article>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}
