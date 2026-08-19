import { ApiError } from "../api/client";
import { loginUser } from "../api/auth";
import type { AuthUser } from "../api/auth";

interface LoginActions {
  clearAuth: () => void;
  loginUser: (user: AuthUser) => void;
}

export async function verifyLogin(
  password: string,
  { clearAuth, loginUser: saveUser }: LoginActions,
  username = "",
): Promise<string> {
  try {
    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) {
      return "请输入用户名和密码";
    }
    const result = await loginUser(normalizedUsername, password);
    saveUser(result.user);
    return "";
  } catch (error) {
    clearAuth();
    if (error instanceof ApiError && error.status === 401) {
      return "用户名或密码错误";
    }
    if (error instanceof ApiError && error.status === 429) {
      return "登录尝试过于频繁，请稍后重试";
    }
    if (error instanceof ApiError) {
      return `后端错误：${error.message}`;
    }
    return "无法连接后端服务，请确认已启动 API（uvicorn backend.main:app --port 8000）";
  }
}
