"use client";

/**
 * The student's own grades for one session (5.6, Step 2).
 *
 * Lives inline on the session detail page rather than in a separate view:
 * /grades/mine is per-session, so a standalone grades page would need its own
 * session picker duplicating the dashboard — and Step 1 has to clear this
 * display the moment a re-upload destroys the grades, which is trivial when
 * both live on one page and awkward across two.
 *
 * Rules from earlier sub-features apply here and are load-bearing:
 *
 * 1. `combined_score` is 0.0 both for a genuine zero AND for an ungraded
 *    submitter, so it is NEVER used to decide grading status. Presence in
 *    `per_file` is the signal, exactly as 5.4 resolved it instructor-side.
 * 2. `GradeRead.rationale` (the structured criterion breakdown) is still
 *    generated and stored exactly as before (reserved for a future "why did
 *    I get this grade" chatbot), but this view no longer renders it
 *    directly. (feature-grade-summary) Per graded file: if `grade.summary`
 *    is present, show ONLY that short paragraph (no per-criterion cards, no
 *    expand/collapse) via `SummaryOnlyRow` below. If `summary` is null (a
 *    grade from before this field existed), fall back to the exact same
 *    `GradeFileRow` this view always used, unchanged -- never a blank
 *    screen for historical grades.
 */

import type { GradeRead, GradeSummary, SubmissionRead } from "@/lib/api";
import { EmptyState, FormError, Loading } from "@/components/ui";
import { GradeFileRow } from "@/components/grade-file-row";

export function MyGradesPanel({
  grades,
  error,
  submission,
  totalAssignmentFiles,
}: {
  /** undefined = loading. */
  grades: GradeSummary | undefined;
  error: string | null;
  /** undefined = loading; null = nothing submitted. */
  submission: SubmissionRead | null | undefined;
  totalAssignmentFiles: number;
}) {
  const gradedFiles = grades?.per_file ?? [];
  const hasGrades = gradedFiles.length > 0;

  return (
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">Your grade</h2>

      {error ? <FormError>{error}</FormError> : null}

      {grades === undefined && !error ? (
        <div className="mt-4"><Loading>Loading your grade...</Loading></div>
      ) : submission === null ? (
        <div className="mt-4">
          <EmptyState>You haven&apos;t submitted for this session yet.</EmptyState>
        </div>
      ) : !hasGrades ? (
        <div className="mt-4">
          <EmptyState>
            Your submission hasn&apos;t been graded yet. Your score and feedback will
            appear here once your instructor grades it.
          </EmptyState>
        </div>
      ) : (
        <div className="mt-4">
          {totalAssignmentFiles > 1 ? (
            <div className="mb-4 flex items-baseline justify-between gap-4 border-b border-neutral-200 pb-3">
              <span className="text-sm text-neutral-600">
                Combined score
                <span className="ml-1 text-xs text-neutral-400">
                  ({totalAssignmentFiles} assignment files)
                </span>
              </span>
              <span className="text-lg font-semibold tabular-nums text-neutral-900">
                {grades?.combined_score ?? 0} / 10
              </span>
            </div>
          ) : null}

          <ul className="divide-y divide-neutral-100">
            {gradedFiles.map((grade) =>
              grade.summary ? (
                <SummaryOnlyRow key={grade.id} grade={grade} />
              ) : (
                <GradeFileRow key={grade.id} grade={grade} />
              ),
            )}
          </ul>

          {gradedFiles.length < totalAssignmentFiles ? (
            <p className="mt-4 border-t border-neutral-200 pt-3 text-xs text-neutral-500">
              {gradedFiles.length} of {totalAssignmentFiles} assignment files graded so far.
              The combined score will update as the remaining files are graded.
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}

/**
 * One graded file, student view, when a `summary` exists: filename + score,
 * and the summary paragraph -- no per-criterion cards, no expand/collapse.
 * The structured `rationale` for this grade is still fetched and stored
 * server-side; this row just never renders it (see file header).
 */
function SummaryOnlyRow({ grade }: { grade: GradeRead }) {
  return (
    <li className="py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="truncate text-sm font-medium text-neutral-900">
          {grade.original_filename}
        </span>
        <span className="shrink-0 text-sm font-medium tabular-nums text-neutral-900">
          {grade.score} / 10
        </span>
      </div>
      <p className="mt-1 text-sm text-neutral-700">{grade.summary}</p>
    </li>
  );
}
