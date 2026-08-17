import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MessageList from "./MessageList";

describe("MessageList", () => {
  it("renders collapsed sources only for a current knowledge-base answer", () => {
    const normal = renderToStaticMarkup(
      createElement(MessageList, {
        messages: [{
          id: "answer",
          role: "assistant",
          content: "报销期限为五天。",
          status: "complete",
          meta: { is_kb: true, sources: ["报销制度.pdf | 第 3 页"] },
        }],
      }),
    );
    const rejected = renderToStaticMarkup(
      createElement(MessageList, {
        messages: [{
          id: "rejected",
          role: "assistant",
          content: "未找到资料。",
          status: "complete",
          meta: { is_kb: true, is_reject: true, sources: ["不应显示.pdf"] },
        }],
      }),
    );

    expect(normal).toContain("引用来源 (1)");
    expect(normal).toContain("报销制度.pdf | 第 3 页");
    expect(rejected).not.toContain("引用来源");
    expect(rejected).not.toContain("不应显示.pdf");
  });

  it("renders a preserved-partial-output state for interrupted errors", () => {
    const markup = renderToStaticMarkup(
      createElement(MessageList, {
        messages: [{
          id: "partial",
          role: "assistant",
          content: "已收到的正文",
          status: "error",
          error: { code: "rate_limit", message: "请稍后重试。", partial: true },
        }],
      }),
    );

    expect(markup).toContain("已保留部分正文，未写入会话历史");
    expect(markup).toContain("模型服务限流或额度不足");
  });

  it("renders eligible follow-up actions and debug evidence without mixing them into the answer", () => {
    const markup = renderToStaticMarkup(
      createElement(MessageList, {
        onAction: () => undefined,
        showDebug: true,
        messages: [
          {
            id: "rejected",
            role: "assistant",
            content: "没有找到资料。",
            status: "complete",
            meta: { is_reject: true, fallback_allowed: true, thought: "严格检索未命中" },
          },
          {
            id: "grounded",
            role: "assistant",
            content: "年假为五天。",
            status: "complete",
            meta: { is_kb: true, draft_allowed: true, thought: "命中员工手册" },
          },
        ],
      }),
    );

    expect(markup).toContain("改用通用办公回答");
    expect(markup).toContain("基于资料起草");
    expect(markup).toContain("回答依据");
  });

  it("renders the G2 or draft context before streaming text", () => {
    const markup = renderToStaticMarkup(
      createElement(MessageList, {
        messages: [
          { id: "general", role: "assistant", action: "general", content: "正在写邮件", status: "streaming" },
          { id: "draft", role: "assistant", action: "draft", content: "正在写草稿", status: "streaming" },
        ],
      }),
    );

    expect(markup.indexOf("通用办公回答，未依据企业知识库。"))
      .toBeLessThan(markup.indexOf("正在写邮件"));
    expect(markup.indexOf("基于当前资料起草。"))
      .toBeLessThan(markup.indexOf("正在写草稿"));
  });
});
