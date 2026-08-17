import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  API_KEY_STORAGE_KEY,
  ApiError,
  SESSION_ID_STORAGE_KEY,
} from "./client";
import { streamChat } from "./sse";
import type { ChatDoneMeta, ChatStreamError } from "../types";

function createStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    clear: () => values.clear(),
    key: (index: number) => [...values.keys()][index] ?? null,
    get length() {
      return values.size;
    },
  };
}

function responseFromBytes(source: string, headers?: HeadersInit) {
  const bytes = new TextEncoder().encode(source);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      // 每次一个字节，覆盖 UTF-8 多字节字符和事件分隔符被任意切开的情况。
      for (const byte of bytes) {
        controller.enqueue(new Uint8Array([byte]));
      }
      controller.close();
    },
  });
  return new Response(stream, { headers });
}

function callbacks() {
  const tokens: string[] = [];
  let done: ChatDoneMeta | undefined;
  let error: ChatStreamError | undefined;
  return {
    tokens,
    get done() {
      return done;
    },
    get error() {
      return error;
    },
    onToken: (token: string) => tokens.push(token),
    onDone: (meta: ChatDoneMeta) => {
      done = meta;
    },
    onError: (streamError: ChatStreamError) => {
      error = streamError;
    },
  };
}

describe("streamChat", () => {
  let storage: ReturnType<typeof createStorage>;
  let dispatchEvent: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    storage = createStorage();
    storage.setItem(API_KEY_STORAGE_KEY, "test-key");
    dispatchEvent = vi.fn();
    vi.stubGlobal("localStorage", storage);
    vi.stubGlobal("window", { dispatchEvent });
    vi.stubGlobal(
      "CustomEvent",
      class {
        readonly type: string;

        constructor(type: string) {
          this.type = type;
        }
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("decodes CRLF SSE frames split across UTF-8 byte boundaries and stores the session", async () => {
    storage.setItem(SESSION_ID_STORAGE_KEY, "previous-session");
    const fetchMock = vi.fn().mockResolvedValue(
      responseFromBytes(
        [
          "event: token\r\n",
          'data: "你"\r\n\r\n',
          "event: token\r\n",
          'data: "好\\n世界"\r\n\r\n',
          "event: done\r\n",
          'data: {"sources":["员工手册.pdf | 第 3 页"],"is_kb":true}\r\n\r\n',
        ].join(""),
        { "X-Session-Id": "new-session" },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const received = callbacks();

    await streamChat("你好", { signal: new AbortController().signal, ...received });

    expect(received.tokens.join("")).toBe("你好\n世界");
    expect(received.done).toEqual({
      sources: ["员工手册.pdf | 第 3 页"],
      is_kb: true,
    });
    expect(storage.getItem(SESSION_ID_STORAGE_KEY)).toBe("new-session");
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("X-API-Key")).toBe("test-key");
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("X-Session-Id")).toBe("previous-session");
  });

  it("preserves delivered tokens before an error even when partial is false", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseFromBytes(
        [
          "event: token\n",
          'data: "已显示的兜底正文"\n\n',
          "event: error\n",
          'data: {"code":"retrieval_error","message":"检索暂时不可用","partial":false}\n\n',
        ].join(""),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const received = callbacks();

    await streamChat("报销制度", { signal: new AbortController().signal, ...received });

    expect(received.tokens).toEqual(["已显示的兜底正文"]);
    expect(received.error).toEqual({
      code: "retrieval_error",
      message: "检索暂时不可用",
      partial: false,
    });
  });

  it("fails safely for malformed event data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFromBytes("event: token\ndata: not-json\n\n")));
    const received = callbacks();

    await expect(
      streamChat("问题", { signal: new AbortController().signal, ...received }),
    ).rejects.toThrow("无法解析");
  });

  it("clears browser credentials when the initial HTTP response is unauthorized", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "访问口令错误" }), {
          status: 401,
          statusText: "Unauthorized",
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const received = callbacks();

    await expect(
      streamChat("问题", { signal: new AbortController().signal, ...received }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(storage.getItem(API_KEY_STORAGE_KEY)).toBeNull();
    expect(storage.getItem(SESSION_ID_STORAGE_KEY)).toBeNull();
    expect(dispatchEvent).toHaveBeenCalledOnce();
  });
});
