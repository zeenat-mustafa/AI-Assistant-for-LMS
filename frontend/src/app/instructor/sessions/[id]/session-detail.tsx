"use client";

/**
 * Instructor session detail — Phase 7.8
 *
 * Rendered inside the right pane of the SessionShell (mounted at
 * /instructor/sessions/[id]). SignedInShell and BackLink have been removed
 * from this file — the shell handles the header and sidebar navigation.
 *
 * All data-fetching and feature logic is unchanged from 7.7.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  deleteAssignment,
  downloadAssignment,
  getGradeReport,
  getSession,
  listAssignments,
  listSubmissions,
  uploadAssignment,
} from "@/lib/api";
import type {
  AssignmentUploadRead,
  SessionGradeReport,
  SessionRead,
  SubmissionRead,
} from "@/lib/api";
import { GradesRoster } from "./grades-roster";
import { LectureFilesPanel } from "@/components/lecture-files-panel";
import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";
import { summaryNamesSession, useGradingAnnouncements } from "@/lib/grading-announcements";
import {
  EmptyState,
  FormError,
  FormNotice,
  Loading,
  SmallButton,
  SubmitButton,
} from "@/components/ui";
import { formatDate } from "@/lib/format";
import { triggerBlobDownload } from "@/lib/download";
import { SessionShell } from "@/components/session-shell";
import type { SessionListItem } from "@/components/session-shell";
import { loadAllSessions } from "@/app/instructor/instructor-dashboard";

// ── Data loaders (unchanged) ──────────────────────────────────────────────────

async function loadSessionDetail(
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

async function loadGradeReport(
  sessionId: number,
): Promise<{ report: SessionGradeReport } | { error: string }> {
  try {
    return { report: await getGradeReport(sessionId) };
  } catch (error) {
    return {
      error:
        error instanceof ApiError ? error.detail : "Could not load the grade report.",
    };
  }
}

async function loadSubmissions(sessionId: number): Promise<Record<number, SubmissionRead>> {
  try {
    const subs = await listSubmissions(sessionId);
    return Object.fromEntries(subs.map((s) => [s.student_id, s]));
  } catch {
    return {};
  }
}

// ── Public export ─────────────────────────────────────────────────────────────

export function SessionDetail({ sessionId }: { sessionId: number }) {
  return (
    <RequireAuth role="instructor">
      <SignedInShell fullWidth>
        <SessionDetailWithShell sessionId={sessionId} />
      </SignedInShell>
    </RequireAuth>
  );
}

// ── Shell wrapper — loads the sidebar session list ────────────────────────────

function SessionDetailWithShell({ sessionId }: { sessionId: number }) {
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
            meta: s.instructor_name ?? undefined,
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
      hrefBase="/instructor/sessions"
      listLabel="Sessions"
      loading={shellLoading}
    >
      <SessionDetailBody sessionId={sessionId} />
    </SessionShell>
  );
}

// ── Detail body (logic unchanged from 7.7) ────────────────────────────────────

function SessionDetailBody({ sessionId }: { sessionId: number }) {
  const [session, setSession] = useState<SessionRead | null>(null);
  const [uploads, setUploads] = useState<AssignmentUploadRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [report, setReport] = useState<SessionGradeReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);

  const [submissionsByStudent, setSubmissionsByStudent] =
    useState<Record<number, SubmissionRead> | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    void loadSessionDetail(sessionId).then((result) => {
      if (cancelled) return;
      if ("error" in result) {
        setLoadError(result.error);
        return;
      }
      setSession(result.session);
      setUploads(result.session.assignment_uploads);
      setLoadError(null);
    });
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadGradeReport(sessionId).then((result) => {
      if (cancelled) return;
      if ("error" in result) {
        setReportError(result.error);
        return;
      }
      setReport(result.report);
      setReportError(null);
    });
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadSubmissions(sessionId).then((byStudent) => {
      if (cancelled) return;
      setSubmissionsByStudent(byStudent);
    });
    return () => { cancelled = true; };
  }, [sessionId]);

  const refreshUploads = useCallback(async () => {
    setUploads(await listAssignments(sessionId));
  }, [sessionId]);

  const { lastCompletion } = useGradingAnnouncements();
  const sessionTitle = session?.title;

  useEffect(() => {
    if (!lastCompletion || !sessionTitle) return;
    if (!summaryNamesSession(lastCompletion.message, sessionTitle)) return;

    let cancelled = false;
    void Promise.all([loadGradeReport(sessionId), loadSubmissions(sessionId)]).then(
      ([reportResult, byStudent]) => {
        if (cancelled) return;
        if ("error" in reportResult) {
          setReportError(reportResult.error);
        } else {
          setReport(reportResult.report);
          setReportError(null);
        }
        setSubmissionsByStudent(byStudent);
      },
    );
    return () => { cancelled = true; };
  }, [lastCompletion, sessionTitle, sessionId]);

  if (loadError) {
    return <FormError>{loadError}</FormError>;
  }

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

      <UploadPanel
        sessionId={sessionId}
        onUploaded={(created) => {
          setUploads((current) => [...(current ?? []), ...created]);
        }}
      />

      <FileListPanel
        sessionId={sessionId}
        uploads={uploads}
        onDeleted={refreshUploads}
      />

      <LectureFilesPanel sessionId={sessionId} canUpload />

      <GradesRoster
        sessionId={sessionId}
        report={report}
        error={reportError}
        totalAssignmentFiles={session.unsolved_files.length}
        submissionsByStudent={submissionsByStudent}
      />
    </div>
  );
}

// ── Upload panel ──────────────────────────────────────────────────────────────

export function describeUpload(created: AssignmentUploadRead[]): string {
  if (created.length === 1) {
    return `Uploaded ${created[0].original_filename}.`;
  }
  return `Uploaded ${created.length} files: ${created
    .map((item) => item.original_filename)
    .join(", ")}.`;
}

function UploadPanel({
  sessionId,
  onUploaded,
}: {
  sessionId: number;
  onUploaded: (created: AssignmentUploadRead[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function handleSelect(event: React.ChangeEvent<HTMLInputElement>) {
    setError(null);
    setNotice(null);
    setSelected(Array.from(event.target.files ?? []));
  }

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (selected.length === 0) {
      setError("Choose at least one file first.");
      return;
    }

    setPending(true);
    try {
      const created = await uploadAssignment(sessionId, selected);
      onUploaded(created);
      setNotice(describeUpload(created));
      setSelected([]);
      if (inputRef.current) inputRef.current.value = "";
    } catch (uploadError) {
      setError(
        uploadError instanceof ApiError
          ? `${uploadError.detail} (nothing was uploaded)`
          : "Upload failed. Nothing was uploaded.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">Upload assignment files</h2>
      <p className="mt-1 mb-4 text-sm text-neutral-500">
        Any file type, single or inside a .zip. What you upload is exactly what students see.
      </p>

      {error ? <FormError>{error}</FormError> : null}
      {notice ? <FormNotice>{notice}</FormNotice> : null}

      <form onSubmit={handleUpload} noValidate>
        <label
          htmlFor="assignment-files"
          className="mb-1 block text-sm font-medium text-neutral-700"
        >
          Files
        </label>
        <input
          id="assignment-files"
          ref={inputRef}
          type="file"
          name="files"
          multiple
          onChange={handleSelect}
          className="mb-4 block w-full text-sm text-neutral-700 file:mr-3 file:rounded file:border file:border-neutral-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-neutral-700"
        />

        {selected.length > 0 ? (
          <ul className="mb-4 list-inside list-disc text-xs text-neutral-600">
            {selected.map((file) => (
              <li key={file.name}>{file.name}</li>
            ))}
          </ul>
        ) : null}

        <SubmitButton pending={pending}>
          {selected.length > 1 ? `Upload ${selected.length} files` : "Upload"}
        </SubmitButton>
      </form>
    </div>
  );
}

// ── File list panel ───────────────────────────────────────────────────────────

function FileListPanel({
  sessionId,
  uploads,
  onDeleted,
}: {
  sessionId: number;
  uploads: AssignmentUploadRead[] | null;
  onDeleted: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
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

  async function handleDelete(file: AssignmentUploadRead) {
    setError(null);
    setBusyId(file.id);
    try {
      await deleteAssignment(sessionId, file.id);
      await onDeleted();
      setConfirmingId(null);
    } catch (deleteError) {
      setError(
        deleteError instanceof ApiError
          ? deleteError.detail
          : `Could not remove ${file.original_filename}.`,
      );
    } finally {
      setBusyId(null);
    }
  }

  const loading = uploads === null;

  return (
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">Assignment files</h2>

      {error ? <FormError>{error}</FormError> : null}

      {loading ? (
        <Loading>Loading files...</Loading>
      ) : uploads.length === 0 ? (
        <div className="mt-4">
          <EmptyState>No assignment files yet. Upload one above.</EmptyState>
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-neutral-100">
          {uploads.map((file) => (
            <li key={file.id} className="flex items-center justify-between gap-4 py-3">
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-neutral-900">
                  {file.original_filename}
                </span>
                <span className="block text-xs text-neutral-400">
                  Uploaded {formatDate(file.uploaded_at)}
                </span>
              </span>

              <span className="flex shrink-0 items-center gap-2">
                <SmallButton
                  onClick={() => handleDownload(file)}
                  disabled={busyId === file.id}
                >
                  Download
                </SmallButton>

                {confirmingId === file.id ? (
                  <>
                    <SmallButton
                      tone="danger"
                      onClick={() => handleDelete(file)}
                      disabled={busyId === file.id}
                    >
                      Confirm remove
                    </SmallButton>
                    <SmallButton onClick={() => setConfirmingId(null)}>Cancel</SmallButton>
                  </>
                ) : (
                  <SmallButton
                    tone="danger"
                    onClick={() => setConfirmingId(file.id)}
                    disabled={busyId === file.id}
                  >
                    Remove
                  </SmallButton>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
