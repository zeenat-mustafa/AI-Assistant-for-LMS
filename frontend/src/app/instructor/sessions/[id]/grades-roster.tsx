"use client";

/**
 * Per-session grade roster (5.4, Step 1).
 *
 * Two things about the backend's shape drive this component, and neither is
 * obvious from the schema docstrings:
 *
 * 1. `session_grade_report` is built from Submission rows, so a student who
 *    has submitted NOTHING never appears at all. There is no student-listing
 *    endpoint anywhere in the API to cross-reference against (the whole
 *    surface is 21 routes; auth.py has only login/me/register), so the table
 *    genuinely cannot be completed client-side. This is a real, permanent
 *    limitation -- documented in docs/phase5-known-gaps-record.txt rather than as
 *    an on-screen footnote (bugfix-roster-footnote removed the latter).
 *
 * 2. `combined_score` is null ONLY when the session has no assignment files.
 *    A student who submitted but has nothing graded yet gets 0.0 -- which
 *    would read as "scored zero" if rendered naively. `per_file.length` is
 *    what actually distinguishes the two, so that is what is checked.
 */

import { useState } from "react";

import { ApiError, downloadSubmissionUpload } from "@/lib/api";
import type { GradeSummary, SessionGradeReport, SubmissionRead } from "@/lib/api";
import { EmptyState, Loading, Panel, SmallButton } from "@/components/ui";
import { FormError } from "@/components/ui";
import { GradeFileRow } from "@/components/grade-file-row";
import { formatDate } from "@/lib/format";
import { triggerBlobDownload } from "@/lib/download";

export function GradesRoster({
  sessionId,
  report,
  error,
  totalAssignmentFiles,
  submissionsByStudent,
}: {
  sessionId: number;
  /** null while loading. */
  report: SessionGradeReport | null;
  error: string | null;
  totalAssignmentFiles: number;
  /**
   * Every student's SubmissionUpload rows, keyed by student_id -- lets this
   * roster also show/download exactly what each student uploaded, the same
   * way assignment files are shown to instructors. `undefined` while loading
   * (a student simply has no entry if they've uploaded nothing).
   */
  submissionsByStudent: Record<number, SubmissionRead> | undefined;
}) {
  return (
    <Panel
      title="Submissions & grades"
      description="One row per student who has submitted to this session."
    >
      {error ? <FormError>{error}</FormError> : null}

      {report === null && !error ? (
        <Loading>Loading grades…</Loading>
      ) : report && report.students.length === 0 ? (
        <EmptyState>No submissions for this session yet.</EmptyState>
      ) : report ? (
        <ul className="divide-y divide-slate-200">
          {report.students.map((student) => (
            <StudentRow
              key={student.student_id}
              sessionId={sessionId}
              student={student}
              totalAssignmentFiles={totalAssignmentFiles}
              uploads={submissionsByStudent?.[student.student_id]?.uploads ?? []}
            />
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}

function StudentRow({
  sessionId,
  student,
  totalAssignmentFiles,
  uploads,
}: {
  sessionId: number;
  student: GradeSummary;
  totalAssignmentFiles: number;
  uploads: SubmissionRead["uploads"];
}) {
  const [expanded, setExpanded] = useState(false);
  const gradedCount = student.per_file.length;
  const nothingGraded = gradedCount === 0;
  const canExpand = gradedCount > 0 || uploads.length > 0;

  return (
    <li className="py-3">
      <div className="flex items-center justify-between gap-4">
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium text-slate-900">
            {student.student_name}
          </span>
          <span className="block text-xs text-slate-500">
            {gradedCount} of {totalAssignmentFiles}{" "}
            {totalAssignmentFiles === 1 ? "file" : "files"} graded
          </span>
        </span>

        <span className="flex shrink-0 items-center gap-3">
          <ScoreBadge student={student} />
          {canExpand ? (
            <SmallButton onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
              {expanded ? "Hide details" : "Show details"}
            </SmallButton>
          ) : null}
        </span>
      </div>

      {expanded ? (
        <div className="mt-3 border-l-2 border-slate-200 pl-4">
          {uploads.length > 0 ? (
            <div className="mb-3">
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-400">
                Submitted files
              </p>
              <ul className="divide-y divide-slate-100">
                {uploads.map((upload) => (
                  <UploadRow key={upload.id} sessionId={sessionId} upload={upload} />
                ))}
              </ul>
            </div>
          ) : null}

          {/* Each graded file collapses to filename + score. */}
          {!nothingGraded ? (
            <ul>
              {student.per_file.map((grade) => (
                <GradeFileRow key={grade.id} grade={grade} dense showSummary />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function UploadRow({
  sessionId,
  upload,
}: {
  sessionId: number;
  upload: SubmissionRead["uploads"][number];
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload() {
    setError(null);
    setBusy(true);
    try {
      const blob = await downloadSubmissionUpload(sessionId, upload.id);
      triggerBlobDownload(blob, upload.original_filename);
    } catch (downloadError) {
      setError(
        downloadError instanceof ApiError
          ? downloadError.detail
          : `Could not download ${upload.original_filename}.`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex items-center justify-between gap-3 py-1.5">
      <span className="min-w-0">
        <span className="block truncate text-xs font-medium text-slate-800">
          {upload.original_filename}
        </span>
        {error ? <span className="block text-xs text-red-600">{error}</span> : null}
        <span className="block text-xs text-slate-500">
          Uploaded {formatDate(upload.uploaded_at)}
        </span>
      </span>
      <SmallButton onClick={() => void handleDownload()} disabled={busy}>
        {busy ? "Downloading…" : "Download"}
      </SmallButton>
    </li>
  );
}

/**
 * A 0.0 from the backend means "nothing graded yet" whenever `per_file` is
 * empty, so it must not be shown as a score of zero.
 */
function ScoreBadge({ student }: { student: GradeSummary }) {
  if (student.per_file.length === 0) {
    return <span className="text-xs text-slate-500">Not graded yet</span>;
  }
  if (student.combined_score === null) {
    // Only reachable when the session has no assignment files at all.
    return <span className="text-xs text-slate-500">No assignment files</span>;
  }
  return (
    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-sm font-medium tabular-nums text-slate-900">
      {student.combined_score} / 10
    </span>
  );
}
