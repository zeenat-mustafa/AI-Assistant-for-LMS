"use client";

/**
 * One session: its details, assignment-file upload, and the file list.
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
  uploadAssignment,
} from "@/lib/api";
import type { SessionGradeReport, SessionRead, UnsolvedFileRead } from "@/lib/api";
import { GradesRoster } from "./grades-roster";
import { GradingChat } from "./grading-chat";
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
import { formatDate } from "../../instructor-dashboard";

/** Mirrors the backend's own `_ALLOWED_EXTENSIONS` in routers/assignments.py. */
const ACCEPTED_EXTENSIONS = [".ipynb", ".zip"];

/**
 * Fetch the session, returning the outcome rather than setting state, so the
 * caller can discard it if it is no longer wanted.
 */
async function loadSessionDetail(
  sessionId: number,
): Promise<{ session: SessionRead } | { error: string }> {
  try {
    // getSession already carries `unsolved_files`, so one request covers both
    // the header and the initial file list.
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
  const [files, setFiles] = useState<UnsolvedFileRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [report, setReport] = useState<SessionGradeReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);

  /** Re-read the roster after a grading run that targeted this session. */
  const refreshReport = useCallback(async () => {
    const result = await loadGradeReport(sessionId);
    if ("error" in result) {
      setReportError(result.error);
      return;
    }
    setReport(result.report);
    setReportError(null);
  }, [sessionId]);

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
      setFiles(result.session.unsolved_files);
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

  const refreshFiles = useCallback(async () => {
    setFiles(await listAssignments(sessionId));
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
        onUploaded={(created) =>
          setFiles((current) => [...(current ?? []), ...created])
        }
      />

      <FileListPanel
        sessionId={sessionId}
        files={files}
        onDeleted={refreshFiles}
      />

      <GradingChat
        sessionTitle={session.title}
        onGraded={() => void refreshReport()}
      />

      <GradesRoster
        report={report}
        error={reportError}
        totalAssignmentFiles={files?.length ?? session.unsolved_files.length}
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

function UploadPanel({
  sessionId,
  onUploaded,
}: {
  sessionId: number;
  onUploaded: (created: UnsolvedFileRead[]) => void;
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
      setError("Choose at least one .ipynb or .zip file first.");
      return;
    }

    // Same rule the backend enforces, checked here so an obvious mistake
    // doesn't cost a round trip. The backend still rejects anything that
    // slips past this.
    const rejected = selected.filter(
      (file) => !ACCEPTED_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext)),
    );
    if (rejected.length > 0) {
      setError(
        `Only .ipynb or .zip files are accepted. Remove: ${rejected
          .map((f) => f.name)
          .join(", ")}`,
      );
      return;
    }

    setPending(true);
    try {
      const created = await uploadAssignment(sessionId, selected);
      onUploaded(created);
      setNotice(
        created.length === 1
          ? `Uploaded ${created[0].original_filename}.`
          : `Uploaded ${created.length} notebooks.`,
      );
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
      description="One or more .ipynb notebooks, or a .zip — nested notebooks inside a zip are extracted automatically."
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
          accept=".ipynb,.zip"
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
  files,
  onDeleted,
}: {
  sessionId: number;
  files: UnsolvedFileRead[] | null;
  onDeleted: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload(file: UnsolvedFileRead) {
    setError(null);
    setBusyId(file.id);
    try {
      // The endpoint requires the bearer token, so a plain <a href> would
      // 401. Fetch the bytes with auth, then hand the browser a blob URL.
      const blob = await downloadAssignment(sessionId, file.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.original_filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Revoking immediately can cancel an in-flight save in some browsers.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
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

  async function handleDelete(file: UnsolvedFileRead) {
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

  return (
    <Panel title="Assignment files">
      {error ? <FormError>{error}</FormError> : null}

      {files === null ? (
        <Loading>Loading files…</Loading>
      ) : files.length === 0 ? (
        <EmptyState>No assignment files yet. Upload one above.</EmptyState>
      ) : (
        <ul className="divide-y divide-slate-200">
          {files.map((file) => (
            <li key={file.id} className="flex items-center justify-between gap-4 py-3">
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-slate-900">
                  {file.original_filename}
                </span>
                <span className="block text-xs text-slate-500">
                  Uploaded {formatDate(file.uploaded_at)}
                  {file.rubric_generated ? " · rubric ready" : " · no rubric yet"}
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
