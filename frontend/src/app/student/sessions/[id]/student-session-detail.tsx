"use client";

/**
 * Student session detail — Phase 7.8
 *
 * Rendered inside the right pane of SessionShell at
 * /student/sessions/[id]. SignedInShell and BackLink removed —
 * the shell handles header and sidebar navigation.
 *
 * All data-fetching and feature logic unchanged from 7.7.
 */

import { useCallback, useEffect, useState } from "react";

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
import { LectureFilesPanel } from "@/components/lecture-files-panel";
import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";
import {
  EmptyState,
  FormError,
  Loading,
  SmallButton,
} from "@/components/ui";
import { formatDate } from "@/lib/format";
import { triggerBlobDownload } from "@/lib/download";
import { SessionShell } from "@/components/session-shell";
import type { SessionListItem } from "@/components/session-shell";
import { loadAllSessions } from "@/app/student/student-dashboard";

// ── Data loaders (unchanged) ──────────────────────────────────────────────────

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

// ── Public export ─────────────────────────────────────────────────────────────

export function StudentSessionDetail({ sessionId }: { sessionId: number }) {
  return (
    <RequireAuth role="student">
      <SignedInShell fullWidth>
        <StudentSessionWithShell sessionId={sessionId} />
      </SignedInShell>
    </RequireAuth>
  );
}

// ── Shell wrapper ─────────────────────────────────────────────────────────────

function StudentSessionWithShell({ sessionId }: { sessionId: number }) {
  const [allSessions, setAllSessions] = useState<SessionListItem[]>([]);
  const [shellLoading, setShellLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void loadAllSessions().then((result) => {
      if (cancelled) return;
      if ("sessions" in result) {
        setAllSessions(
          result.sessions.map((s) => ({
            id: s.id,
            title: s.title,
            created_at: s.created_at,
            file_count: s.unsolved_files.length,
          })),
        );
      }
      setShellLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  return (
    <SessionShell
      sessions={allSessions}
      selectedId={sessionId}
      onSelect={() => {/* navigation handled by Link inside SessionShell */}}
      hrefBase="/student/sessions"
      listLabel="Sessions"
      loading={shellLoading}
    >
      <StudentSessionBody sessionId={sessionId} />
    </SessionShell>
  );
}

// ── Detail body (logic unchanged) ─────────────────────────────────────────────

function StudentSessionBody({ sessionId }: { sessionId: number }) {
  const [session, setSession] = useState<SessionRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [submission, setSubmission] = useState<SubmissionRead | null | undefined>(undefined);
  const [submissionError, setSubmissionError] = useState<string | null>(null);

  const [grades, setGrades] = useState<GradeSummary | undefined>(undefined);
  const [gradesError, setGradesError] = useState<string | null>(null);

  const refreshSubmission = useCallback(async () => {
    const result = await loadMySubmission(sessionId);
    if ("error" in result) { setSubmissionError(result.error); return; }
    setSubmission(result.submission);
    setSubmissionError(null);
  }, [sessionId]);

  const refreshGrades = useCallback(async () => {
    const result = await loadMyGrades(sessionId);
    if ("error" in result) { setGradesError(result.error); return; }
    setGrades(result.grades);
    setGradesError(null);
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadSession(sessionId).then((result) => {
      if (cancelled) return;
      if ("error" in result) { setLoadError(result.error); return; }
      setSession(result.session);
      setLoadError(null);
    });
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadMySubmission(sessionId).then((result) => {
      if (cancelled) return;
      if ("error" in result) { setSubmissionError(result.error); setSubmission(null); return; }
      setSubmission(result.submission);
      setSubmissionError(null);
    });
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadMyGrades(sessionId).then((result) => {
      if (cancelled) return;
      if ("error" in result) { setGradesError(result.error); return; }
      setGrades(result.grades);
      setGradesError(null);
    });
    return () => { cancelled = true; };
  }, [sessionId]);

  if (loadError) return <FormError>{loadError}</FormError>;
  if (!session) return <Loading>Loading session details...</Loading>;

  return (
    <div className="space-y-6">
      {/* Session header */}
      <div className="border-b border-neutral-200 pb-4">
        <h1 className="text-2xl font-semibold text-neutral-900">{session.title}</h1>
        <p className="mt-1 text-sm text-neutral-500">
          Created {formatDate(session.created_at)}
        </p>
      </div>

      <AssignmentFilesPanel
        sessionId={sessionId}
        uploads={session.assignment_uploads}
      />

      <SubmissionStatusPanel
        sessionId={sessionId}
        submission={submission}
        error={submissionError}
        onDeleted={async () => {
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

      <LectureFilesPanel sessionId={sessionId} canUpload={false} />
    </div>
  );
}

// ── Assignment files panel ────────────────────────────────────────────────────

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
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">Assignment files</h2>
      <p className="mt-1 mb-4 text-sm text-neutral-500">
        Download a file, solve any notebooks inside it, then upload your solution below.
      </p>

      {error ? <FormError>{error}</FormError> : null}

      {uploads.length === 0 ? (
        <EmptyState>
          Your instructor hasn&apos;t uploaded any assignment files for this session yet.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-neutral-100">
          {uploads.map((file) => (
            <li key={file.id} className="flex items-center justify-between gap-4 py-3">
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-neutral-900">
                  {file.original_filename}
                </span>
                <span className="block text-xs text-neutral-400">
                  Added {formatDate(file.uploaded_at)}
                </span>
              </span>
              <SmallButton
                onClick={() => void handleDownload(file)}
                disabled={busyId === file.id}
              >
                {busyId === file.id ? "Downloading..." : "Download"}
              </SmallButton>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Submission status panel ───────────────────────────────────────────────────

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
    setDownloadError((cur) => { const { [upload.id]: _, ...rest } = cur; return rest; });
    setDownloadBusyId(upload.id);
    try {
      const blob = await downloadMySubmissionUpload(sessionId, upload.id);
      triggerBlobDownload(blob, upload.original_filename);
    } catch (dlError) {
      setDownloadError((cur) => ({
        ...cur,
        [upload.id]: dlError instanceof ApiError ? dlError.detail : `Could not download ${upload.original_filename}.`,
      }));
    } finally {
      setDownloadBusyId(null);
    }
  }

  async function handleDelete(uploadId: number, confirm: boolean) {
    setItemError((cur) => { const { [uploadId]: _, ...rest } = cur; return rest; });
    setBusyId(uploadId);
    try {
      await deleteSubmissionUpload(sessionId, uploadId, { confirm });
      setConfirmText((cur) => { const { [uploadId]: _, ...rest } = cur; return rest; });
      await onDeleted();
    } catch (deleteError) {
      if (deleteError instanceof ApiError && deleteError.status === 409) {
        setConfirmText((cur) => ({ ...cur, [uploadId]: deleteError.detail }));
      } else {
        setItemError((cur) => ({
          ...cur,
          [uploadId]: deleteError instanceof ApiError ? deleteError.detail : "Could not remove this upload.",
        }));
      }
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">Your submission</h2>

      {error ? <FormError>{error}</FormError> : null}

      {submission === undefined && !error ? (
        <div className="mt-4"><Loading>Checking your submission...</Loading></div>
      ) : submission === null || submission.uploads.length === 0 ? (
        <div className="mt-4">
          <EmptyState>You haven&apos;t submitted anything for this session yet.</EmptyState>
        </div>
      ) : (
        <div className="mt-4">
          <p className="text-xs text-neutral-500">
            {submission.uploads.length}{" "}
            {submission.uploads.length === 1 ? "upload" : "uploads"},{" "}
            {submission.files.length}{" "}
            {submission.files.length === 1 ? "notebook" : "notebooks"},{" "}
            {submission.files.filter((f) => f.graded).length} graded
          </p>

          <ul className="mt-3 divide-y divide-neutral-100">
            {submission.uploads.map((upload) => {
              const producedFiles = submission.files.filter(
                (f) => f.source_upload_id === upload.id,
              );
              return (
                <li key={upload.id} className="py-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-neutral-900">
                        {upload.original_filename}
                      </span>
                      <span className="block text-xs text-neutral-400">
                        Uploaded {formatDate(upload.uploaded_at)}
                        {producedFiles.length > 0
                          ? `, ${producedFiles.length} ${producedFiles.length === 1 ? "notebook" : "notebooks"} (${producedFiles.filter((f) => f.graded).length} graded)`
                          : ""}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      <SmallButton
                        onClick={() => void handleDownload(upload)}
                        disabled={downloadBusyId === upload.id}
                      >
                        {downloadBusyId === upload.id ? "Downloading..." : "Download"}
                      </SmallButton>
                      <SmallButton
                        tone="danger"
                        onClick={() => void handleDelete(upload.id, false)}
                        disabled={busyId === upload.id}
                      >
                        {busyId === upload.id ? "Removing..." : "Remove"}
                      </SmallButton>
                    </span>
                  </div>

                  {downloadError[upload.id] ? (
                    <p className="mt-2 text-xs text-danger-600" role="alert">
                      {downloadError[upload.id]}
                    </p>
                  ) : null}

                  {itemError[upload.id] ? (
                    <p className="mt-2 text-xs text-danger-600" role="alert">
                      {itemError[upload.id]}
                    </p>
                  ) : null}

                  {confirmText[upload.id] ? (
                    <div className="mt-2 lms-alert lms-alert-warning">
                      <p className="text-sm">{confirmText[upload.id]}</p>
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
                              const { [upload.id]: _, ...rest } = cur;
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
      )}
    </div>
  );
}
