import { apiFetch, captureSessionId } from "./client";
import type { ChatDoneMeta, ChatStreamError } from "../types";

export type StreamCallbacks = {
  onToken: (token: string) => void;
  onDone: (meta: ChatDoneMeta) => void;
  onError: (error: ChatStreamError) => void;
};

export type StreamOptions = StreamCallbacks & {
  signal: AbortSignal;
};

const DONE_BOOLEAN_KEYS = [
  "is_reject",
  "is_kb_busy",
  "fallback_allowed",
  "draft_allowed",
  "from_cache",
  "is_interrupted",
  "is_empty",
  "action_failed",
  "is_kb_stale",
  "is_kb",
  "is_general",
  "is_draft",
] as const;

class SseProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SseProtocolError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseDoneMeta(value: unknown): ChatDoneMeta {
  if (!isRecord(value)) {
    throw new SseProtocolError("后端返回了格式错误的完成事件。");
  }
  const meta: ChatDoneMeta = {};
  if (Array.isArray(value.sources)) {
    meta.sources = value.sources.filter(
      (source): source is string => typeof source === "string" && source.trim().length > 0,
    );
  }
  if (Array.isArray(value.source_refs)) {
    meta.source_refs = value.source_refs.flatMap((reference) => (
      isRecord(reference)
      && typeof reference.id === "string"
      && reference.id.trim().length > 0
      && typeof reference.label === "string"
      && reference.label.trim().length > 0
        ? [{ id: reference.id, label: reference.label }]
        : []
    ));
  }
  if (isRecord(value.citation_validation)
    && typeof value.citation_validation.has_citations === "boolean"
    && Array.isArray(value.citation_validation.cited_ids)
    && Array.isArray(value.citation_validation.unknown_ids)
    && typeof value.citation_validation.valid === "boolean") {
    meta.citation_validation = {
      has_citations: value.citation_validation.has_citations,
      cited_ids: value.citation_validation.cited_ids.filter(
        (id): id is string => typeof id === "string" && id.trim().length > 0,
      ),
      unknown_ids: value.citation_validation.unknown_ids.filter(
        (id): id is string => typeof id === "string" && id.trim().length > 0,
      ),
      valid: value.citation_validation.valid,
    };
  }
  if (typeof value.thought === "string" || value.thought === null) {
    meta.thought = value.thought;
  }
  if (typeof value.error_code === "string") {
    meta.error_code = value.error_code;
  }
  if (typeof value.query === "string") {
    meta.query = value.query;
  }
  if (typeof value.retrieval_query === "string") {
    meta.retrieval_query = value.retrieval_query;
  }
  for (const key of DONE_BOOLEAN_KEYS) {
    const candidate = value[key];
    if (typeof candidate === "boolean") {
      meta[key] = candidate;
    }
  }
  return meta;
}

function parseStreamError(value: unknown): ChatStreamError {
  if (!isRecord(value) || typeof value.message !== "string") {
    throw new SseProtocolError("后端返回了格式错误的错误事件。");
  }
  return {
    code: typeof value.code === "string" ? value.code : "stream_error",
    message: value.message,
    partial: value.partial === true,
  };
}

function parseFrame(frame: string, callbacks: StreamCallbacks): "done" | "error" | null {
  let event = "message";
  const data: string[] = [];

  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice("data:".length).replace(/^ /, ""));
    }
  }

  if (data.length === 0) {
    return null;
  }

  let payload: unknown;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    throw new SseProtocolError("后端返回了无法解析的流式数据。");
  }

  if (event === "token") {
    if (typeof payload !== "string") {
      throw new SseProtocolError("后端返回了格式错误的文本片段。");
    }
    callbacks.onToken(payload);
    return null;
  }
  if (event === "done") {
    callbacks.onDone(parseDoneMeta(payload));
    return "done";
  }
  if (event === "error") {
    callbacks.onError(parseStreamError(payload));
    return "error";
  }
  return null;
}

function consumeFrames(
  buffer: string,
  callbacks: StreamCallbacks,
): { remaining: string; terminal: "done" | "error" | null } {
  let remaining = buffer;
  while (true) {
    const boundary = remaining.search(/\r?\n\r?\n/);
    if (boundary < 0) {
      return { remaining, terminal: null };
    }
    const frame = remaining.slice(0, boundary);
    const separatorLength = remaining.startsWith("\r\n\r\n", boundary) ? 4 : 2;
    remaining = remaining.slice(boundary + separatorLength);
    const terminal = parseFrame(frame, callbacks);
    if (terminal) {
      return { remaining, terminal };
    }
  }
}

async function streamJson(
  path: string,
  body: Record<string, string>,
  options: StreamOptions,
): Promise<void> {
  const response = await apiFetch(path, {
    method: "POST",
    body: JSON.stringify(body),
    signal: options.signal,
  });
  captureSessionId(response);

  if (!response.body) {
    throw new SseProtocolError("浏览器未收到可读取的流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal: "done" | "error" | null = null;

  try {
    while (!terminal) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const consumed = consumeFrames(buffer, options);
      buffer = consumed.remaining;
      terminal = consumed.terminal;
    }

    if (!terminal) {
      buffer += decoder.decode();
      const consumed = consumeFrames(buffer, options);
      buffer = consumed.remaining;
      terminal = consumed.terminal;
    }
    if (!terminal && buffer.trim()) {
      terminal = parseFrame(buffer, options);
    }
    if (!terminal) {
      throw new SseProtocolError("流式响应在完成前意外结束。");
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // 已完成或已中断的流不需要额外处理。
    }
    reader.releaseLock();
  }
}

export function streamChat(query: string, options: StreamOptions): Promise<void> {
  return streamJson("/chat", { query }, options);
}

export function streamGeneral(query: string, options: StreamOptions): Promise<void> {
  return streamJson("/chat/general", { query }, options);
}

export function streamDraft(
  query: string,
  retrievalQuery: string,
  options: StreamOptions,
): Promise<void> {
  return streamJson("/chat/draft", { query, retrieval_query: retrievalQuery }, options);
}
