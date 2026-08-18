import { create } from "zustand";
import {
  API_KEY_STORAGE_KEY,
  clearSessionId,
  LOCAL_AUTH_STORAGE_KEY,
} from "../api/client";

interface AuthState {
  apiKey: string;
  isAuthenticated: boolean;
  login: (key: string) => void;
  prepareLogin: (key: string) => void;
  completeLogin: () => void;
  logout: () => void;
}

function storedValue(key: string) {
  return typeof localStorage === "undefined" ? null : localStorage.getItem(key);
}

const storedApiKey = storedValue(API_KEY_STORAGE_KEY) ?? "";
const storedLocalAuth = storedValue(LOCAL_AUTH_STORAGE_KEY) === "1";

export const useAuth = create<AuthState>((set) => ({
  apiKey: storedApiKey,
  isAuthenticated: Boolean(storedApiKey) || storedLocalAuth,
  login: (key: string) => {
    const normalizedKey = key.trim();
    if (normalizedKey) {
      localStorage.setItem(API_KEY_STORAGE_KEY, normalizedKey);
      localStorage.removeItem(LOCAL_AUTH_STORAGE_KEY);
    } else {
      localStorage.removeItem(API_KEY_STORAGE_KEY);
      localStorage.setItem(LOCAL_AUTH_STORAGE_KEY, "1");
    }
    clearSessionId();
    set({ apiKey: normalizedKey, isAuthenticated: true });
  },
  // Persist the credential so the health probe can authenticate, but keep the
  // UI on the login screen until that probe succeeds.
  prepareLogin: (key: string) => {
    const normalizedKey = key.trim();
    if (normalizedKey) {
      localStorage.setItem(API_KEY_STORAGE_KEY, normalizedKey);
      localStorage.removeItem(LOCAL_AUTH_STORAGE_KEY);
    } else {
      localStorage.removeItem(API_KEY_STORAGE_KEY);
      localStorage.setItem(LOCAL_AUTH_STORAGE_KEY, "1");
    }
    clearSessionId();
    set({ apiKey: normalizedKey, isAuthenticated: false });
  },
  completeLogin: () => set({ isAuthenticated: true }),
  logout: () => {
    localStorage.removeItem(API_KEY_STORAGE_KEY);
    localStorage.removeItem(LOCAL_AUTH_STORAGE_KEY);
    clearSessionId();
    set({ apiKey: "", isAuthenticated: false });
  },
}));
