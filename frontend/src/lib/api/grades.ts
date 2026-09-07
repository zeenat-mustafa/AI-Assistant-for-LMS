/** Endpoints from backend/app/routers/grades.py. */

import { apiFetch, type RequestOptions } from "./client";
import type { GradeSummary, SessionGradeReport } from "./types";

/**
 * GET /sessions/{id}/grades -- instructor only.
 *
 * One `GradeSummary` per student who has submitted. Students with zero
 * submissions do not appear at all (a known, deliberate backend gap).
 */
export function getGradeReport(
  sessionId: number,
  options: RequestOptions = {},
): Promise<SessionGradeReport> {
  return apiFetch<SessionGradeReport>(`/sessions/${sessionId}/grades`, {
    ...options,
    method: "GET",
  });
}

/**
 * GET /sessions/{id}/grades/mine -- the caller's own grades for a session.
 * `combined_score` is null until at least one of their files is graded.
 */
export function getMyGrades(
  sessionId: number,
  options: RequestOptions = {},
): Promise<GradeSummary> {
  return apiFetch<GradeSummary>(`/sessions/${sessionId}/grades/mine`, {
    ...options,
    method: "GET",
  });
}

/** GET /sessions/{id}/grades/{studentId} -- one student's grades. Instructor only. */
export function getStudentGrades(
  sessionId: number,
  studentId: number,
  options: RequestOptions = {},
): Promise<GradeSummary> {
  return apiFetch<GradeSummary>(`/sessions/${sessionId}/grades/${studentId}`, {
    ...options,
    method: "GET",
  });
}
