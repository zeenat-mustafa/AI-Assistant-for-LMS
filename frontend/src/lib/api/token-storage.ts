/**
 * Auth token storage.
 *
 * Post-7.8 fix: the JWT lives in `sessionStorage` (not localStorage), so each
 * browser tab can hold a different user's token independently. This lets an
 * instructor and student be logged in side-by-side for demos without one tab
 * overwriting the other's auth state.
 *
 * `sessionStorage` is per-tab, per-origin: closing the tab clears the token.
 * localStorage would persist across tabs and browser restarts, but that shared
 * state made dual-role demos impossible.
 *
 * Every accessor is SSR-safe: `sessionStorage` does not exist during Next.js
 * server rendering, so reads return null and writes are no-ops there.
 */

const TOKEN_KEY = "lms_access_token";

function hasStorage(): boolean {
  return typeof window !== "undefined" && typeof window.sessionStorage !== "undefined";
}

/** The stored JWT, or null when absent / unavailable (SSR, blocked storage). */
export function getToken(): string | null {
  if (!hasStorage()) return null;
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** Persist the JWT. No-op when storage is unavailable. */
export function setToken(token: string): void {
  if (!hasStorage()) return;
  try {
    window.sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage full or blocked -- the caller still has the token in memory */
  }
}

/** Remove the stored JWT (logout). */
export function clearToken(): void {
  if (!hasStorage()) return;
  try {
    window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing to do */
  }
}
