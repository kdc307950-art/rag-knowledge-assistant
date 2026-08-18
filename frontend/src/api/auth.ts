import { request, setAccessToken, clearAccessToken } from "./client";

export interface AuthUser {
  id: number | string;
  username: string;
  display_name?: string;
  department: string;
  roles: string[];
  active: boolean;
}

interface LoginResponse {
  token: string;
  expires_in: number;
  user: AuthUser;
}

export async function loginUser(username: string, password: string) {
  const result = await request<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setAccessToken(result.token);
  return result;
}

export async function getCurrentUser() {
  const result = await request<{ user: AuthUser }>("/auth/me");
  return result.user;
}

export async function logoutUser() {
  try {
    await request<{ ok: boolean }>("/auth/logout", { method: "POST" });
  } finally {
    clearAccessToken();
  }
}
