/**
 * Floating grading-chat widget (Phase 5 post-fix, Feature 6).
 *
 * Covers: collapsed-by-default, expand/collapse, empty input with no prefill
 * regardless of the "page" it's used from, no localStorage/sessionStorage
 * calls, role-gating (instructor only), and the same SSE/progressive/
 * outcome/error-suppression behaviour carried over from the old embedded
 * panel. All API calls are mocked; no real network.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { ChatStreamEvent, UserRead } from "@/lib/api";

const streamChatMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, streamChat: (...a: unknown[]) => streamChatMock(...a) };
});

const INSTRUCTOR: UserRead = {
  id: 1,
  name: "Demo Instructor",
  email: "instructor@demo.com",
  role: "instructor",
  created_at: "2026-09-06T15:26:49.014311",
};

const STUDENT: UserRead = {
  id: 2,
  name: "Demo Student",
  email: "student@demo.com",
  role: "student",
  created_at: "2026-09-06T15:26:49.014311",
};

let authValue: { user: UserRead | null; status: "loading" | "authenticated" | "anonymous" } = {
  user: INSTRUCTOR,
  status: "authenticated",
};

vi.mock("@/lib/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/auth-context")>();
  return {
    ...actual,
    useAuth: () => ({
      ...authValue,
      signIn: vi.fn(),
      signOut: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});

import { FloatingChatWidget } from "./floating-chat-widget";

/** Simple all-at-once stream for tests that don't care about timing. */
function streamOf(...events: ChatStreamEvent[]) {
  return (async function* () {
    for (const event of events) yield event;
  })();
}

async function open() {
  await userEvent.click(screen.getByRole("button", { name: /open grading chat/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  authValue = { user: INSTRUCTOR, status: "authenticated" };
});

describe("<FloatingChatWidget /> — collapse/expand", () => {
  it("renders collapsed by default", () => {
    render(<FloatingChatWidget />);
    expect(screen.getByRole("button", { name: /open grading chat/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Instruction")).not.toBeInTheDocument();
  });

  it("expands on click, showing an empty instruction input with no prefill", async () => {
    render(<FloatingChatWidget />);
    await open();

    const input = screen.getByLabelText("Instruction");
    expect(input).toHaveValue("");
    expect(screen.getByRole("button", { name: /close grading chat/i })).toBeInTheDocument();
  });

  it("collapses on close, and reopening starts with a clean, empty chat", async () => {
    streamChatMock.mockReturnValue(
      streamOf({ status: "no_session_match", message: "I couldn't find that session." }),
    );
    render(<FloatingChatWidget />);
    await open();

    await userEvent.type(screen.getByLabelText("Instruction"), "grade Week 3 Day 1");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));
    await waitFor(() =>
      expect(screen.getByText(/couldn't find that session/i)).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole("button", { name: /close grading chat/i }));
    expect(screen.queryByLabelText("Instruction")).not.toBeInTheDocument();

    // Reopen: no accidental persistence of the prior turn or input text.
    await open();
    expect(screen.getByLabelText("Instruction")).toHaveValue("");
    expect(screen.queryByText(/couldn't find that session/i)).not.toBeInTheDocument();
  });

  it("starts empty with no prefill no matter which instructor page it's rendered from", async () => {
    // The widget takes no session/page prop at all -- rendering it standing
    // alone (as it would on the dashboard, which has no session context)
    // proves there is nothing page-specific to prefill from.
    render(<FloatingChatWidget />);
    await open();
    expect(screen.getByLabelText("Instruction")).toHaveValue("");
    expect(
      screen.getByPlaceholderText(/ask me to grade a session/i),
    ).toBeInTheDocument();
  });
});

describe("<FloatingChatWidget /> — role gating", () => {
  it("renders nothing for a student", () => {
    authValue = { user: STUDENT, status: "authenticated" };
    render(<FloatingChatWidget />);
    expect(screen.queryByRole("button", { name: /open grading chat/i })).not.toBeInTheDocument();
  });

  it("renders nothing while auth is loading or anonymous", () => {
    authValue = { user: null, status: "loading" };
    const { rerender } = render(<FloatingChatWidget />);
    expect(screen.queryByRole("button", { name: /open grading chat/i })).not.toBeInTheDocument();

    authValue = { user: null, status: "anonymous" };
    rerender(<FloatingChatWidget />);
    expect(screen.queryByRole("button", { name: /open grading chat/i })).not.toBeInTheDocument();
  });
});

describe("<FloatingChatWidget /> — no persistence", () => {
  it("never touches localStorage or sessionStorage", async () => {
    const localSpy = vi.spyOn(Storage.prototype, "setItem");
    const localGetSpy = vi.spyOn(Storage.prototype, "getItem");

    streamChatMock.mockReturnValue(
      streamOf({
        event: "summary",
        total: 1,
        graded: 1,
        failed: 0,
        failures: [],
        message: "Graded 1 of 1 submission in Week 3 Day 1.",
      }),
    );

    render(<FloatingChatWidget />);
    await open();
    await userEvent.type(screen.getByLabelText("Instruction"), "grade Week 3 Day 1");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));
    await waitFor(() =>
      expect(screen.getByText(/Graded 1 of 1 submission/)).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole("button", { name: /close grading chat/i }));
    await open();

    expect(localSpy).not.toHaveBeenCalled();
    expect(localGetSpy).not.toHaveBeenCalled();

    localSpy.mockRestore();
    localGetSpy.mockRestore();
  });
});

describe("<FloatingChatWidget /> — sending", () => {
  it("does not send an empty instruction", async () => {
    render(<FloatingChatWidget />);
    await open();
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(streamChatMock).not.toHaveBeenCalled();
  });

  it("sends the typed instruction verbatim", async () => {
    streamChatMock.mockReturnValue(streamOf());
    render(<FloatingChatWidget />);
    await open();

    await userEvent.type(screen.getByLabelText("Instruction"), "grade Week 1 Day 1 for Fiza");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(streamChatMock).toHaveBeenCalledTimes(1);
    expect(streamChatMock.mock.calls[0][0]).toBe("grade Week 1 Day 1 for Fiza");
  });

  it("clears the input after sending", async () => {
    streamChatMock.mockReturnValue(streamOf());
    render(<FloatingChatWidget />);
    await open();

    const input = screen.getByLabelText("Instruction");
    await userEvent.type(input, "grade Week 1 Day 1");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(input).toHaveValue("");
  });
});

describe("<FloatingChatWidget /> — the non-graded outcomes are replies, not errors", () => {
  it("shows no_session_match as a normal message", async () => {
    streamChatMock.mockReturnValue(
      streamOf({
        status: "no_session_match",
        message: "I couldn't find a session matching that instruction.",
      }),
    );
    render(<FloatingChatWidget />);
    await open();
    await userEvent.type(screen.getByLabelText("Instruction"), "grade nonsense");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() =>
      expect(screen.getByText(/couldn't find a session matching/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders an unrecognized_instruction reply as-is, reusing the existing clarification behaviour", async () => {
    const message =
      'I can only help with grading instructions, like "grade Week 3 Day 1" or ' +
      '"grade Week 3 Day 1 for a specific student". I can\'t answer other kinds ' +
      "of questions.";
    streamChatMock.mockReturnValue(streamOf({ status: "unrecognized_instruction", message }));
    render(<FloatingChatWidget />);
    await open();
    await userEvent.type(screen.getByLabelText("Instruction"), "what is a tensor?");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lists ambiguous_session candidates with their confidence", async () => {
    streamChatMock.mockReturnValue(
      streamOf({
        status: "ambiguous_session",
        message: "I found a few sessions that could match — did you mean one of these?",
        candidates: [
          { session_id: 1, session_title: "Week 1 Day 1", confidence: 1 },
          { session_id: 2, session_title: "Week 1 Day 2", confidence: 0.82 },
        ],
      }),
    );
    render(<FloatingChatWidget />);
    await open();
    await userEvent.type(screen.getByLabelText("Instruction"), "grade week 1");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() => expect(screen.getByText(/Week 1 Day 1/)).toBeInTheDocument());
    expect(screen.getByText(/100% match/)).toBeInTheDocument();
    expect(screen.getByText(/82% match/)).toBeInTheDocument();
  });
});

describe("<FloatingChatWidget /> — progressive streaming and errors", () => {
  it("shows a transport failure as an actual error", async () => {
    streamChatMock.mockImplementation(() => {
      throw new ApiError(403, "Instructor role required.");
    });
    render(<FloatingChatWidget />);
    await open();
    await userEvent.type(screen.getByLabelText("Instruction"), "grade Week 3 Day 1");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Instructor role required."),
    );
  });

  it("disables the input while a run streams", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    streamChatMock.mockImplementation(() =>
      (async function* () {
        await gate;
      })(),
    );

    render(<FloatingChatWidget />);
    await open();
    await userEvent.type(screen.getByLabelText("Instruction"), "grade Week 3 Day 1");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() => expect(screen.getByLabelText("Instruction")).toBeDisabled());
    expect(screen.getByRole("button", { name: /grading…/i })).toBeDisabled();

    release();
    await waitFor(() => expect(screen.getByLabelText("Instruction")).toBeEnabled());
  });

  it("suppresses a raw provider dump and logs it instead", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const rawDump =
      "LLM call failed: Gemini, Groq, and Ollama all failed. Gemini: 429 " +
      "RESOURCE_EXHAUSTED { 'quota_metric': 'x', 'org_id': '884271345921' }";
    streamChatMock.mockReturnValue(
      streamOf({
        event: "failed",
        student_id: 2,
        student_name: "Fiza",
        filename: "a.ipynb",
        error: rawDump,
      }),
    );
    render(<FloatingChatWidget />);
    await open();
    await userEvent.type(screen.getByLabelText("Instruction"), "grade Week 3 Day 1");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() =>
      expect(screen.getByText(/details in the console/i)).toBeInTheDocument(),
    );
    expect(document.body.textContent).not.toContain("quota_metric");
    expect(spy.mock.calls.flat().join(" ")).toContain("quota_metric");

    spy.mockRestore();
  });
});
