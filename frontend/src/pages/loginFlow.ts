import { ApiError } from "../api/client";
import { loginUser } from "../api/auth";
import { getHealth } from "../api/health";

interface LoginActions {
  prepareLogin: (password: string) => void;
  completeLogin: () => void;
  logout: () => void;
  loginUser?: (token: string, user: Awaited<ReturnType<typeof loginUser>>["user"]) => void;
}

export async function verifyLogin(
  password: string,
  { prepareLogin, completeLogin, logout, loginUser: saveUser }: LoginActions,
  username = "",
): Promise<string> {
  try {
    if (username.trim()) {
      const result = await loginUser(username.trim(), password);
      saveUser?.(result.token, result.user);
      completeLogin();
      return "";
    }
    // client.ts reads the persisted credential when the health request is sent.
    prepareLogin(password);
    await getHealth();
    completeLogin();
    return "";
  } catch (error) {
    logout();
    if (error instanceof ApiError && error.status === 401) {
      return "访问口令错误";
    }
    if (error instanceof ApiError) {
      return `后端错误：${error.message}`;
    }
    return "无法连接后端服务，请确认已启动 API（uvicorn backend.main:app --port 8000）";
  }
}
