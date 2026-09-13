"use client";

/**
 * Practice-quiz card and result (Phase 7.7, surfacing 7.6).
 *
 * - The card reads ONLY `question`, `options`, `source_citation` from each
 *   question, so even if a future backend started sending an answer early it
 *   could not reach the DOM before submission.
 * - The score is displayed exactly as returned. Nothing here tallies
 *   `is_correct`, rounds, or re-derives `score_label`.
 * - Both the card and the result carry a permanent, non-dismissible
 *   "does not affect real grades" line.
 */

import { useState, type ReactNode } from "react";

import { ApiError, getQuizAttempt, isQuizResult, submitQuiz } from "@/lib/api";
import type { QuizAttemptOut, QuizResultOut, QuizScopeDetail } from "@/lib/api";
import { FormError } from "@/components/ui";
import { QuizSourceLine } from "@/components/citation-list";

export const PRACTICE_ONLY_LINE =
  "Practice only — this quiz never changes your real grades.";

const SCOPE_LABELS: Record<string, string> = {
  assignment_file: "Assignment file",
  session: "Session",
  multiple_sessions: "Multiple sessions",
  topic: "Topic",
  uploaded_file: "Uploaded file",
};

/** The requested scope as stored: the type plus its raw detail values, nothing looked up or invented. */
export function describeScope(scopeType: string, detail: QuizScopeDetail): string {
  const label = SCOPE_LABELS[scopeType] ?? scopeType;
  const parts = Object.entries(detail)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`);
  return parts.length ? `${label} (${parts.join("; ")})` : label;
}

function PracticeLabel({ notice }: { notice?: string }) {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
      {notice ? <p>{notice}</p> : null}
      <p className="font-medium">{PRACTICE_ONLY_LINE}</p>
    </div>
  );
}

/** One generated quiz inside the chat transcript: answer, submit, then the backend's result. */
export function QuizCard({
  attempt,
  resultFooter,
}: {
  attempt: QuizAttemptOut;
  /** Extra line under the result (the chat page's "this visit only" note). */
  resultFooter?: ReactNode;
}) {
  const [answers, setAnswers] = useState<(number | null)[]>(() =>
    attempt.questions.map(() => null),
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QuizResultOut | null>(null);

  if (result) return <QuizResultView result={result} footer={resultFooter} />;

  const allAnswered = answers.every((a) => a !== null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!allAnswered || pending) return;
    setError(null);
    setPending(true);
    try {
      setResult(await submitQuiz(attempt.id, answers as number[]));
    } catch (submitError) {
      if (submitError instanceof ApiError && submitError.status === 409) {
        // Already submitted (e.g. another tab) -- show the stored result, not an error.
        try {
          const stored = await getQuizAttempt(attempt.id);
          if (isQuizResult(stored)) {
            setResult(stored);
          } else {
            setError(submitError.detail);
          }
        } catch (fetchError) {
          setError(
            fetchError instanceof ApiError ? fetchError.detail : "Could not load this quiz's result.",
          );
        }
      } else {
        setError(
          submitError instanceof ApiError ? submitError.detail : "Could not submit this quiz.",
        );
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section
      className="rounded-lg border border-slate-200 bg-white px-4 py-3"
      aria-label="Practice quiz"
    >
      <h3 className="text-sm font-semibold text-slate-900">Practice quiz</h3>
      <p className="mb-2 text-xs text-slate-500">
        {describeScope(attempt.scope_type, attempt.scope_detail)}
      </p>
      <PracticeLabel notice={attempt.notice} />

      <form onSubmit={handleSubmit} noValidate className="mt-3 space-y-4">
        {attempt.questions.map((question, qi) => (
          <fieldset key={qi} disabled={pending}>
            <legend className="text-sm font-medium text-slate-900">
              {qi + 1}. {question.question}
            </legend>
            <div className="mt-1 space-y-1">
              {question.options.map((option, oi) => (
                <label key={oi} className="flex items-start gap-2 text-sm text-slate-700">
                  <input
                    type="radio"
                    name={`quiz-${attempt.id}-q${qi}`}
                    checked={answers[qi] === oi}
                    onChange={() =>
                      setAnswers((current) => current.map((a, i) => (i === qi ? oi : a)))
                    }
                    className="mt-1"
                  />
                  <span>{option}</span>
                </label>
              ))}
            </div>
            <QuizSourceLine text={question.source_citation} />
          </fieldset>
        ))}

        {error ? <FormError>{error}</FormError> : null}

        {!allAnswered ? (
          <p className="text-xs text-slate-500">
            Answer all {attempt.questions.length} questions to submit.
          </p>
        ) : null}

        <button
          type="submit"
          disabled={!allAnswered || pending}
          className="w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
        >
          {pending ? "Submitting…" : "Submit answers"}
        </button>
      </form>
    </section>
  );
}

/** A submitted quiz, rendered exactly from `QuizResultOut`. Used in chat and in quiz history. */
export function QuizResultView({
  result,
  footer,
}: {
  result: QuizResultOut;
  footer?: ReactNode;
}) {
  return (
    <section
      className="rounded-lg border border-slate-200 bg-white px-4 py-3"
      aria-label="Practice quiz result"
    >
      <h3 className="text-sm font-semibold text-slate-900">Practice quiz result</h3>
      <p className="mb-2 text-xs text-slate-500">
        {describeScope(result.scope_type, result.scope_detail)}
      </p>

      <p className="text-lg font-semibold text-slate-900" data-testid="quiz-score">
        {result.score} / {result.max_score}
      </p>
      <p className="mb-2 text-sm text-slate-700">{result.score_label}</p>
      {result.not_a_real_grade === true ? (
        <p className="mb-2 text-xs text-slate-500">Recorded by the server as not a real grade.</p>
      ) : null}
      <PracticeLabel notice={result.notice} />

      <ol className="mt-3 space-y-3">
        {result.questions.map((question, qi) => (
          <li key={qi}>
            <p className="text-sm font-medium text-slate-900">
              {qi + 1}. {question.question}{" "}
              <span className={question.is_correct ? "text-emerald-700" : "text-red-700"}>
                {question.is_correct ? "✓ Correct" : "✕ Incorrect"}
              </span>
            </p>
            <ul className="mt-1 space-y-0.5">
              {question.options.map((option, oi) => {
                const isCorrect = oi === question.correct_option_index;
                const isChosen = oi === question.student_answer_index;
                return (
                  <li
                    key={oi}
                    className={`text-sm ${isCorrect ? "font-medium text-emerald-800" : "text-slate-700"}`}
                  >
                    {option}
                    {isChosen ? <span className="ml-2 text-xs text-slate-500">(your answer)</span> : null}
                    {isCorrect ? (
                      <span className="ml-2 text-xs text-emerald-700">(correct answer)</span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            <QuizSourceLine text={question.source_citation} />
          </li>
        ))}
      </ol>

      {footer ? <div className="mt-3 text-xs text-slate-500">{footer}</div> : null}
    </section>
  );
}
