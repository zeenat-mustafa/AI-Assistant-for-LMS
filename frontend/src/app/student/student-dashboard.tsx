"use client";

/**
 * Student dashboard: every session in the system, honestly labelled.
 *
 * Scope decision (signed off): show ALL sessions, with a visible note that
 * there is no enrolment concept. GET /sessions lists every session to any
 * authenticated user and the schema has no enrolment table at all, so there
 * is nothing to filter by that would be correct.
 *
 * Filtering by "has a submission" was rejected against real data: the seeded
 * demo student has no submissions anywhere, so that filter would render an
 * empty dashboard with no route to any session — hiding sessions at exactly
 * the moment a student needs to find one.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import { ApiError, listSessions } from "@/lib/api";
import type { SessionRead } from "@/lib/api";
import { EmptyState, FormError, Loading, Panel } from "@/components/ui";
import { formatDate } from "@/lib/format";

/** The backend's maximum page size; see the instructor dashboard's note. */
const PAGE_LIMIT = 200;

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

export function StudentDashboard() {
  const [sessions, setSessions] = useState<SessionRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
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

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Sessions</h1>
        <p className="mt-1 text-sm text-slate-600">
          Open a session to download its assignment files.
        </p>
      </div>

      <Panel title="All sessions">
        {loadError ? <FormError>{loadError}</FormError> : null}

        {sessions === null ? (
          <Loading>Loading sessions…</Loading>
        ) : sessions.length === 0 ? (
          <>
            <EmptyState>No sessions have been created yet.</EmptyState>
            <EnrolmentCaveat />
          </>
        ) : (
          <>
            <ul className="divide-y divide-slate-200">
              {sessions.map((session) => (
                <li key={session.id}>
                  <Link
                    href={`/student/sessions/${session.id}`}
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
            <EnrolmentCaveat />
          </>
        )}
      </Panel>
    </div>
  );
}

/**
 * States the scope plainly rather than letting the list imply these sessions
 * were assigned to this student.
 */
function EnrolmentCaveat() {
  return (
    <p className="mt-4 border-t border-slate-200 pt-3 text-xs text-slate-500">
      There is no enrolment in this system yet, so this lists every session —
      not only the ones assigned to you.
    </p>
  );
}
