/** Endpoints from backend/app/routers/submissions.py. */

import { apiFetch, type RequestOptions } from "./client";
import type { GradeSubmissionFileResult, SubmissionRead } from "./types";

/**
 * POST /sessions/{id}/submissions -- 201.
 *
 * One `.ipynb` or one `.zip` per call; the multipart field name is `file`
 * (singular, unlike the assignment upload's `files`). Any student can call it
 * for themselves; the backend takes the student id from the token.
 */
export function uploadSubmission(
  sessionId: number,
  file: File,
  options: RequestOptions = {},
): Promise<SubmissionRead> {
  const form = new FormData();
  form.append("file", file, file.name);
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
