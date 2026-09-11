"use client";

/**
 * One session: its details, assignment-file upload, and the file list.
 *
 * One list: exactly what was uploaded (bugfix-original-upload-preservation)
 * ------------------------------------------------------------------------
 * Any file type can be uploaded, single or inside a `.zip`. Whatever was
 * uploaded is the ONLY thing shown or downloadable here -- a zip is one row
 * with its own filename, never a list of what's inside it. Notebooks
 * (standalone or bundled in a zip) are still extracted internally by the
 * backend for grading, exactly as before; that extraction never surfaces as
 * its own row here. Deleting a row removes the upload and everything it
 * produced internally.
 *
 * `session.unsolved_files`/`resource_files` still ride along on `SessionRead`
 * (used only to compute `totalAssignmentFiles` for the roster below) -- they
 * are never rendered as their own list anymore.
 *
 * Upload goes through 5.1's `uploadAssignment`, which builds a `FormData`
 * and posts it with the bearer token -- deliberately not a `<form action>`
 * Server Action, which runs on the Next server and could not read the JWT
 * from localStorage.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

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
import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";
import {
  EmptyState,
  FormError,
  FormNotice,
  Loading,
  Panel,
  SmallButton,
  SubmitButton,
} from "@/components/ui";
import { formatDate } from "@/lib/format";
import { triggerBlobDownload } from "@/lib/download";

/**
 * Fetch the session, returning the outcome rather than setting state, so the
 * caller can discard it if it is no longer wanted.
 */
async function loadSessionDetail(
  sessionId: number,
): Promise<{ session: SessionRead } | { error: string }> {
  try {
    // getSession already carries assignment_uploads, so one request covers
    // both the header and the initial file list.
    return { session: await getSession(sessionId) };
  } catch (error) {
    return {
      error: error instanceof ApiError ? error.detail : "Could not load this session.",
    };
  }
}

/** Same discard-if-stale shape as loadSessionDetail. */
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

/** Same discard-if-stale shape as the loaders above. Silently empty on failure --
 * this only adds download links to the roster, so a failure here must not
 * block the grade report itself from rendering. */
async function loadSubmissions(sessionId: number): Promise<Record<number, SubmissionRead>> {
  try {
    const subs = await listSubmissions(sessionId);
    return Object.fromEntries(subs.map((s) => [s.student_id, s]));
  } catch {
    return {};
  }
}

export function SessionDetail({ sessionId }: { sessionId: number }) {
  return (
    <RequireAuth role="instructor">
      <SignedInShell>
        <SessionDetailBody sessionId={sessionId} />
      </SignedInShell>
    </RequireAuth>
  );
}

function SessionDetailBody({ sessionId }: { sessionId: number }) {
  const [session, setSession] = useState<SessionRead | null>(null);
  const [uploads, setUploads] = useState<AssignmentUploadRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [report, setReport] = useState<SessionGradeReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);

  const [submissionsByStudent, setSubmissionsByStudent] =
    useState<Record<number, SubmissionRead> | undefined>(undefined);

  useEffect(() => {
    // Guards against a slow response for one session id landing after the
    // user has already navigated to another.
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
    return () => {
      cancelled = true;
    };
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
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    void loadSubmissions(sessionId).then((byStudent) => {
      if (cancelled) return;
      setSubmissionsByStudent(byStudent);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const refreshUploads = useCallback(async () => {
    setUploads(await listAssignments(sessionId));
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

      {/*
        Notebooks extracted internally, not the uploads list -- resources are
        never graded and must not inflate the denominator of the combined
        score. Read straight off the session, since notebooks are no longer
        separately listed/refreshed in this component.
      */}
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

function BackLink() {
  return (
    <Link href="/instructor" className="text-sm text-slate-500 underline">
      ← All sessions
    </Link>
  );
}

// ── Upload ───────────────────────────────────────────────────────────────────

/** Summarise an upload response -- one entry per file uploaded, whatever it was. */
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
      // The backend validates the whole batch before writing anything, so a
      // rejection means nothing was saved -- say so, rather than leaving the
      // instructor unsure which files landed.
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
    <Panel
      title="Upload assignment files"
      description="Any file type, single or inside a .zip. What you upload is exactly what students and the grade report see -- a zip stays one file; notebooks inside it (standalone or nested) are still graded normally."
    >
      {error ? <FormError>{error}</FormError> : null}
      {notice ? <FormNotice>{notice}</FormNotice> : null}

      <form onSubmit={handleUpload} noValidate>
        <label
          htmlFor="assignment-files"
          className="mb-1 block text-sm font-medium text-slate-700"
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
          className="mb-4 block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700"
        />

        {selected.length > 0 ? (
          <ul className="mb-4 list-inside list-disc text-xs text-slate-600">
            {selected.map((file) => (
              <li key={file.name}>{file.name}</li>
            ))}
          </ul>
        ) : null}

        <SubmitButton pending={pending}>
          {selected.length > 1 ? `Upload ${selected.length} files` : "Upload"}
        </SubmitButton>
      </form>
    </Panel>
  );
}

// ── File list ────────────────────────────────────────────────────────────────

function FileListPanel({
  sessionId,
  uploads,
  onDeleted,
}: {
  sessionId: number;
  /** null while loading. */
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
      // The endpoint requires the bearer token, so a plain <a href> would
      // 401. Fetch the bytes with auth, then hand the browser a blob URL.
      // Returns the ORIGINAL bytes exactly -- a zip downloads as that zip.
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
    <Panel title="Assignment files">
      {error ? <FormError>{error}</FormError> : null}

      {loading ? (
        <Loading>Loading files…</Loading>
      ) : uploads.length === 0 ? (
        <EmptyState>No assignment files yet. Upload one above.</EmptyState>
      ) : (
        <ul className="divide-y divide-slate-200">
          {uploads.map((file) => (
            <li key={file.id} className="flex items-center justify-between gap-4 py-3">
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-slate-900">
                  {file.original_filename}
                </span>
                <span className="block text-xs text-slate-500">
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
    </Panel>
  );
}
