import { ApiError } from "../api/client";
import { getHealth } from "../api/health";

interface LoginActions {
  prepareLogin: (password: string) => void;
  completeLogin: () => void;
  logout: () => void;
}

export async function verifyLogin(
  password: string,
  { prepareLogin, completeLogin, logout }: LoginActions,
): Promise<string> {
  try {
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
