import { useRef } from "react";
import { ApiError, friendlyApiError } from "../api/client";
import { friendlyFeedbackError, submitAnswerFeedback } from "../api/feedback";
import { streamChat, streamDraft, streamGeneral, type StreamOptions } from "../api/sse";
import { useChat } from "../store/chat";
import type { ChatAction, ChatMessage, FeedbackReason, FeedbackVerdict } from "../types";
import ChatInput from "./ChatInput";
import MessageList from "./MessageList";

function newMessageId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto?.getRandomValues?.(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === "AbortError";
}

function unexpectedStreamError(error: unknown) {
  if (error instanceof ApiError) {
    return { code: `http_${error.status}`, message: friendlyApiError(error, "请求失败"), partial: false };
  }
  if (error instanceof Error) {
    return { code: "stream_error", message: error.message, partial: false };
  }
  return { code: "stream_error", message: "流式回答异常结束，请重试。", partial: false };
}

interface StreamRun {
  query: string;
  retrievalQuery?: string;
  action?: ChatAction;
  originMessageId?: string;
}

export default function ChatPanel() {
  const messages = useChat((state) => state.messages);
  const isStreaming = useChat((state) => state.isStreaming);
  const addMessage = useChat((state) => state.addMessage);
  const appendToken = useChat((state) => state.appendToken);
  const setMessageStatus = useChat((state) => state.setMessageStatus);
  const finishMessage = useChat((state) => state.finishMessage);
  const failMessage = useChat((state) => state.failMessage);
  const markActionUsed = useChat((state) => state.markActionUsed);
  const setMessageFeedback = useChat((state) => state.setMessageFeedback);
  const setStreaming = useChat((state) => state.setStreaming);
  const controllerRef = useRef<AbortController | null>(null);
  const activeRunRef = useRef(0);

  const runStream = ({ query, retrievalQuery = query, action, originMessageId }: StreamRun) => {
    if (controllerRef.current) {
      return;
    }

    const runId = activeRunRef.current + 1;
    activeRunRef.current = runId;
    const controller = new AbortController();
    const messageId = action ? undefined : newMessageId();
    const assistantMessage: ChatMessage = {
      id: messageId ?? newMessageId(),
      messageId,
      role: "assistant",
      content: "",
      status: "streaming",
      action,
      request: action ? undefined : { query, retrievalQuery },
    };

    controllerRef.current = controller;
    if (!action) {
      addMessage({ id: newMessageId(), role: "user", content: query });
    }
    addMessage(assistantMessage);
    setStreaming(true);

    const isCurrent = () => activeRunRef.current === runId && !controller.signal.aborted;
    const callbacks: StreamOptions = {
      signal: controller.signal,
      messageId,
      onToken: (token) => {
        if (isCurrent()) {
          appendToken(assistantMessage.id, token);
        }
      },
      onDone: (meta) => {
        if (isCurrent()) {
          finishMessage(assistantMessage.id, meta);
          if (originMessageId && action) {
            markActionUsed(originMessageId, action);
          }
        }
      },
      onError: (error) => {
        if (isCurrent()) {
          // 无论 partial 标志如何，都保留已经收到的文本。
          failMessage(assistantMessage.id, error);
        }
      },
    };
    const stream = action === "general"
      ? streamGeneral(query, callbacks)
      : action === "draft"
        ? streamDraft(query, retrievalQuery, callbacks)
        : streamChat(query, callbacks);

    void stream
      .catch((error: unknown) => {
        if (activeRunRef.current !== runId) {
          return;
        }
        if (controller.signal.aborted || isAbortError(error)) {
          setMessageStatus(assistantMessage.id, "stopped");
          return;
        }
        failMessage(assistantMessage.id, unexpectedStreamError(error));
      })
      .finally(() => {
        if (activeRunRef.current !== runId) {
          return;
        }
        controllerRef.current = null;
        setStreaming(false);
      });
  };

  const submit = (query: string) => {
    runStream({ query });
  };

  const runAction = (message: ChatMessage, action: ChatAction) => {
    if (!message.request) {
      return;
    }
    runStream({
      query: message.request.query,
      retrievalQuery: message.request.retrievalQuery,
      action,
      originMessageId: message.id,
    });
  };

  const stop = () => {
    const controller = controllerRef.current;
    if (!controller) {
      return;
    }
    controller.abort();
  };

  const submitFeedback = (
    message: ChatMessage,
    verdict: FeedbackVerdict,
    reason?: FeedbackReason,
  ) => {
    const messageId = message.meta?.message_id ?? message.messageId;
    if (!messageId || message.feedback?.status === "submitting") {
      return;
    }
    setMessageFeedback(message.id, { status: "submitting", verdict });
    void submitAnswerFeedback(messageId, verdict, reason)
      .then(() => setMessageFeedback(message.id, { status: "submitted", verdict }))
      .catch((error: unknown) => setMessageFeedback(message.id, {
        status: "error",
        verdict,
        error: friendlyFeedbackError(error),
      }));
  };

  return (
    <section className="mx-auto flex h-full w-full max-w-5xl flex-col bg-gray-50">
      <MessageList
        messages={messages}
        isStreaming={isStreaming}
        onAction={runAction}
        onFeedback={submitFeedback}
      />
      <ChatInput isStreaming={isStreaming} onSubmit={submit} onStop={stop} />
    </section>
  );
}
