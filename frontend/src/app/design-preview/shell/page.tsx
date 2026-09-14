"use client";

/**
 * Session shell preview — Phase 7.8
 * Route: /design-preview/shell
 *
 * Renders the SessionShell with dummy data to verify layout,
 * selection highlight, scrolling, and responsive stacking.
 * Delete this file once Phase 7.8 is complete.
 */

import { useState } from "react";
import { SessionShell, SessionShellEmpty } from "@/components/session-shell";

const DUMMY_SESSIONS = Array.from({ length: 18 }, (_, i) => ({
  id: i + 1,
  title: `Week ${Math.floor(i / 2) + 1} Day ${(i % 2) + 1}`,
  created_at: new Date(2026, 7, 1 + i * 2).toISOString(),
  file_count: Math.floor(Math.random() * 4) + 1,
  meta: i % 3 === 0 ? "Instructor: Demo Instructor" : undefined,
}));

export default function ShellPreviewPage() {
  const [selectedId, setSelectedId] = useState<number | null>(3);

  const selected = DUMMY_SESSIONS.find((s) => s.id === selectedId);

  return (
    <div className="flex flex-col min-h-screen bg-neutral-50">
      {/* Fake header bar */}
      <header className="border-b border-neutral-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-neutral-900">AI Assistant for LMS</span>
          <span className="text-xs text-neutral-400">/design-preview/shell — delete after 7.8</span>
        </div>
      </header>

      <div className="flex-1 flex flex-col">
        <SessionShell
          sessions={DUMMY_SESSIONS}
          selectedId={selectedId}
          onSelect={setSelectedId}
          hrefBase="/design-preview/shell"
          listLabel="Sessions"
          emptyLabel="No sessions yet."
        >
          {selected ? (
            <div className="space-y-4">
              {/* Detail header */}
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-neutral-400 mb-1">
                  Session
                </p>
                <h1 className="text-2xl font-semibold text-neutral-900">{selected.title}</h1>
                <p className="mt-1 text-sm text-neutral-500">
                  Created {new Date(selected.created_at).toLocaleDateString(undefined, {
                    year: "numeric", month: "long", day: "numeric"
                  })}
                </p>
              </div>

              {/* Example cards */}
              <div className="lms-card">
                <h2 className="text-base font-semibold text-neutral-900">Assignment Files</h2>
                <p className="mt-1 text-sm text-neutral-500">
                  {selected.file_count} file{selected.file_count === 1 ? "" : "s"} uploaded for this session.
                </p>
                <div className="mt-4 space-y-2">
                  {Array.from({ length: selected.file_count ?? 1 }, (_, fi) => (
                    <div
                      key={fi}
                      className="flex items-center justify-between rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2"
                    >
                      <span className="text-sm text-neutral-700">
                        assignment_{fi + 1}.ipynb
                      </span>
                      <span className="lms-badge lms-badge-success">Gradeable notebook</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="lms-card">
                <h2 className="text-base font-semibold text-neutral-900">Submissions</h2>
                <p className="mt-1 text-sm text-neutral-500">3 students submitted.</p>
                <div className="mt-4">
                  <p className="lms-alert lms-alert-info text-xs">
                    Grading not yet triggered for this session.
                  </p>
                </div>
              </div>

              {/* Lots of placeholder cards to test independent scrolling */}
              {Array.from({ length: 6 }, (_, ci) => (
                <div key={ci} className="lms-card">
                  <h2 className="text-base font-semibold text-neutral-900">
                    Section {ci + 1}
                  </h2>
                  <p className="mt-2 text-sm text-neutral-500">
                    Placeholder content to test that the right pane scrolls independently
                    of the sidebar. The sidebar list should stay fixed while you scroll here.
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <SessionShellEmpty label="No session selected" />
          )}
        </SessionShell>
      </div>
    </div>
  );
}
