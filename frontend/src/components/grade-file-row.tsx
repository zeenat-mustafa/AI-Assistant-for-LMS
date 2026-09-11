"use client";

/**
 * One graded file: collapsed to filename + score, expandable in place.
 *
 * Shared by the student's own-grades panel and the instructor's roster so the
 * two cannot drift — the collapse behaviour and, more importantly, the
 * zero-versus-ungraded handling have to be identical in both.
 *
 * Zero versus ungraded
 * --------------------
 * A `GradeRead` in `per_file` always represents a real, completed grading run,
 * so `score` here is always a genuine score — 0 included. The ambiguous value
 * is `GradeSummary.combined_score`, which is 0.0 both for a real zero and for
 * a submitter with nothing graded; that distinction is made by the CALLER,
 * from `per_file.length`, before it ever renders a row. This component is
 * therefore never asked to guess: if it is rendering, the file is graded, and
 * "0 / 10" means the student scored zero.
 *
 * Content is unchanged from what these panels showed before except for three
 * things: (bugfix-structured-rationale-display) when the backend has a
 * structured per-criterion breakdown (`GradeRead.rationale`), that renders as
 * the primary detail -- one block per criterion with its name, "X / Y", and
 * explanation -- instead of the flat `feedback_text` paragraph. `feedback_text`
 * is the fallback, shown only when `rationale` is missing or empty, exactly as
 * it rendered before this fix. (bugfix-session-naming-attribution) a "Graded
 * by {name}" line renders alongside the existing "Graded {date}" line when
 * `graded_by_name` is present -- omitted entirely for historical/MCP grades
 * with no attribution, never shown as "Graded by null" or similar. (feature-
 * grade-summary) an optional `showSummary` prop renders `grade.summary` as a
 * paragraph above the (otherwise completely unchanged) rationale/feedback
 * block -- opt-in and additive, so the instructor roster can show it while
 * the student's own view renders its own separate summary-only row instead
 * of this component entirely when a summary exists (see my-grades-panel.tsx).
 */

import { useId, useState } from "react";

import type { GradeRead } from "@/lib/api";
import { formatDate } from "@/lib/format";

export function GradeFileRow({
  grade,
  dense = false,
  showSummary = false,
}: {
  grade: GradeRead;
  /** Tighter type scale, for the roster's nested per-student list. */
  dense?: boolean;
  /** Render `grade.summary` (if present) above the unchanged breakdown below. */
  showSummary?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const panelId = useId();

  const nameClass = dense
    ? "truncate text-xs font-medium text-slate-800"
    : "truncate text-sm font-medium text-slate-900";
  const scoreClass = dense
    ? "shrink-0 text-xs tabular-nums text-slate-600"
    : "shrink-0 text-sm font-medium tabular-nums text-slate-900";

  return (
    <li className={dense ? "py-1" : "py-1.5"}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls={panelId}
        className="flex w-full items-baseline justify-between gap-3 rounded-md py-1 text-left hover:bg-slate-50"
      >
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span
            aria-hidden
            className={`shrink-0 text-slate-400 transition-transform ${
              expanded ? "rotate-90" : ""
            }`}
          >
            ›
          </span>
          <span className={nameClass}>{grade.original_filename}</span>
        </span>
        <span className={scoreClass}>{grade.score} / 10</span>
      </button>

      {/*
        Kept mounted and toggled with `hidden` rather than unmounted, so the
        aria-controls target always exists for assistive tech.
      */}
      <div id={panelId} hidden={!expanded} className={dense ? "pl-4" : "pl-4"}>
        <p className="mt-0.5 text-xs text-slate-500">
          Graded {formatDate(grade.graded_at)}
        </p>
        {grade.graded_by_name ? (
          <p className="text-xs text-slate-500">Graded by {grade.graded_by_name}</p>
        ) : null}
        {showSummary && grade.summary ? (
          <p
            className={
              dense
                ? "mt-1 text-xs text-slate-700"
                : "mt-2 text-sm text-slate-700"
            }
          >
            {grade.summary}
          </p>
        ) : null}
        {grade.rationale && grade.rationale.length > 0 ? (
          <ul className={dense ? "mt-1 space-y-1.5" : "mt-2 space-y-2"}>
            {grade.rationale.map((entry, index) => (
              <li
                key={index}
                className="rounded-md border border-slate-200 bg-slate-50 p-2"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span
                    className={
                      dense
                        ? "text-xs font-medium text-slate-800"
                        : "text-sm font-medium text-slate-800"
                    }
                  >
                    {entry.criterion}
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-slate-600">
                    {entry.points_awarded} / {entry.points_possible}
                  </span>
                </div>
                <p
                  className={
                    dense
                      ? "mt-1 text-xs text-slate-600"
                      : "mt-1 text-sm text-slate-700"
                  }
                >
                  {entry.explanation}
                </p>
              </li>
            ))}
          </ul>
        ) : grade.feedback_text ? (
          <p
            className={
              dense
                ? "mt-1 whitespace-pre-wrap text-xs text-slate-600"
                : "mt-2 whitespace-pre-wrap text-sm text-slate-700"
            }
          >
            {grade.feedback_text}
          </p>
        ) : (
          <p className="mt-1 text-xs text-slate-400">
            No written feedback was recorded for this file.
          </p>
        )}
      </div>
    </li>
  );
}
