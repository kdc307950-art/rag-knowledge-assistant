import { ApiError, request } from "./client";
import type { FeedbackReason, FeedbackVerdict } from "../types";

export type FeedbackResponse = {
  id: number;
  status: "created" | "idempotent";
};

export async function submitAnswerFeedback(
  messageId: string,
  verdict: FeedbackVerdict,
  reason?: FeedbackReason,
): Promise<FeedbackResponse> {
  return request<FeedbackResponse>("/feedback", {
    method: "POST",
    body: JSON.stringify({
      message_id: messageId,
      verdict,
      ...(verdict === "down" ? { reason } : {}),
    }),
  });
}

export function friendlyFeedbackError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "反馈未提交，请稍后重试。";
  }
  if (error.status === 401) return "登录已失效，请重新登录。";
  if (error.status === 403) return "不能评价其他用户的回答。";
  if (error.status === 409) return "该回答当前不可反馈或已有相反反馈。";
  if (error.status === 422) return "请选择有效的反馈原因。";
  if (error.status === 429) return "反馈过于频繁，请稍后重试。";
  if (error.status === 503) return "质量反馈服务尚未配置。";
  return error.message || "反馈未提交，请稍后重试。";
}
