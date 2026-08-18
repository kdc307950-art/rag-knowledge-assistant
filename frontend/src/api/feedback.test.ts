import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { submitAnswerFeedback } from "./feedback";

describe("answer feedback API", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    });
    vi.stubGlobal("sessionStorage", {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("posts a down verdict with its reason", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 3, status: "created" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(submitAnswerFeedback("message-1", "down", "wrong_source"))
      .resolves.toEqual({ id: 3, status: "created" });
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string)).toEqual({
      message_id: "message-1",
      verdict: "down",
      reason: "wrong_source",
    });
  });
});
