/**
 * Endpoints from backend/app/routers/quiz.py (Phase 7.6). All student only.
 *
 * Practice quizzes never touch real grades. The score is always whatever the
 * backend returned -- nothing here (or in the UI) computes one.
 */

import { apiFetch, type RequestOptions } from "./client";
import type {
  QuizAttemptOut,
  QuizGenerateRequest,
  QuizHistoryOut,
  QuizResultOut,
} from "./types";

/** POST /quiz/generate -- JSON scope: assignment_file | session | multiple_sessions | topic. 201. */
export function generateQuiz(
  payload: QuizGenerateRequest,
  options: RequestOptions = {},
): Promise<QuizAttemptOut> {
  return apiFetch<QuizAttemptOut>("/quiz/generate", {
    ...options,
    method: "POST",
    json: payload,
  });
}

/**
 * POST /quiz/generate/upload -- multipart field `file`, a single .pptx or
 * .ipynb. The backend uses the bytes for this quiz only and never stores them.
 */
export function generateQuizFromUpload(
  file: File,
  options: RequestOptions = {},
): Promise<QuizAttemptOut> {
  const form = new FormData();
  form.append("file", file, file.name);
  return apiFetch<QuizAttemptOut>("/quiz/generate/upload", {
    ...options,
    method: "POST",
    body: form,
  });
}

/** POST /quiz/{id}/submit -- exactly 5 answers, each 0-3. 409 if already submitted. */
export function submitQuiz(
  attemptId: number,
  answers: number[],
  options: RequestOptions = {},
): Promise<QuizResultOut> {
  return apiFetch<QuizResultOut>(`/quiz/${attemptId}/submit`, {
    ...options,
    method: "POST",
    json: { answers },
  });
}

/** GET /quiz/{id} -- `QuizAttemptOut` before submit, `QuizResultOut` after (see `isQuizResult`). */
export function getQuizAttempt(
  attemptId: number,
  options: RequestOptions = {},
): Promise<QuizAttemptOut | QuizResultOut> {
  return apiFetch<QuizAttemptOut | QuizResultOut>(`/quiz/${attemptId}`, {
    ...options,
    method: "GET",
  });
}

/** GET /quiz/history -- the student's own SUBMITTED attempts, most recent first. */
export function getQuizHistory(options: RequestOptions = {}): Promise<QuizHistoryOut> {
  return apiFetch<QuizHistoryOut>("/quiz/history", { ...options, method: "GET" });
}
