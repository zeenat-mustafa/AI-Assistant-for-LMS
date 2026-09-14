"use client";

/**
 * SessionShell — Phase 7.8, Prompt 2
 *
 * Shared two-column layout for both instructor and student session pages:
 *   LEFT  (~28%): scrollable session list with selection highlight
 *   RIGHT (~72%): scrollable detail pane for the selected session
 *
 * ── Routing approach ──────────────────────────────────────────────────────
 * Uses <Link> for navigation, not internal state. The session detail body
 * is already tied to a URL-derived sessionId via the App Router dynamic
 * segment (/[role]/sessions/[id]). Switching sessions via <Link> gives
 * client-side navigation (no full reload), lets the browser own the URL
 * (back/forward works), and keeps the existing page → detail component
 * contract intact without reimplementing any data-fetching here.
 *
 * ── Mobile fallback ───────────────────────────────────────────────────────
 * On narrow screens (below md breakpoint) the two columns stack vertically:
 * session list on top, detail below. The detail pane shows a sticky
 * "Sessions" back-nav link when a session is selected, so mobile users can
 * scroll back to the list easily. No dropdown — stacking is idiomatic for
 * this type of admin/course app.
 *
 * ── Design tokens ─────────────────────────────────────────────────────────
 * Uses .lms-card, CSS custom property colours, and Tailwind utilities
 * mapped from globals.css @theme. No hardcoded hex values.
 */

import Link from "next/link";
import type { ReactNode } from "react";

// ── Public types ─────────────────────────────────────────────────────────────

export interface SessionListItem {
  id: number;
  title: string;
  /** ISO-8601 date string, shown as short local date */
  created_at: string;
  /** Shown as "N files" in the list */
  file_count?: number;
  /** Optional extra line under the title (e.g. instructor name) */
  meta?: string;
}

export interface SessionShellProps {
  /** Flat list of sessions to render in the left sidebar. */
  sessions: SessionListItem[];
  /** The id of the currently selected session, or null if none. */
  selectedId: number | null;
  /**
   * Called with a session id when the user clicks a session row.
   */
  onSelect: (id: number) => void;
  /**
   * href base for the Link elements, e.g. "/student/sessions" or
   * "/instructor/sessions".  Each row becomes `${hrefBase}/${session.id}`.
   */
  hrefBase: string;
  /** Label shown at the top of the sidebar (e.g. "Sessions" or "My Sessions") */
  listLabel?: string;
  /** Slot for the detail pane content. */
  children: ReactNode;
  /** Shown in the sidebar footer / empty state when sessions is empty. */
  emptyLabel?: string;
  /** Whether the session list is still loading. */
  loading?: boolean;
  /**
   * Optional: render extra controls after each session row (e.g. rename/delete).
   * When omitted the row is a plain Link; when provided the row still links but
   * the extra controls are appended inside the row.
   */
  renderItemControls?: (session: SessionListItem) => ReactNode;
  /**
   * Optional: render a fully custom row replacing the default Link row.
   * When provided, onSelect / hrefBase are still called/used by the caller
   * but the built-in Link is not rendered.
   */
  renderItem?: (session: SessionListItem, isSelected: boolean) => ReactNode;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

export function SessionShell({
  sessions,
  selectedId,
  onSelect,
  hrefBase,
  listLabel = "Sessions",
  children,
  emptyLabel = "No sessions yet.",
  loading = false,
  renderItem,
}: SessionShellProps) {
  return (
    /*
     * Outer wrapper: full remaining height of the page.
     * Uses CSS grid with fixed sidebar column so the sidebar never grows
     * past its allotted width even with long session titles.
     *
     * md+ : two columns (sidebar | detail)
     * <md : single column (list stacked above detail)
     */
    <div
      className="
        grid gap-0
        grid-cols-1
        md:grid-cols-[minmax(0,_280px)_1fr]
        min-h-0 h-full
      "
      style={{ minHeight: "calc(100vh - 65px)" }}   /* 65px = approx header height */
    >

      {/* ── LEFT SIDEBAR ─────────────────────────────────────────────────── */}
      <aside
        className="
          flex flex-col
          border-b border-neutral-200
          md:border-b-0 md:border-r md:border-neutral-200
          bg-neutral-50
        "
      >
        {/* Sidebar header */}
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <h2 className="text-sm font-semibold text-neutral-700 uppercase tracking-wide">
            {listLabel}
          </h2>
          {loading ? (
            <span className="text-xs text-neutral-400">Loading...</span>
          ) : (
            <span className="text-xs text-neutral-400">{sessions.length}</span>
          )}
        </div>

        {/* Session list — scrolls independently */}
        <nav
          className="flex-1 overflow-y-auto"
          aria-label={listLabel}
        >
          {loading ? (
            <div className="px-4 py-6 text-center text-sm text-neutral-400">
              Loading sessions...
            </div>
          ) : sessions.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-neutral-400">
              {emptyLabel}
            </div>
          ) : (
            <ul role="list" className="divide-y divide-neutral-100">
              {sessions.map((s) => {
                const isSelected = s.id === selectedId;
                return (
                  <li key={s.id}>
                    {renderItem ? renderItem(s, isSelected) : (
                      <Link
                        href={`${hrefBase}/${s.id}`}
                        onClick={() => onSelect(s.id)}
                        aria-current={isSelected ? "page" : undefined}
                        className={`
                          block px-4 py-3 transition-colors
                          ${
                            isSelected
                              ? "bg-primary-50 border-l-2 border-primary-600"
                              : "border-l-2 border-transparent hover:bg-neutral-100"
                          }
                        `}
                      >
                        <p
                          className={`text-sm font-medium leading-snug ${
                            isSelected ? "text-primary-700" : "text-neutral-800"
                          }`}
                        >
                          {s.title}
                        </p>
                        <p className="mt-0.5 text-xs text-neutral-400">
                          {formatDate(s.created_at)}
                          {s.file_count !== undefined
                            ? ` · ${s.file_count} file${s.file_count === 1 ? "" : "s"}`
                            : ""}
                        </p>
                        {s.meta ? (
                          <p className="mt-0.5 text-xs text-neutral-400 truncate">{s.meta}</p>
                        ) : null}
                      </Link>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </nav>
      </aside>

      {/* ── RIGHT DETAIL PANE ─────────────────────────────────────────────── */}
      <div className="flex flex-col overflow-y-auto bg-neutral-50">

        {/* Mobile back-nav — only visible below md when a session is selected */}
        {selectedId !== null ? (
          <div className="md:hidden border-b border-neutral-200 bg-white px-4 py-2">
            <Link
              href={hrefBase}
              className="text-sm text-primary-600 hover:underline"
              onClick={() => onSelect(0)}
            >
              &larr; {listLabel}
            </Link>
          </div>
        ) : null}

        {/* Detail content */}
        <div className="flex-1 p-6">
          {children}
        </div>
      </div>

    </div>
  );
}

// ── Empty / placeholder states used by the preview ───────────────────────────

export function SessionShellEmpty({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center py-24">
      <div className="text-center">
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-primary-50">
          <span className="text-xl text-primary-400">&#9783;</span>
        </div>
        <p className="text-sm font-medium text-neutral-600">{label}</p>
        <p className="mt-1 text-xs text-neutral-400">Select a session from the list.</p>
      </div>
    </div>
  );
}
