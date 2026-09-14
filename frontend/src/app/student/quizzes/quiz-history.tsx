"use client";

/**
 * The student's own practice-quiz history (GET /quiz/history). The backend
 * lists SUBMITTED attempts only, so an abandoned quiz never appears here.
 */

import { useEffect, useState } from "react";

import { ApiError, getQuizAttempt, getQuizHistory, isQuizResult } from "@/lib/api";
import type { QuizHistoryItem, QuizHistoryOut, QuizResultOut } from "@/lib/api";
import { EmptyState, FormError, Loading, SmallButton } from "@/components/ui";
import { PRACTICE_ONLY_LINE, QuizResultView, describeScope } from "@/components/quiz-views";
import { formatDate } from "@/lib/format";

export function QuizHistory() {
  const [history, setHistory] = useState<QuizHistoryOut | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getQuizHistory().then(
      (h) => {
        if (!cancelled) setHistory(h);
      },
      (error: unknown) => {
        if (cancelled) return;
        setLoadError(error instanceof ApiError ? error.detail : "Could not load your quiz history.");
      },
    );
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-neutral-900">Practice quiz history</h1>
        <p className="mt-1 text-sm text-neutral-500">{PRACTICE_ONLY_LINE}</p>
      </div>

      <div className="lms-card">
        <h2 className="text-base font-semibold text-neutral-900">Submitted quizzes</h2>
        <p className="mt-1 mb-4 text-sm text-neutral-500">
          Only submitted quizzes appear here.
        </p>

        {loadError ? <FormError>{loadError}</FormError> : null}

        {history ? (
          history.attempts.length === 0 ? (
            <EmptyState>You haven&apos;t submitted any practice quizzes yet.</EmptyState>
          ) : (
            <ul className="divide-y divide-neutral-100">
              {history.attempts.map((item) => (
                <QuizHistoryRow key={item.attempt_id} item={item} />
              ))}
            </ul>
          )
        ) : !loadError ? (
          <Loading>Loading quiz history...</Loading>
        ) : null}
      </div>
    </div>
  );
}

// ── Per-row component with inline expand ──────────────────────────────────────

function QuizHistoryRow({ item }: { item: QuizHistoryItem }) {
  const [expanded, setExpanded] = useState(false);
  const [result, setResult] = useState<QuizResultOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleToggle() {
    if (expanded) {
      setExpanded(false);
      return;
    }
    // Already loaded — just expand
    if (result) {
      setExpanded(true);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const attempt = await getQuizAttempt(item.attempt_id);
      if (isQuizResult(attempt)) {
        setResult(attempt);
        setExpanded(true);
      } else {
        setError("This quiz has not been submitted yet.");
      }
    } catch (fetchError) {
      setError(fetchError instanceof ApiError ? fetchError.detail : "Could not load this quiz.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <li>
      <button
        type="button"
        onClick={() => void handleToggle()}
        disabled={loading}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between gap-4 py-3 text-left transition hover:bg-neutral-50 disabled:opacity-50"
      >
        <span className="min-w-0">
          <span className="block text-sm font-medium text-neutral-900">
            {describeScope(item.scope_type, item.scope_detail)}
          </span>
          <span className="block text-xs text-neutral-500">
            Submitted {formatDate(item.submitted_at)}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-3">
          <span className="text-sm font-medium text-neutral-700">{item.score_label}</span>
          <span className="text-xs text-neutral-400">
            {loading ? "Loading…" : expanded ? "Hide details ▲" : "Show details ▼"}
          </span>
        </span>
      </button>

      {error ? (
        <div className="pb-3">
          <FormError>{error}</FormError>
        </div>
      ) : null}

      {expanded && result ? (
        <div className="pb-4">
          <QuizResultView result={result} />
          <div className="mt-2 flex justify-end">
            <SmallButton onClick={() => setExpanded(false)}>Hide details</SmallButton>
          </div>
        </div>
      ) : null}
    </li>
  );
}
