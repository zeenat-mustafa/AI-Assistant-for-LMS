/**
 * Auth token storage.
 *
 * MVP decision: the JWT lives in `localStorage`, read client-side and attached
 * as an `Authorization: Bearer` header by the fetch wrapper.
 *
 * Tradeoff, stated rather than engineered around: `localStorage` is readable
 * by any script running on the page, so a successful XSS can steal the token
 * (an httpOnly cookie could not be read by script). Choosing it anyway because
 * the backend already authenticates with a plain Bearer JWT (no cookie/CSRF
 * handling exists on the FastAPI side at all), and this is a local capstone
 * demo, not deployed software. Moving to httpOnly cookies would require
 * backend changes -- explicitly out of scope for this sub-feature.
 *
 * Every accessor is SSR-safe: `localStorage` does not exist during Next.js
 * server rendering, so reads return null and writes are no-ops there.
 */

const TOKEN_KEY = "lms_access_token";

function hasStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

/** The stored JWT, or null when absent / unavailable (SSR, blocked storage). */
export function getToken(): string | null {
  if (!hasStorage()) return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** Persist the JWT. No-op when storage is unavailable. */
export function setToken(token: string): void {
  if (!hasStorage()) return;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage full or blocked -- the caller still has the token in memory */
  }
}

/** Remove the stored JWT (logout). */
export function clearToken(): void {
  if (!hasStorage()) return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing to do */
  }
}
