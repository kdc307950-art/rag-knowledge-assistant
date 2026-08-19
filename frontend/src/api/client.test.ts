import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError, SESSION_ID_STORAGE_KEY } from "./client";

function createStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

describe("browser API client", () => {
  let session: ReturnType<typeof createStorage>;
  let dispatchEvent: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    session = createStorage();
    dispatchEvent = vi.fn();
    vi.stubGlobal("sessionStorage", session);
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

  afterEach(() => vi.unstubAllGlobals());

  it("uses cookies and strips browser-supplied credential headers", async () => {
    session.setItem(SESSION_ID_STORAGE_KEY, "session-1");
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/kb/stats", {
      headers: {
        Authorization: "Bearer forbidden-browser-token",
        "X-API-Key": "forbidden-browser-key",
      },
    });

    const [, init] = fetchMock.mock.calls[0] ?? [];
    const headers = new Headers(init?.headers);
    expect(init?.credentials).toBe("include");
    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("X-API-Key")).toBeNull();
    expect(headers.get("X-Session-Id")).toBe("session-1");
  });

  it("clears only the non-auth session hint on 401", async () => {
    session.setItem(SESSION_ID_STORAGE_KEY, "session-1");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));

    await expect(apiFetch("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(session.getItem(SESSION_ID_STORAGE_KEY)).toBeNull();
    expect(dispatchEvent).toHaveBeenCalledOnce();
  });
});
