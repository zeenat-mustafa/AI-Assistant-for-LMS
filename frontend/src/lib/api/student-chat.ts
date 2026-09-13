/**
 * Endpoint from backend/app/routers/student_chat.py (Phase 7.5).
 *
 * POST /student-chat/stream is SSE over a POST with a bearer token, so --
 * exactly as for /chat/stream -- `EventSource` cannot be used. The read loop
 * below is a deliberate local copy of the one inside chat.ts's `streamChat`
 * (7.7 must not modify chat.ts); only the exported `parseSseFrame` is shared.
 * Consolidate in 7.8.
 */

import { ApiError, buildRequestInit, type RequestOptions } from "./client";
import { parseSseFrame } from "./chat";
import type {
  StudentChatCitationsEvent,
  StudentChatClarificationEvent,
  StudentChatDoneEvent,
  StudentChatErrorEvent,
  StudentChatEvent,
  StudentChatQuestion,
  StudentChatResolvedEvent,
  StudentChatTokenEvent,
} from "./types";

const PATH = "/student-chat/stream";

const KNOWN_EVENTS = new Set<string>([
  "clarification_needed",
  "resolved",
  "citations",
  "token",
  "done",
  "error",
]);

export interface StudentChatHandlers {
  onClarification?: (event: StudentChatClarificationEvent) => void;
  onResolved?: (event: StudentChatResolvedEvent) => void;
  onCitations?: (event: StudentChatCitationsEvent) => void;
  onToken?: (event: StudentChatTokenEvent) => void;
  onDone?: (event: StudentChatDoneEvent) => void;
  onError?: (event: StudentChatErrorEvent) => void;
}

/**
 * How the stream ended.
 *  - "done"          -- a `done` event arrived; the answer is complete.
 *  - "clarification" -- the backend asked which session was meant.
 *  - "error"         -- the backend sent an `error` event.
 *  - "incomplete"    -- the stream closed without any of the above.
 */
export type StudentChatOutcome = "done" | "clarification" | "error" | "incomplete";

export interface StudentChatResult {
  outcome: StudentChatOutcome;
  /** Frames whose `event` name this client does not know -- ignored, but counted. */
  unknownEventCount: number;
}

function dispatch(event: StudentChatEvent, handlers: StudentChatHandlers): void {
  switch (event.event) {
    case "clarification_needed":
      handlers.onClarification?.(event);
      break;
    case "resolved":
      handlers.onResolved?.(event);
      break;
    case "citations":
      handlers.onCitations?.(event);
      break;
    case "token":
      handlers.onToken?.(event);
      break;
    case "done":
      handlers.onDone?.(event);
      break;
    case "error":
      handlers.onError?.(event);
      break;
  }
}

/** Decode one frame, re-labelling parseSseFrame's /chat/stream-specific error. */
function decodeFrame(frame: string): unknown {
  try {
    return parseSseFrame(frame);
  } catch (error) {
    if (error instanceof ApiError) {
      throw new ApiError(0, `Malformed SSE payload from ${PATH}: ${frame}`);
    }
    throw error;
  }
}

/**
 * POST /student-chat/stream -- student only.
 *
 * `question` is sent exactly as given. Handlers fire as each event arrives;
 * the promise resolves once the stream closes. Rejects with `ApiError` on a
 * non-2xx response or transport failure. `options.signal` aborts the fetch
 * (used on unmount only).
 */
export async function streamStudentChat(
  params: { question: string; currentSessionId: number | null },
  handlers: StudentChatHandlers = {},
  options: RequestOptions = {},
): Promise<StudentChatResult> {
  const body: StudentChatQuestion = {
    question: params.question,
    current_session_id: params.currentSessionId,
  };
  const { url, init } = buildRequestInit({
    ...options,
    method: "POST",
    json: body,
    headers: { Accept: "text/event-stream", ...(options.headers ?? {}) },
  });
  const target = url(PATH);

  let response: Response;
  try {
    response = await fetch(target, init);
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `Could not reach the API at ${target}: ${reason}`);
  }

  if (!response.ok) {
    const text = await response.text();
    let detail = text || `Request failed with status ${response.status}.`;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      /* not JSON -- keep the raw text */
    }
    throw new ApiError(response.status, detail, text);
  }

  if (!response.body) {
    throw new ApiError(0, `${PATH} returned no readable body.`);
  }

  let outcome: StudentChatOutcome = "incomplete";
  let unknownEventCount = 0;

  const handleFrame = (frame: string) => {
    const payload = decodeFrame(frame);
    if (!payload || typeof payload !== "object") return;
    const name = (payload as { event?: unknown }).event;
    if (typeof name !== "string" || !KNOWN_EVENTS.has(name)) {
      unknownEventCount += 1;
      return;
    }
    const event = payload as StudentChatEvent;
    if (event.event === "done") outcome = "done";
    else if (event.event === "clarification_needed") outcome = "clarification";
    else if (event.event === "error") outcome = "error";
    dispatch(event, handlers);
  };

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary !== -1) {
        const match = /\r?\n\r?\n/.exec(buffer.slice(boundary))!;
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + match[0].length);
        handleFrame(frame);
        boundary = buffer.search(/\r?\n\r?\n/);
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) handleFrame(buffer);
  } finally {
    reader.releaseLock();
  }

  return { outcome, unknownEventCount };
}
