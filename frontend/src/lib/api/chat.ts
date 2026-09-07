/**
 * Endpoints from backend/app/routers/chat.py.
 *
 * Why not `EventSource` for /chat/stream
 * --------------------------------------
 * The browser's `EventSource` can only issue a GET, cannot carry a request
 * body, and cannot set request headers -- so it can send neither the
 * `{"instruction": ...}` body nor the `Authorization: Bearer` header this
 * endpoint requires. (Its only options are `withCredentials`, which is about
 * cookies, and a URL.) The standard workaround, and what is implemented here,
 * is a normal `fetch` POST whose `response.body` is read incrementally with a
 * `ReadableStream` reader, decoding SSE frames as they arrive.
 *
 * The backend frames every event as `data: {json}\n\n` with no `event:` or
 * `id:` fields, so the parser below only needs to handle `data:` lines --
 * but it tolerates comments and multi-line data for correctness.
 */

import { ApiError, apiFetch, buildRequestInit, type RequestOptions } from "./client";
import type { ChatResponse, ChatStreamEvent } from "./types";

/**
 * POST /chat -- instructor only.
 *
 * Always resolves with HTTP 200 for conversational outcomes; discriminate on
 * `status`. Rejects with `ApiError` only for real failures (403 for a
 * non-instructor, 422 for a malformed body).
 */
export function postChat(
  instruction: string,
  options: RequestOptions = {},
): Promise<ChatResponse> {
  return apiFetch<ChatResponse>("/chat", {
    ...options,
    method: "POST",
    json: { instruction },
  });
}

/**
 * Decode one SSE frame (the text between blank lines) into its JSON payload.
 * Returns null for frames carrying no usable `data:` (comments, keep-alives).
 * Exported for tests.
 */
export function parseSseFrame(frame: string): ChatStreamEvent | null {
  const dataLines: string[] = [];
  for (const rawLine of frame.split(/\r?\n/)) {
    const line = rawLine.replace(/\r$/, "");
    if (!line || line.startsWith(":")) continue; // blank or comment
    if (!line.startsWith("data:")) continue; // event:/id:/retry: -- unused here
    dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return null;
  const payload = dataLines.join("\n");
  if (!payload.trim()) return null;
  try {
    return JSON.parse(payload) as ChatStreamEvent;
  } catch {
    // A malformed frame is surfaced, not silently dropped.
    throw new ApiError(0, `Malformed SSE payload from /chat/stream: ${payload}`);
  }
}

/**
 * POST /chat/stream -- instructor only. Yields each event as it arrives.
 *
 * Usage:
 *   for await (const event of streamChat("grade week 1 day 2")) { ... }
 *
 * The stream is either exactly one early-exit outcome (`status` field, then
 * the stream closes) or the pipeline's live `checking`/`graded`/`failed`
 * events ending in `summary`. Use `isChatEarlyExit` / `isGradingEvent` from
 * `./types` to narrow. Pass `options.signal` to cancel a long batch.
 */
export async function* streamChat(
  instruction: string,
  options: RequestOptions = {},
): AsyncGenerator<ChatStreamEvent, void, undefined> {
  const { url, init } = buildRequestInit({
    ...options,
    method: "POST",
    json: { instruction },
    headers: { Accept: "text/event-stream", ...(options.headers ?? {}) },
  });
  const target = url("/chat/stream");

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
    throw new ApiError(0, "/chat/stream returned no readable body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line; keep the trailing partial one.
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary !== -1) {
        const match = /\r?\n\r?\n/.exec(buffer.slice(boundary))!;
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + match[0].length);
        const event = parseSseFrame(frame);
        if (event) yield event;
        boundary = buffer.search(/\r?\n\r?\n/);
      }
    }

    // Flush anything left without a trailing blank line.
    buffer += decoder.decode();
    if (buffer.trim()) {
      const event = parseSseFrame(buffer);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}
