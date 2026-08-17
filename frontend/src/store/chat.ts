import { create } from "zustand";
import type { ChatDoneMeta, ChatMessage, ChatMessageStatus, ChatStreamError } from "../types";

interface ChatState {
  messages: ChatMessage[];
  isStreaming: boolean;
  addMessage: (message: ChatMessage) => void;
  appendToken: (id: string, token: string) => void;
  setMessageStatus: (id: string, status: ChatMessageStatus) => void;
  finishMessage: (id: string, meta: ChatDoneMeta) => void;
  failMessage: (id: string, error: ChatStreamError) => void;
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
  addMessage: (message) => set((state) => ({ messages: [...state.messages, message] })),
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
        status: "complete",
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
  setStreaming: (isStreaming) => set({ isStreaming }),
}));
