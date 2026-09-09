"use client";

/**
 * Embedded grading chat (5.4, Step 2).
 *
 * Scoping — the decision this component exists around
 * ---------------------------------------------------
 * POST /chat/stream accepts exactly one field, `{"instruction": string}`.
 * There is no session_id: Phase 3 resolves the session by matching the
 * instruction TEXT (session_matcher.match_instruction_to_session). So an
 * embedded panel cannot say "this session" except through what the
 * instructor types.
 *
 * Signed off: PREFILL, DON'T REWRITE. The input starts pre-filled with this
 * session's exact title and is sent verbatim — nothing is injected behind
 * the instructor's back. If they edit the session name, that is an explicit
 * choice and Phase 3's matcher receives one coherent instruction, so its
 * deliberate never-guess-on-ambiguity behaviour is left intact.
 *
 * Cross-session detection — and its one real limitation
 * -----------------------------------------------------
 * A RESOLVED run streams only grade_session_batch's own events; unlike the
 * early-exit outcomes, none of them carries a session_id. The only signal
 * that a run targeted a different session is that 3.5's summary message
 * interpolates the session title verbatim ("...in {session_title}."). This
 * component therefore matches on `in {title}`. If that wording ever changes
 * the check fails toward showing the warning and skipping the refresh, which
 * is the safe direction — never toward silently claiming a refresh happened.
 *
 * Message rendering — as-is, with one backstop
 * --------------------------------------------
 * The outcome and summary messages (`turn.outcome.message`, `turn.summary.message`)
 * come from the backend and are rendered verbatim — nothing here reformats,
 * truncates or parses them; the only string inspection done on them is
 * `summaryNamesSession` above, which routes and never edits what is shown.
 *
 * The per-file `checking`/`graded`/`failed` lines are the one exception: the
 * backend has no message text for these (grade_session_batch only carries
 * raw fields — student_id/student_name/filename/score/error), so `EventLine`
 * below builds "Checking X for Y…" etc. itself from those fields.
 *
 * Separately, `safeChatText` passes a normal backend message through
 * untouched and swaps in a generic line only when the text looks like raw
 * internals (see lib/chat-safety.ts). The backend already sanitises the
 * known all-providers-failed case; this catches anything that slips past it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, isChatEarlyExit, streamChat } from "@/lib/api";
import type { ChatEarlyExit, GradingEvent, GradingSummaryEvent } from "@/lib/api";
import { Panel, SmallButton, SubmitButton } from "@/components/ui";
import {
  GENERIC_FAILURE_FALLBACK,
  safeChatText,
} from "@/lib/chat-safety";

interface Turn {
  id: number;
  instruction: string;
  /** checking/graded/failed, appended live as the stream yields them. */
  events: GradingEvent[];
  /** One of the five non-graded outcomes; a normal reply, not an error. */
  outcome: ChatEarlyExit | null;
  summary: GradingSummaryEvent | null;
  /** Transport/API failure — this one IS an error. */
  error: string | null;
  streaming: boolean;
  /** True when the summary names a session other than this page's. */
  ranElsewhere: boolean;
}

/** The instruction the input is pre-filled with; also the reset target. */
export function defaultInstruction(sessionTitle: string): string {
  return `grade ${sessionTitle} `;
}

/**
 * Did this summary's conversational message name the session we are on?
 * See the module docstring for why this is a string check.
 */
export function summaryNamesSession(message: string | undefined, sessionTitle: string): boolean {
  if (!message) return false;
  return message.includes(`in ${sessionTitle}`);
}

export function GradingChat({
  sessionTitle,
  onGraded,
}: {
  sessionTitle: string;
  /** Called only when a run that targeted THIS session finishes. */
  onGraded: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [instruction, setInstruction] = useState(() => defaultInstruction(sessionTitle));
  const [streaming, setStreaming] = useState(false);

  const nextId = useRef(1);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // Abandon an in-flight stream if the instructor navigates away mid-run.
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
        ranElsewhere: false,
      },
    ]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    let graded = false;
    let ranElsewhere = false;

    try {
      for await (const event of streamChat(text, { signal: controller.signal })) {
        if (isChatEarlyExit(event)) {
          // no_session_match / ambiguous_session / student_not_found /
          // ambiguous_student / unsupported_filter — all normal replies.
          patch(id, { outcome: event });
          continue;
        }
        if (event.event === "summary") {
          ranElsewhere = !summaryNamesSession(event.message, sessionTitle);
          graded = true;
          patch(id, { summary: event, ranElsewhere });
          continue;
        }
        // checking / graded / failed — appended one at a time so the list
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

    // Only refresh when the run actually touched this session's grades.
    if (graded && !ranElsewhere) onGraded();
  }

  return (
    <Panel
      title="Grading chat"
      description="Ask in plain language. The session name has to be in the instruction — the backend resolves it from your words, not from this page."
    >
      <div
        ref={logRef}
        className="mb-4 max-h-96 space-y-4 overflow-y-auto"
        aria-live="polite"
        aria-busy={streaming}
      >
        {turns.length === 0 ? (
          <p className="py-2 text-sm text-slate-500">
            Try <code className="rounded bg-slate-100 px-1">grade {sessionTitle}</code> or{" "}
            <code className="rounded bg-slate-100 px-1">grade {sessionTitle} for Fiza</code>.
          </p>
        ) : (
          turns.map((turn) => <TurnView key={turn.id} turn={turn} />)
        )}
      </div>

      <form onSubmit={handleSubmit} noValidate>
        <label htmlFor="chat-instruction" className="mb-1 block text-sm font-medium text-slate-700">
          Instruction
        </label>
        <input
          id="chat-instruction"
          name="instruction"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          disabled={streaming}
          className="mb-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-slate-400 disabled:bg-slate-100"
        />
        <div className="mb-4 flex items-center justify-between gap-3">
          <SmallButton
            onClick={() => setInstruction(defaultInstruction(sessionTitle))}
            disabled={streaming}
          >
            Reset to this session
          </SmallButton>
          <span className="text-xs text-slate-500">Sent exactly as written.</span>
        </div>
        <SubmitButton pending={streaming} pendingLabel="Grading…">
          Send
        </SubmitButton>
      </form>
    </Panel>
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
            {turn.ranElsewhere ? (
              <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-800">
                That instruction resolved to a different session, so this page&apos;s roster
                is unchanged.
              </p>
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
 * The five non-graded outcomes. Rendered as ordinary replies — these are
 * conversational results ("which student did you mean?"), not failures.
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
