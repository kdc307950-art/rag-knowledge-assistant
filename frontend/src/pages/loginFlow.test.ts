import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { loginUser } from "../api/auth";
import { verifyLogin } from "./loginFlow";

vi.mock("../api/auth", () => ({ loginUser: vi.fn() }));

const loginUserMock = vi.mocked(loginUser);
const user = { id: 1, username: "alice", department: "hr", roles: ["viewer"], active: true };

afterEach(() => vi.clearAllMocks());

describe("login verification", () => {
  it("uses username/password login and stores the cookie-backed identity", async () => {
    const saveUser = vi.fn();
    const clearAuth = vi.fn();
    loginUserMock.mockResolvedValue({ user });

    await expect(verifyLogin("secret", { clearAuth, loginUser: saveUser }, "alice")).resolves.toBe("");
    expect(loginUserMock).toHaveBeenCalledWith("alice", "secret");
    expect(saveUser).toHaveBeenCalledWith(user);
    expect(clearAuth).not.toHaveBeenCalled();
  });

  it("requires a username and password", async () => {
    const saveUser = vi.fn();
    await expect(verifyLogin("", { clearAuth: vi.fn(), loginUser: saveUser }, "alice"))
      .resolves.toBe("请输入用户名和密码");
    expect(saveUser).not.toHaveBeenCalled();
  });

  it("distinguishes rejected credentials and rate limits", async () => {
    const clearAuth = vi.fn();
    loginUserMock.mockRejectedValueOnce(new ApiError(401, "用户名或密码错误"));
    await expect(verifyLogin("bad", { clearAuth, loginUser: vi.fn() }, "alice"))
      .resolves.toBe("用户名或密码错误");
    expect(clearAuth).toHaveBeenCalledOnce();

    loginUserMock.mockRejectedValueOnce(new ApiError(429, "登录尝试过于频繁"));
    await expect(verifyLogin("bad", { clearAuth, loginUser: vi.fn() }, "alice"))
      .resolves.toBe("登录尝试过于频繁，请稍后重试");
  });

  it("shows service readiness and connection errors separately", async () => {
    const clearAuth = vi.fn();
    loginUserMock.mockRejectedValueOnce(new ApiError(503, "服务未就绪"));
    await expect(verifyLogin("key", { clearAuth, loginUser: vi.fn() }, "alice"))
      .resolves.toBe("后端错误：服务未就绪");

    loginUserMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    await expect(verifyLogin("key", { clearAuth, loginUser: vi.fn() }, "alice"))
      .resolves.toContain("无法连接后端服务");
  });
});
