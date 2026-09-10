"use client";

/**
 * Instructor dashboard: create a session, and list every session in the
 * workspace.
 *
 * Shared faculty workspace, deliberately (see README): any instructor can
 * see/edit/grade any session regardless of who created it, so this loads
 * every session, not just the current instructor's own. `instructor_name`
 * is shown per session so it's still clear who created what -- informative,
 * not restrictive.
 *
 * Rename and delete controls (bugfix-session-naming-attribution) live here
 * rather than on the session detail page: this dashboard is already the
 * canonical list of every session, so managing one doesn't require
 * navigating into it first. Rename follows an inline edit-in-place pattern
 * (no confirm step -- renaming isn't destructive); delete reuses the same
 * confirm/cancel pattern as the assignment-file Remove button on the
 * session detail page (frontend/src/app/instructor/sessions/[id]/
 * session-detail.tsx's FileListPanel) so the two destructive actions in
 * this app behave identically.
 *
 * Plain `useEffect` + state rather than SWR/TanStack Query. The Next docs
 * recommend those once you need revalidation, polling or request dedup;
 * this page loads one list once, so a data library would be weight without
 * a payoff at this scope.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import {
  ApiError,
  createSession,
  deleteSession,
  listSessions,
  renameSession,
} from "@/lib/api";
import type { SessionRead } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  EmptyState,
  Field,
  FormError,
  Loading,
  Panel,
  SmallButton,
  SubmitButton,
} from "@/components/ui";

/**
 * The backend caps `limit` at 200. One page is a real (if generous) limit
 * on total workspace-wide session count, not a narrowing of what any one
 * instructor can see -- see the README note.
 */
const PAGE_LIMIT = 200;

/**
 * Load every session in the workspace.
 *
 * Returns the outcome instead of setting state, so a stale response can be
 * discarded by the caller.
 */
export async function loadAllSessions(): Promise<
  { sessions: SessionRead[] } | { error: string }
> {
  try {
    const page = await listSessions({ limit: PAGE_LIMIT });
    return { sessions: page.items };
  } catch (error) {
    return {
      error: error instanceof ApiError ? error.detail : "Could not load sessions.",
    };
  }
}

export function InstructorDashboard() {
  // null = still loading; [] = loaded and genuinely empty.
  const [sessions, setSessions] = useState<SessionRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [titleError, setTitleError] = useState<string | undefined>();
  const [createError, setCreateError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    // Guards against a slow response landing after the component has
    // already unmounted (e.g. navigating away mid-request).
    let cancelled = false;
    void loadAllSessions().then((result) => {
      if (cancelled) return;
      if ("error" in result) {
        setLoadError(result.error);
        setSessions([]);
        return;
      }
      setSessions(result.sessions);
      setLoadError(null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError(null);

    const trimmed = title.trim();
    if (!trimmed) {
      setTitleError("Session title is required.");
      return;
    }
    setTitleError(undefined);

    setPending(true);
    try {
      const created = await createSession(trimmed);
      // Prepend rather than refetch: the backend orders newest-first, so this
      // matches what a reload would show, without one.
      setSessions((current) => [created, ...(current ?? [])]);
      setTitle("");
    } catch (error) {
      // e.g. the backend's 409: "A session titled 'X' already exists
      // (id=N by Instructor Name)." Title uniqueness is global, not
      // per-instructor -- shared workspace, see README.
      setCreateError(
        error instanceof ApiError ? error.detail : "Could not create the session.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Sessions</h1>
        <p className="mt-1 text-sm text-slate-600">
          Shared across every instructor — create a session for each class, then
          upload its assignment notebooks. Anyone can view, edit, and grade any
          session here, regardless of who created it.
        </p>
      </div>

      <Panel
        title="Create a session"
        description="Use the naming your class already uses, e.g. “Week 3 Day 1”."
      >
        {createError ? <FormError>{createError}</FormError> : null}
        <form onSubmit={handleCreate} noValidate>
          <Field
            label="Session title"
            name="title"
            placeholder="Week 3 Day 1"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            error={titleError}
          />
          <SubmitButton pending={pending}>Create session</SubmitButton>
        </form>
      </Panel>

      <Panel title="All sessions">
        {loadError ? <FormError>{loadError}</FormError> : null}

        {sessions === null ? (
          <Loading>Loading sessions…</Loading>
        ) : sessions.length === 0 ? (
          <EmptyState>
            No sessions yet. Create one above to start uploading assignment files.
          </EmptyState>
        ) : (
          <SessionList
            sessions={sessions}
            onRenamed={(updated) =>
              setSessions((current) =>
                (current ?? []).map((s) => (s.id === updated.id ? updated : s)),
              )
            }
            onDeleted={(id) =>
              setSessions((current) => (current ?? []).filter((s) => s.id !== id))
            }
          />
        )}
      </Panel>
    </div>
  );
}

function SessionList({
  sessions,
  onRenamed,
  onDeleted,
}: {
  sessions: SessionRead[];
  onRenamed: (session: SessionRead) => void;
  onDeleted: (sessionId: number) => void;
}) {
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [renamePending, setRenamePending] = useState(false);

  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  function startRename(session: SessionRead) {
    setRenamingId(session.id);
    setRenameValue(session.title);
    setRenameError(null);
  }

  function cancelRename() {
    setRenamingId(null);
    setRenameError(null);
  }

  async function saveRename(session: SessionRead) {
    const trimmed = renameValue.trim();
    if (!trimmed) {
      setRenameError("Session title is required.");
      return;
    }
    if (trimmed === session.title) {
      setRenamingId(null);
      return;
    }
    setRenameError(null);
    setRenamePending(true);
    try {
      const updated = await renameSession(session.id, trimmed);
      onRenamed(updated);
      setRenamingId(null);
    } catch (error) {
      // e.g. the backend's 409 naming the conflicting session and its owner.
      setRenameError(
        error instanceof ApiError ? error.detail : "Could not rename this session.",
      );
    } finally {
      setRenamePending(false);
    }
  }

  async function handleDelete(session: SessionRead) {
    setDeleteError(null);
    setBusyId(session.id);
    try {
      await deleteSession(session.id);
      onDeleted(session.id);
      setConfirmingDeleteId(null);
    } catch (error) {
      setDeleteError(
        error instanceof ApiError
          ? error.detail
          : `Could not delete "${session.title}".`,
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      {deleteError ? <FormError>{deleteError}</FormError> : null}
      <ul className="divide-y divide-slate-200">
        {sessions.map((session) => {
          const isRenaming = renamingId === session.id;
          const isConfirmingDelete = confirmingDeleteId === session.id;
          const isBusy = busyId === session.id;

          return (
            <li key={session.id} className="flex items-center justify-between gap-4 py-3">
              {isRenaming ? (
                <div className="min-w-0 flex-1">
                  <input
                    autoFocus
                    aria-label={`Rename "${session.title}"`}
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveRename(session);
                      if (e.key === "Escape") cancelRename();
                    }}
                    disabled={renamePending}
                    className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-slate-400 disabled:bg-slate-100"
                  />
                  {renameError ? (
                    <p role="alert" className="mt-1 text-xs text-red-600">
                      {renameError}
                    </p>
                  ) : null}
                </div>
              ) : (
                <Link
                  href={`/instructor/sessions/${session.id}`}
                  className="min-w-0 flex-1 rounded-md py-1 transition hover:bg-slate-50"
                >
                  <span className="block truncate text-sm font-medium text-slate-900">
                    {session.title}
                  </span>
                  <span className="block text-xs text-slate-500">
                    {session.instructor_name ? `${session.instructor_name} · ` : ""}
                    Created {formatDate(session.created_at)} ·{" "}
                    {session.unsolved_files.length}{" "}
                    {session.unsolved_files.length === 1 ? "file" : "files"}
                  </span>
                </Link>
              )}

              <span className="flex shrink-0 items-center gap-2">
                {isRenaming ? (
                  <>
                    <SmallButton onClick={() => saveRename(session)} disabled={renamePending}>
                      {renamePending ? "Saving…" : "Save"}
                    </SmallButton>
                    <SmallButton onClick={cancelRename} disabled={renamePending}>
                      Cancel
                    </SmallButton>
                  </>
                ) : isConfirmingDelete ? (
                  <>
                    <SmallButton
                      tone="danger"
                      onClick={() => handleDelete(session)}
                      disabled={isBusy}
                    >
                      Confirm delete
                    </SmallButton>
                    <SmallButton
                      onClick={() => setConfirmingDeleteId(null)}
                      disabled={isBusy}
                    >
                      Cancel
                    </SmallButton>
                  </>
                ) : (
                  <>
                    <SmallButton onClick={() => startRename(session)}>Rename</SmallButton>
                    <SmallButton
                      tone="danger"
                      onClick={() => setConfirmingDeleteId(session.id)}
                    >
                      Delete
                    </SmallButton>
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
