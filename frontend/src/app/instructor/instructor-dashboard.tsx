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
 * Plain `useEffect` + state rather than SWR/TanStack Query. The Next docs
 * recommend those once you need revalidation, polling or request dedup;
 * this page loads one list once, so a data library would be weight without
 * a payoff at this scope.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import { ApiError, createSession, listSessions } from "@/lib/api";
import type { SessionRead } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  EmptyState,
  Field,
  FormError,
  Loading,
  Panel,
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
      // e.g. the backend's 409: "A session titled 'X' already exists (id=N)."
      // Title uniqueness is per-instructor, not global.
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
          <ul className="divide-y divide-slate-200">
            {sessions.map((session) => (
              <li key={session.id}>
                <Link
                  href={`/instructor/sessions/${session.id}`}
                  className="flex items-center justify-between gap-4 py-3 transition hover:bg-slate-50"
                >
                  <span>
                    <span className="block text-sm font-medium text-slate-900">
                      {session.title}
                    </span>
                    <span className="block text-xs text-slate-500">
                      {session.instructor_name ? `${session.instructor_name} · ` : ""}
                      Created {formatDate(session.created_at)} ·{" "}
                      {session.unsolved_files.length}{" "}
                      {session.unsolved_files.length === 1 ? "file" : "files"}
                    </span>
                  </span>
                  <span aria-hidden className="text-slate-400">
                    →
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
