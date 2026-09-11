"use client";

/**
 * Student's view of one session: submission status, upload, own grades, and
 * the session's downloadable files.
 *
 * One list: exactly what the instructor uploaded
 * ------------------------------------------------
 * bugfix-original-upload-preservation: assignment files are shown and
 * downloaded as one list of AssignmentUpload rows -- a zip is one row with
 * its own filename, never a browsable list of the notebooks/resources
 * extracted from it. A student who needs one notebook out of a bundled zip
 * downloads the zip and extracts it themselves; there is deliberately no way
 * to download just the piece inside it anymore, on either the instructor or
 * student side.
 *
 * Every endpoint used is gated on `get_current_user`, not `require_instructor`,
 * so 5.3's authenticated blob-download helper works unchanged with a student
 * token — verified, not assumed.
 *
 * Upload and grades live on this one page on purpose: /grades/mine is
 * per-session, and an upload can destroy the grades shown here, so the two
 * have to stay in step. See submission-upload-panel.tsx and my-grades-panel.tsx.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  ApiError,
  deleteSubmissionUpload,
  downloadAssignment,
  downloadMySubmissionUpload,
  getMyGrades,
  getMySubmission,
  getSession,
} from "@/lib/api";
import type {
  AssignmentUploadRead,
  GradeSummary,
  SessionRead,
  SubmissionRead,
} from "@/lib/api";
import { MyGradesPanel } from "./my-grades-panel";
import { SubmissionUploadPanel } from "./submission-upload-panel";
import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";
import {
  EmptyState,
  FormError,
  Loading,
  Panel,
  SmallButton,
} from "@/components/ui";
import { formatDate } from "@/lib/format";
import { triggerBlobDownload } from "@/lib/download";

export function StudentSessionDetail({ sessionId }: { sessionId: number }) {
  return (
    <RequireAuth role="student">
      <SignedInShell>
        <StudentSessionBody sessionId={sessionId} />
      </SignedInShell>
    </RequireAuth>
  );
}

async function loadSession(
  sessionId: number,
): Promise<{ session: SessionRead } | { error: string }> {
  try {
    return { session: await getSession(sessionId) };
  } catch (error) {
    return {
      error: error instanceof ApiError ? error.detail : "Could not load this session.",
    };
  }
}

/**
 * `GET /sessions/{id}/submissions/mine` answers 200 with a `null` body when
 * the student has not submitted — it is NOT a 404. So "no submission yet" is
 * a successful, expected result and must never surface as an error.
 */
async function loadMySubmission(
  sessionId: number,
): Promise<{ submission: SubmissionRead | null } | { error: string }> {
  try {
    return { submission: await getMySubmission(sessionId) };
  } catch (error) {
    return {
      error:
        error instanceof ApiError
          ? error.detail
          : "Could not check your submission status.",
    };
  }
}

/** Same discard-if-stale shape as the loaders above. */
async function loadMyGrades(
  sessionId: number,
): Promise<{ grades: GradeSummary } | { error: string }> {
  try {
    return { grades: await getMyGrades(sessionId) };
  } catch (error) {
    return {
      error: error instanceof ApiError ? error.detail : "Could not load your grade.",
    };
  }
}

function StudentSessionBody({ sessionId }: { sessionId: number }) {
  const [session, setSession] = useState<SessionRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // `undefined` = still loading; `null` = loaded, nothing submitted.
  const [submission, setSubmission] = useState<SubmissionRead | null | undefined>(undefined);
  const [submissionError, setSubmissionError] = useState<string | null>(null);

  const [grades, setGrades] = useState<GradeSummary | undefined>(undefined);
  const [gradesError, setGradesError] = useState<string | null>(null);

  /** Re-read the student's own submission -- used after upload and after a per-item delete. */
  const refreshSubmission = useCallback(async () => {
    const result = await loadMySubmission(sessionId);
    if ("error" in result) {
      setSubmissionError(result.error);
      return;
    }
    setSubmission(result.submission);
    setSubmissionError(null);
  }, [sessionId]);

  /** Re-read grades. Called on mount, after an upload, and after a delete that removed a graded file. */
  const refreshGrades = useCallback(async () => {
    const result = await loadMyGrades(sessionId);
    if ("error" in result) {
      setGradesError(result.error);
      return;
    }
    setGrades(result.grades);
    setGradesError(null);
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadSession(sessionId).then((result) => {
      if (cancelled) return;
      if ("error" in result) {
        setLoadError(result.error);
        return;
      }
      setSession(result.session);
      setLoadError(null);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadMySubmission(sessionId).then((result) => {
      if (cancelled) return;
      if ("error" in result) {
        setSubmissionError(result.error);
        setSubmission(null);
        return;
      }
      setSubmission(result.submission);
      setSubmissionError(null);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadMyGrades(sessionId).then((result) => {
      if (cancelled) return;
      if ("error" in result) {
        setGradesError(result.error);
        return;
      }
      setGrades(result.grades);
      setGradesError(null);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  if (loadError) {
    return (
      <>
        <BackLink />
        <FormError>{loadError}</FormError>
      </>
    );
  }

  if (!session) return <Loading>Loading session…</Loading>;

  return (
    <div className="space-y-8">
      <div>
        <BackLink />
        <h1 className="mt-2 text-2xl font-semibold text-slate-900">{session.title}</h1>
        <p className="mt-1 text-sm text-slate-600">
          Created {formatDate(session.created_at)}
        </p>
      </div>

      <SubmissionStatusPanel
        sessionId={sessionId}
        submission={submission}
        error={submissionError}
        onDeleted={async () => {
          // A delete can remove a graded file (after explicit confirm), so
          // both the upload list and the grade display need a fresh read.
          await refreshSubmission();
          await refreshGrades();
        }}
      />

      <SubmissionUploadPanel
        sessionId={sessionId}
        submission={submission}
        onUploaded={(updated) => {
          setSubmission(updated);
          void refreshGrades();
        }}
      />

      <MyGradesPanel
        grades={grades}
        error={gradesError}
        submission={submission}
        totalAssignmentFiles={session.unsolved_files.length}
      />

      <AssignmentFilesPanel
        sessionId={sessionId}
        uploads={session.assignment_uploads}
      />
    </div>
  );
}

function BackLink() {
  return (
    <Link href="/student" className="text-sm text-slate-500 underline">
      ← All sessions
    </Link>
  );
}

/**
 * Lists every SubmissionUpload the student has made (additive, so possibly
 * many) with a per-item delete control. Delete enforcement is real and
 * server-side now (a 409 naming the actual score(s) that would be lost when
 * the upload produced a graded file), not just a client-side guardrail —
 * the confirm prompt shown here is the backend's own message, verbatim.
 */
function SubmissionStatusPanel({
  sessionId,
  submission,
  error,
  onDeleted,
}: {
  sessionId: number;
  submission: SubmissionRead | null | undefined;
  error: string | null;
  onDeleted: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [confirmText, setConfirmText] = useState<Record<number, string>>({});
  const [itemError, setItemError] = useState<Record<number, string>>({});
  const [downloadBusyId, setDownloadBusyId] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<Record<number, string>>({});

  async function handleDownload(upload: SubmissionRead["uploads"][number]) {
    setDownloadError((cur) => {
      const { [upload.id]: _removed, ...rest } = cur;
      return rest;
    });
    setDownloadBusyId(upload.id);
    try {
      const blob = await downloadMySubmissionUpload(sessionId, upload.id);
      triggerBlobDownload(blob, upload.original_filename);
    } catch (dlError) {
      setDownloadError((cur) => ({
        ...cur,
        [upload.id]:
          dlError instanceof ApiError ? dlError.detail : `Could not download ${upload.original_filename}.`,
      }));
    } finally {
      setDownloadBusyId(null);
    }
  }

  async function handleDelete(uploadId: number, confirm: boolean) {
    setItemError((cur) => {
      const { [uploadId]: _removed, ...rest } = cur;
      return rest;
    });
    setBusyId(uploadId);
    try {
      await deleteSubmissionUpload(sessionId, uploadId, { confirm });
      setConfirmText((cur) => {
        const { [uploadId]: _removed, ...rest } = cur;
        return rest;
      });
      await onDeleted();
    } catch (deleteError) {
      if (deleteError instanceof ApiError && deleteError.status === 409) {
        setConfirmText((cur) => ({ ...cur, [uploadId]: deleteError.detail }));
      } else {
        setItemError((cur) => ({
          ...cur,
          [uploadId]:
            deleteError instanceof ApiError
              ? deleteError.detail
              : "Could not remove this upload.",
        }));
      }
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Panel title="Your submission">
      {error ? <FormError>{error}</FormError> : null}

      {submission === undefined && !error ? (
        <Loading>Checking your submission…</Loading>
      ) : submission === null || submission.uploads.length === 0 ? (
        // The normal, expected "nothing yet" case — not a failure.
        <EmptyState>
          You haven&apos;t submitted anything for this session yet.
        </EmptyState>
      ) : submission ? (
        <div>
          <p className="text-xs text-slate-500">
            {submission.uploads.length}{" "}
            {submission.uploads.length === 1 ? "upload" : "uploads"} ·{" "}
            {submission.files.length}{" "}
            {submission.files.length === 1 ? "notebook" : "notebooks"} ·{" "}
            {submission.files.filter((f) => f.graded).length} graded
          </p>

          <ul className="mt-3 divide-y divide-slate-200 border-t border-slate-200">
            {submission.uploads.map((upload) => {
              const producedFiles = submission.files.filter(
                (f) => f.source_upload_id === upload.id,
              );
              return (
                <li key={upload.id} className="py-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-slate-900">
                        {upload.original_filename}
                      </span>
                      <span className="block text-xs text-slate-500">
                        Uploaded {formatDate(upload.uploaded_at)}
                        {producedFiles.length > 0
                          ? ` · ${producedFiles.length} ${producedFiles.length === 1 ? "notebook" : "notebooks"} (${producedFiles.filter((f) => f.graded).length} graded)`
                          : ""}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      <SmallButton
                        onClick={() => void handleDownload(upload)}
                        disabled={downloadBusyId === upload.id}
                      >
                        {downloadBusyId === upload.id ? "Downloading…" : "Download"}
                      </SmallButton>
                      <SmallButton
                        tone="danger"
                        onClick={() => void handleDelete(upload.id, false)}
                        disabled={busyId === upload.id}
                      >
                        {busyId === upload.id ? "Removing…" : "Remove"}
                      </SmallButton>
                    </span>
                  </div>

                  {downloadError[upload.id] ? (
                    <p className="mt-2 text-xs text-red-600" role="alert">
                      {downloadError[upload.id]}
                    </p>
                  ) : null}

                  {itemError[upload.id] ? (
                    <p className="mt-2 text-xs text-red-600" role="alert">
                      {itemError[upload.id]}
                    </p>
                  ) : null}

                  {confirmText[upload.id] ? (
                    <div className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-3">
                      <p className="text-sm text-amber-900">{confirmText[upload.id]}</p>
                      <div className="mt-3 flex items-center gap-2">
                        <SmallButton
                          tone="danger"
                          onClick={() => void handleDelete(upload.id, true)}
                          disabled={busyId === upload.id}
                        >
                          Remove and delete my grade
                        </SmallButton>
                        <SmallButton
                          onClick={() =>
                            setConfirmText((cur) => {
                              const { [upload.id]: _removed, ...rest } = cur;
                              return rest;
                            })
                          }
                          disabled={busyId === upload.id}
                        >
                          Cancel
                        </SmallButton>
                      </div>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}

function AssignmentFilesPanel({
  sessionId,
  uploads,
}: {
  sessionId: number;
  uploads: AssignmentUploadRead[];
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload(file: AssignmentUploadRead) {
    setError(null);
    setBusyId(file.id);
    try {
      // Requires the bearer token, so a plain <a href> would 401. Returns
      // the ORIGINAL bytes exactly -- a zip downloads as that zip, never a
      // browsable list of what's inside it.
      const blob = await downloadAssignment(sessionId, file.id);
      triggerBlobDownload(blob, file.original_filename);
    } catch (downloadError) {
      setError(
        downloadError instanceof ApiError
          ? downloadError.detail
          : `Could not download ${file.original_filename}.`,
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Panel
      title="Assignment files"
      description="Download a file, solve any notebooks inside it, then upload your solved copy. A zip downloads exactly as your instructor uploaded it."
    >
      {error ? <FormError>{error}</FormError> : null}

      {uploads.length === 0 ? (
        <EmptyState>
          Your instructor hasn&apos;t uploaded any assignment files for this session yet.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-slate-200">
          {uploads.map((file) => (
            <li key={file.id} className="flex items-center justify-between gap-4 py-3">
              <span className="min-w-0">
                <span className="truncate text-sm font-medium text-slate-900">
                  {file.original_filename}
                </span>
                <span className="block text-xs text-slate-500">
                  Added {formatDate(file.uploaded_at)}
                </span>
              </span>
              <SmallButton
                onClick={() => handleDownload(file)}
                disabled={busyId === file.id}
              >
                {busyId === file.id ? "Downloading…" : "Download"}
              </SmallButton>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
