/**
 * API base URL resolution.
 *
 * `NEXT_PUBLIC_API_BASE_URL` is inlined at build time by Next.js, so it must
 * be read as a whole literal expression -- `process.env[name]` would not be
 * substituted. See frontend/.env.example for the expected value.
 */

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000/api/v1";

/** Backend base URL, including the /api/v1 prefix, with no trailing slash. */
export const API_BASE_URL: string = (
  process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/+$/, "");

/** Join the base URL with a leading-slash path, e.g. buildUrl("/auth/me"). */
export function buildUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}
