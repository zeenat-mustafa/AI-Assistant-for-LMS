"use client";

/**
 * Per-session grade roster.
 *
 * One row per student who has submitted. Students with no submission never
 * appear — there is no student-listing endpoint to cross-reference against.
 * combined_score is 0.0 for both "nothing graded" and a genuine zero;
 * per_file.length is the real grading-status signal.
 */

import { useState } from "react";

import { ApiError, downloadSubmissionUpload } from "@/lib/api";
import type { GradeSummary, SessionGradeReport, SubmissionRead } from "@/lib/api";
import { EmptyState, FormError, Loading, SmallButton } from "@/components/ui";
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
  report: SessionGradeReport | null;
  error: string | null;
  totalAssignmentFiles: number;
  submissionsByStudent: Record<number, SubmissionRead> | undefined;
}) {
  return (
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">Submissions &amp; grades</h2>
      <p className="mt-1 mb-4 text-sm text-neutral-500">One row per student who has submitted.</p>

      {error ? <FormError>{error}</FormError> : null}

      {report === null && !error ? (
        <Loading>Loading grades...</Loading>
      ) : report && report.students.length === 0 ? (
        <EmptyState>No submissions for this session yet.</EmptyState>
      ) : report ? (
        <ul className="divide-y divide-neutral-100">
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
    </div>
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
          <span className="block truncate text-sm font-medium text-neutral-900">
            {student.student_name}
          </span>
          <span className="block text-xs text-neutral-500">
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
        <div className="mt-3 border-l-2 border-neutral-200 pl-4">
          {uploads.length > 0 ? (
            <div className="mb-3">
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-neutral-400">
                Submitted files
              </p>
              <ul className="divide-y divide-neutral-100">
                {uploads.map((upload) => (
                  <UploadRow key={upload.id} sessionId={sessionId} upload={upload} />
                ))}
              </ul>
            </div>
          ) : null}

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
        <span className="block truncate text-xs font-medium text-neutral-800">
          {upload.original_filename}
        </span>
        {error ? <span className="block text-xs text-danger-600">{error}</span> : null}
        <span className="block text-xs text-neutral-500">
          Uploaded {formatDate(upload.uploaded_at)}
        </span>
      </span>
      <SmallButton onClick={() => void handleDownload()} disabled={busy}>
        {busy ? "Downloading..." : "Download"}
      </SmallButton>
    </li>
  );
}

function ScoreBadge({ student }: { student: GradeSummary }) {
  if (student.per_file.length === 0) {
    return <span className="text-xs text-neutral-500">Not graded yet</span>;
  }
  if (student.combined_score === null) {
    return <span className="text-xs text-neutral-500">No assignment files</span>;
  }
  return (
    <span className="lms-badge lms-badge-primary tabular-nums text-sm px-2.5 py-1">
      {student.combined_score} / 10
    </span>
  );
}
