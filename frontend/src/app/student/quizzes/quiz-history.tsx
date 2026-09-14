"use client";

/**
 * The student's own practice-quiz history (GET /quiz/history). The backend
 * lists SUBMITTED attempts only, so an abandoned quiz never appears here.
 */

import { useEffect, useState } from "react";

import { ApiError, getQuizAttempt, getQuizHistory, isQuizResult } from "@/lib/api";
import type { QuizHistoryItem, QuizHistoryOut, QuizResultOut } from "@/lib/api";
import { EmptyState, FormError, Loading } from "@/components/ui";
import { PRACTICE_ONLY_LINE, QuizResultView, describeScope } from "@/components/quiz-views";
import { formatDate } from "@/lib/format";

export function QuizHistory() {
  const [history, setHistory] = useState<QuizHistoryOut | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selected, setSelected] = useState<QuizResultOut | null>(null);
  const [selectedError, setSelectedError] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<number | null>(null);

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

  async function open(item: QuizHistoryItem) {
    setSelectedError(null);
    setLoadingId(item.attempt_id);
    try {
      const attempt = await getQuizAttempt(item.attempt_id);
      if (isQuizResult(attempt)) {
        setSelected(attempt);
      } else {
        setSelected(null);
        setSelectedError("This quiz has not been submitted yet.");
      }
    } catch (error) {
      setSelected(null);
      setSelectedError(error instanceof ApiError ? error.detail : "Could not load this quiz.");
    } finally {
      setLoadingId(null);
    }
  }

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
            <>
              <p className="mb-3 text-xs text-neutral-500">{history.notice}</p>
              <ul className="divide-y divide-neutral-100">
              {history.attempts.map((item) => (
                <li key={item.attempt_id}>
                  <button
                    type="button"
                    onClick={() => void open(item)}
                    disabled={loadingId === item.attempt_id}
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
                    <span className="shrink-0 text-sm font-medium text-neutral-700">
                      {item.score_label}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            </>
          )
        ) : !loadError ? (
          <Loading>Loading quiz history...</Loading>
        ) : null}
      </div>

      {selectedError ? <FormError>{selectedError}</FormError> : null}
      {selected ? <QuizResultView result={selected} /> : null}
    </div>
  );
}
