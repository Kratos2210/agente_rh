// Sesión del dashboard (solo cliente): token JWT + usuario, guardados en localStorage.
// El token se adjunta como `Authorization: Bearer` en cada request (ver lib/api.ts).

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: "admin" | "recruiter" | "viewer" | string;
  tenant_id: string;
}

const TOKEN_KEY = "hira_token";
const USER_KEY = "hira_user";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  try {
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function setSession(token: string, user: AuthUser): void {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

export function isAdmin(): boolean {
  return getUser()?.role === "admin";
}
