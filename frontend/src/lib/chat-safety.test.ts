/**
 * The raw-internals backstop for chat text.
 *
 * The "clean message" fixtures below are the backend's real output, taken from
 * `chat_response_formatter.build_response_message` and
 * `grading_pipeline._GRADING_UNAVAILABLE_MESSAGE` against the running server —
 * not invented, so a wording change upstream shows up here.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  GENERIC_CHAT_FALLBACK,
  GENERIC_FAILURE_FALLBACK,
  MAX_REASONABLE_MESSAGE_LENGTH,
  looksLikeRawError,
  safeChatText,
} from "./chat-safety";

/** The backend's real all-providers-failed message (Fix 3, backend). */
const CLEAN_UNAVAILABLE =
  "Grading is temporarily unavailable — the AI grading service could not be " +
  "reached (all providers failed). Please try again in a few minutes.";

/** The real messages `build_response_message` returns, verified live. */
const REAL_BACKEND_MESSAGES = [
  "I couldn't find a session matching that instruction. Could you double-check the session name or date?",
  'I can only help with grading instructions, like "grade Week 3 Day 1" or "grade Week 3 Day 1 for a specific student". I can\'t answer other kinds of questions.',
  "I couldn't find a student matching 'Zainab' in that session.",
  "I can't handle that kind of filtering yet (exclusionary). Try naming a single student instead, or grade everyone.",
  "Graded 3 of 4 submissions in Week 3 Day 1. 1 failed — see the details below.",
  CLEAN_UNAVAILABLE,
];

/** An abridged sample of what used to reach the UI when every provider failed. */
const RAW_PROVIDER_DUMP =
  "LLM call failed: Gemini, Groq, and Ollama all failed. Gemini: 429 " +
  "RESOURCE_EXHAUSTED { 'quota_metric': " +
  "'generativelanguage.googleapis.com/generate_content_free_tier_requests', " +
  "'quota_id': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', " +
  "'org_id': '884271345921' } retry_delay { seconds: 51 }";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("looksLikeRawError", () => {
  it("accepts every message the backend actually produces", () => {
    for (const message of REAL_BACKEND_MESSAGES) {
      expect(looksLikeRawError(message)).toBe(false);
    }
  });

  it("flags provider internals by marker", () => {
    expect(looksLikeRawError(RAW_PROVIDER_DUMP)).toBe(true);
    expect(looksLikeRawError("something quota_metric something")).toBe(true);
    expect(looksLikeRawError("org_id: 884271345921")).toBe(true);
    expect(looksLikeRawError("[WinError 10061] connection refused")).toBe(true);
  });

  it("matches markers case-insensitively", () => {
    expect(looksLikeRawError("QUOTA_METRIC blew up")).toBe(true);
    expect(looksLikeRawError("winerror 2")).toBe(true);
  });

  it("flags an unreasonably long message even with no marker", () => {
    expect(looksLikeRawError("x".repeat(MAX_REASONABLE_MESSAGE_LENGTH + 1))).toBe(true);
    expect(looksLikeRawError("x".repeat(MAX_REASONABLE_MESSAGE_LENGTH))).toBe(false);
  });

  it("leaves comfortable headroom above the longest real message", () => {
    const longest = Math.max(...REAL_BACKEND_MESSAGES.map((m) => m.length));
    expect(longest).toBeLessThan(MAX_REASONABLE_MESSAGE_LENGTH / 2);
  });
});

describe("safeChatText", () => {
  it("returns a clean message byte for byte, and logs nothing", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    for (const message of REAL_BACKEND_MESSAGES) {
      expect(safeChatText(message, "test")).toBe(message);
    }
    expect(spy).not.toHaveBeenCalled();
  });

  it("replaces raw internals with the fallback and logs the real content", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(safeChatText(RAW_PROVIDER_DUMP, "summary message")).toBe(
      GENERIC_CHAT_FALLBACK,
    );

    expect(spy).toHaveBeenCalledTimes(1);
    // The true text must survive somewhere -- suppressed from the UI, not lost.
    expect(spy.mock.calls[0].join(" ")).toContain("quota_metric");
    expect(spy.mock.calls[0].join(" ")).toContain("summary message");
  });

  it("uses the short fallback where one is given", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(
      safeChatText(RAW_PROVIDER_DUMP, "failed event", GENERIC_FAILURE_FALLBACK),
    ).toBe(GENERIC_FAILURE_FALLBACK);
  });

  it("treats undefined and empty as empty, without logging", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(safeChatText(undefined, "test")).toBe("");
    expect(safeChatText("", "test")).toBe("");
    expect(spy).not.toHaveBeenCalled();
  });

  it("never returns text containing a raw marker", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const out = safeChatText(RAW_PROVIDER_DUMP, "test");
    expect(out).not.toContain("quota_metric");
    expect(out).not.toContain("org_id");
  });
});
