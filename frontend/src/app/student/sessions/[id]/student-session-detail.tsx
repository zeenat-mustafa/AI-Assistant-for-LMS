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
  downloadAssignment,
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

  /**
   * Re-read grades. Called on mount and after an upload -- a replacement
   * deletes the previous grades, so the old display must not linger.
   */
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
        submission={submission}
        error={submissionError}
      />

      <SubmissionUploadPanel
        sessionId={sessionId}
        submission={submission}
        grades={grades}
        onUploaded={(created) => {
          // The replacement already deleted any previous grades, so clear the
          // stale display immediately, then re-read the real state.
          setSubmission(created);
          setGrades(undefined);
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

function SubmissionStatusPanel({
  submission,
  error,
}: {
  submission: SubmissionRead | null | undefined;
  error: string | null;
}) {
  return (
    <Panel title="Your submission">
      {error ? <FormError>{error}</FormError> : null}

      {submission === undefined && !error ? (
        <Loading>Checking your submission…</Loading>
      ) : submission === null ? (
        // The normal, expected "nothing yet" case — not a failure.
        <EmptyState>
          You haven&apos;t submitted anything for this session yet.
        </EmptyState>
      ) : submission ? (
        <div>
          <p className="text-sm font-medium text-slate-900">
            Submitted <span className="text-slate-500">·</span>{" "}
            {submission.original_filename}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Uploaded {formatDate(submission.submitted_at)} · {submission.files.length}{" "}
            {submission.files.length === 1 ? "notebook" : "notebooks"} ·{" "}
            {submission.files.filter((f) => f.graded).length} graded
          </p>

          {submission.files.length > 0 ? (
            <ul className="mt-3 divide-y divide-slate-200 border-t border-slate-200">
              {submission.files.map((file) => (
                <li
                  key={file.id}
                  className="flex items-center justify-between gap-3 py-2"
                >
                  <span className="min-w-0 truncate text-xs text-slate-800">
                    {file.original_filename}
                  </span>
                  <span className="shrink-0 text-xs text-slate-500">
                    {file.matched_unsolved_file_id === null
                      ? "not matched to an assignment"
                      : file.graded
                        ? "graded"
                        : "awaiting grading"}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
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
