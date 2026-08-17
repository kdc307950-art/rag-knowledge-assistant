import { useState } from "react";

interface ChatInputProps {
  isStreaming: boolean;
  onSubmit: (query: string) => void;
  onStop: () => void;
}

export default function ChatInput({ isStreaming, onSubmit, onStop }: ChatInputProps) {
  const [query, setQuery] = useState("");
  const trimmedQuery = query.trim();

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!trimmedQuery || isStreaming) {
      return;
    }
    onSubmit(trimmedQuery);
    setQuery("");
  };

  return (
    <form onSubmit={submit} className="border-t bg-white px-4 py-3 sm:px-6">
      <label className="sr-only" htmlFor="chat-query">
        输入问题
      </label>
      <div className="flex items-end gap-3">
        <textarea
          id="chat-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="输入与知识库相关的问题"
          rows={2}
          disabled={isStreaming}
          className="min-h-20 flex-1 resize-y border border-gray-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-blue-600 disabled:cursor-not-allowed disabled:bg-gray-50"
        />
        {isStreaming ? (
          <button
            type="button"
            onClick={onStop}
            className="shrink-0 border border-red-300 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50"
          >
            停止
          </button>
        ) : (
          <button
            type="submit"
            disabled={!trimmedQuery}
            className="shrink-0 bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            发送
          </button>
        )}
      </div>
    </form>
  );
}
