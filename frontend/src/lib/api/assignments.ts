/** Endpoints from backend/app/routers/assignments.py. */

import { ApiError, apiFetch, buildRequestInit, type RequestOptions } from "./client";
import { buildUrl } from "./config";
import type { GenerateRubricResult, UnsolvedFileRead } from "./types";

/**
 * POST /sessions/{id}/assignments -- instructor only, 201.
 *
 * Accepts one or more `.ipynb` files, or a single `.zip` that the backend
 * extracts recursively. The multipart field name is `files` and repeats once
 * per file; the response is always a list, even for a single upload.
 */
export function uploadAssignment(
  sessionId: number,
  files: File[],
  options: RequestOptions = {},
): Promise<UnsolvedFileRead[]> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  return apiFetch<UnsolvedFileRead[]>(`/sessions/${sessionId}/assignments`, {
    ...options,
    method: "POST",
    body: form,
  });
}

/** GET /sessions/{id}/assignments -- assignment files for a session. */
export function listAssignments(
  sessionId: number,
  options: RequestOptions = {},
): Promise<UnsolvedFileRead[]> {
  return apiFetch<UnsolvedFileRead[]>(`/sessions/${sessionId}/assignments`, {
    ...options,
    method: "GET",
  });
}

/**
 * URL of GET /sessions/{id}/assignments/{fileId}/download.
 *
 * Returned as a URL rather than fetched, because a download is a navigation,
 * not a JSON call. Note the endpoint requires auth, so a bare `<a href>` will
 * 401 -- use `downloadAssignment` below to fetch it with the bearer token.
 */
export function assignmentDownloadUrl(sessionId: number, fileId: number): string {
  return buildUrl(`/sessions/${sessionId}/assignments/${fileId}/download`);
}

/**
 * Fetch an assignment file's bytes as a Blob, authenticated.
 * The caller turns it into an object URL to trigger the browser download.
 */
export async function downloadAssignment(
  sessionId: number,
  fileId: number,
  options: RequestOptions = {},
): Promise<Blob> {
  // Not apiFetch: the response is a binary file, not JSON.
  const { url, init } = buildRequestInit({ ...options, method: "GET" });
  const target = url(`/sessions/${sessionId}/assignments/${fileId}/download`);
  let response: Response;
  try {
    response = await fetch(target, init);
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `Could not reach the API at ${target}: ${reason}`);
  }
  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return response.blob();
}

/** DELETE /sessions/{id}/assignments/{fileId} -- instructor only. 204. */
export function deleteAssignment(
  sessionId: number,
  fileId: number,
  options: RequestOptions = {},
): Promise<void> {
  return apiFetch<void>(`/sessions/${sessionId}/assignments/${fileId}`, {
    ...options,
    method: "DELETE",
  });
}

/**
 * POST /sessions/{id}/assignments/{fileId}/generate-rubric -- instructor only.
 * `force: true` regenerates over an existing cached rubric.
 */
export function generateRubric(
  sessionId: number,
  fileId: number,
  params: { force?: boolean } = {},
  options: RequestOptions = {},
): Promise<GenerateRubricResult> {
  return apiFetch<GenerateRubricResult>(
    `/sessions/${sessionId}/assignments/${fileId}/generate-rubric`,
    { ...options, method: "POST", query: { force: params.force } },
  );
}
