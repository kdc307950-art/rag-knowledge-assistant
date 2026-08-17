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
});
