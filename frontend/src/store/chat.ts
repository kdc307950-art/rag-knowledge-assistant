import { create } from "zustand";
import type {
  ChatAction,
  ChatDoneMeta,
  ChatMessage,
  ChatMessageStatus,
  ChatStreamError,
  FeedbackStatus,
  FeedbackVerdict,
} from "../types";

const MAX_MESSAGES = 80;

interface ChatState {
  messages: ChatMessage[];
  isStreaming: boolean;
  addMessage: (message: ChatMessage) => void;
  appendToken: (id: string, token: string) => void;
  setMessageStatus: (id: string, status: ChatMessageStatus) => void;
  finishMessage: (id: string, meta: ChatDoneMeta) => void;
  failMessage: (id: string, error: ChatStreamError) => void;
  markActionUsed: (id: string, action: ChatAction) => void;
  setMessageFeedback: (
    id: string,
    feedback: { status: FeedbackStatus; verdict?: FeedbackVerdict; error?: string },
  ) => void;
  setStreaming: (isStreaming: boolean) => void;
}

function updateMessage(
  messages: ChatMessage[],
  id: string,
  update: (message: ChatMessage) => ChatMessage,
) {
  return messages.map((message) => (message.id === id ? update(message) : message));
}

export const useChat = create<ChatState>((set) => ({
  messages: [],
  isStreaming: false,
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message].slice(-MAX_MESSAGES) })),
  appendToken: (id, token) =>
    set((state) => ({
      messages: updateMessage(state.messages, id, (message) => ({
        ...message,
        content: message.content + token,
      })),
    })),
  setMessageStatus: (id, status) =>
    set((state) => ({
      messages: updateMessage(state.messages, id, (message) => ({ ...message, status })),
    })),
  finishMessage: (id, meta) =>
    set((state) => ({
      messages: updateMessage(state.messages, id, (message) => ({
        ...message,
        meta,
        messageId: meta.message_id ?? message.messageId,
        status: "complete",
        request: message.request
          ? {
              query: meta.query ?? message.request.query,
              retrievalQuery: meta.retrieval_query ?? message.request.retrievalQuery,
            }
          : undefined,
      })),
    })),
  failMessage: (id, error) =>
    set((state) => ({
      messages: updateMessage(state.messages, id, (message) => ({
        ...message,
        error,
        status: "error",
      })),
    })),
  markActionUsed: (id, action) =>
    set((state) => ({
      messages: updateMessage(state.messages, id, (message) => ({
        ...message,
        actionState: { ...message.actionState, [action]: true },
      })),
    })),
  setMessageFeedback: (id, feedback) =>
    set((state) => ({
      messages: updateMessage(state.messages, id, (message) => ({ ...message, feedback })),
    })),
  setStreaming: (isStreaming) => set({ isStreaming }),
}));
