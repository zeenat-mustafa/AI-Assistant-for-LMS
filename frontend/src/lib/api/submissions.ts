/** Endpoints from backend/app/routers/submissions.py. */

import { ApiError, apiFetch, buildRequestInit, type RequestOptions } from "./client";
import { buildUrl } from "./config";
import type { GradeSubmissionFileResult, SubmissionRead } from "./types";

/**
 * POST /sessions/{id}/submissions -- 201.
 *
 * Any file type is accepted, single or inside a `.zip` -- no extension
 * restriction, and no rule requiring a zip to contain at least one
 * `.ipynb`. The multipart field name is `files` and repeats once per file,
 * matching the assignment-side upload exactly.
 *
 * Uploads are ADDITIVE: each call adds to the student's existing submission
 * for this session rather than replacing it. Returns the full updated
 * `SubmissionRead`, including every upload made so far.
 */
export function uploadSubmission(
  sessionId: number,
  files: File[],
  options: RequestOptions = {},
): Promise<SubmissionRead> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  return apiFetch<SubmissionRead>(`/sessions/${sessionId}/submissions`, {
    ...options,
    method: "POST",
    body: form,
  });
}

/** GET /sessions/{id}/submissions -- every student's submissions. Instructor only. */
export function listSubmissions(
  sessionId: number,
  options: RequestOptions = {},
): Promise<SubmissionRead[]> {
  return apiFetch<SubmissionRead[]>(`/sessions/${sessionId}/submissions`, {
    ...options,
    method: "GET",
  });
}

/**
 * GET /sessions/{id}/submissions/mine -- the caller's own submission.
 * Resolves to `null` (not a 404) when they haven't submitted anything.
 */
export function getMySubmission(
  sessionId: number,
  options: RequestOptions = {},
): Promise<SubmissionRead | null> {
  return apiFetch<SubmissionRead | null>(`/sessions/${sessionId}/submissions/mine`, {
    ...options,
    method: "GET",
  });
}

/**
 * Fetch a download endpoint's bytes as an authenticated Blob.
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
 * GET /sessions/{id}/submissions/uploads/{uploadId}/download -- instructor
 * only. Returns the exact bytes the student uploaded, byte-for-byte (a zip
 * comes back as that same zip, never a reconstruction).
 */
export function downloadSubmissionUpload(
  sessionId: number,
  uploadId: number,
  options: RequestOptions = {},
): Promise<Blob> {
  return fetchFileBlob(`/sessions/${sessionId}/submissions/uploads/${uploadId}/download`, options);
}

/**
 * GET /sessions/{id}/submissions/mine/uploads/{uploadId}/download -- the
 * owning student only, never another student's upload (404, not 403 --
 * same not-yours-not-found convention as `deleteSubmissionUpload`). Returns
 * the exact bytes the student uploaded, byte-for-byte -- lets a student
 * confirm exactly what they sent, the same guarantee the instructor
 * download already gives.
 */
export function downloadMySubmissionUpload(
  sessionId: number,
  uploadId: number,
  options: RequestOptions = {},
): Promise<Blob> {
  return fetchFileBlob(`/sessions/${sessionId}/submissions/mine/uploads/${uploadId}/download`, options);
}

/**
 * DELETE /sessions/{id}/submissions/uploads/{uploadId} -- the owning
 * student only, never another student's upload.
 *
 * If the upload includes a file that already has a Grade, the backend
 * refuses with 409 (an `ApiError` whose `.detail` names the real score(s))
 * unless `confirm: true` is passed -- real server-side enforcement, not just
 * a UI guardrail. Call once without `confirm` to check; if it throws a 409,
 * show `.detail` to the student and call again with `confirm: true` only on
 * their explicit approval.
 */
export function deleteSubmissionUpload(
  sessionId: number,
  uploadId: number,
  params: { confirm?: boolean } = {},
  options: RequestOptions = {},
): Promise<void> {
  return apiFetch<void>(`/sessions/${sessionId}/submissions/uploads/${uploadId}`, {
    ...options,
    method: "DELETE",
    query: { confirm: params.confirm },
  });
}

/**
 * URL of GET /sessions/{id}/submissions/uploads/{uploadId}/download.
 * Returned as a URL rather than fetched for the same reason as
 * `assignmentDownloadUrl` -- the endpoint requires auth, so a bare
 * `<a href>` will 401; use `downloadSubmissionUpload` to fetch with the
 * bearer token instead.
 */
export function submissionUploadDownloadUrl(sessionId: number, uploadId: number): string {
  return buildUrl(`/sessions/${sessionId}/submissions/uploads/${uploadId}/download`);
}

/**
 * POST /sessions/{id}/submissions/files/{fileId}/grade -- instructor only.
 * Grades one submission file; re-calling overwrites the existing grade.
 */
export function gradeSubmissionFile(
  sessionId: number,
  submissionFileId: number,
  options: RequestOptions = {},
): Promise<GradeSubmissionFileResult> {
  return apiFetch<GradeSubmissionFileResult>(
    `/sessions/${sessionId}/submissions/files/${submissionFileId}/grade`,
    { ...options, method: "POST" },
  );
}
