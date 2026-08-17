import { create } from "zustand";

interface SettingsState {
  showDebug: boolean;
  setShowDebug: (showDebug: boolean) => void;
}

export const useSettings = create<SettingsState>((set) => ({
  showDebug: false,
  setShowDebug: (showDebug) => set({ showDebug }),
}));
