import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { getHealth } from "../api/health";
import { verifyLogin } from "./loginFlow";

vi.mock("../api/auth", () => ({
  loginUser: vi.fn(),
}));
import { loginUser } from "../api/auth";

vi.mock("../api/health", () => ({
  getHealth: vi.fn(),
}));

const getHealthMock = vi.mocked(getHealth);
const loginUserMock = vi.mocked(loginUser);

afterEach(() => {
  vi.clearAllMocks();
});

describe("login verification", () => {
  it("uses the multi-user login endpoint and keeps the token out of browser storage", async () => {
    const completeLogin = vi.fn();
    const saveUser = vi.fn();
    loginUserMock.mockResolvedValue({
      token: "memory-token",
      expires_in: 3600,
      user: { id: 1, username: "alice", department: "hr", roles: ["viewer"], active: true },
    });

    await expect(
      verifyLogin("secret", {
        prepareLogin: vi.fn(),
        completeLogin,
        logout: vi.fn(),
        loginUser: saveUser,
      }, "alice"),
    ).resolves.toBe("");

    expect(loginUserMock).toHaveBeenCalledWith("alice", "secret");
    expect(saveUser).toHaveBeenCalledWith("memory-token", expect.objectContaining({ username: "alice" }));
    expect(completeLogin).toHaveBeenCalledOnce();
    expect(getHealthMock).not.toHaveBeenCalled();
  });

  it("persists the submitted credential before checking backend health", async () => {
    const calls: string[] = [];
    const prepareLogin = vi.fn(() => calls.push("prepare"));
    const completeLogin = vi.fn(() => calls.push("complete"));
    const logout = vi.fn();
    getHealthMock.mockImplementation(async () => {
      calls.push("health");
      return { ok: true, vector_status: "ready" };
    });

    await expect(verifyLogin(" test-key ", { prepareLogin, completeLogin, logout })).resolves.toBe("");

    expect(prepareLogin).toHaveBeenCalledWith(" test-key ");
    expect(completeLogin).toHaveBeenCalledOnce();
    expect(calls).toEqual(["prepare", "health", "complete"]);
    expect(logout).not.toHaveBeenCalled();
  });

  it("rolls back authentication and shows a specific message for a rejected key", async () => {
    const prepareLogin = vi.fn();
    const completeLogin = vi.fn();
    const logout = vi.fn();
    getHealthMock.mockRejectedValue(new ApiError(401, "unauthorized"));

    await expect(verifyLogin("bad-key", { prepareLogin, completeLogin, logout })).resolves.toBe("访问口令错误");

    expect(logout).toHaveBeenCalledOnce();
    expect(completeLogin).not.toHaveBeenCalled();
  });

  it("distinguishes backend errors from connection failures", async () => {
    const prepareLogin = vi.fn();
    const completeLogin = vi.fn();
    const logout = vi.fn();
    getHealthMock.mockRejectedValueOnce(new ApiError(503, "服务未就绪"));

    await expect(verifyLogin("key", { prepareLogin, completeLogin, logout })).resolves.toBe("后端错误：服务未就绪");

    getHealthMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    await expect(verifyLogin("key", { prepareLogin, completeLogin, logout })).resolves.toContain("无法连接后端服务");
    expect(logout).toHaveBeenCalledTimes(2);
  });
});
