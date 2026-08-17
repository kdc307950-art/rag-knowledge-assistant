import { beforeEach, describe, expect, it } from "vitest";
import { useChat } from "./chat";

describe("chat store", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], isStreaming: false });
  });

  it("uses canonical backend retrieval inputs and only consumes an action after done", () => {
    const store = useChat.getState();
    store.addMessage({
      id: "origin",
      role: "assistant",
      content: "知识库未命中。",
      status: "streaming",
      request: { query: "帮我写请假邮件", retrievalQuery: "帮我写请假邮件" },
    });

    store.finishMessage("origin", {
      is_reject: true,
      fallback_allowed: true,
      query: "帮我写请假邮件",
      retrieval_query: "请假制度",
    });

    expect(useChat.getState().messages[0]?.request).toEqual({
      query: "帮我写请假邮件",
      retrievalQuery: "请假制度",
    });
    expect(useChat.getState().messages[0]?.actionState).toBeUndefined();

    useChat.getState().markActionUsed("origin", "general");

    expect(useChat.getState().messages[0]?.actionState).toEqual({ general: true });
  });

  it("keeps the client message history bounded like the backend session", () => {
    for (let index = 0; index < 82; index += 1) {
      useChat.getState().addMessage({
        id: String(index),
        role: "user",
        content: String(index),
      });
    }

    const messages = useChat.getState().messages;
    expect(messages).toHaveLength(80);
    expect(messages[0]?.content).toBe("2");
  });
});
