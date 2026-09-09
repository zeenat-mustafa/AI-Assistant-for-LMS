/**
 * Last-line defence against raw internal error text reaching the chat UI.
 *
 * The backend now sanitises the one case that used to leak: when every LLM
 * provider fails, `grading_pipeline._user_facing_error` replaces a
 * concatenation of provider URLs, quota-metric names, org ids and retry-delay
 * JSON with a clean sentence. That fix is upstream and this module does NOT
 * duplicate it — a clean message passes through here untouched, byte for byte.
 *
 * This exists for the cases the backend fix does not cover: a provider error
 * raised on a path that does not run through `_user_facing_error`, a new
 * provider whose error text does not match the backend's signature substring,
 * or a future regression. Any of those would render verbatim in a chat bubble.
 *
 * The rule is deliberately narrow. Every message the backend actually produces
 * is a short sentence — the longest, measured against the running backend, is
 * 157 characters — while a real all-providers-failed dump runs to thousands.
 * So the threshold sits far above every legitimate message and far below the
 * failure case, and the markers are strings that cannot occur in prose the
 * backend writes.
 */

/**
 * Substrings that only ever appear in raw provider/runtime internals.
 * Matched case-insensitively.
 */
export const RAW_ERROR_MARKERS = [
  "quota_metric", // Gemini quota failures
  "org_id", // provider account internals
  "WinError", // a Windows OSError reached the response
] as const;

/**
 * Longest plausible legitimate message. The real maximum is 157 characters
 * (`unrecognized_instruction`); `ambiguous_session` grows with its candidate
 * list but stays far below this. Generous on purpose — this is a backstop,
 * not a formatter, and truncating a real message would be a worse bug than
 * the one it guards against.
 */
export const MAX_REASONABLE_MESSAGE_LENGTH = 600;

/** Shown in place of a message that looks like raw internals. */
export const GENERIC_CHAT_FALLBACK =
  "Something went wrong on the grading service. The full details were logged " +
  "to the browser console.";

/** Shorter variant for a per-file failure line, which sits inside a list. */
export const GENERIC_FAILURE_FALLBACK = "grading failed — details in the console";

/** Does this text look like raw internals rather than a written message? */
export function looksLikeRawError(text: string): boolean {
  if (text.length > MAX_REASONABLE_MESSAGE_LENGTH) return true;
  const lower = text.toLowerCase();
  return RAW_ERROR_MARKERS.some((marker) => lower.includes(marker.toLowerCase()));
}

/**
 * Return `text` unchanged when it reads like a real message; otherwise log the
 * true content to the console and return a generic stand-in.
 *
 * The real text is never silently discarded — it goes to the console so the
 * failure is still diagnosable, it just does not reach the user's chat bubble.
 *
 * @param label  where the text came from, for the console line.
 * @param fallback  what to show instead. Defaults to the full-sentence form.
 */
export function safeChatText(
  text: string | undefined,
  label: string,
  fallback: string = GENERIC_CHAT_FALLBACK,
): string {
  if (!text) return "";
  if (!looksLikeRawError(text)) return text;
  console.error(`[grading chat] suppressed raw error text from ${label}:`, text);
  return fallback;
}
