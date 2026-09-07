/** Endpoints from backend/app/routers/auth.py. */

import { apiFetch, type RequestOptions } from "./client";
import { setToken, clearToken } from "./token-storage";
import type { Token, UserRead, UserRegister } from "./types";

/**
 * POST /auth/login.
 *
 * The backend uses FastAPI's `OAuth2PasswordRequestForm`, so this is
 * form-encoded with `username` (the email) and `password` -- not JSON.
 * On success the returned token is stored, so subsequent calls authenticate
 * automatically. Pass `{ persist: false }` to skip storing it.
 */
export async function login(
  email: string,
  password: string,
  options: { persist?: boolean } = {},
): Promise<Token> {
  const token = await apiFetch<Token>("/auth/login", {
    method: "POST",
    form: { username: email, password },
    auth: false,
  });
  if (options.persist !== false) setToken(token.access_token);
  return token;
}

/** POST /auth/register -- public, creates a student account (201). */
export function register(body: UserRegister): Promise<UserRead> {
  return apiFetch<UserRead>("/auth/register", {
    method: "POST",
    json: body,
    auth: false,
  });
}

/** GET /auth/me -- the currently authenticated user (for role-based routing). */
export function getCurrentUser(options: RequestOptions = {}): Promise<UserRead> {
  return apiFetch<UserRead>("/auth/me", options);
}

/** Drop the stored token. Purely client-side -- the backend keeps no session. */
export function logout(): void {
  clearToken();
}
