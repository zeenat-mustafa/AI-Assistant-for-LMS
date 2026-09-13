/**
 * Student chat page (7.7): verbatim sending, progressive tokens, session
 * banner, follow-up session id, citations, errors, clarification resend.
 * `streamStudentChat` is mocked; no real network.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type {
  SessionRead,
  StudentChatEvent,
  StudentChatHandlers,
  StudentChatResult,
  UserRead,
} from "@/lib/api";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/student/sessions/7/chat",
  useSearchParams: () => new URLSearchParams(),
}));

const getSessionMock = vi.fn();
const streamStudentChatMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getSession: (...a: unknown[]) => getSessionMock(...a),
    streamStudentChat: (...a: unknown[]) => streamStudentChatMock(...a),
  };
});

const STUDENT: UserRead = {
  id: 2,
  name: "Demo Student",
  email: "student@demo.com",
  role: "student",
  created_at: "2026-09-06T15:26:49.014311",
};
let currentUser: UserRead = STUDENT;
vi.mock("@/lib/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/auth-context")>();
  return {
    ...actual,
    useAuth: () => ({
      user: currentUser,
      status: "authenticated" as const,
      signIn: vi.fn(),
      signOut: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});

import { StudentChat, TRANSCRIPT_NOTICE } from "./student-chat";

const SESSION: SessionRead = {
  id: 7,
  title: "Week 10 Day 3",
  instructor_id: 1,
  instructor_name: "Demo Instructor",
  created_at: "2026-09-06T15:43:53",
  assignment_uploads: [],
  unsolved_files: [],
  resource_files: [],
};

function dispatch(event: StudentChatEvent, h: StudentChatHandlers) {
  switch (event.event) {
    case "resolved": return h.onResolved?.(event);
    case "citations": return h.onCitations?.(event);
    case "token": return h.onToken?.(event);
    case "done": return h.onDone?.(event);
    case "error": return h.onError?.(event);
    case "clarification_needed": return h.onClarification?.(event);
  }
}

/** A mock stream that fires `events` in order and resolves with `outcome`. */
function scripted(events: StudentChatEvent[], outcome: StudentChatResult["outcome"] = "done") {
  return async (_params: unknown, handlers: StudentChatHandlers): Promise<StudentChatResult> => {
    for (const event of events) dispatch(event, handlers);
    return { outcome, unknownEventCount: 0 };
  };
}

const resolvedHere: StudentChatEvent = {
  event: "resolved",
  session_id: 7,
  session_title: "Week 10 Day 3",
  resolution: "current_session",
};
const done: StudentChatEvent = { event: "done", thread_id: 1, user_message_id: 1, assistant_message_id: 2 };

async function renderChat() {
  render(<StudentChat sessionId={7} />);
  await screen.findByRole("heading", { name: /ask about week 10 day 3/i });
}

async function ask(text: string) {
  await userEvent.type(screen.getByLabelText("Your question"), text);
  await userEvent.click(screen.getByRole("button", { name: /^send$/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  currentUser = STUDENT;
  getSessionMock.mockResolvedValue(SESSION);
  streamStudentChatMock.mockImplementation(scripted([resolvedHere, { event: "token", text: "Answer." }, done]));
});

describe("<StudentChat /> — page", () => {
  it("names the page's session and states the transcript is not durable", async () => {
    await renderChat();
    expect(getSessionMock).toHaveBeenCalledWith(7);
    expect(screen.getByText(TRANSCRIPT_NOTICE)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to session/i })).toHaveAttribute("href", "/student/sessions/7");
  });

  it("offers no solve-style quick actions", async () => {
    await renderChat();
    expect(screen.queryByRole("button", { name: /write|solve|code for me/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
  });

  it("redirects an instructor away", async () => {
    currentUser = { ...STUDENT, id: 1, role: "instructor" };
    render(<StudentChat sessionId={7} />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/instructor"));
    expect(streamStudentChatMock).not.toHaveBeenCalled();
  });
});

describe("<StudentChat /> — sending", () => {
  it("transmits the typed text unmodified (spaces and newlines kept) with the page's session id", async () => {
    await renderChat();
    await userEvent.type(
      screen.getByLabelText("Your question"),
      "  What does{Shift>}{Enter}{/Shift}bind_tools do?  ",
    );
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    expect(streamStudentChatMock).toHaveBeenCalledTimes(1);
    expect(streamStudentChatMock.mock.calls[0][0]).toEqual({
      question: "  What does\nbind_tools do?  ",
      currentSessionId: 7,
    });
  });

  it("sends on Enter", async () => {
    await renderChat();
    await userEvent.type(screen.getByLabelText("Your question"), "hello{Enter}");
    expect(streamStudentChatMock).toHaveBeenCalledTimes(1);
    expect(streamStudentChatMock.mock.calls[0][0].question).toBe("hello");
  });

  it("does not send a blank message", async () => {
    await renderChat();
    await ask("   ");
    expect(streamStudentChatMock).not.toHaveBeenCalled();
  });

  it("renders tokens progressively and disables input while streaming", async () => {
    let releaseSecond: () => void = () => {};
    const secondGate = new Promise<void>((r) => (releaseSecond = r));
    let releaseEnd: () => void = () => {};
    const endGate = new Promise<void>((r) => (releaseEnd = r));

    streamStudentChatMock.mockImplementation(async (_p: unknown, h: StudentChatHandlers) => {
      h.onResolved?.(resolvedHere as never);
      h.onToken?.({ event: "token", text: "Tool calling " });
      await secondGate;
      h.onToken?.({ event: "token", text: "lets a model ask for a function." });
      await endGate;
      h.onDone?.(done as never);
      return { outcome: "done", unknownEventCount: 0 };
    });

    await renderChat();
    await ask("what is tool calling?");

    const answer = await screen.findByTestId("assistant-message");
    expect(answer).toHaveTextContent(/^Tool calling$/);
    expect(screen.getByLabelText("Your question")).toBeDisabled();

    releaseSecond();
    await waitFor(() =>
      expect(screen.getByTestId("assistant-message")).toHaveTextContent(
        "Tool calling lets a model ask for a function.",
      ),
    );
    expect(screen.getByLabelText("Your question")).toBeDisabled();

    releaseEnd();
    await waitFor(() => expect(screen.getByLabelText("Your question")).toBeEnabled());
  });
});

describe("<StudentChat /> — session resolution", () => {
  it("shows no banner when the answer comes from this page's session", async () => {
    await renderChat();
    await ask("q");
    await screen.findByTestId("assistant-message");
    expect(screen.queryByTestId("session-banner")).not.toBeInTheDocument();
  });

  it("shows a banner naming the other session and resolution, and uses its id for the follow-up", async () => {
    streamStudentChatMock.mockImplementationOnce(
      scripted([
        { event: "resolved", session_id: 9, session_title: "Week 9 Day 2", resolution: "redirected" },
        { event: "token", text: "From week 9." },
        done,
      ]),
    );
    await renderChat();
    await ask("what about pandas groupby?");

    expect(await screen.findByTestId("session-banner")).toHaveTextContent(
      "Answering from Week 9 Day 2 (redirected)",
    );
    // The page header still names the page's own session.
    expect(screen.getByRole("heading", { name: /ask about week 10 day 3/i })).toBeInTheDocument();

    await ask("and what does it return?");
    expect(streamStudentChatMock.mock.calls[1][0]).toEqual({
      question: "and what does it return?",
      currentSessionId: 9,
    });
  });
});

describe("<StudentChat /> — citations", () => {
  it("renders the locator alone for a null filename, with no placeholder text", async () => {
    streamStudentChatMock.mockImplementation(
      scripted([
        resolvedHere,
        {
          event: "citations",
          citations: [
            {
              source_type: "lecture",
              source_file_id: 3,
              session_id: 7,
              similarity: 0.6,
              snippet: "Speaker notes text",
              filename: null,
              slide_number: 4,
              source: "notes",
            },
          ],
        },
        { event: "token", text: "Answer." },
        done,
      ]),
    );
    await renderChat();
    await ask("q");

    const list = await screen.findByTestId("citation-list");
    expect(list).toHaveTextContent("Slide 4 · speaker notes");
    expect(list.textContent).not.toMatch(/undefined|null|N\/A|NaN/);
    // No filename -> no download link can be offered.
    expect(within(list).queryByRole("button", { name: /download lecture/i })).not.toBeInTheDocument();
    // No notebook citation -> no 0-indexed legend.
    expect(list).not.toHaveTextContent(/0-indexed/);
  });

  it("renders a notebook citation's raw 0-based cell_index with the legend, and a truncated preview", async () => {
    streamStudentChatMock.mockImplementation(
      scripted([
        resolvedHere,
        {
          event: "citations",
          citations: [
            {
              source_type: "notebook",
              source_file_id: 20,
              session_id: 7,
              similarity: 0.5,
              snippet: "# Build the calculator tool",
              filename: "Week 10_Lab3.ipynb",
              cell_index: 0,
              cell_type: "markdown",
            },
            {
              source_type: "lecture",
              source_file_id: 3,
              session_id: 7,
              similarity: 0.4,
              snippet: "Slide text",
              filename: "Week10_Lecture.pptx",
              slide_number: null,
              source: null,
            },
          ],
        },
        done,
      ]),
    );
    await renderChat();
    await ask("q");

    const list = await screen.findByTestId("citation-list");
    expect(list).toHaveTextContent("Week 10_Lab3.ipynb");
    expect(list).toHaveTextContent("Cell 0 · markdown");
    expect(list).not.toHaveTextContent("Cell 1");
    expect(list).toHaveTextContent(/0-indexed/);
    expect(list).toHaveTextContent("Week10_Lecture.pptx");
    expect(list.textContent).not.toMatch(/undefined|null|N\/A|Slide null/);
    // Similarity is deliberately not displayed.
    expect(list).not.toHaveTextContent("0.5");

    await userEvent.click(within(list).getAllByRole("button", { name: /show preview/i })[0]);
    expect(list).toHaveTextContent("# Build the calculator tool");
    expect(list).toHaveTextContent(/truncated preview/i);
  });

  it("renders no citation area when citations are empty or absent", async () => {
    streamStudentChatMock.mockImplementationOnce(
      scripted([resolvedHere, { event: "citations", citations: [] }, { event: "token", text: "A." }, done]),
    );
    await renderChat();
    await ask("q1");
    await screen.findByText("A.");
    expect(screen.queryByTestId("citation-list")).not.toBeInTheDocument();

    streamStudentChatMock.mockImplementationOnce(scripted([resolvedHere, { event: "token", text: "B." }, done]));
    await ask("q2");
    await screen.findByText("B.");
    expect(screen.queryByTestId("citation-list")).not.toBeInTheDocument();
  });
});

describe("<StudentChat /> — errors", () => {
  it("renders an error event as an error state, not as an assistant answer", async () => {
    streamStudentChatMock.mockImplementation(
      scripted([resolvedHere, { event: "error", message: "The course assistant is temporarily unavailable." }], "error"),
    );
    await renderChat();
    await ask("q");

    expect(await screen.findByRole("alert")).toHaveTextContent("The course assistant is temporarily unavailable.");
    expect(screen.queryByTestId("assistant-message")).not.toBeInTheDocument();
  });

  it("flags a stream that ended without done as incomplete", async () => {
    streamStudentChatMock.mockImplementation(
      scripted([resolvedHere, { event: "token", text: "Half an ans" }], "incomplete"),
    );
    await renderChat();
    await ask("q");
    expect(await screen.findByRole("alert")).toHaveTextContent(/incomplete/i);
  });

  it("renders a transport/API failure with its real message", async () => {
    streamStudentChatMock.mockRejectedValue(new ApiError(403, "Student role required."));
    await renderChat();
    await ask("q");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Student role required.");
    expect(alert).toHaveTextContent(/incomplete/i);
  });
});

describe("<StudentChat /> — search all sessions toggle", () => {
  const toggle = () => screen.getByLabelText("Search all sessions, not only this one");

  it("is off by default, and off sends the page's session id", async () => {
    await renderChat();
    expect(toggle()).not.toBeChecked();
    await ask("q");
    expect(streamStudentChatMock.mock.calls[0][0]).toEqual({ question: "q", currentSessionId: 7 });
  });

  it("on sends current_session_id: null", async () => {
    await renderChat();
    await userEvent.click(toggle());
    await ask("q");
    expect(streamStudentChatMock.mock.calls[0][0]).toEqual({ question: "q", currentSessionId: null });
  });

  it("wins over a stored resolved id, stays on after a resolution, and off resumes the last resolved id", async () => {
    streamStudentChatMock
      .mockImplementationOnce(
        scripted([{ event: "resolved", session_id: 9, session_title: "Week 9 Day 2", resolution: "redirected" }, done]),
      )
      .mockImplementationOnce(
        scripted([{ event: "resolved", session_id: 4, session_title: "Week 2 Day 2", resolution: "broad_search" }, done]),
      )
      .mockImplementationOnce(
        scripted([{ event: "resolved", session_id: 4, session_title: "Week 2 Day 2", resolution: "broad_search" }, done]),
      );
    await renderChat();

    await ask("first");
    expect(streamStudentChatMock.mock.calls[0][0].currentSessionId).toBe(7);

    await userEvent.click(toggle());
    await ask("second");
    expect(streamStudentChatMock.mock.calls[1][0].currentSessionId).toBeNull();
    await waitFor(() => expect(screen.getAllByTestId("session-banner")).toHaveLength(2));
    // Not silently flipped off by the resolution.
    expect(toggle()).toBeChecked();

    await ask("third");
    expect(streamStudentChatMock.mock.calls[2][0].currentSessionId).toBeNull();

    await userEvent.click(toggle());
    await ask("fourth");
    expect(streamStudentChatMock.mock.calls[3][0].currentSessionId).toBe(4);
  });

  it("renders a broad_search resolution in the banner with the real resolution value", async () => {
    streamStudentChatMock.mockImplementationOnce(
      scripted([
        { event: "resolved", session_id: 4, session_title: "Week 2 Day 2", resolution: "broad_search" },
        { event: "token", text: "From week 2." },
        done,
      ]),
    );
    await renderChat();
    await userEvent.click(toggle());
    await ask("how does groupby work?");
    expect(await screen.findByTestId("session-banner")).toHaveTextContent("Answering from Week 2 Day 2 (broad_search)");
    expect(screen.getByRole("heading", { name: /ask about week 10 day 3/i })).toBeInTheDocument();
  });

  it("with the toggle on, choosing a clarification candidate still sends that candidate's id", async () => {
    streamStudentChatMock.mockImplementationOnce(
      scripted(
        [
          {
            event: "clarification_needed",
            message: "Which session?",
            candidates: [{ session_id: 6, session_title: "Week 10 Day 2", best_similarity: 0.58 }],
          },
        ],
        "clarification",
      ),
    );
    await renderChat();
    await userEvent.click(toggle());
    await ask("What is the TODO in this lab?");
    await userEvent.click(await screen.findByRole("button", { name: /similarity/ }));
    await waitFor(() => expect(streamStudentChatMock).toHaveBeenCalledTimes(2));
    expect(streamStudentChatMock.mock.calls[1][0]).toEqual({ question: "What is the TODO in this lab?", currentSessionId: 6 });
  });
});

describe("<StudentChat /> — clarification", () => {
  it("renders one button per candidate and resends the identical question as a new visible turn", async () => {
    const original = "What should I do for the TODO in this assignment?";
    streamStudentChatMock.mockImplementationOnce(
      scripted(
        [
          {
            event: "clarification_needed",
            message: "I found a few sessions that could match — did you mean one of these? Week 10 Day 2, Week 10 Day 3",
            candidates: [
              { session_id: 6, session_title: "Week 10 Day 2", best_similarity: 0.5812 },
              { session_id: 7, session_title: "Week 10 Day 3", best_similarity: 0.5731 },
            ],
          },
        ],
        "clarification",
      ),
    );
    await renderChat();
    await ask(original);

    const buttons = await screen.findAllByRole("button", { name: /similarity/ });
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveTextContent("Week 10 Day 2 (similarity 0.5812)");

    await userEvent.click(buttons[0]);

    await waitFor(() => expect(streamStudentChatMock).toHaveBeenCalledTimes(2));
    expect(streamStudentChatMock.mock.calls[1][0]).toEqual({ question: original, currentSessionId: 6 });
    const userTurns = screen.getAllByTestId("user-message");
    expect(userTurns).toHaveLength(2);
    expect(userTurns[1].textContent).toBe(original);
  });
});
