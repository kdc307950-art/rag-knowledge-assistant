import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function createStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

describe("auth store", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("localStorage", createStorage());
    vi.stubGlobal("sessionStorage", createStorage());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("supports a persisted local mode when the backend has no password", async () => {
    const { useAuth } = await import("./auth");
    useAuth.getState().login("   ");

    expect(useAuth.getState().isAuthenticated).toBe(true);
    expect(useAuth.getState().apiKey).toBe("");
    expect(localStorage.getItem("localAuth")).toBe("1");
    expect(localStorage.getItem("apiKey")).toBeNull();
  });

  it("replaces local mode with a normalized API key and clears both on logout", async () => {
    const { useAuth } = await import("./auth");
    useAuth.getState().login("  test-key  ");

    expect(useAuth.getState().apiKey).toBe("test-key");
    expect(localStorage.getItem("apiKey")).toBe("test-key");
    expect(localStorage.getItem("localAuth")).toBeNull();

    useAuth.getState().logout();
    expect(useAuth.getState().isAuthenticated).toBe(false);
    expect(localStorage.getItem("apiKey")).toBeNull();
    expect(localStorage.getItem("localAuth")).toBeNull();
  });
});
