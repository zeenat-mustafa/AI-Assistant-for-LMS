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
 * Two rules from earlier sub-features apply here and are load-bearing:
 *
 * 1. `combined_score` is 0.0 both for a genuine zero AND for an ungraded
 *    submitter, so it is NEVER used to decide grading status. Presence in
 *    `per_file` is the signal, exactly as 5.4 resolved it instructor-side.
 * 2. `GradeRead.rationale` (the structured criterion breakdown) IS returned
 *    by this endpoint and, as of bugfix-structured-rationale-display, IS the
 *    primary detail rendered per file (via the shared `GradeFileRow`) --
 *    `feedback_text` is now only a fallback for when `rationale` is missing.
 */

import type { GradeSummary, SubmissionRead } from "@/lib/api";
import { EmptyState, FormError, Loading, Panel } from "@/components/ui";
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
    <Panel title="Your grade">
      {error ? <FormError>{error}</FormError> : null}

      {grades === undefined && !error ? (
        <Loading>Loading your grade…</Loading>
      ) : submission === null ? (
        <EmptyState>
          Nothing to grade yet — you haven&apos;t submitted for this session.
        </EmptyState>
      ) : !hasGrades ? (
        // Submitted, but no Grade rows exist. combined_score is 0.0 here and
        // must not be shown as a score.
        <EmptyState>
          Your submission hasn&apos;t been graded yet. Your score and feedback will
          appear here once your instructor grades it.
        </EmptyState>
      ) : (
        <>
          {totalAssignmentFiles > 1 ? (
            <div className="mb-4 flex items-baseline justify-between gap-4 border-b border-slate-200 pb-3">
              <span className="text-sm text-slate-600">
                Combined score
                <span className="ml-1 text-xs text-slate-400">
                  (across all {totalAssignmentFiles} assignment files)
                </span>
              </span>
              <span className="text-lg font-semibold tabular-nums text-slate-900">
                {grades?.combined_score ?? 0} / 10
              </span>
            </div>
          ) : null}

          {/*
            Summary first: the combined score above stays the headline, and
            each file collapses to filename + score until asked to open. The
            detail is unchanged, only hidden by default.
          */}
          <ul className="divide-y divide-slate-100">
            {gradedFiles.map((grade) => (
              <GradeFileRow key={grade.id} grade={grade} />
            ))}
          </ul>

          {gradedFiles.length < totalAssignmentFiles ? (
            <p className="mt-4 border-t border-slate-200 pt-3 text-xs text-slate-500">
              {gradedFiles.length} of {totalAssignmentFiles} assignment files graded so
              far. The combined score counts every assignment file in the session, so it
              will change as the rest are graded.
            </p>
          ) : null}
        </>
      )}
    </Panel>
  );
}
