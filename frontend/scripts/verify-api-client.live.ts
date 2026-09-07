/**
 * LIVE verification of the typed API client against a real running backend.
 *
 * Not part of `npm test` -- run it deliberately:
 *
 *   cd backend && uvicorn app.main:app --reload --port 8000
 *   cd frontend && npm run verify:api
 *
 * It exists because 5.1 ships no UI to click through, so the only way to prove
 * the client actually matches the backend contract is to call the backend.
 * Credentials default to the demo seed users from backend/.env.example.
 */

import { describe, expect, it } from "vitest";

import { API_BASE_URL } from "@/lib/api/config";
import { getCurrentUser, login } from "@/lib/api/auth";
import { listSessions, getSession } from "@/lib/api/sessions";
import { listSubmissions } from "@/lib/api/submissions";
import { getGradeReport } from "@/lib/api/grades";
import { postChat } from "@/lib/api/chat";
import { isChatEarlyExit, isGradingEvent } from "@/lib/api/types";
import { streamChat } from "@/lib/api/chat";

const EMAIL = process.env.VERIFY_INSTRUCTOR_EMAIL ?? "instructor@demo.com";
const PASSWORD = process.env.VERIFY_INSTRUCTOR_PASSWORD ?? "instructor123";

let token = "";

function log(label: string, value: unknown) {
  console.log(`\n--- ${label} ---`);
  console.log(typeof value === "string" ? value : JSON.stringify(value, null, 2));
}

describe("live API client verification", () => {
  it("logs in and receives a real JWT", async () => {
    log("API_BASE_URL", API_BASE_URL);
    // persist:false -- there is no localStorage here; the token is passed explicitly.
    const result = await login(EMAIL, PASSWORD, { persist: false });
    token = result.access_token;

    log("POST /auth/login", {
      token_type: result.token_type,
      access_token_prefix: `${token.slice(0, 24)}...`,
      access_token_length: token.length,
      jwt_segments: token.split(".").length,
    });

    expect(result.token_type).toBe("bearer");
    expect(token.split(".")).toHaveLength(3);
  });

  it("resolves the current user with that token", async () => {
    const user = await getCurrentUser({ token });
    log("GET /auth/me", user);
    expect(user.email).toBe(EMAIL);
    expect(user.role).toBe("instructor");
  });

  it("lists real sessions", async () => {
    const sessions = await listSessions({ limit: 5 }, { token });
    log("GET /sessions?limit=5", sessions);
    expect(typeof sessions.total).toBe("number");
    expect(Array.isArray(sessions.items)).toBe(true);
    for (const s of sessions.items) {
      expect(typeof s.id).toBe("number");
      expect(typeof s.title).toBe("string");
      expect(Array.isArray(s.unsolved_files)).toBe(true);
    }
  });

  it("reads one session's detail and its grade report", async () => {
    const sessions = await listSessions({ limit: 1 }, { token });
    if (sessions.items.length === 0) {
      console.log("\n(no sessions in the dev DB -- skipping detail/report checks)");
      return;
    }
    const sessionId = sessions.items[0].id;

    const detail = await getSession(sessionId, { token });
    log(`GET /sessions/${sessionId}`, detail);
    expect(detail.id).toBe(sessionId);

    const report = await getGradeReport(sessionId, { token });
    log(`GET /sessions/${sessionId}/grades`, {
      session_id: report.session_id,
      session_title: report.session_title,
      student_count: report.students.length,
      students: report.students.map((s) => ({
        student_name: s.student_name,
        combined_score: s.combined_score,
        graded_files: s.per_file.length,
      })),
    });
    expect(report.session_id).toBe(sessionId);
  });

  it("round-trips POST /chat without triggering a grading run", async () => {
    // A deliberately unmatchable instruction: proves the /chat contract and
    // the typed union without spending LLM calls on real grading.
    const response = await postChat(
      "grade week 9999 day 9999 for nobody",
      { token },
    );
    log("POST /chat (unmatchable instruction)", response);
    expect(typeof response.status).toBe("string");
    expect(typeof response.message).toBe("string");
  });

  it("consumes POST /chat/stream as SSE", async () => {
    const events = [];
    for await (const event of streamChat("grade week 9999 day 9999 for nobody", { token })) {
      events.push(event);
    }
    log("POST /chat/stream (unmatchable instruction)", events);
    expect(events.length).toBeGreaterThan(0);
    const last = events[events.length - 1];
    expect(isChatEarlyExit(last) || isGradingEvent(last)).toBe(true);
  });

  it("streams grading-pipeline events (not just an early exit) for a resolved instruction", async () => {
    // Pick a session whose submission files are ALL already graded, so the
    // instruction resolves and reaches grade_session_batch but finds nothing
    // ungraded -- exercising the resolved SSE path at zero LLM cost.
    const sessions = await listSessions({ limit: 50 }, { token });
    let target: { id: number; title: string } | null = null;
    for (const session of sessions.items) {
      const submissions = await listSubmissions(session.id, { token });
      const files = submissions.flatMap((s) => s.files);
      if (files.length > 0 && files.every((f) => f.graded)) {
        target = { id: session.id, title: session.title };
        break;
      }
    }
    if (!target) {
      console.log("\n(no fully-graded session in the dev DB -- skipping resolved-stream check)");
      return;
    }

    const events = [];
    for await (const event of streamChat(`grade ${target.title}`, { token })) {
      events.push(event);
    }
    log(`POST /chat/stream ("grade ${target.title}")`, events);

    expect(events.length).toBeGreaterThan(0);
    // Resolved path: pipeline events, not a `status` early exit.
    expect(events.every(isGradingEvent)).toBe(true);
    const last = events[events.length - 1];
    expect(isGradingEvent(last) && last.event).toBe("summary");
  });
});
