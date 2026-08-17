// fetch 封装：统一带认证与会话头，401 触发登出事件。
const API_BASE = "/api";
export const API_KEY_STORAGE_KEY = "apiKey";
export const SESSION_ID_STORAGE_KEY = "sessionId";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function unauthorized(): never {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
  clearSessionId();
  window.dispatchEvent(new CustomEvent("auth:logout"));
  throw new ApiError(401, "unauthorized");
}

export function clearSessionId() {
  sessionStorage.removeItem(SESSION_ID_STORAGE_KEY);
}

export function captureSessionId(response: Response) {
  const sessionId = response.headers.get("X-Session-Id");
  if (sessionId) {
    sessionStorage.setItem(SESSION_ID_STORAGE_KEY, sessionId);
  }
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const apiKey = localStorage.getItem(API_KEY_STORAGE_KEY) ?? "";
  const sessionId = sessionStorage.getItem(SESSION_ID_STORAGE_KEY) ?? "";
  const hasBody = init?.body != null;
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const headers = new Headers(init?.headers);

  if (hasBody && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (apiKey && !headers.has("X-API-Key")) {
    headers.set("X-API-Key", apiKey);
  }
  if (sessionId && !headers.has("X-Session-Id")) {
    headers.set("X-Session-Id", sessionId);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (res.status === 401) {
    unauthorized();
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
  return res;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init);
  captureSessionId(res);
  return res.json() as Promise<T>;
}
