import { describe, expect, it } from "vitest";
import { messageSources, messageStatusLabel, streamErrorTitle } from "./chatPresentation";
import type { ChatMessage } from "./types";

function assistant(meta?: ChatMessage["meta"]): ChatMessage {
  return { id: "assistant", role: "assistant", content: "回答", status: "complete", meta };
}

describe("chat presentation", () => {
  it("shows sources only for a normal knowledge-base answer", () => {
    expect(messageSources(assistant({ sources: ["制度.pdf | 第 1 页"], is_kb: true }))).toEqual([
      "制度.pdf | 第 1 页",
    ]);
    expect(messageSources(assistant({ sources: ["制度.pdf"] }))).toEqual([]);
    expect(messageSources(assistant({ sources: ["制度.pdf"], is_kb: false }))).toEqual([]);
    expect(messageSources(assistant({ sources: ["制度.pdf"], is_kb: true, is_reject: true }))).toEqual([]);
    expect(messageSources(assistant({ sources: ["制度.pdf"], is_kb: true, is_kb_busy: true }))).toEqual([]);
    expect(messageSources(assistant({ sources: ["制度.pdf"], is_kb: true, is_kb_stale: true }))).toEqual([]);
  });

  it("distinguishes strict rejection and knowledge-base updates", () => {
    expect(messageStatusLabel(assistant({ is_reject: true }))).toContain("严格知识库模式");
    expect(messageStatusLabel(assistant({ is_kb_busy: true }))).toContain("正在更新");
  });

  it("maps stable stream error codes to actionable titles", () => {
    expect(streamErrorTitle({ code: "rate_limit", message: "额度不足", partial: false })).toBe(
      "模型服务限流或额度不足",
    );
    expect(streamErrorTitle({ code: "unknown", message: "错误", partial: false })).toBe("回答失败");
  });

  it("distinguishes preserved partial output from a failure without output", () => {
    expect(
      messageStatusLabel({
        ...assistant(),
        status: "error",
        error: { code: "rate_limit", message: "额度不足", partial: true },
      }),
    ).toContain("已保留部分正文");
    expect(
      messageStatusLabel({
        ...assistant(),
        status: "error",
        error: { code: "authentication", message: "鉴权失败", partial: false },
      }),
    ).toContain("生成失败");
  });
});
