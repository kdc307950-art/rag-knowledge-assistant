// fetch 封装：浏览器认证统一使用 HttpOnly Cookie，另带会话头。
const API_BASE = "/api";
export const SESSION_ID_STORAGE_KEY = "sessionId";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function friendlyApiError(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) {
    return error instanceof Error ? error.message : fallback;
  }
  if (error.status === 401) return "登录已失效，请重新登录。";
  if (error.status === 403) return "当前账号没有执行此操作的权限。";
  if (error.status === 503) return "服务尚未就绪，请检查部署鉴权和依赖状态。";
  return error.message || fallback;
}

function unauthorized(): never {
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
  const sessionId = sessionStorage.getItem(SESSION_ID_STORAGE_KEY) ?? "";
  const hasBody = init?.body != null;
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const headers = new Headers(init?.headers);

  if (hasBody && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Browser requests must not fall back to client-controlled credentials. The
  // backend authenticates multi-user sessions from its HttpOnly cookie.
  headers.delete("Authorization");
  headers.delete("X-API-Key");
  if (sessionId && !headers.has("X-Session-Id")) {
    headers.set("X-Session-Id", sessionId);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
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
