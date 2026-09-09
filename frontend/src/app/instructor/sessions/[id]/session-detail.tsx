"use client";

/**
 * One session: its details, assignment-file upload, and the file list.
 *
 * Two kinds of file, two separate tables
 * --------------------------------------
 * A session holds gradeable NOTEBOOKS (`unsolved_files`) and downloadable
 * RESOURCES (`resource_files`) -- datasets, slides, PDFs bundled in a zip.
 * The backend keeps them in structurally separate tables and returns them as
 * two separate fields, so this page keeps two separate lists rather than one
 * list with a role flag. Only notebooks are gradeable and only notebooks
 * count toward the session's assignment total, but both kinds can be
 * removed -- notebook and resource ids collide (both tables start at 1), so
 * every id-keyed piece of state below (busy state, delete confirmation) is
 * keyed by role+id, never by the bare id.
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
  deleteResource,
  downloadAssignment,
  downloadResource,
  getGradeReport,
  getSession,
  listAssignments,
  listResources,
  uploadAssignment,
} from "@/lib/api";
import type {
  AssignmentUploadItem,
  ResourceFileRead,
  SessionGradeReport,
  SessionRead,
  UnsolvedFileRead,
} from "@/lib/api";
import { GradesRoster } from "./grades-roster";
import { GradingChat } from "./grading-chat";
import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";
import {
  EmptyState,
  FileRoleBadge,
  FormError,
  FormNotice,
  Loading,
  Panel,
  SmallButton,
  SubmitButton,
} from "@/components/ui";
import { formatDate } from "@/lib/format";
import { triggerBlobDownload } from "@/lib/download";

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
  const [resources, setResources] = useState<ResourceFileRead[] | null>(null);
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
      // getSession carries BOTH lists, so one request covers the header and
      // both file sections -- no extra round trip for resources on mount.
      setFiles(result.session.unsolved_files);
      setResources(result.session.resource_files);
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

  const refreshResources = useCallback(async () => {
    setResources(await listResources(sessionId));
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
          // One upload can create rows in BOTH tables, so split the response
          // on file_role and append each to its own list.
          const notebooks = created.filter((item) => item.file_role === "notebook");
          const uploadedResources = created.filter((item) => item.file_role === "resource");
          if (notebooks.length > 0) {
            setFiles((current) => [...(current ?? []), ...notebooks]);
          }
          if (uploadedResources.length > 0) {
            setResources((current) => [...(current ?? []), ...uploadedResources]);
          }
        }}
      />

      <FileListPanel
        sessionId={sessionId}
        files={files}
        resources={resources}
        onDeleted={refreshFiles}
        onResourceDeleted={refreshResources}
      />

      <GradingChat
        sessionTitle={session.title}
        onGraded={() => void refreshReport()}
      />

      {/*
        Notebooks only. Resources are never graded and must not inflate the
        denominator of the combined score.
      */}
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

/**
 * Summarise an upload response, which may contain notebooks, resources, or
 * both. Exported for direct testing -- the wording is the only place the
 * instructor learns that a zip's non-notebook contents were kept as
 * resources rather than silently dropped.
 */
export function describeUpload(created: AssignmentUploadItem[]): string {
  const notebooks = created.filter((item) => item.file_role === "notebook").length;
  const resources = created.length - notebooks;

  if (created.length === 1) {
    const only = created[0];
    const kind = only.file_role === "notebook" ? "notebook" : "resource file";
    return `Uploaded ${only.original_filename} as a ${kind}.`;
  }

  const parts: string[] = [];
  if (notebooks > 0) parts.push(`${notebooks} ${notebooks === 1 ? "notebook" : "notebooks"}`);
  if (resources > 0) {
    parts.push(`${resources} ${resources === 1 ? "resource file" : "resource files"}`);
  }
  return `Uploaded ${parts.join(" and ")}.`;
}

function UploadPanel({
  sessionId,
  onUploaded,
}: {
  sessionId: number;
  onUploaded: (created: AssignmentUploadItem[]) => void;
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
      description="One or more .ipynb notebooks, or a .zip. A zip is extracted at any folder depth: .ipynb files become gradeable notebooks, and anything else (datasets, slides, PDFs) becomes a downloadable resource. A resource-only zip is fine."
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
  resources,
  onDeleted,
  onResourceDeleted,
}: {
  sessionId: number;
  /** null while loading. */
  files: UnsolvedFileRead[] | null;
  /** null while loading. */
  resources: ResourceFileRead[] | null;
  onDeleted: () => Promise<void>;
  onResourceDeleted: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Ids collide across the two tables (both start at 1), so busy state and
  // delete confirmation are both keyed by role+id, never by the bare id.
  const busyKey = (role: "notebook" | "resource", id: number) => role + ":" + id;

  async function handleDownload(
    role: "notebook" | "resource",
    file: { id: number; original_filename: string },
  ) {
    setError(null);
    setBusyId(busyKey(role, file.id));
    try {
      // The endpoints require the bearer token, so a plain <a href> would
      // 401. Fetch the bytes with auth, then hand the browser a blob URL.
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

  async function handleDelete(file: UnsolvedFileRead) {
    setError(null);
    setBusyId(busyKey("notebook", file.id));
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

  async function handleDeleteResource(file: ResourceFileRead) {
    setError(null);
    setBusyId(busyKey("resource", file.id));
    try {
      await deleteResource(sessionId, file.id);
      await onResourceDeleted();
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

  const loading = files === null || resources === null;
  const nothingAtAll = !loading && files.length === 0 && resources.length === 0;

  return (
    <Panel title="Assignment files">
      {error ? <FormError>{error}</FormError> : null}

      {loading ? (
        <Loading>Loading files…</Loading>
      ) : nothingAtAll ? (
        <EmptyState>No assignment files yet. Upload one above.</EmptyState>
      ) : (
        <div className="space-y-6">
          {/* Gradeable notebooks. */}
          <section aria-labelledby="notebooks-heading">
            <h3
              id="notebooks-heading"
              className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase"
            >
              Notebooks ({files.length})
            </h3>
            <p className="mb-2 text-xs text-slate-500">
              Graded against a generated rubric. These are the files a student&apos;s
              submission is matched against.
            </p>

            {files.length === 0 ? (
              <EmptyState>
                No gradeable notebooks yet — this session has only resource files, so
                there is nothing to grade.
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
                        Uploaded {formatDate(file.uploaded_at)}
                        {file.rubric_generated ? " · rubric ready" : " · no rubric yet"}
                      </span>
                    </span>

                    <span className="flex shrink-0 items-center gap-2">
                      <SmallButton
                        onClick={() => handleDownload("notebook", file)}
                        disabled={busyId === busyKey("notebook", file.id)}
                      >
                        Download
                      </SmallButton>

                      {confirmingId === busyKey("notebook", file.id) ? (
                        <>
                          <SmallButton
                            tone="danger"
                            onClick={() => handleDelete(file)}
                            disabled={busyId === busyKey("notebook", file.id)}
                          >
                            Confirm remove
                          </SmallButton>
                          <SmallButton onClick={() => setConfirmingId(null)}>
                            Cancel
                          </SmallButton>
                        </>
                      ) : (
                        <SmallButton
                          tone="danger"
                          onClick={() => setConfirmingId(busyKey("notebook", file.id))}
                          disabled={busyId === busyKey("notebook", file.id)}
                        >
                          Remove
                        </SmallButton>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Resource files -- same download + remove pattern as notebooks. */}
          {resources.length > 0 ? (
            <section aria-labelledby="resources-heading">
              <h3
                id="resources-heading"
                className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase"
              >
                Resource files ({resources.length})
              </h3>
              <p className="mb-2 text-xs text-slate-500">
                Supporting material — datasets, slides, PDFs. Downloadable by students,
                never graded, and not counted in the combined score.
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
                        Uploaded {formatDate(file.uploaded_at)}
                      </span>
                    </span>

                    <span className="flex shrink-0 items-center gap-2">
                      <SmallButton
                        onClick={() => handleDownload("resource", file)}
                        disabled={busyId === busyKey("resource", file.id)}
                      >
                        Download
                      </SmallButton>

                      {confirmingId === busyKey("resource", file.id) ? (
                        <>
                          <SmallButton
                            tone="danger"
                            onClick={() => handleDeleteResource(file)}
                            disabled={busyId === busyKey("resource", file.id)}
                          >
                            Confirm remove
                          </SmallButton>
                          <SmallButton onClick={() => setConfirmingId(null)}>
                            Cancel
                          </SmallButton>
                        </>
                      ) : (
                        <SmallButton
                          tone="danger"
                          onClick={() => setConfirmingId(busyKey("resource", file.id))}
                          disabled={busyId === busyKey("resource", file.id)}
                        >
                          Remove
                        </SmallButton>
                      )}
                    </span>
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
