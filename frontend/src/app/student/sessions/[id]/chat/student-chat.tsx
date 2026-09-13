"use client";

/**
 * Student course-assistant chat for one session (Phase 7.7, Layout A),
 * surfacing 7.4 memory + 7.5 Q&A + 7.6 practice quizzes.
 *
 * Honest limits, stated in the UI rather than papered over:
 *  - The backend keeps a memory thread per (student, session) but has no
 *    endpoint to read past messages, so the on-screen transcript starts
 *    fresh each visit. Nothing is cached client-side to fake history.
 *  - The backend may answer from ANOTHER session (`redirected` /
 *    `broad_search`); that turn then shows a visible banner naming it.
 *  - Follow-ups send the previous turn's resolved session id, per 7.5's
 *    contract; the first turn of a visit sends this page's session id.
 *  - "Search all sessions" (per visit, never persisted) sends
 *    `current_session_id: null`, which is the only way the backend's
 *    clarification and broad_search paths can be reached. While it is on it
 *    wins over the stored resolved id for every typed turn; turning it off
 *    resumes the last resolved id. Choosing a clarification candidate always
 *    sends that candidate's id -- it is the student's explicit answer.
 *  - Scope safety ("explain, never solve") is enforced in the backend's
 *    system prompt. This page adds no filtering and no solve-style shortcuts.
 *  - No "Stop" control: aborting the fetch has not been shown to stop the
 *    backend's generation. The fetch is only aborted on unmount.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { ApiError, getSession, streamStudentChat } from "@/lib/api";
import type {
  Citation,
  QuizAttemptOut,
  SessionRead,
  StudentChatClarificationEvent,
  StudentChatResolvedEvent,
} from "@/lib/api";
import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";
import { CitationList } from "@/components/citation-list";
import { QuizCard } from "@/components/quiz-views";
import { FormError, Loading, SmallButton } from "@/components/ui";
import { QuizScopeForm } from "./quiz-scope-form";

interface ChatEntry {
  kind: "chat";
  id: number;
  question: string;
  resolved: StudentChatResolvedEvent | null;
  citations: Citation[] | null;
  answer: string;
  clarification: StudentChatClarificationEvent | null;
  /** Backend `error` event or transport failure -- never rendered as assistant prose. */
  error: string | null;
  /** The stream closed without `done` / `clarification_needed` / `error`. */
  incomplete: boolean;
  streaming: boolean;
  /** Kept in memory only; not displayed (it carries nothing a student can act on). */
  threadId: number | null;
  unknownEventCount: number;
}

interface QuizEntry {
  kind: "quiz";
  id: number;
  attempt: QuizAttemptOut;
}

type Entry = ChatEntry | QuizEntry;

export const TRANSCRIPT_NOTICE =
  "Your conversation for this session continues on the server, but this on-screen transcript starts fresh each visit — earlier messages aren't shown here.";

export function StudentChat({ sessionId }: { sessionId: number }) {
  return (
    <RequireAuth role="student">
      <SignedInShell>
        <StudentChatBody sessionId={sessionId} />
      </SignedInShell>
    </RequireAuth>
  );
}

function StudentChatBody({ sessionId }: { sessionId: number }) {
  const [session, setSession] = useState<SessionRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [entries, setEntries] = useState<Entry[]>([]);
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [quizFormOpen, setQuizFormOpen] = useState(false);
  const [searchAll, setSearchAll] = useState(false);

  const nextId = useRef(1);
  /** `current_session_id` for the next turn (A2). */
  const nextSessionId = useRef<number>(sessionId);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    getSession(sessionId).then(
      (s) => {
        if (cancelled) return;
        setSession(s);
        setLoadError(null);
      },
      (error: unknown) => {
        if (cancelled) return;
        setLoadError(error instanceof ApiError ? error.detail : "Could not load this session.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [entries]);

  const patchChat = useCallback(
    (id: number, change: Partial<Omit<ChatEntry, "kind" | "id">>) => {
      setEntries((current) =>
        current.map((e) => (e.kind === "chat" && e.id === id ? { ...e, ...change } : e)),
      );
    },
    [],
  );

  const send = useCallback(
    async (text: string, currentSessionId: number | null) => {
      const id = nextId.current++;
      setEntries((current) => [
        ...current,
        {
          kind: "chat",
          id,
          question: text,
          resolved: null,
          citations: null,
          answer: "",
          clarification: null,
          error: null,
          incomplete: false,
          streaming: true,
          threadId: null,
          unknownEventCount: 0,
        },
      ]);
      setStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const result = await streamStudentChat(
          { question: text, currentSessionId },
          {
            onResolved: (event) => {
              nextSessionId.current = event.session_id;
              patchChat(id, { resolved: event });
            },
            onCitations: (event) => patchChat(id, { citations: event.citations }),
            onToken: (event) =>
              setEntries((current) =>
                current.map((e) =>
                  e.kind === "chat" && e.id === id ? { ...e, answer: e.answer + event.text } : e,
                ),
              ),
            onDone: (event) => patchChat(id, { threadId: event.thread_id }),
            onError: (event) => patchChat(id, { error: event.message }),
            onClarification: (event) => patchChat(id, { clarification: event }),
          },
          { signal: controller.signal },
        );
        patchChat(id, {
          incomplete: result.outcome === "incomplete",
          unknownEventCount: result.unknownEventCount,
        });
      } catch (error) {
        if (!controller.signal.aborted) {
          patchChat(id, {
            error: error instanceof ApiError ? error.detail : "The course assistant could not be reached.",
            incomplete: true,
          });
        }
      } finally {
        patchChat(id, { streaming: false });
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [patchChat],
  );

  function submitQuestion() {
    // Sent exactly as typed; only a blank message is refused.
    if (streaming || !question.trim()) return;
    const text = question;
    setQuestion("");
    void send(text, searchAll ? null : nextSessionId.current);
  }

  if (loadError) {
    return (
      <>
        <BackLink sessionId={sessionId} />
        <FormError>{loadError}</FormError>
      </>
    );
  }
  if (!session) return <Loading>Loading session…</Loading>;

  return (
    <div className="space-y-6">
      <div>
        <BackLink sessionId={sessionId} />
        <h1 className="mt-2 text-2xl font-semibold text-slate-900">
          Ask about {session.title}
        </h1>
        <p className="mt-1 text-sm text-slate-600">{TRANSCRIPT_NOTICE}</p>
        <p className="mt-1 text-sm">
          <Link href="/student/quizzes" className="text-slate-500 underline">
            Practice quiz history
          </Link>
        </p>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white">
        <div
          ref={logRef}
          className="max-h-[36rem] min-h-[12rem] space-y-4 overflow-y-auto px-4 py-4"
          aria-live="polite"
          aria-busy={streaming}
          data-testid="chat-transcript"
        >
          {entries.length === 0 ? (
            <p className="text-sm text-slate-500">
              Ask a question about this session&apos;s lectures and assignment material.
            </p>
          ) : (
            entries.map((entry) =>
              entry.kind === "chat" ? (
                <ChatTurn
                  key={entry.id}
                  entry={entry}
                  pageSessionId={sessionId}
                  disabled={streaming}
                  onChooseCandidate={(candidateId) => void send(entry.question, candidateId)}
                />
              ) : (
                <QuizCard
                  key={entry.id}
                  attempt={entry.attempt}
                  resultFooter={
                    <>
                      This result stays in this transcript for this visit only.{" "}
                      <Link href="/student/quizzes" className="underline">
                        Your quiz history
                      </Link>{" "}
                      keeps the permanent record.
                    </>
                  }
                />
              ),
            )
          )}
        </div>

        <div className="space-y-3 border-t border-slate-200 p-4">
          {quizFormOpen ? (
            <QuizScopeForm
              session={session}
              onCancel={() => setQuizFormOpen(false)}
              onGenerated={(attempt) => {
                setEntries((current) => [
                  ...current,
                  { kind: "quiz", id: nextId.current++, attempt },
                ]);
                setQuizFormOpen(false);
              }}
            />
          ) : (
            <SmallButton onClick={() => setQuizFormOpen(true)}>Quiz me</SmallButton>
          )}

          <div>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={searchAll}
                onChange={(e) => setSearchAll(e.target.checked)}
              />
              Search all sessions, not only this one
            </label>
            <p className="mt-1 text-xs text-slate-500">
              When on, your question is matched against course material from every session
              instead of only {session.title}, and the assistant may ask which session you mean.
              It stays on until you turn it off, and resets when you leave this page.
            </p>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitQuestion();
            }}
            noValidate
          >
            <label htmlFor="student-chat-question" className="sr-only">
              Your question
            </label>
            <textarea
              id="student-chat-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submitQuestion();
                }
              }}
              disabled={streaming}
              rows={3}
              placeholder="Ask a question (Enter to send, Shift+Enter for a new line)"
              className="mb-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-slate-400 disabled:bg-slate-100"
            />
            <button
              type="submit"
              disabled={streaming}
              className="w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              {streaming ? "Answering…" : "Send"}
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}

function BackLink({ sessionId }: { sessionId: number }) {
  return (
    <Link href={`/student/sessions/${sessionId}`} className="text-sm text-slate-500 underline">
      ← Back to session
    </Link>
  );
}

function ChatTurn({
  entry,
  pageSessionId,
  disabled,
  onChooseCandidate,
}: {
  entry: ChatEntry;
  pageSessionId: number;
  disabled: boolean;
  onChooseCandidate: (sessionId: number) => void;
}) {
  const elsewhere = entry.resolved !== null && entry.resolved.session_id !== pageSessionId;

  return (
    <div className="space-y-2" data-testid="chat-turn">
      <p className="text-right">
        <span
          className="inline-block whitespace-pre-wrap rounded-lg bg-slate-900 px-3 py-1.5 text-left text-sm text-white"
          data-testid="user-message"
        >
          {entry.question}
        </span>
      </p>

      <div className="rounded-lg bg-slate-50 px-3 py-2">
        {elsewhere && entry.resolved ? (
          <p
            className="mb-2 rounded-md border border-sky-200 bg-sky-50 px-2 py-1 text-xs text-sky-900"
            data-testid="session-banner"
          >
            Answering from {entry.resolved.session_title} ({entry.resolved.resolution})
          </p>
        ) : null}

        {entry.clarification ? (
          <div>
            <p className="text-sm text-slate-800">{entry.clarification.message}</p>
            {entry.clarification.candidates.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {entry.clarification.candidates.map((candidate) => (
                  <SmallButton
                    key={candidate.session_id}
                    onClick={() => onChooseCandidate(candidate.session_id)}
                    disabled={disabled}
                  >
                    {candidate.session_title} (similarity {candidate.best_similarity})
                  </SmallButton>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {entry.answer ? (
          <p className="whitespace-pre-wrap text-sm text-slate-800" data-testid="assistant-message">
            {entry.answer}
          </p>
        ) : null}

        {entry.streaming && !entry.answer && !entry.clarification && !entry.error ? (
          <p className="text-xs text-slate-500">Thinking…</p>
        ) : null}

        {entry.error ? (
          <p role="alert" className="text-sm text-red-700">
            {entry.error}
            {entry.incomplete ? " This answer is incomplete." : ""}
          </p>
        ) : entry.incomplete ? (
          <p role="alert" className="text-sm text-red-700">
            The answer stream ended before it finished — this answer is incomplete.
          </p>
        ) : null}

        {entry.citations ? <CitationList citations={entry.citations} /> : null}

        {entry.unknownEventCount > 0 ? (
          <p className="mt-1 text-[11px] text-slate-400">
            {entry.unknownEventCount} unrecognised stream event
            {entry.unknownEventCount === 1 ? "" : "s"} ignored.
          </p>
        ) : null}
      </div>
    </div>
  );
}
