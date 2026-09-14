"use client";

/**
 * Student dashboard — Phase 7.8
 *
 * Two-column layout via SessionShell. Left sidebar: real session list.
 * Right pane: intro text and "select a session" prompt.
 * Session detail lives at /student/sessions/[id].
 *
 * Scope: shows ALL sessions — no enrolment concept in the system.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, listSessions } from "@/lib/api";
import type { SessionRead } from "@/lib/api";
import { FormError } from "@/components/ui";
import { SessionShell, SessionShellEmpty } from "@/components/session-shell";
import type { SessionListItem } from "@/components/session-shell";

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
  const router = useRouter();
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
    return () => { cancelled = true; };
  }, []);

  const sessionItems: SessionListItem[] = (sessions ?? []).map((s) => ({
    id: s.id,
    title: s.title,
    created_at: s.created_at,
    file_count: s.unsolved_files.length,
  }));

  return (
    <SessionShell
      sessions={sessionItems}
      selectedId={null}
      onSelect={(id) => router.push(`/student/sessions/${id}`)}
      hrefBase="/student/sessions"
      listLabel="Sessions"
      loading={sessions === null}
      emptyLabel="No sessions have been created yet."
    >
      {loadError ? (
        <div className="p-6">
          <FormError>{loadError}</FormError>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="lms-card">
            <h1 className="text-xl font-semibold text-neutral-900">Welcome</h1>
            <p className="mt-2 text-sm text-neutral-500">
              Select a session from the left to download assignment files,
              upload your solution, and view your grades.
            </p>
          </div>
          <p className="text-xs text-neutral-400">
            There is no enrolment in this system yet — all sessions are shown,
            not only the ones assigned to you.
          </p>
          <SessionShellEmpty label="No session selected" />
        </div>
      )}
    </SessionShell>
  );
}
