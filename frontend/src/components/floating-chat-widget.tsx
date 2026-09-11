"use client";

/**
 * Floating grading-chat widget (Phase 5 post-fix, Feature 6).
 *
 * Replaces the old per-session embedded panel (formerly
 * app/instructor/sessions/[id]/grading-chat.tsx). Mounted once in
 * app/instructor/layout.tsx so it is available on every instructor page
 * without remounting on navigation between them.
 *
 * No session context, by design
 * ------------------------------
 * POST /chat/stream accepts exactly one field, `{"instruction": string}` --
 * there is no session_id and no server-side conversation memory. The old
 * panel lived inside one session's page and could therefore prefill the
 * input with that session's title. This widget has no page to anchor to
 * (it floats over the dashboard just as much as any session page), so there
 * is nothing to prefill from and nothing to auto-fill -- the instructor must
 * always name the session in what they type, exactly as if editing the old
 * prefill away by hand. The "did this run target the page I'm on" tracking
 * (summaryNamesSession/ranElsewhere/onGraded) went away for the same reason:
 * there is no longer a "this page's session" to compare against here -- but
 * see "Announcing completion" below, which restores it for whichever page
 * is actually mounted.
 *
 * Announcing completion
 * ----------------------
 * On every `summary` event this widget calls announceGradingCompletion with
 * the raw conversational message (lib/grading-announcements.tsx). That is
 * the full extent of its involvement -- it does not know whether a session
 * page is mounted, or which one, or compare anything itself. Whichever
 * session-detail page happens to be listening runs its own
 * summaryNamesSession check and decides whether to refetch. This keeps the
 * widget exactly as page-agnostic as before.
 *
 * No persistence
 * ---------------
 * Chat history lives only in useState and resets whenever the widget
 * unmounts (a full page reload) or is deliberately cleared on close, per the
 * locked "no persistent history" decision. Nothing here touches
 * localStorage/sessionStorage.
 *
 * Everything else -- SSE consumption via streamChat, progressive
 * checking/graded/failed rendering, the five early-exit outcomes, and the
 * raw-error backstop via safeChatText -- is carried over unchanged from the
 * old panel.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, isChatEarlyExit, streamChat } from "@/lib/api";
import type { ChatEarlyExit, GradingEvent, GradingSummaryEvent } from "@/lib/api";
import { useAuth } from "@/lib/auth/auth-context";
import { SubmitButton } from "@/components/ui";
import { GENERIC_FAILURE_FALLBACK, safeChatText } from "@/lib/chat-safety";
import { useGradingAnnouncements } from "@/lib/grading-announcements";

interface Turn {
  id: number;
  instruction: string;
  /** checking/graded/failed, appended live as the stream yields them. */
  events: GradingEvent[];
  /** One of the five non-graded outcomes; a normal reply, not an error. */
  outcome: ChatEarlyExit | null;
  summary: GradingSummaryEvent | null;
  /** Transport/API failure -- this one IS an error. */
  error: string | null;
  streaming: boolean;
}

/**
 * Floating icon + expandable panel, mounted once for every instructor page.
 * Renders nothing unless the signed-in user is an instructor -- this is a
 * defensive check on top of only ever being mounted under app/instructor/,
 * so it can never flash during an anonymous/wrong-role redirect.
 */
export function FloatingChatWidget() {
  const { user, status } = useAuth();
  const [isOpen, setIsOpen] = useState(false);

  if (status !== "authenticated" || user?.role !== "instructor") return null;

  return (
    <div className="fixed bottom-6 right-6 z-50">
      {isOpen ? (
        <ChatPanel onClose={() => setIsOpen(false)} />
      ) : (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          aria-label="Open grading chat"
          className="flex h-14 w-14 items-center justify-center rounded-full bg-slate-900 text-white shadow-lg transition hover:bg-slate-700"
        >
          <span aria-hidden className="text-2xl">
            💬
          </span>
        </button>
      )}
    </div>
  );
}

function ChatPanel({ onClose }: { onClose: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [instruction, setInstruction] = useState("");
  const [streaming, setStreaming] = useState(false);
  const { announceGradingCompletion } = useGradingAnnouncements();

  const nextId = useRef(1);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // Abandon an in-flight stream if the widget is closed mid-run.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Keep the newest event visible while a run streams.
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [turns]);

  const patch = useCallback((id: number, change: Partial<Turn>) => {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? { ...turn, ...change } : turn)),
    );
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = instruction.trim();
    if (!text || streaming) return;

    const id = nextId.current++;
    setTurns((current) => [
      ...current,
      {
        id,
        instruction: text,
        events: [],
        outcome: null,
        summary: null,
        error: null,
        streaming: true,
      },
    ]);
    setInstruction("");
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for await (const event of streamChat(text, { signal: controller.signal })) {
        if (isChatEarlyExit(event)) {
          // no_session_match / ambiguous_session / student_not_found /
          // ambiguous_student / unsupported_filter / unrecognized_instruction
          // -- all normal replies.
          patch(id, { outcome: event });
          continue;
        }
        if (event.event === "summary") {
          patch(id, { summary: event });
          // The message is the only signal of which session a run targeted
          // (see the module docstring) -- announce it as-is and let whichever
          // session-detail page is mounted decide if it applies to them.
          if (event.message) announceGradingCompletion(event.message);
          continue;
        }
        // checking / graded / failed -- appended one at a time so the list
        // grows as the backend works, rather than appearing all at once.
        setTurns((current) =>
          current.map((turn) =>
            turn.id === id ? { ...turn, events: [...turn.events, event] } : turn,
          ),
        );
      }
    } catch (streamError) {
      if (!controller.signal.aborted) {
        patch(id, {
          error:
            streamError instanceof ApiError
              ? streamError.detail
              : "The grading stream failed. Please try again.",
        });
      }
    } finally {
      patch(id, { streaming: false });
      setStreaming(false);
      abortRef.current = null;
    }
  }

  return (
    <section className="flex h-[32rem] w-96 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Grading chat</h2>
          <p className="text-xs text-slate-500">
            Ask me to grade a session, e.g. &ldquo;grade Week 3 Day 1&rdquo;.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close grading chat"
          className="rounded-md p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
        >
          <span aria-hidden className="text-lg leading-none">
            ✕
          </span>
        </button>
      </header>

      <div
        ref={logRef}
        className="flex-1 space-y-4 overflow-y-auto px-4 py-3"
        aria-live="polite"
        aria-busy={streaming}
      >
        {turns.length === 0 ? (
          <p className="py-2 text-sm text-slate-500">
            Try <code className="rounded bg-slate-100 px-1">grade Week 3 Day 1</code> or{" "}
            <code className="rounded bg-slate-100 px-1">grade Week 3 Day 1 for Fiza</code>.
          </p>
        ) : (
          turns.map((turn) => <TurnView key={turn.id} turn={turn} />)
        )}
      </div>

      <form onSubmit={handleSubmit} noValidate className="border-t border-slate-200 p-3">
        <label htmlFor="floating-chat-instruction" className="sr-only">
          Instruction
        </label>
        <input
          id="floating-chat-instruction"
          name="instruction"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          disabled={streaming}
          placeholder="Ask me to grade a session, e.g. 'grade Week 3 Day 1'"
          className="mb-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-slate-400 disabled:bg-slate-100"
        />
        <SubmitButton pending={streaming} pendingLabel="Grading…">
          Send
        </SubmitButton>
      </form>
    </section>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  return (
    <div className="space-y-2">
      <p className="text-right">
        <span className="inline-block rounded-lg bg-slate-900 px-3 py-1.5 text-sm text-white">
          {turn.instruction}
        </span>
      </p>

      <div className="rounded-lg bg-slate-50 px-3 py-2">
        {turn.error ? (
          <p role="alert" className="text-sm text-red-700">
            {turn.error}
          </p>
        ) : null}

        {turn.outcome ? <OutcomeView outcome={turn.outcome} /> : null}

        {turn.events.length > 0 ? (
          <ul className="space-y-1">
            {turn.events.map((event, index) => (
              <li key={index} className="text-xs text-slate-600">
                <EventLine event={event} />
              </li>
            ))}
          </ul>
        ) : null}

        {turn.streaming && !turn.outcome ? (
          <p className="mt-1 text-xs text-slate-500">Working…</p>
        ) : null}

        {turn.summary ? (
          <div className="mt-2 border-t border-slate-200 pt-2">
            <p className="text-sm text-slate-800">
              {safeChatText(turn.summary.message, "summary message")}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {turn.summary.graded} graded · {turn.summary.failed} failed ·{" "}
              {turn.summary.total} total
            </p>
            {turn.summary.failures.length > 0 ? (
              <ul className="mt-1 list-inside list-disc text-xs text-red-700">
                {turn.summary.failures.map((failure, index) => (
                  <li key={index}>
                    {failure.filename}:{" "}
                    {safeChatText(
                      failure.error,
                      `summary failure for ${failure.filename}`,
                      GENERIC_FAILURE_FALLBACK,
                    )}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function EventLine({ event }: { event: GradingEvent }) {
  if (event.event === "checking") {
    return (
      <>
        <span aria-hidden>⏳</span> Checking <strong>{event.filename}</strong> for{" "}
        {event.student_name}…
      </>
    );
  }
  if (event.event === "graded") {
    return (
      <>
        <span aria-hidden>✓</span> Graded <strong>{event.filename}</strong> for{" "}
        {event.student_name} — {event.score} / 10
      </>
    );
  }
  if (event.event === "failed") {
    return (
      <span className="text-red-700">
        <span aria-hidden>✕</span> Failed <strong>{event.filename}</strong> for{" "}
        {event.student_name} —{" "}
        {safeChatText(
          event.error,
          `failed event for ${event.filename}`,
          GENERIC_FAILURE_FALLBACK,
        )}
      </span>
    );
  }
  return null;
}

/**
 * The five non-graded outcomes (plus unrecognized_instruction). Rendered as
 * ordinary replies -- these are conversational results ("which student did
 * you mean?"), not failures.
 */
function OutcomeView({ outcome }: { outcome: ChatEarlyExit }) {
  return (
    <>
      <p className="text-sm text-slate-800">
        {safeChatText(outcome.message, `${outcome.status} message`)}
      </p>

      {outcome.status === "ambiguous_session" ? (
        <ul className="mt-1 list-inside list-disc text-xs text-slate-600">
          {outcome.candidates.map((candidate) => (
            <li key={candidate.session_id}>
              {candidate.session_title}{" "}
              <span className="text-slate-400">
                ({Math.round(candidate.confidence * 100)}% match)
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {outcome.status === "ambiguous_student" ? (
        <ul className="mt-1 list-inside list-disc text-xs text-slate-600">
          {outcome.candidates.map((candidate) => (
            <li key={candidate.student_id}>{candidate.student_name}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
