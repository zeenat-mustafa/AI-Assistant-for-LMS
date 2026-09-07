"use client";

/**
 * Student's view of one session: its assignment files (downloadable) and
 * their own submission status.
 *
 * No upload UI here — that is 5.6. No scores either: this shows whether a
 * file has been graded, not what it scored, which is 5.6's job.
 *
 * Every endpoint used is gated on `get_current_user`, not `require_instructor`,
 * so 5.3's authenticated blob-download helper works unchanged with a student
 * token — verified, not assumed.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import { ApiError, downloadAssignment, getMySubmission, getSession } from "@/lib/api";
import type { SessionRead, SubmissionRead, UnsolvedFileRead } from "@/lib/api";
import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";
import { EmptyState, FormError, Loading, Panel, SmallButton } from "@/components/ui";
import { formatDate } from "@/lib/format";

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

function StudentSessionBody({ sessionId }: { sessionId: number }) {
  const [session, setSession] = useState<SessionRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // `undefined` = still loading; `null` = loaded, nothing submitted.
  const [submission, setSubmission] = useState<SubmissionRead | null | undefined>(undefined);
  const [submissionError, setSubmissionError] = useState<string | null>(null);

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

      <AssignmentFilesPanel sessionId={sessionId} files={session.unsolved_files} />
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
  files,
}: {
  sessionId: number;
  files: UnsolvedFileRead[];
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload(file: UnsolvedFileRead) {
    setError(null);
    setBusyId(file.id);
    try {
      // The endpoint requires the bearer token, so a plain <a href> would 401.
      const blob = await downloadAssignment(sessionId, file.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.original_filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
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

  return (
    <Panel
      title="Assignment files"
      description="Download a notebook, solve it, then upload your solved copy."
    >
      {error ? <FormError>{error}</FormError> : null}

      {files.length === 0 ? (
        <EmptyState>
          Your instructor hasn&apos;t uploaded any assignment files for this session yet.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-slate-200">
          {files.map((file) => (
            <li key={file.id} className="flex items-center justify-between gap-4 py-3">
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-slate-900">
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
