"use client";

/**
 * Student's view of one session: submission status, upload, own grades, and
 * the session's downloadable files.
 *
 * Files come in two kinds, from two structurally separate backend tables:
 * gradeable NOTEBOOKS (`unsolved_files`) and downloadable RESOURCES
 * (`resource_files`) -- datasets, slides, notes. Students need both: a
 * notebook that reads `titanic.csv` cannot be solved without the dataset.
 * They are rendered as two labelled lists, matching the instructor view.
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
  downloadResource,
  getMyGrades,
  getMySubmission,
  getSession,
} from "@/lib/api";
import type {
  GradeSummary,
  ResourceFileRead,
  SessionRead,
  SubmissionRead,
  UnsolvedFileRead,
} from "@/lib/api";
import { MyGradesPanel } from "./my-grades-panel";
import { SubmissionUploadPanel } from "./submission-upload-panel";
import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";
import {
  EmptyState,
  FileRoleBadge,
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
        files={session.unsolved_files}
        resources={session.resource_files}
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
  files,
  resources,
}: {
  sessionId: number;
  files: UnsolvedFileRead[];
  resources: ResourceFileRead[];
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Ids collide across the two tables (both start at 1), so busy state is
  // keyed by role+id, never by the bare id.
  const busyKey = (role: "notebook" | "resource", id: number) => role + ":" + id;

  async function handleDownload(
    role: "notebook" | "resource",
    file: { id: number; original_filename: string },
  ) {
    setError(null);
    setBusyId(busyKey(role, file.id));
    try {
      // Both endpoints require the bearer token, so a plain <a href> would
      // 401. Same authenticated blob fetch the instructor page uses; the
      // resources route is gated on get_current_user, not require_instructor,
      // so a student token works here -- verified live, not assumed.
      const blob =
        role === "notebook"
          ? await downloadAssignment(sessionId, file.id)
          : await downloadResource(sessionId, file.id);
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

  const nothingAtAll = files.length === 0 && resources.length === 0;

  return (
    <Panel
      title="Assignment files"
      description="Download a notebook, solve it, then upload your solved copy. Resource files are supporting material — datasets, slides, notes — and are not graded."
    >
      {error ? <FormError>{error}</FormError> : null}

      {nothingAtAll ? (
        <EmptyState>
          Your instructor hasn&apos;t uploaded any assignment files for this session yet.
        </EmptyState>
      ) : (
        <div className="space-y-6">
          <section aria-labelledby="student-notebooks-heading">
            <h3
              id="student-notebooks-heading"
              className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase"
            >
              Notebooks ({files.length})
            </h3>
            <p className="mb-2 text-xs text-slate-500">
              These are what you solve and upload. Your grade comes from these.
            </p>

            {files.length === 0 ? (
              <EmptyState>
                No notebooks to solve in this session yet — only resource files so far.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-slate-200">
                {files.map((file) => (
                  <li
                    key={file.id}
                    className="flex items-center justify-between gap-4 py-3"
                  >
                    <span className="min-w-0">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-slate-900">
                          {file.original_filename}
                        </span>
                        <FileRoleBadge role="notebook" />
                      </span>
                      <span className="block text-xs text-slate-500">
                        Added {formatDate(file.uploaded_at)}
                      </span>
                    </span>
                    <SmallButton
                      onClick={() => handleDownload("notebook", file)}
                      disabled={busyId === busyKey("notebook", file.id)}
                    >
                      {busyId === busyKey("notebook", file.id)
                        ? "Downloading…"
                        : "Download"}
                    </SmallButton>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/*
            A student needs the dataset and slides as much as the notebook --
            a notebook that reads `titanic.csv` is unsolvable without it.
            Download only: students never upload or remove these.
          */}
          {resources.length > 0 ? (
            <section aria-labelledby="student-resources-heading">
              <h3
                id="student-resources-heading"
                className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase"
              >
                Resource files ({resources.length})
              </h3>
              <p className="mb-2 text-xs text-slate-500">
                Supporting material your notebooks may need. Not graded, and not part
                of your score.
              </p>

              <ul className="divide-y divide-slate-200">
                {resources.map((file) => (
                  <li
                    key={file.id}
                    className="flex items-center justify-between gap-4 py-3"
                  >
                    <span className="min-w-0">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-slate-900">
                          {file.original_filename}
                        </span>
                        <FileRoleBadge role="resource" />
                      </span>
                      <span className="block text-xs text-slate-500">
                        Added {formatDate(file.uploaded_at)}
                      </span>
                    </span>
                    <SmallButton
                      onClick={() => handleDownload("resource", file)}
                      disabled={busyId === busyKey("resource", file.id)}
                    >
                      {busyId === busyKey("resource", file.id)
                        ? "Downloading…"
                        : "Download"}
                    </SmallButton>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      )}
    </Panel>
  );
}
