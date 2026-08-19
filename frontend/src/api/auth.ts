import { request } from "./client";

export interface AuthUser {
  id: number | string;
  username: string;
  display_name?: string;
  department: string;
  roles: string[];
  active: boolean;
}

interface LoginResponse {
  expires_in: number;
  user: AuthUser;
}

export async function loginUser(username: string, password: string) {
  await request<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  // Confirm that the cookie was accepted and use the canonical server-side
  // identity. This also prevents treating a successful JSON response as a
  // logged-in browser session when cookie delivery is misconfigured.
  const user = await getCurrentUser();
  return { user };
}

export async function getCurrentUser() {
  const result = await request<{ user: AuthUser }>("/auth/me");
  return result.user;
}

export async function logoutUser() {
  await request<{ ok: boolean }>("/auth/logout", { method: "POST" });
}
