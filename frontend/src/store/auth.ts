import { create } from "zustand";
import { clearSessionId } from "../api/client";
import { getCurrentUser, logoutUser } from "../api/auth";
import type { AuthUser } from "../api/auth";

interface AuthState {
  user: AuthUser | null;
  isAuthenticated: boolean;
  restoring: boolean;
  restoreSession: () => Promise<void>;
  loginUser: (user: AuthUser) => void;
  clearAuth: () => void;
  logout: () => Promise<void>;
}

function resetState(set: (state: Partial<AuthState>) => void) {
  clearSessionId();
  set({ user: null, isAuthenticated: false, restoring: false });
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  restoring: true,
  restoreSession: async () => {
    set({ restoring: true });
    try {
      const user = await getCurrentUser();
      set({ user, isAuthenticated: true, restoring: false });
    } catch {
      // A missing or expired cookie is the normal logged-out state.
      resetState(set);
    }
  },
  loginUser: (user) => {
    clearSessionId();
    set({ user, isAuthenticated: true, restoring: false });
  },
  clearAuth: () => resetState(set),
  logout: async () => {
    try {
      await logoutUser();
    } catch {
      // The local state must still be cleared if the server is unavailable or
      // has already invalidated the cookie.
    } finally {
      resetState(set);
    }
  },
}));
