/**
 * Tests for the base fetch wrapper and endpoint functions.
 *
 * Everything here mocks `fetch` -- no real backend is contacted (real-backend
 * verification is a separate manual script, `scripts/verify-api-client.live.ts, run via `npm run verify:api``).
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "@/lib/api/client";
import { API_BASE_URL, buildUrl } from "@/lib/api/config";
import { login } from "@/lib/api/auth";
import { listSessions } from "@/lib/api/sessions";
import { uploadSubmission } from "@/lib/api/submissions";
import { getMyGrades } from "@/lib/api/grades";
import { parseSseFrame, streamChat } from "@/lib/api/chat";
import { isChatEarlyExit, isGradingEvent } from "@/lib/api/types";
import type { ChatStreamEvent, GradeSummary, SessionList } from "@/lib/api/types";

/** Build a `fetch` stub returning one JSON response, and capture the call. */
function mockJson(body: unknown, init: { status?: number; headers?: HeadersInit } = {}) {
  const status = init.status ?? 200;
  // Typed via the generic (not named params) so `mock.calls[0][1]` is a
  // RequestInit without introducing unused-parameter lint noise.
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    async () =>
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: init.headers ?? { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Assert a call rejects with ApiError, and hand the typed error back. */
async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => null,
    (e: unknown) => e,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("config", () => {
  it("builds URLs against the configured base, with or without a leading slash", () => {
    expect(buildUrl("/auth/me")).toBe(`${API_BASE_URL}/auth/me`);
    expect(buildUrl("auth/me")).toBe(`${API_BASE_URL}/auth/me`);
  });

  it("has no trailing slash on the base URL", () => {
    expect(API_BASE_URL.endsWith("/")).toBe(false);
  });
});

describe("apiFetch — request building", () => {
  it("attaches an explicitly supplied bearer token", async () => {
    const fetchMock = mockJson({ ok: true });
    await apiFetch("/auth/me", { token: "abc123" });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer abc123");
  });

  it("omits the Authorization header when auth is disabled", async () => {
    const fetchMock = mockJson({ ok: true });
    await apiFetch("/auth/login", { auth: false, token: "abc123" });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Authorization"]).toBeUndefined();
  });

  it("drops null/undefined query params and appends the rest", async () => {
    const fetchMock = mockJson({ total: 0, items: [] });
    await apiFetch("/sessions", { query: { skip: 0, limit: 10, missing: undefined } });
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/sessions?skip=0&limit=10`);
  });

  it("form-encodes /auth/login the way OAuth2PasswordRequestForm expects", async () => {
    const fetchMock = mockJson({ access_token: "t", token_type: "bearer" });
    await login("instructor@demo.com", "instructor123", { persist: false });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/x-www-form-urlencoded",
    );
    expect(init.body).toBe("username=instructor%40demo.com&password=instructor123");
  });

  it("leaves Content-Type unset for FormData so the browser adds the boundary", async () => {
    const fetchMock = mockJson({ id: 1, session_id: 1, student_id: 2, files: [] });
    const file = new File(["{}"], "solved.ipynb", { type: "application/json" });
    await uploadSubmission(1, file, { token: "t" });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
  });
});

describe("apiFetch — responses", () => {
  it("returns the parsed body typed as the schema", async () => {
    mockJson({
      total: 1,
      items: [
        {
          id: 3,
          title: "Week 1 Day 2",
          instructor_id: 1,
          created_at: "2026-09-01T10:00:00",
          unsolved_files: [],
        },
      ],
    } satisfies SessionList);
    const result = await listSessions({}, { token: "t" });
    expect(result.total).toBe(1);
    expect(result.items[0].title).toBe("Week 1 Day 2");
  });

  it("preserves a null combined_score rather than coercing it", async () => {
    mockJson({
      student_id: 2,
      student_name: "Demo Student",
      per_file: [],
      combined_score: null,
    } satisfies GradeSummary);
    const summary = await getMyGrades(1, { token: "t" });
    expect(summary.combined_score).toBeNull();
  });

  it("resolves to undefined for a 204 with no body", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(apiFetch<void>("/sessions/1", { method: "DELETE" })).resolves.toBeUndefined();
  });
});

describe("apiFetch — error handling", () => {
  it("throws ApiError carrying the status and FastAPI detail string", async () => {
    mockJson({ detail: "Incorrect email or password." }, { status: 401 });
    const error = await expectApiError(apiFetch("/auth/login", { auth: false }));
    expect(error.status).toBe(401);
    expect(error.detail).toBe("Incorrect email or password.");
    expect(error.message).toBe("Incorrect email or password.");
  });

  it("flattens a 422 validation-error array into a readable message", async () => {
    mockJson(
      { detail: [{ loc: ["body", "instruction"], msg: "Field required", type: "missing" }] },
      { status: 422 },
    );
    const error = await expectApiError(apiFetch("/chat", { method: "POST", json: {} }));
    expect(error.status).toBe(422);
    expect(error.detail).toBe("body.instruction: Field required");
  });

  it("falls back to a status message when the body carries no detail", async () => {
    mockJson({}, { status: 500 });
    const error = await expectApiError(apiFetch("/sessions"));
    expect(error.status).toBe(500);
    expect(error.detail).toContain("500");
  });

  it("surfaces a transport failure as ApiError with status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    const error = await expectApiError(apiFetch("/sessions"));
    expect(error.status).toBe(0);
    expect(error.detail).toContain("fetch failed");
  });

  it("never returns a falsy value in place of an error", async () => {
    mockJson({ detail: "Not authorised." }, { status: 403 });
    await expect(apiFetch("/sessions/1/grades")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("SSE parsing for /chat/stream", () => {
  it("decodes a data: frame into its JSON payload", () => {
    const event = parseSseFrame('data: {"event": "graded", "student_id": 2, "filename": "a.ipynb", "score": 7.5}');
    expect(event).toEqual({
      event: "graded",
      student_id: 2,
      filename: "a.ipynb",
      score: 7.5,
    });
  });

  it("ignores comment/keep-alive frames", () => {
    expect(parseSseFrame(": keep-alive")).toBeNull();
    expect(parseSseFrame("")).toBeNull();
  });

  it("throws ApiError on a malformed payload instead of swallowing it", () => {
    expect(() => parseSseFrame("data: {not json")).toThrow(ApiError);
  });

  it("yields every event in order, including across a split chunk", async () => {
    const frames = [
      'data: {"event": "checking", "student_id": 2, "filename": "a.ipynb"}\n\ndata: {"eve',
      'nt": "graded", "student_id": 2, "filename": "a.ipynb", "score": 8.0}\n\n',
      'data: {"event": "summary", "total": 1, "graded": 1, "failed": 0, "failures": [], "message": "Done."}\n\n',
    ];
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const f of frames) controller.enqueue(encoder.encode(f));
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(stream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      })),
    );

    const received: ChatStreamEvent[] = [];
    for await (const event of streamChat("grade week 1 day 2", { token: "t" })) {
      received.push(event);
    }

    expect(received.map((e) => (isGradingEvent(e) ? e.event : "?"))).toEqual([
      "checking",
      "graded",
      "summary",
    ]);
    const summary = received[2];
    expect(isGradingEvent(summary) && summary.event === "summary" && summary.graded).toBe(1);
  });

  it("yields a single early-exit outcome and stops", async () => {
    const body = 'data: {"status": "no_session_match", "message": "Could not find that session."}\n\n';
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      })),
    );

    const received: ChatStreamEvent[] = [];
    for await (const event of streamChat("grade week 99", { token: "t" })) received.push(event);

    expect(received).toHaveLength(1);
    expect(isChatEarlyExit(received[0])).toBe(true);
    expect(isChatEarlyExit(received[0]) && received[0].status).toBe("no_session_match");
  });

  it("throws ApiError when /chat/stream rejects the request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Instructor role required." }), {
        status: 403,
        headers: { "content-type": "application/json" },
      })),
    );
    const iterator = streamChat("grade week 1", { token: "t" })[Symbol.asyncIterator]();
    const error = await expectApiError(iterator.next());
    expect(error.status).toBe(403);
    expect(error.detail).toBe("Instructor role required.");
  });
});
