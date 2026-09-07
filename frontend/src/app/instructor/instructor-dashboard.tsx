"use client";

/**
 * Instructor dashboard: create a session, and list the ones they own.
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
import { useAuth } from "@/lib/auth/auth-context";
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
 * The backend caps `limit` at 200 and has no server-side instructor filter
 * (GET /sessions is literally "List all sessions"), so ownership is narrowed
 * client-side below. One page of 200 keeps that narrowing correct at demo
 * scale -- with real pagination, filtering a page after the fact would drop
 * sessions that sit beyond it. See the README note.
 */
const PAGE_LIMIT = 200;

/**
 * Load the sessions this instructor owns.
 *
 * Returns the outcome instead of setting state, so a stale response can be
 * discarded by the caller.
 */
export async function loadOwnSessions(
  instructorId: number,
): Promise<{ sessions: SessionRead[] } | { error: string }> {
  try {
    const page = await listSessions({ limit: PAGE_LIMIT });
    // `page.total` counts every instructor's sessions, so it is deliberately
    // not shown anywhere -- the filtered array's length is the real count.
    return { sessions: page.items.filter((s) => s.instructor_id === instructorId) };
  } catch (error) {
    return {
      error: error instanceof ApiError ? error.detail : "Could not load your sessions.",
    };
  }
}

export function InstructorDashboard() {
  const { user } = useAuth();

  // null = still loading; [] = loaded and genuinely empty.
  const [sessions, setSessions] = useState<SessionRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [titleError, setTitleError] = useState<string | undefined>();
  const [createError, setCreateError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const instructorId = user?.id;

  useEffect(() => {
    if (instructorId === undefined) return;
    // Guards against a response for one user landing after a re-login as
    // another, which would show the wrong person's sessions.
    let cancelled = false;
    void loadOwnSessions(instructorId).then((result) => {
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
  }, [instructorId]);

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
          Create a session for each class, then upload its assignment notebooks.
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

      <Panel title="Your sessions">
        {loadError ? <FormError>{loadError}</FormError> : null}

        {sessions === null ? (
          <Loading>Loading your sessions…</Loading>
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
