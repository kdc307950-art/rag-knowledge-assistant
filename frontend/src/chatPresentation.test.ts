import { describe, expect, it } from "vitest";
import { messageActions, messageSources, messageStatusLabel, streamErrorTitle } from "./chatPresentation";
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
    expect(messageSources(assistant({ sources: ["制度.pdf"], is_kb: true, is_general: true }))).toEqual([]);
    expect(messageSources(assistant({ sources: ["制度.pdf"], is_kb: true, is_reject: true }))).toEqual([]);
    expect(messageSources(assistant({ sources: ["制度.pdf"], is_kb: true, is_kb_busy: true }))).toEqual([]);
    expect(messageSources(assistant({ sources: ["制度.pdf"], is_kb: true, is_kb_stale: true }))).toEqual([]);
  });

  it("prefers stable source identifiers when the server provides them", () => {
    expect(messageSources(assistant({
      is_kb: true,
      sources: ["legacy.pdf"],
      source_refs: [{ id: "S1", label: "员工手册.pdf | 第 3 页" }],
    }))).toEqual(["[S1] 员工手册.pdf | 第 3 页"]);
  });

  it("distinguishes strict rejection and knowledge-base updates", () => {
    expect(messageStatusLabel(assistant({ is_reject: true }))).toContain("严格知识库模式");
    expect(messageStatusLabel(assistant({ is_kb_busy: true }))).toContain("正在更新");
  });

  it("maps stable stream error codes to actionable titles", () => {
    expect(streamErrorTitle({ code: "rate_limit", message: "额度不足", partial: false })).toBe(
      "模型服务限流或额度不足",
    );
    expect(
      streamErrorTitle({ code: "document_governance_unresolved", message: "关系未确认", partial: false }),
    ).toBe("文档生效关系尚未确认");
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

  it("exposes G2 and drafting only while their originating answer remains eligible", () => {
    expect(messageActions(assistant({ is_reject: true, fallback_allowed: true }))).toEqual(["general"]);
    expect(
      messageActions(assistant({ is_kb: true, draft_allowed: true, sources: ["制度.pdf"] })),
    ).toEqual(["draft"]);
    expect(
      messageActions({
        ...assistant({ is_reject: true, fallback_allowed: true }),
        actionState: { general: true },
      }),
    ).toEqual([]);
  });
});
