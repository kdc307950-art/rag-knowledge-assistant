// fetch 封装：统一带 X-API-Key，401 触发登出事件。
const API_BASE = "/api";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const apiKey = localStorage.getItem("apiKey") ?? "";
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(apiKey ? { "X-API-Key": apiKey } : {}),
      ...init?.headers,
    },
  });
  if (res.status === 401) {
    localStorage.removeItem("apiKey");
    window.dispatchEvent(new CustomEvent("auth:logout"));
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      detail = typeof body?.detail === "string" ? body.detail : res.statusText;
    } catch {
      // 忽略非 JSON 错误体
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}
