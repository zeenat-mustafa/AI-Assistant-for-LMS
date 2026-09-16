"use client";

/**
 * Instructor dashboard — Phase 7.8
 *
 * Two-column layout via SessionShell. The left sidebar contains the full
 * session list with inline rename/delete controls (same logic as 7.7).
 * The right pane shows the "Create session" form when nothing is selected,
 * or a prompt to pick a session. Session detail lives at
 * /instructor/sessions/[id].
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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
  SmallButton,
  SubmitButton,
} from "@/components/ui";
import { SessionShell } from "@/components/session-shell";
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

export function InstructorDashboard() {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Rename state
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [renamePending, setRenamePending] = useState(false);

  // Delete state
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

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
    if (!trimmed) { setRenameError("Session title is required."); return; }
    if (trimmed === session.title) { setRenamingId(null); return; }
    setRenameError(null);
    setRenamePending(true);
    try {
      const updated = await renameSession(session.id, trimmed);
      setSessions((current) =>
        (current ?? []).map((s) => (s.id === updated.id ? updated : s)),
      );
      setRenamingId(null);
    } catch (error) {
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
      setSessions((current) => (current ?? []).filter((s) => s.id !== session.id));
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

  const sessionItems: SessionListItem[] = (sessions ?? []).map((s) => ({
    id: s.id,
    title: s.title,
    created_at: s.created_at,
    file_count: s.unsolved_files.length,
    meta: s.instructor_name ?? undefined,
  }));

  // Build a map for quick lookup when rendering custom rows
  const sessionMap = new Map((sessions ?? []).map((s) => [s.id, s]));

  function renderItem(item: SessionListItem, isSelected: boolean) {
    const session = sessionMap.get(item.id);
    if (!session) return null;

    const isRenaming = renamingId === session.id;
    const isConfirmingDelete = confirmingDeleteId === session.id;
    const isBusy = busyId === session.id;

    return (
      <div
        className={`px-3 py-2 border-l-2 transition-colors ${
          isSelected
            ? "bg-primary-50 border-primary-600"
            : "border-transparent hover:bg-neutral-100"
        }`}
      >
        {isRenaming ? (
          <div className="space-y-1">
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
              className="lms-input text-xs py-1"
            />
            {renameError ? (
              <p role="alert" className="text-xs text-danger-600">{renameError}</p>
            ) : null}
            <div className="flex gap-1">
              <SmallButton onClick={() => void saveRename(session)} disabled={renamePending}>
                {renamePending ? "Saving..." : "Save"}
              </SmallButton>
              <SmallButton onClick={cancelRename} disabled={renamePending}>Cancel</SmallButton>
            </div>
          </div>
        ) : (
          <>
            <Link
              href={`/instructor/sessions/${session.id}`}
              className="block"
            >
              <p className={`text-sm font-medium leading-snug truncate ${
                isSelected ? "text-primary-700" : "text-neutral-800"
              }`}>
                {session.title}
              </p>
              <p className="mt-0.5 text-xs text-neutral-400">
                {session.instructor_name ? `${session.instructor_name} - ` : ""}
                {formatDate(session.created_at)}
                {` · ${session.unsolved_files.length} file${session.unsolved_files.length === 1 ? "" : "s"}`}
              </p>
            </Link>
            <div className="mt-1 flex gap-1">
              {isConfirmingDelete ? (
                <>
                  <SmallButton
                    tone="danger"
                    onClick={() => void handleDelete(session)}
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
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <SessionShell
      sessions={sessionItems}
      selectedId={null}
      onSelect={(id) => router.push(`/instructor/sessions/${id}`)}
      hrefBase="/instructor/sessions"
      listLabel="Sessions"
      loading={sessions === null}
      emptyLabel="No sessions yet."
      renderItem={renderItem}
    >
      <div className="space-y-6">
        {loadError ? <FormError>{loadError}</FormError> : null}
        {deleteError ? <FormError>{deleteError}</FormError> : null}

        <CreateSessionForm
          onCreated={(created) => {
            setSessions((current) => [created, ...(current ?? [])]);
            router.push(`/instructor/sessions/${created.id}`);
          }}
        />

      </div>
    </SessionShell>
  );
}

// ── Create session form ───────────────────────────────────────────────────────

function CreateSessionForm({ onCreated }: { onCreated: (s: SessionRead) => void }) {
  const [title, setTitle] = useState("");
  const [titleError, setTitleError] = useState<string | undefined>();
  const [createError, setCreateError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError(null);
    const trimmed = title.trim();
    if (!trimmed) { setTitleError("Session title is required."); return; }
    setTitleError(undefined);
    setPending(true);
    try {
      const created = await createSession(trimmed);
      setTitle("");
      onCreated(created);
    } catch (error) {
      setCreateError(
        error instanceof ApiError ? error.detail : "Could not create the session.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">Create a session</h2>
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
    </div>
  );
}
