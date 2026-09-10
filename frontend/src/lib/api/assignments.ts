/** Endpoints from backend/app/routers/assignments.py. */

import { ApiError, apiFetch, buildRequestInit, type RequestOptions } from "./client";
import { buildUrl } from "./config";
import type { AssignmentUploadRead, GenerateRubricResult } from "./types";

/**
 * POST /sessions/{id}/assignments -- instructor only, 201.
 *
 * bugfix-original-upload-preservation: any file type is accepted, single or
 * inside a `.zip` -- there is no extension restriction anymore. The
 * multipart field name is `files` and repeats once per file; the response
 * is always a list, one entry per file uploaded (a `.zip` is one entry with
 * its own filename, never a list of what's inside it).
 *
 * Notebooks (standalone or bundled in a zip) are still extracted internally
 * for grading exactly as before; that never appears in this response.
 *
 * The backend validates the entire batch (duplicate filenames, across
 * uploads and whatever they'd extract into) before writing anything, so a
 * rejection means nothing at all was saved.
 */
export function uploadAssignment(
  sessionId: number,
  files: File[],
  options: RequestOptions = {},
): Promise<AssignmentUploadRead[]> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  return apiFetch<AssignmentUploadRead[]>(`/sessions/${sessionId}/assignments`, {
    ...options,
    method: "POST",
    body: form,
  });
}

/**
 * GET /sessions/{id}/assignments -- exactly what was uploaded to a session,
 * one row per upload event.
 */
export function listAssignments(
  sessionId: number,
  options: RequestOptions = {},
): Promise<AssignmentUploadRead[]> {
  return apiFetch<AssignmentUploadRead[]>(`/sessions/${sessionId}/assignments`, {
    ...options,
    method: "GET",
  });
}

/**
 * URL of GET /sessions/{id}/assignments/{uploadId}/download.
 *
 * Returned as a URL rather than fetched, because a download is a navigation,
 * not a JSON call. Note the endpoint requires auth, so a bare `<a href>` will
 * 401 -- use `downloadAssignment` below to fetch it with the bearer token.
 */
export function assignmentDownloadUrl(sessionId: number, uploadId: number): string {
  return buildUrl(`/sessions/${sessionId}/assignments/${uploadId}/download`);
}

/**
 * Fetch a download endpoint's bytes as an authenticated Blob.
 *
 * Not `apiFetch`: the response is a binary file, not JSON.
 */
async function fetchFileBlob(path: string, options: RequestOptions): Promise<Blob> {
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
    throw new ApiError(response.status, await response.text());
  }
  return response.blob();
}

/**
 * Fetch an assignment upload's bytes as a Blob, authenticated -- exactly
 * what was uploaded, byte-for-byte (a zip comes back as that same zip, never
 * a reconstruction). The caller turns it into an object URL to trigger the
 * browser download.
 */
export function downloadAssignment(
  sessionId: number,
  uploadId: number,
  options: RequestOptions = {},
): Promise<Blob> {
  return fetchFileBlob(`/sessions/${sessionId}/assignments/${uploadId}/download`, options);
}

/**
 * DELETE /sessions/{id}/assignments/{uploadId} -- instructor only. 204.
 *
 * Removes the upload AND everything it produced internally (extracted
 * notebooks/resources, in the DB and on disk) -- deleting a zip that
 * contained a notebook also removes that notebook's rubric/grading
 * candidacy, though any Grade already recorded against it is untouched.
 */
export function deleteAssignment(
  sessionId: number,
  uploadId: number,
  options: RequestOptions = {},
): Promise<void> {
  return apiFetch<void>(`/sessions/${sessionId}/assignments/${uploadId}`, {
    ...options,
    method: "DELETE",
  });
}

/**
 * POST /sessions/{id}/assignments/{fileId}/generate-rubric -- instructor only.
 * Unchanged: still keyed by the internal UnsolvedFile id (grading-pipeline
 * use), not an AssignmentUpload id. `force: true` regenerates over an
 * existing cached rubric.
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
