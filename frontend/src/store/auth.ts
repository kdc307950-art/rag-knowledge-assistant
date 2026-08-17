import { create } from "zustand";

const API_KEY_KEY = "apiKey";

interface AuthState {
  apiKey: string;
  isAuthenticated: boolean;
  login: (key: string) => void;
  logout: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  apiKey: localStorage.getItem(API_KEY_KEY) ?? "",
  isAuthenticated: Boolean(localStorage.getItem(API_KEY_KEY)),
  login: (key: string) => {
    localStorage.setItem(API_KEY_KEY, key);
    set({ apiKey: key, isAuthenticated: true });
  },
  logout: () => {
    localStorage.removeItem(API_KEY_KEY);
    set({ apiKey: "", isAuthenticated: false });
  },
}));
