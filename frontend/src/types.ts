export type ChatRole = "user" | "assistant";
export type ChatMessageStatus = "streaming" | "complete" | "stopped" | "error";
export type ChatAction = "general" | "draft";
export type FeedbackVerdict = "up" | "down";
export type FeedbackStatus = "submitting" | "submitted" | "error";
export type FeedbackReason =
  | "not_accurate"
  | "missing_source"
  | "wrong_source"
  | "refusal_unexpected"
  | "latency"
  | "irrelevant"
  | "unsafe"
  | "other";

export interface ChatDoneMeta {
  sources?: string[];
  source_refs?: Array<{ id: string; label: string }>;
  citation_validation?: {
    has_citations: boolean;
    cited_ids: string[];
    unknown_ids: string[];
    valid: boolean;
  };
  thought?: string | null;
  is_reject?: boolean;
  is_kb_busy?: boolean;
  fallback_allowed?: boolean;
  draft_allowed?: boolean;
  from_cache?: boolean;
  is_interrupted?: boolean;
  is_empty?: boolean;
  action_failed?: boolean;
  error_code?: string;
  is_kb_stale?: boolean;
  is_kb?: boolean;
  is_general?: boolean;
  is_draft?: boolean;
  query?: string;
  retrieval_query?: string;
  message_id?: string;
  feedback_eligible?: boolean;
}

export interface ChatStreamError {
  code: string;
  message: string;
  partial: boolean;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  status?: ChatMessageStatus;
  meta?: ChatDoneMeta;
  error?: ChatStreamError;
  request?: {
    query: string;
    retrievalQuery: string;
  };
  action?: ChatAction;
  actionState?: Partial<Record<ChatAction, boolean>>;
  messageId?: string;
  feedback?: {
    verdict?: FeedbackVerdict;
    status: FeedbackStatus;
    error?: string;
  };
}
