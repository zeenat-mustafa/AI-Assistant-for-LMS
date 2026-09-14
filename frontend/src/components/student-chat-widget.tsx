"use client";

/**
 * Floating student course-assistant widget (Phase 7.8 redesign).
 *
 * All chat logic, quiz logic, and API calls are unchanged from 7.7.
 * This file is a visual/layout-only rewrite:
 *  - Wider panel (420px), more breathing room between messages.
 *  - User bubbles: right-aligned, primary-600 background.
 *  - Assistant bubbles: left-aligned, white card with border — not a gray box.
 *  - Citations: small, muted, clearly secondary.
 *  - Quiz cards use the new quiz-views.tsx layout (card per question, etc).
 *  - Removed clutter: "Chat history clears only on a full page reload." line
 *    (behavior is correct, no need to narrate it every time the panel is
 *    empty), unknownEventCount debug line (never meaningful to students).
 *
 * Session detection, session id tracking, streaming, quiz generation,
 * multi-session picker, and clarification flow are all byte-identical to 7.7.
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

// ── Types ─────────────────────────────────────────────────────────────────────

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
  unknownEventCount: number;
}

interface QuizEntry {
  id: number;
  kind: "quiz";
  attempt: QuizAttemptOut;
}

type Entry = ChatEntry | QuizEntry;
type Tab = "chat" | "history";

function sessionIdFromPathname(pathname: string): number | null {
  const match = /^\/student\/sessions\/(\d+)/.exec(pathname);
  if (!match) return null;
  const n = Number(match[1]);
  return Number.isFinite(n) && n > 0 ? n : null;
}

// ── Root widget ───────────────────────────────────────────────────────────────

export function StudentChatWidget() {
  const { user, status } = useAuth();
  const [isOpen, setIsOpen] = useState(false);

  if (status !== "authenticated" || user?.role !== "student") return null;

  return (
    <div className="fixed bottom-6 right-6 z-50">
      {/* Panel — CSS hidden, never unmounted, so conversation persists */}
      <div className={isOpen ? "block" : "hidden"} hidden={!isOpen}>
        <ChatWidgetPanel isOpen={isOpen} onClose={() => setIsOpen(false)} />
      </div>

      {/* Open button */}
      {!isOpen ? (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          aria-label="Open course assistant"
          className="flex h-14 w-14 items-center justify-center rounded-full bg-primary-600 text-white shadow-lg transition hover:bg-primary-700"
        >
          <span aria-hidden className="text-xs font-bold tracking-tight">AI</span>
        </button>
      ) : null}
    </div>
  );
}

// ── Panel ─────────────────────────────────────────────────────────────────────

function ChatWidgetPanel({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const urlSessionId = sessionIdFromPathname(pathname);

  const [tab, setTab] = useState<Tab>("chat");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [quizFormOpen, setQuizFormOpen] = useState(false);

  const nextSessionId = useRef<number | null>(urlSessionId);

  useEffect(() => {
    if (urlSessionId !== null) {
      nextSessionId.current = urlSessionId;
    }
  }, [urlSessionId]);

  const nextId = useRef(1);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const lastEntryRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) {
      abortRef.current?.abort();
      setStreaming(false);
    }
  }, [isOpen]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Scroll the TOP of the newest entry into view when a new entry is added.
  // We track the entry count so we only scroll on additions, not on streaming
  // token patches (which update entries too but shouldn't move the viewport).
  const prevEntryCount = useRef(0);
  useEffect(() => {
    const newCount = entries.length;
    if (newCount > prevEntryCount.current && lastEntryRef.current) {
      lastEntryRef.current.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
    prevEntryCount.current = newCount;
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
          onClarification: (event) => patch(id, { clarification: event }),
          onResolved: (event) => {
            patch(id, { resolved: event });
            nextSessionId.current = event.session_id;
          },
          onCitations: (event) => patch(id, { citations: event.citations }),
          onToken: (event) => {
            setEntries((current) =>
              current.map((e) =>
                e.id === id && e.kind === "chat"
                  ? { ...e, answer: e.answer + event.text }
                  : e,
              ),
            );
          },
          onDone: () => {},
          onError: (event) => patch(id, { error: event.message }),
        },
        { signal: controller.signal },
      );

      if (result.outcome === "incomplete") patch(id, { incomplete: true });
      if (result.unknownEventCount > 0) patch(id, { unknownEventCount: result.unknownEventCount });
    } catch (err) {
      if (!controller.signal.aborted) {
        patch(id, {
          error: err instanceof ApiError
            ? err.detail
            : "The course assistant is unavailable. Please try again.",
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

  const scopeLabel = urlSessionId !== null ? `session #${urlSessionId}` : "all sessions";

  return (
    <section
      className="flex h-[40rem] w-[420px] flex-col overflow-hidden rounded-xl border border-neutral-200 bg-neutral-50 shadow-2xl"
      aria-label="Course assistant"
    >
      {/* Header */}
      <header className="flex shrink-0 items-center justify-between border-b border-neutral-200 bg-white px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-neutral-900">Course assistant</h2>
          <p className="text-xs text-neutral-500">Ask about lectures or assignments.</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close course assistant"
          className="ml-2 rounded p-1 text-neutral-400 transition hover:bg-neutral-100 hover:text-neutral-700"
        >
          <span aria-hidden className="text-base font-bold leading-none">x</span>
        </button>
      </header>

      {/* Tabs */}
      <div className="flex shrink-0 border-b border-neutral-200 bg-white">
        {(["chat", "history"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            aria-selected={tab === t}
            className={`flex-1 px-4 py-2 text-xs font-medium transition ${
              tab === t
                ? "border-b-2 border-primary-600 text-primary-700"
                : "text-neutral-500 hover:text-neutral-700"
            }`}
          >
            {t === "chat" ? "Chat" : "Quiz history"}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "history" ? (
        <div className="flex-1 overflow-y-auto p-4">
          <QuizHistory />
        </div>
      ) : (
        <>
          {/* Transcript */}
          <div
            ref={logRef}
            className="flex-1 space-y-5 overflow-y-auto px-4 py-4"
            aria-live="polite"
            aria-busy={streaming}
            data-testid="student-chat-transcript"
          >
            {entries.length === 0 ? (
              <p className="text-sm text-neutral-500">
                Ask a question about your course material.{" "}
                Currently scoped to <strong className="text-neutral-700">{scopeLabel}</strong>.
              </p>
            ) : (
              entries.map((entry, index) => {
                const isLast = index === entries.length - 1;
                return (
                  <div key={entry.id} ref={isLast ? lastEntryRef : undefined}>
                    {entry.kind === "quiz" ? (
                      <QuizCard
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
                        entry={entry}
                        disabled={streaming}
                        onChooseCandidate={(candidateId) => void send(entry.question, candidateId)}
                      />
                    )}
                  </div>
                );
              })
            )}
          </div>

          {/* Input area */}
          <div className="shrink-0 border-t border-neutral-200 bg-white p-3 space-y-2">
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
              onSubmit={(e) => { e.preventDefault(); submitQuestion(); }}
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
                placeholder="Ask a question (Enter to send, Shift+Enter for new line)"
                className="lms-input mb-2 resize-none"
              />
              <button
                type="submit"
                disabled={streaming || !question.trim()}
                className="lms-btn-primary w-full"
              >
                {streaming ? "Answering..." : "Send"}
              </button>
            </form>
          </div>
        </>
      )}
    </section>
  );
}

// ── One chat turn ─────────────────────────────────────────────────────────────

function ChatTurnView({
  entry,
  disabled,
  onChooseCandidate,
}: {
  entry: ChatEntry;
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
      {/* User bubble — right aligned */}
      <div className="flex justify-end">
        <span
          className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-primary-600 px-3.5 py-2 text-sm text-white"
          data-testid="student-user-message"
        >
          {entry.question}
        </span>
      </div>

      {/* Assistant bubble — left aligned */}
      <div className="flex justify-start">
        <div className="max-w-[90%] rounded-2xl rounded-tl-sm border border-neutral-200 bg-white px-3.5 py-2.5 shadow-sm">
          {/* Session redirect / broad search banner */}
          {bannerText ? (
            <p
              className="mb-2 rounded border border-primary-200 bg-primary-50 px-2 py-1 text-xs text-primary-800"
              data-testid="student-session-banner"
            >
              {bannerText}
            </p>
          ) : null}

          {/* Clarification */}
          {entry.clarification ? (
            <div>
              <p className="text-sm text-neutral-800">{entry.clarification.message}</p>
              {entry.clarification.candidates.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {entry.clarification.candidates.map((candidate: ClarificationCandidate) => (
                    <SmallButton
                      key={candidate.session_id}
                      onClick={() => onChooseCandidate(candidate.session_id)}
                      disabled={disabled}
                    >
                      {candidate.session_title} ({candidate.best_similarity})
                    </SmallButton>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {/* Answer — markdown rendered */}
          {entry.answer ? (
            <div
              className="prose prose-sm max-w-none text-neutral-800"
              data-testid="student-assistant-message"
            >
              <ReactMarkdown
                components={{
                  p: ({ children }) => <p className="mb-1 last:mb-0 text-sm">{children}</p>,
                  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                  em: ({ children }) => <em className="italic">{children}</em>,
                  ul: ({ children }) => <ul className="my-1 list-disc pl-4 text-sm">{children}</ul>,
                  ol: ({ children }) => <ol className="my-1 list-decimal pl-4 text-sm">{children}</ol>,
                  li: ({ children }) => <li className="mb-0.5">{children}</li>,
                  h1: ({ children }) => <p className="font-semibold text-sm">{children}</p>,
                  h2: ({ children }) => <p className="font-semibold text-sm">{children}</p>,
                  h3: ({ children }) => <p className="font-semibold text-sm">{children}</p>,
                  code: ({ children }) => (
                    <code className="rounded bg-neutral-100 px-1 py-0.5 font-mono text-xs text-neutral-700">
                      {children}
                    </code>
                  ),
                  pre: ({ children }) => (
                    <pre className="my-1 overflow-x-auto rounded bg-neutral-100 px-2 py-1.5 font-mono text-xs text-neutral-700">
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
            <p className="text-xs italic text-neutral-400">Thinking...</p>
          ) : null}

          {/* Error / incomplete */}
          {entry.error ? (
            <p role="alert" className="text-sm text-danger-700">
              {entry.error}
              {entry.incomplete ? " This answer is incomplete." : ""}
            </p>
          ) : entry.incomplete ? (
            <p role="alert" className="text-sm text-danger-700">
              The answer stream ended before it finished — this answer is incomplete.
            </p>
          ) : null}

          {/* Citations — muted, below the answer */}
          {entry.citations ? <CitationList citations={entry.citations} /> : null}
        </div>
      </div>
    </div>
  );
}

// ── Widget quiz scope form ────────────────────────────────────────────────────

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

  useEffect(() => {
    if (mode !== "multiple_sessions" || allSessions !== null) return;
    let cancelled = false;
    listSessions({ limit: 200 }).then(
      (page) => { if (!cancelled) setAllSessions(page.items); },
      (loadError: unknown) => {
        if (cancelled) return;
        setSessionsError(loadError instanceof ApiError ? loadError.detail : "Could not load sessions.");
        setAllSessions([]);
      },
    );
    return () => { cancelled = true; };
  }, [mode, allSessions]);

  function isReady(): boolean {
    switch (mode) {
      case "session": return urlSessionId !== null;
      case "multiple_sessions": return chosenSessions.length >= 2;
      case "topic": return topic.trim().length > 0;
      case "upload": return upload !== null;
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
      setError(generateError instanceof ApiError ? generateError.detail : "Could not generate a quiz.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      onSubmit={handleGenerate}
      noValidate
      className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-3"
      aria-label="Start a practice quiz"
    >
      <p className="text-sm font-semibold text-neutral-900">Practice quiz</p>
      <p className="mb-2 text-xs text-neutral-500">5 MCQs — never changes your real grades.</p>

      <fieldset disabled={pending} className="mb-2 space-y-1.5">
        <legend className="mb-1 text-xs font-medium text-neutral-700">Quiz me on</legend>
        {availableModes.map((m) => (
          <label key={m.value} className="flex items-center gap-2 text-sm text-neutral-700">
            <input
              type="radio"
              name="widget-quiz-mode"
              value={m.value}
              checked={mode === m.value}
              onChange={() => { setMode(m.value); setError(null); }}
              className="accent-primary-600"
            />
            {m.label}
          </label>
        ))}
      </fieldset>

      {mode === "topic" ? (
        <div className="mb-2">
          <label htmlFor="widget-quiz-topic" className="mb-1 block text-xs font-medium text-neutral-700">
            Topic
          </label>
          <input
            id="widget-quiz-topic"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            disabled={pending}
            className="lms-input text-sm py-1.5"
          />
        </div>
      ) : null}

      {mode === "upload" ? (
        <div className="mb-2">
          <label htmlFor="widget-quiz-upload" className="mb-1 block text-xs font-medium text-neutral-700">
            File (.pptx or .ipynb)
          </label>
          <input
            id="widget-quiz-upload"
            type="file"
            accept=".pptx,.ipynb"
            disabled={pending}
            onChange={(e) => setUpload(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-neutral-700"
          />
          <p className="mt-1 text-xs text-neutral-400">
            Used for this quiz only — not saved anywhere.
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
              <legend className="mb-1 text-xs font-medium text-neutral-700">
                Choose at least 2 sessions
              </legend>
              <div className="max-h-32 space-y-1 overflow-y-auto">
                {allSessions.map((s) => (
                  <label key={s.id} className="flex items-center gap-2 text-sm text-neutral-700">
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
                      className="accent-primary-600"
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
        <p role="alert" className="mb-2 text-xs text-danger-600">{error}</p>
      ) : null}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={pending || !isReady()}
          className="lms-btn-primary py-1.5 px-3 text-sm"
        >
          {pending ? "Generating..." : "Generate quiz"}
        </button>
        <SmallButton onClick={onCancel} disabled={pending}>Cancel</SmallButton>
      </div>
    </form>
  );
}
