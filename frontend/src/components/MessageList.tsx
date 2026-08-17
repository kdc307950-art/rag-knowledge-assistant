import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";

interface MessageListProps {
  messages: ChatMessage[];
}

function statusLabel(message: ChatMessage) {
  if (message.status === "streaming") return "正在生成";
  if (message.status === "stopped") return "已停止，未写入会话历史";
  if (message.status === "error") return "生成未完成，未写入会话历史";
  if (message.meta?.is_kb_busy) return "知识库正在更新，本次未执行检索。";
  if (message.meta?.is_kb_stale) return "知识库已更新，此回答未写入缓存。";
  return null;
}

export default function MessageList({ messages }: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);

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
          const status = statusLabel(message);
          return (
            <article key={message.id} className={isUser ? "self-end max-w-[85%]" : "max-w-[85%]"}>
              <p className="mb-1 text-xs font-medium text-gray-500">{isUser ? "你" : "知识库助手"}</p>
              <div
                className={
                  "whitespace-pre-wrap border px-3 py-2 text-sm leading-6 " +
                  (isUser
                    ? "border-blue-700 bg-blue-700 text-white"
                    : "border-gray-200 bg-white text-gray-900")
                }
              >
                {message.content || (message.status === "streaming" ? "正在连接..." : "未收到内容")}
              </div>
              {status && <p className="mt-1 text-xs text-amber-700">{status}</p>}
              {message.error && <p className="mt-1 text-xs text-red-700">{message.error.message}</p>}
            </article>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}
