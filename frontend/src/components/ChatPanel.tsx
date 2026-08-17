import { useRef } from "react";
import { ApiError } from "../api/client";
import { streamChat } from "../api/sse";
import { useChat } from "../store/chat";
import type { ChatMessage } from "../types";
import ChatInput from "./ChatInput";
import MessageList from "./MessageList";

function newMessageId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === "AbortError";
}

function unexpectedStreamError(error: unknown) {
  if (error instanceof ApiError) {
    return { code: `http_${error.status}`, message: error.message, partial: false };
  }
  if (error instanceof Error) {
    return { code: "stream_error", message: error.message, partial: false };
  }
  return { code: "stream_error", message: "流式回答异常结束，请重试。", partial: false };
}

export default function ChatPanel() {
  const messages = useChat((state) => state.messages);
  const isStreaming = useChat((state) => state.isStreaming);
  const addMessage = useChat((state) => state.addMessage);
  const appendToken = useChat((state) => state.appendToken);
  const setMessageStatus = useChat((state) => state.setMessageStatus);
  const finishMessage = useChat((state) => state.finishMessage);
  const failMessage = useChat((state) => state.failMessage);
  const setStreaming = useChat((state) => state.setStreaming);
  const controllerRef = useRef<AbortController | null>(null);
  const activeRunRef = useRef(0);

  const submit = (query: string) => {
    if (controllerRef.current) {
      return;
    }

    const runId = activeRunRef.current + 1;
    activeRunRef.current = runId;
    const controller = new AbortController();
    const userMessage: ChatMessage = { id: newMessageId(), role: "user", content: query };
    const assistantMessage: ChatMessage = {
      id: newMessageId(),
      role: "assistant",
      content: "",
      status: "streaming",
    };

    controllerRef.current = controller;
    addMessage(userMessage);
    addMessage(assistantMessage);
    setStreaming(true);

    const isCurrent = () => activeRunRef.current === runId && !controller.signal.aborted;

    void streamChat(query, {
      signal: controller.signal,
      onToken: (token) => {
        if (isCurrent()) {
          appendToken(assistantMessage.id, token);
        }
      },
      onDone: (meta) => {
        if (isCurrent()) {
          finishMessage(assistantMessage.id, meta);
        }
      },
      onError: (error) => {
        if (isCurrent()) {
          // 无论 partial 标志如何，都保留已经收到的文本。
          failMessage(assistantMessage.id, error);
        }
      },
    })
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

  const stop = () => {
    const controller = controllerRef.current;
    if (!controller) {
      return;
    }
    controller.abort();
  };

  return (
    <section className="mx-auto flex h-full w-full max-w-5xl flex-col bg-gray-50">
      <MessageList messages={messages} />
      <ChatInput isStreaming={isStreaming} onSubmit={submit} onStop={stop} />
    </section>
  );
}
