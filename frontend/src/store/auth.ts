import { create } from "zustand";
import { API_KEY_STORAGE_KEY, SESSION_ID_STORAGE_KEY } from "../api/client";

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
    localStorage.removeItem(SESSION_ID_STORAGE_KEY);
    set({ apiKey: key, isAuthenticated: true });
  },
  logout: () => {
    localStorage.removeItem(API_KEY_STORAGE_KEY);
    localStorage.removeItem(SESSION_ID_STORAGE_KEY);
    set({ apiKey: "", isAuthenticated: false });
  },
}));
