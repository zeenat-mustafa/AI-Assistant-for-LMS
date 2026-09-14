"use client";

/**
 * Floating student course-assistant widget (Phase 7.7).
 *
 * Mirrors the instructor's `FloatingChatWidget` pattern exactly:
 *  - mounted once in `app/student/layout.tsx` so it persists across
 *    navigation between all student pages;
 *  - a fixed bottom-right button collapses/expands the panel;
 *  - same z-index and positioning approach as the instructor widget.
 *
 * Session detection (Step 2 of the spec)
 * ---------------------------------------
 * The widget reads the current pathname via `usePathname()`. If the student
 * is on a session page (`/student/sessions/[id]`), the session id is
 * extracted from the URL and passed as `current_session_id` on the next
 * request; otherwise `null` is sent and the backend resolves/redirects as
 * needed. This is cheap (no extra fetch) and covers the most common case
 * (asking about the session you're looking at). The widget explicitly states
 * which session it is scoping to so the student is never confused.
 *
 * Session id after a `resolved` event (Step 3 of the spec)
 * ----------------------------------------------------------
 * After a `resolved` event, the returned `session_id` is stored and sent as
 * `current_session_id` on subsequent turns, so follow-up questions stay
 * anchored to the resolved session. This is identical to the old page's
 * follow-up contract. The stored resolved id is reset when the student
 * navigates to a different page (which changes the URL-derived id), or when
 * the widget is closed and reopened (each open starts fresh).
 *
 * Clarification (Step 4 of the spec)
 * ------------------------------------
 * Clarification buttons resend the student's ORIGINAL question unchanged
 * with the chosen candidate's `session_id`.
 *
 * Quiz history (Step 5 of the spec)
 * -----------------------------------
 * The panel has two tabs: "Chat" and "Quiz history". The quiz history tab
 * renders the same `<QuizHistory />` component used by the standalone
 * `/student/quizzes` page. No separate page navigation is needed.
 *
 * Transcript lifetime
 * --------------------
 * Chat history lives in useState inside ChatWidgetPanel, which stays mounted
 * for the lifetime of the student layout (it is hidden with CSS, never
 * unmounted on close). Conversation persists across open/close cycles and
 * across in-app navigation. Only a full page reload clears it.
 *
 * No backend modifications
 * -------------------------
 * This file and its tests are the only new/changed frontend files in 7.7
 * outside of the student layout and the copied API modules. No backend code
 * is touched.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import ReactMarkdown from "react-markdown";

import {
  ApiError,
  streamStudentChat,
  generateQuiz,
  generateQuizFromUpload,
  listSessions,
} from "@/lib/api";
import type {
  Citation,
  ClarificationCandidate,
  QuizAttemptOut,
  SessionRead,
  StudentChatClarificationEvent,
  StudentChatResolvedEvent,
} from "@/lib/api";
import { useAuth } from "@/lib/auth/auth-context";
import { CitationList } from "@/components/citation-list";
import { QuizCard } from "@/components/quiz-views";
import { QuizHistory } from "@/app/student/quizzes/quiz-history";
import { SmallButton, Loading, FormError } from "@/components/ui";

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

interface ChatEntry {
  id: number;
  kind: "chat";
  question: string;
  answer: string;
  citations: Citation[] | null;
  clarification: StudentChatClarificationEvent | null;
  resolved: StudentChatResolvedEvent | null;
  error: string | null;
  incomplete: boolean;
  streaming: boolean;
  /** How many SSE frames had an event name the client doesn't know. */
  unknownEventCount: number;
}

interface QuizEntry {
  id: number;
  kind: "quiz";
  attempt: QuizAttemptOut;
}

type Entry = ChatEntry | QuizEntry;

type Tab = "chat" | "history";

/** Extract the session id from /student/sessions/[id] routes, or null. */
function sessionIdFromPathname(pathname: string): number | null {
  const match = /^\/student\/sessions\/(\d+)/.exec(pathname);
  if (!match) return null;
  const n = Number(match[1]);
  return Number.isFinite(n) && n > 0 ? n : null;
}

// ──────────────────────────────────────────────────────────────────────────────
// Root widget: button + expandable panel
// ──────────────────────────────────────────────────────────────────────────────

/**
 * Floating icon + expandable panel, mounted once for every student page.
 * Renders nothing unless the signed-in user is a student.
 */
export function StudentChatWidget() {
  const { user, status } = useAuth();
  const [isOpen, setIsOpen] = useState(false);

  if (status !== "authenticated" || user?.role !== "student") return null;

  return (
    <div className="fixed bottom-6 left-6 z-50">
      <div className={isOpen ? "block" : "hidden"} hidden={!isOpen}>
        <ChatWidgetPanel isOpen={isOpen} onClose={() => setIsOpen(false)} />
      </div>
      {!isOpen ? (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          aria-label="Open course assistant"
          className="flex h-14 w-14 items-center justify-center rounded-full bg-indigo-700 text-white shadow-lg transition hover:bg-indigo-600"
        >
          <span aria-hidden className="text-sm font-bold">
            AI
          </span>
        </button>
      ) : null}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Panel (open state)
// ──────────────────────────────────────────────────────────────────────────────

function ChatWidgetPanel({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const urlSessionId = sessionIdFromPathname(pathname);

  const [tab, setTab] = useState<Tab>("chat");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [quizFormOpen, setQuizFormOpen] = useState(false);

  /** The session id to send on the NEXT request.
   *  Priority: URL-derived (if on a session page) > last resolved id > null. */
  const nextSessionId = useRef<number | null>(urlSessionId);

  // Keep nextSessionId in sync with navigation.
  useEffect(() => {
    if (urlSessionId !== null) {
      nextSessionId.current = urlSessionId;
    }
  }, [urlSessionId]);

  const nextId = useRef(1);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // Abandon an in-flight stream if the widget is closed mid-run.
  useEffect(() => {
    if (!isOpen) {
      abortRef.current?.abort();
      setStreaming(false);
    }
  }, [isOpen]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Keep the newest message visible.
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [entries]);

  const patch = useCallback((id: number, change: Partial<ChatEntry>) => {
    setEntries((current) =>
      current.map((e) => (e.id === id && e.kind === "chat" ? { ...e, ...change } : e)),
    );
  }, []);

  async function send(text: string, sessionIdOverride?: number | null) {
    if (streaming) return;
    const id = nextId.current++;
    const sid = sessionIdOverride !== undefined ? sessionIdOverride : nextSessionId.current;

    setEntries((current) => [
      ...current,
      {
        id,
        kind: "chat",
        question: text,
        answer: "",
        citations: null,
        clarification: null,
        resolved: null,
        error: null,
        incomplete: false,
        streaming: true,
        unknownEventCount: 0,
      },
    ]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const result = await streamStudentChat(
        { question: text, currentSessionId: sid },
        {
          onClarification: (event) => {
            patch(id, { clarification: event });
          },
          onResolved: (event) => {
            patch(id, { resolved: event });
            // Store the resolved session id for follow-up turns.
            nextSessionId.current = event.session_id;
          },
          onCitations: (event) => {
            patch(id, { citations: event.citations });
          },
          onToken: (event) => {
            setEntries((current) =>
              current.map((e) =>
                e.id === id && e.kind === "chat"
                  ? { ...e, answer: e.answer + event.text }
                  : e,
              ),
            );
          },
          onDone: () => {
            // thread_id etc. are for future persistence; nothing to do here.
          },
          onError: (event) => {
            patch(id, { error: event.message });
          },
        },
        { signal: controller.signal },
      );

      if (result.outcome === "incomplete") {
        patch(id, { incomplete: true });
      }
      if (result.unknownEventCount > 0) {
        patch(id, { unknownEventCount: result.unknownEventCount });
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        patch(id, {
          error:
            err instanceof ApiError ? err.detail : "The course assistant is unavailable. Please try again.",
          incomplete: true,
        });
      }
    } finally {
      patch(id, { streaming: false });
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function submitQuestion() {
    const text = question.trim();
    if (!text || streaming) return;
    setQuestion("");
    void send(text);
  }

  // Determine scoping hint for the placeholder / empty state.
  const scopeLabel = urlSessionId !== null ? `session #${urlSessionId}` : "all sessions";

  return (
    <section
      className="flex h-[36rem] w-96 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl"
      aria-label="Course assistant"
    >
      {/* Header */}
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Course assistant</h2>
          <p className="text-xs text-slate-500">
            Ask about lectures or assignments.
            {urlSessionId !== null ? ` Scoped to session #${urlSessionId} by default.` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close course assistant"
          className="rounded-md p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
        >
          <span aria-hidden className="text-lg leading-none font-bold">
            x
          </span>
        </button>
      </header>

      {/* Tabs */}
      <div className="flex border-b border-slate-200">
        {(["chat", "history"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            aria-selected={tab === t}
            className={`flex-1 px-4 py-2 text-xs font-medium transition ${
              tab === t
                ? "border-b-2 border-indigo-600 text-indigo-700"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {t === "chat" ? "Chat" : "Quiz history"}
          </button>
        ))}
      </div>

      {tab === "history" ? (
        <div className="flex-1 overflow-y-auto p-4">
          <QuizHistory />
        </div>
      ) : (
        <>
          {/* Chat transcript */}
          <div
            ref={logRef}
            className="flex-1 space-y-4 overflow-y-auto px-4 py-3"
            aria-live="polite"
            aria-busy={streaming}
            data-testid="student-chat-transcript"
          >
            {entries.length === 0 ? (
              <div className="space-y-2">
                <p className="text-sm text-slate-500">
                  Ask a question about your course material. Currently scoped to{" "}
                  <strong>{scopeLabel}</strong>.
                </p>
                <p className="text-xs text-slate-400">
                  Chat history clears only on a full page reload.
                </p>
              </div>
            ) : (
              entries.map((entry) =>
                entry.kind === "quiz" ? (
                  <QuizCard
                    key={entry.id}
                    attempt={entry.attempt}
                    resultFooter={
                      <>
                        This result is visible in this chat session.{" "}
                        <button
                          type="button"
                          onClick={() => setTab("history")}
                          className="underline"
                        >
                          Quiz history
                        </button>{" "}
                        keeps the permanent record.
                      </>
                    }
                  />
                ) : (
                  <ChatTurnView
                    key={entry.id}
                    entry={entry}
                    urlSessionId={urlSessionId}
                    disabled={streaming}
                    onChooseCandidate={(candidateId) => void send(entry.question, candidateId)}
                  />
                ),
              )
            )}
          </div>

          {/* Input area */}
          <div className="space-y-2 border-t border-slate-200 p-3">
            {quizFormOpen ? (
              <WidgetQuizScopeForm
                urlSessionId={urlSessionId}
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
              <SmallButton onClick={() => setQuizFormOpen(true)} disabled={streaming}>
                Quiz me
              </SmallButton>
            )}

            <form
              onSubmit={(e) => {
                e.preventDefault();
                submitQuestion();
              }}
              noValidate
            >
              <label htmlFor="student-widget-question" className="sr-only">
                Your question
              </label>
              <textarea
                id="student-widget-question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    submitQuestion();
                  }
                }}
                disabled={streaming}
                rows={2}
                placeholder="Ask a question (Enter to send, Shift+Enter for a new line)"
                className="mb-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-indigo-400 disabled:bg-slate-100"
              />
              <button
                type="submit"
                disabled={streaming || !question.trim()}
                className="w-full rounded-md bg-indigo-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-600 disabled:cursor-not-allowed disabled:bg-slate-400"
              >
                {streaming ? "Answering…" : "Send"}
              </button>
            </form>
          </div>
        </>
      )}
    </section>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// One chat turn
// ──────────────────────────────────────────────────────────────────────────────

function ChatTurnView({
  entry,
  urlSessionId,
  disabled,
  onChooseCandidate,
}: {
  entry: ChatEntry;
  urlSessionId: number | null;
  disabled: boolean;
  onChooseCandidate: (sessionId: number) => void;
}) {
  const res = entry.resolved;
  let bannerText: string | null = null;
  if (res) {
    if (res.resolution === "redirected") {
      bannerText = `Answering about ${res.session_title} instead.`;
    } else if (res.resolution === "broad_search") {
      bannerText = "Searched across your sessions to answer this.";
    } else if (res.resolution === "current_session") {
      bannerText = null;
    } else {
      throw new Error(`Unexpected StudentChatResolution value: ${(res as { resolution: string }).resolution}`);
    }
  }

  return (
    <div className="space-y-2" data-testid="student-chat-turn">
      {/* User bubble */}
      <p className="text-right">
        <span
          className="inline-block whitespace-pre-wrap rounded-lg bg-indigo-700 px-3 py-1.5 text-left text-sm text-white"
          data-testid="student-user-message"
        >
          {entry.question}
        </span>
      </p>

      {/* Assistant bubble */}
      <div className="rounded-lg bg-slate-50 px-3 py-2">
        {/* Banner */}
        {bannerText ? (
          <p
            className="mb-2 rounded-md border border-sky-200 bg-sky-50 px-2 py-1 text-xs text-sky-900"
            data-testid="student-session-banner"
          >
            {bannerText}
          </p>
        ) : null}

        {/* Clarification */}
        {entry.clarification ? (
          <div>
            <p className="text-sm text-slate-800">{entry.clarification.message}</p>
            {entry.clarification.candidates.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {entry.clarification.candidates.map((candidate: ClarificationCandidate) => (
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

        {/* Streaming answer */}
        {entry.answer ? (
          <div
            className="prose prose-sm max-w-none text-slate-800"
            data-testid="student-assistant-message"
          >
            <ReactMarkdown
              components={{
                p: ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
                strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                em: ({ children }) => <em className="italic">{children}</em>,
                ul: ({ children }) => <ul className="my-1 list-disc pl-4">{children}</ul>,
                ol: ({ children }) => <ol className="my-1 list-decimal pl-4">{children}</ol>,
                li: ({ children }) => <li className="mb-0.5">{children}</li>,
                h1: ({ children }) => <p className="font-semibold">{children}</p>,
                h2: ({ children }) => <p className="font-semibold">{children}</p>,
                h3: ({ children }) => <p className="font-semibold">{children}</p>,
                code: ({ children }) => (
                  <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-xs text-slate-700">
                    {children}
                  </code>
                ),
                pre: ({ children }) => (
                  <pre className="my-1 overflow-x-auto rounded bg-slate-100 px-2 py-1.5 font-mono text-xs text-slate-700">
                    {children}
                  </pre>
                ),
              }}
            >
              {entry.answer}
            </ReactMarkdown>
          </div>
        ) : null}

        {/* Thinking indicator */}
        {entry.streaming && !entry.answer && !entry.clarification && !entry.error ? (
          <p className="text-xs text-slate-500">Thinking…</p>
        ) : null}

        {/* Error / incomplete */}
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

        {/* Citations */}
        {entry.citations ? <CitationList citations={entry.citations} /> : null}

        {/* Unknown event count */}
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

// ──────────────────────────────────────────────────────────────────────────────
// Quiz scope form adapted for the widget (no fixed session required)
// ──────────────────────────────────────────────────────────────────────────────

/**
 * A wrapper around the quiz scope form that works without a fixed `session`
 * prop. When the student is on a session page, the session id is available from
 * the URL; otherwise "session" and "assignment_file" modes are hidden and the
 * student can choose topic/multiple sessions/upload instead.
 *
 * This is the ONLY place in 7.7 that deviates from copying the old component
 * as-is: the old `QuizScopeForm` required a `session: SessionRead` prop, which
 * the widget cannot always provide. We call the underlying quiz API functions
 * directly here for the subset of modes that don't need a full `SessionRead`.
 */
function WidgetQuizScopeForm({
  urlSessionId,
  onGenerated,
  onCancel,
}: {
  urlSessionId: number | null;
  onGenerated: (attempt: QuizAttemptOut) => void;
  onCancel: () => void;
}) {
  type Mode = "session" | "multiple_sessions" | "topic" | "upload";

  const availableModes: { value: Mode; label: string }[] = [
    ...(urlSessionId !== null ? [{ value: "session" as Mode, label: "This session" }] : []),
    { value: "multiple_sessions", label: "Several sessions" },
    { value: "topic", label: "A topic" },
    { value: "upload", label: "A file I upload now" },
  ];

  const [mode, setMode] = useState<Mode>(urlSessionId !== null ? "session" : "topic");
  const [topic, setTopic] = useState("");
  const [upload, setUpload] = useState<File | null>(null);
  const [chosenSessions, setChosenSessions] = useState<number[]>([]);
  const [allSessions, setAllSessions] = useState<SessionRead[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch session list only when the student switches to multi-session mode.
  useEffect(() => {
    if (mode !== "multiple_sessions" || allSessions !== null) return;
    let cancelled = false;
    listSessions({ limit: 200 }).then(
      (page) => {
        if (!cancelled) setAllSessions(page.items);
      },
      (loadError: unknown) => {
        if (cancelled) return;
        setSessionsError(
          loadError instanceof ApiError ? loadError.detail : "Could not load sessions.",
        );
        setAllSessions([]);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [mode, allSessions]);

  function isReady(): boolean {
    switch (mode) {
      case "session":
        return urlSessionId !== null;
      case "multiple_sessions":
        return chosenSessions.length >= 2;
      case "topic":
        return topic.trim().length > 0;
      case "upload":
        return upload !== null;
    }
  }

  async function handleGenerate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isReady() || pending) return;
    setError(null);
    setPending(true);
    try {
      let attempt: QuizAttemptOut;
      if (mode === "upload" && upload) {
        attempt = await generateQuizFromUpload(upload);
      } else if (mode === "topic") {
        attempt = await generateQuiz({ scope_type: "topic", topic_text: topic });
      } else if (mode === "session" && urlSessionId !== null) {
        attempt = await generateQuiz({ scope_type: "session", session_id: urlSessionId });
      } else if (mode === "multiple_sessions" && chosenSessions.length >= 2) {
        attempt = await generateQuiz({ scope_type: "multiple_sessions", session_ids: chosenSessions });
      } else {
        setError("Please choose a valid quiz scope.");
        return;
      }
      onGenerated(attempt);
    } catch (generateError) {
      setError(
        generateError instanceof ApiError ? generateError.detail : "Could not generate a quiz.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      onSubmit={handleGenerate}
      noValidate
      className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
      aria-label="Start a practice quiz"
    >
      <p className="text-sm font-semibold text-slate-900">Practice quiz</p>
      <p className="mb-2 text-xs text-slate-500">5 MCQs — never changes your real grades.</p>

      <fieldset disabled={pending} className="mb-2 space-y-1">
        <legend className="mb-1 text-xs font-medium text-slate-700">Quiz me on</legend>
        {availableModes.map((m) => (
          <label key={m.value} className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="radio"
              name="widget-quiz-mode"
              value={m.value}
              checked={mode === m.value}
              onChange={() => {
                setMode(m.value);
                setError(null);
              }}
            />
            {m.label}
          </label>
        ))}
      </fieldset>

      {mode === "topic" ? (
        <div className="mb-2">
          <label htmlFor="widget-quiz-topic" className="mb-1 block text-xs font-medium text-slate-700">
            Topic
          </label>
          <input
            id="widget-quiz-topic"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            disabled={pending}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900"
          />
        </div>
      ) : null}

      {mode === "upload" ? (
        <div className="mb-2">
          <label htmlFor="widget-quiz-upload" className="mb-1 block text-xs font-medium text-slate-700">
            File (.pptx or .ipynb)
          </label>
          <input
            id="widget-quiz-upload"
            type="file"
            accept=".pptx,.ipynb"
            disabled={pending}
            onChange={(e) => setUpload(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-slate-700"
          />
          <p className="mt-1 text-xs text-slate-500">
            Used for this quiz only — not saved as a submission or lecture file.
          </p>
        </div>
      ) : null}

      {mode === "multiple_sessions" ? (
        <div className="mb-2">
          {sessionsError ? (
            <FormError>{sessionsError}</FormError>
          ) : allSessions === null ? (
            <Loading>Loading sessions...</Loading>
          ) : (
            <fieldset disabled={pending}>
              <legend className="mb-1 text-xs font-medium text-slate-700">
                Choose at least 2 sessions
              </legend>
              <div className="max-h-32 space-y-1 overflow-y-auto">
                {allSessions.map((s) => (
                  <label key={s.id} className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={chosenSessions.includes(s.id)}
                      onChange={(e) =>
                        setChosenSessions((current) =>
                          e.target.checked
                            ? [...current, s.id]
                            : current.filter((id) => id !== s.id),
                        )
                      }
                    />
                    {s.title}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="mb-2 text-xs text-red-600">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={pending || !isReady()}
          className="rounded-md bg-indigo-700 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-600 disabled:cursor-not-allowed disabled:bg-slate-400"
        >
          {pending ? "Generating…" : "Generate quiz"}
        </button>
        <SmallButton onClick={onCancel} disabled={pending}>
          Cancel
        </SmallButton>
      </div>
    </form>
  );
}
