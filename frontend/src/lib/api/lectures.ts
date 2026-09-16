/** Endpoints from backend/app/routers/lectures.py (Phase 7.1). */

import { ApiError, apiFetch, buildRequestInit, type RequestOptions } from "./client";
import type { LectureFileRead } from "./types";

/**
 * POST /sessions/{id}/lectures -- instructor only, 201.
 *
 * Exactly one `.pptx` per request, multipart field `file`. 422 for any other
 * extension (including legacy `.ppt`), 409 when a lecture with the same
 * filename already exists in the session. There is no delete endpoint.
 *
 * Extraction runs inside this request; a failure still returns 201 with
 * `extracted: false` and the real `extraction_error`.
 */
export function uploadLecture(
  sessionId: number,
  file: File,
  options: RequestOptions = {},
): Promise<LectureFileRead> {
  const form = new FormData();
  form.append("file", file, file.name);
  return apiFetch<LectureFileRead>(`/sessions/${sessionId}/lectures`, {
    ...options,
    method: "POST",
    body: form,
  });
}

/** GET /sessions/{id}/lectures -- any authenticated user. Oldest upload first, as the backend orders it. */
export function listLectures(
  sessionId: number,
  options: RequestOptions = {},
): Promise<LectureFileRead[]> {
  return apiFetch<LectureFileRead[]>(`/sessions/${sessionId}/lectures`, {
    ...options,
    method: "GET",
  });
}

/**
 * Authenticated Blob fetch for a binary download.
 *
 * Deliberate duplicate of assignments.ts's private `fetchFileBlob` (7.7 must
 * not modify assignments.ts) -- consolidate in 7.8.
 */
async function fetchLectureBlob(path: string, options: RequestOptions): Promise<Blob> {
  const { url, init } = buildRequestInit({ ...options, method: "GET" });
  const target = url(path);
  let response: Response;
  try {
    response = await fetch(target, init);
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `Could not reach the API at ${target}: ${reason}`);
  }
  if (!response.ok) {
    const text = await response.text();
    let detail = text || `Request failed with status ${response.status}.`;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      /* not JSON -- keep the raw text */
    }
    throw new ApiError(response.status, detail, text);
  }
  return response.blob();
}

/**
 * GET /sessions/{id}/lectures/{lectureId}/download -- any authenticated user.
 * The original .pptx bytes exactly as uploaded. A bare `<a href>` would 401.
 */
export function downloadLecture(
  sessionId: number,
  lectureId: number,
  options: RequestOptions = {},
): Promise<Blob> {
  return fetchLectureBlob(`/sessions/${sessionId}/lectures/${lectureId}/download`, options);
}
