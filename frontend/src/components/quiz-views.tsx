"use client";

/**
 * Practice-quiz card and result (Phase 7.8 redesign).
 *
 * Logic and API calls unchanged from 7.7. Appearance rebuilt:
 *  - Each question in its own card block with clear spacing.
 *  - Selected radio option highlighted with primary-50 background.
 *  - Result view: success/danger color per option row, large score headline.
 *  - Removed: "Recorded by the server as not a real grade." (redundant with
 *    PracticeLabel which already states this on every quiz).
 */

import { useState, type ReactNode } from "react";

import { ApiError, getQuizAttempt, isQuizResult, submitQuiz } from "@/lib/api";
import type { QuizAttemptOut, QuizResultOut, QuizScopeDetail } from "@/lib/api";
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

export function describeScope(scopeType: string, detail: QuizScopeDetail): string {
  const label = SCOPE_LABELS[scopeType] ?? scopeType;
  const parts = Object.entries(detail)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`);
  return parts.length ? `${label} (${parts.join("; ")})` : label;
}

function PracticeLabel({ notice }: { notice?: string }) {
  return (
    <div className="rounded border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
      {notice ? <p className="mb-0.5">{notice}</p> : null}
      <p className="font-medium">{PRACTICE_ONLY_LINE}</p>
    </div>
  );
}

// ── Quiz card (unanswered) ────────────────────────────────────────────────────

export function QuizCard({
  attempt,
  resultFooter,
}: {
  attempt: QuizAttemptOut;
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
      className="rounded-xl border border-neutral-200 bg-white shadow-sm"
      aria-label="Practice quiz"
    >
      {/* Quiz header */}
      <div className="border-b border-neutral-100 px-4 py-3">
        <h3 className="text-sm font-semibold text-neutral-900">Practice quiz</h3>
        <p className="mt-0.5 text-xs text-neutral-500">
          {describeScope(attempt.scope_type, attempt.scope_detail)}
        </p>
      </div>

      <div className="px-4 pt-3 pb-1">
        <PracticeLabel notice={attempt.notice} />
      </div>

      <form onSubmit={handleSubmit} noValidate className="divide-y divide-neutral-100">
        {attempt.questions.map((question, qi) => (
          <fieldset key={qi} disabled={pending} className="px-4 py-4">
            <legend className="text-sm font-medium text-neutral-900 leading-snug">
              <span className="text-neutral-400 mr-1">{qi + 1}.</span>
              {question.question}
            </legend>

            <div className="mt-3 space-y-2">
              {question.options.map((option, oi) => {
                const isSelected = answers[qi] === oi;
                return (
                  <label
                    key={oi}
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                      isSelected
                        ? "border-primary-300 bg-primary-50"
                        : "border-neutral-200 bg-neutral-50 hover:border-neutral-300 hover:bg-white"
                    }`}
                  >
                    <input
                      type="radio"
                      name={`quiz-${attempt.id}-q${qi}`}
                      checked={isSelected}
                      onChange={() =>
                        setAnswers((current) => current.map((a, i) => (i === qi ? oi : a)))
                      }
                      className="mt-0.5 shrink-0 accent-primary-600"
                    />
                    <span className={`text-sm ${isSelected ? "text-primary-800 font-medium" : "text-neutral-700"}`}>
                      {option}
                    </span>
                  </label>
                );
              })}
            </div>

            <QuizSourceLine text={question.source_citation} />
          </fieldset>
        ))}

        <div className="px-4 py-3 space-y-2">
          {error ? (
            <p role="alert" className="lms-alert lms-alert-error text-xs">
              {error}
            </p>
          ) : null}

          {!allAnswered ? (
            <p className="text-xs text-neutral-400">
              Answer all {attempt.questions.length} questions to submit.
            </p>
          ) : null}

          <button
            type="submit"
            disabled={!allAnswered || pending}
            className="lms-btn-primary w-full"
          >
            {pending ? "Submitting..." : "Submit answers"}
          </button>
        </div>
      </form>
    </section>
  );
}

// ── Quiz result view ──────────────────────────────────────────────────────────

export function QuizResultView({
  result,
  footer,
}: {
  result: QuizResultOut;
  footer?: ReactNode;
}) {
  return (
    <section
      className="rounded-xl border border-neutral-200 bg-white shadow-sm"
      aria-label="Practice quiz result"
    >
      {/* Score header */}
      <div className="border-b border-neutral-100 px-4 py-3">
        <h3 className="text-sm font-semibold text-neutral-900">Quiz result</h3>
        <p className="mt-0.5 text-xs text-neutral-500">
          {describeScope(result.scope_type, result.scope_detail)}
        </p>
      </div>

      {/* Per-question breakdown */}
      <ol className="divide-y divide-neutral-100">
        {result.questions.map((question, qi) => {
          const isCorrect = question.is_correct;
          return (
            <li key={qi} className="px-4 py-4">
              <p className="text-sm font-medium text-neutral-900 leading-snug">
                <span className="text-neutral-400 mr-1">{qi + 1}.</span>
                {question.question}{" "}
                <span
                  className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold ${
                    isCorrect
                      ? "bg-success-50 text-success-700"
                      : "bg-danger-50 text-danger-700"
                  }`}
                >
                  {isCorrect ? "Correct" : "Incorrect"}
                </span>
              </p>

              <ul className="mt-2 space-y-1.5">
                {question.options.map((option, oi) => {
                  const isCorrectOption = oi === question.correct_option_index;
                  const isChosen = oi === question.student_answer_index;
                  const isWrongChoice = isChosen && !isCorrectOption;

                  return (
                    <li
                      key={oi}
                      className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                        isCorrectOption
                          ? "border-success-200 bg-success-50 text-success-700 font-medium"
                          : isWrongChoice
                          ? "border-danger-200 bg-danger-50 text-danger-700"
                          : "border-neutral-100 bg-neutral-50 text-neutral-600"
                      }`}
                    >
                      <span className="shrink-0 text-xs mt-0.5 w-4">
                        {isCorrectOption ? "✓" : isWrongChoice ? "✗" : " "}
                      </span>
                      <span className="flex-1">{option}</span>
                      {isChosen && !isCorrectOption ? (
                        <span className="shrink-0 text-xs text-danger-600">your answer</span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>

              <QuizSourceLine text={question.source_citation} />
            </li>
          );
        })}
      </ol>

      {/* Score block — shown after questions so students see answers first */}
      <div className="px-4 pt-4 pb-3 border-t border-neutral-100">
        <p
          className="text-2xl font-bold text-neutral-900"
          data-testid="quiz-score"
        >
          {result.score} / {result.max_score}
        </p>
        <p className="mt-0.5 text-sm text-neutral-600">{result.score_label}</p>
        <div className="mt-3">
          <PracticeLabel notice={result.notice} />
        </div>
      </div>

      {footer ? (
        <div className="border-t border-neutral-100 px-4 py-3 text-xs text-neutral-400">
          {footer}
        </div>
      ) : null}
    </section>
  );
}
