import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const user = { id: 1, username: "alice", department: "hr", roles: ["viewer"], active: true };

describe("auth store", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("sessionStorage", { removeItem: vi.fn() });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("starts logged out and never reads browser credentials", async () => {
    const { useAuth } = await import("./auth");
    expect(useAuth.getState().isAuthenticated).toBe(false);
    expect(useAuth.getState().user).toBeNull();
    expect(useAuth.getState().restoring).toBe(true);
  });

  it("stores only the server-provided user after cookie login", async () => {
    const { useAuth } = await import("./auth");
    useAuth.getState().loginUser(user);
    expect(useAuth.getState().user).toEqual(user);
    expect(useAuth.getState().isAuthenticated).toBe(true);
    expect(useAuth.getState().restoring).toBe(false);
  });

  it("clears local state on logout even when the server request fails", async () => {
    vi.doMock("../api/auth", () => ({
      getCurrentUser: vi.fn(),
      logoutUser: vi.fn().mockRejectedValue(new Error("offline")),
    }));
    const { useAuth } = await import("./auth");
    useAuth.getState().loginUser(user);
    await useAuth.getState().logout();
    expect(useAuth.getState().isAuthenticated).toBe(false);
    expect(useAuth.getState().user).toBeNull();
  });
});
