import { create } from "zustand";
import { API_KEY_STORAGE_KEY, clearSessionId } from "../api/client";

interface AuthState {
  apiKey: string;
  isAuthenticated: boolean;
  login: (key: string) => void;
  logout: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  apiKey: localStorage.getItem(API_KEY_STORAGE_KEY) ?? "",
  isAuthenticated: Boolean(localStorage.getItem(API_KEY_STORAGE_KEY)),
  login: (key: string) => {
    localStorage.setItem(API_KEY_STORAGE_KEY, key);
    clearSessionId();
    set({ apiKey: key, isAuthenticated: true });
  },
  logout: () => {
    localStorage.removeItem(API_KEY_STORAGE_KEY);
    clearSessionId();
    set({ apiKey: "", isAuthenticated: false });
  },
}));
