/**
 * Base fetch wrapper: URL building, auth header, body encoding, JSON parsing
 * and error surfacing. Every typed endpoint function in this folder goes
 * through `apiFetch` -- nothing calls `fetch` directly except `streamChat`,
 * which needs the raw Response to read the SSE body.
 */

import { buildUrl } from "./config";
import { getToken } from "./token-storage";

/**
 * A non-2xx response, or a transport failure.
 *
 * Errors are always thrown, never returned as a null/undefined result, so a
 * failed call can't be mistaken for an empty one.
 */
export class ApiError extends Error {
  /** HTTP status, or 0 when the request never reached the server. */
  readonly status: number;
  /** FastAPI's `detail`, flattened to a string when it was a validation array. */
  readonly detail: string;
  /** The parsed response body, when there was one. */
  readonly body: unknown;

  constructor(status: number, detail: string, body?: unknown) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

type QueryValue = string | number | boolean | undefined | null;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** Serialised as a JSON body with `Content-Type: application/json`. */
  json?: unknown;
  /** Serialised as `application/x-www-form-urlencoded` (used by /auth/login). */
  form?: Record<string, string>;
  /** Sent as-is; use for `FormData` uploads (browser sets the boundary). */
  body?: BodyInit;
  /** Appended as a query string; null/undefined entries are dropped. */
  query?: Record<string, QueryValue>;
  /** Attach the stored bearer token. Default true. */
  auth?: boolean;
  /**
   * Use this token instead of the stored one. Lets non-browser callers (the
   * verification script, tests) authenticate without `localStorage`.
   */
  token?: string | null;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

function withQuery(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}${path.includes("?") ? "&" : "?"}${qs}` : path;
}

/** Flatten FastAPI's `detail` (a string, or a 422 validation-error array). */
function extractDetail(body: unknown, status: number, statusText: string): string {
  const fallback = `Request failed with status ${status}${statusText ? ` ${statusText}` : ""}.`;
  if (typeof body === "string" && body.trim()) return body.trim();
  if (!body || typeof body !== "object") return fallback;

  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (item && typeof item === "object") {
          const loc = (item as { loc?: unknown[] }).loc;
          const msg = (item as { msg?: unknown }).msg;
          const where = Array.isArray(loc) ? loc.join(".") : "";
          if (msg) return where ? `${where}: ${String(msg)}` : String(msg);
        }
        return JSON.stringify(item);
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

/** Parse a body as JSON when the response says so, otherwise as text. */
async function parseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  const raw = await response.text();
  if (!raw) return null;
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  return raw;
}

/**
 * Build the Request init shared by `apiFetch` and `streamChat`.
 * Exported for the SSE consumer, not for general use.
 */
export function buildRequestInit(options: RequestOptions = {}): {
  url: (path: string) => string;
  init: RequestInit;
} {
  const { method = "GET", json, form, body, auth = true, token, signal } = options;
  const headers: Record<string, string> = { ...(options.headers ?? {}) };

  if (auth) {
    const bearer = token !== undefined ? token : getToken();
    if (bearer) headers["Authorization"] = `Bearer ${bearer}`;
  }

  let payload: BodyInit | undefined;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(json);
  } else if (form !== undefined) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    payload = new URLSearchParams(form).toString();
  } else if (body !== undefined) {
    // FormData: deliberately no Content-Type -- the browser adds the boundary.
    payload = body;
  }

  return {
    url: (path: string) => buildUrl(withQuery(path, options.query)),
    init: { method, headers, body: payload, signal },
  };
}

/**
 * Perform a request and return its parsed JSON body typed as `T`.
 *
 * Throws `ApiError` on any non-2xx response or transport failure. A 204 (or an
 * otherwise empty body) resolves to `undefined`, cast to `T` -- call such
 * endpoints as `apiFetch<void>(...)`.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { url, init } = buildRequestInit(options);

  let response: Response;
  try {
    response = await fetch(url(path), init);
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `Could not reach the API at ${url(path)}: ${reason}`);
  }

  if (response.status === 204) return undefined as T;

  const parsed = await parseBody(response);

  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractDetail(parsed, response.status, response.statusText),
      parsed,
    );
  }

  return parsed as T;
}
