export type ChatRole = "user" | "assistant";
export type ChatMessageStatus = "streaming" | "complete" | "stopped" | "error";

export interface ChatDoneMeta {
  sources?: string[];
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
}
