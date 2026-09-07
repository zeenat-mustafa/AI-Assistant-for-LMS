/** Endpoints from backend/app/routers/sessions.py. */

import { apiFetch, type RequestOptions } from "./client";
import type { BatchGradeResult, SessionList, SessionRead } from "./types";

/** POST /sessions -- instructor only. 409 if the title is already theirs. */
export function createSession(
  title: string,
  options: RequestOptions = {},
): Promise<SessionRead> {
  return apiFetch<SessionRead>("/sessions", { ...options, method: "POST", json: { title } });
}

/** GET /sessions -- paginated. `limit` is capped at 200 by the backend. */
export function listSessions(
  params: { skip?: number; limit?: number } = {},
  options: RequestOptions = {},
): Promise<SessionList> {
  return apiFetch<SessionList>("/sessions", {
    ...options,
    method: "GET",
    query: { skip: params.skip, limit: params.limit },
  });
}

/** GET /sessions/{id} -- one session with its assignment files. */
export function getSession(
  sessionId: number,
  options: RequestOptions = {},
): Promise<SessionRead> {
  return apiFetch<SessionRead>(`/sessions/${sessionId}`, { ...options, method: "GET" });
}

/** DELETE /sessions/{id} -- instructor only. 204, no body. */
export function deleteSession(
  sessionId: number,
  options: RequestOptions = {},
): Promise<void> {
  return apiFetch<void>(`/sessions/${sessionId}`, { ...options, method: "DELETE" });
}

/**
 * POST /sessions/{id}/grade -- instructor only.
 *
 * Grades every ungraded submission file in the session and returns all
 * pipeline events at once. For live progress use `streamChat` instead; this
 * one blocks until the whole batch finishes.
 */
export function gradeSession(
  sessionId: number,
  options: RequestOptions = {},
): Promise<BatchGradeResult> {
  return apiFetch<BatchGradeResult>(`/sessions/${sessionId}/grade`, {
    ...options,
    method: "POST",
  });
}
