/**
 * Floating student course-assistant widget tests (Phase 7.7).
 * Tests:
 *  - visibility & collapse/expand
 *  - session detection from pathname
 *  - tabs (Chat vs Quiz history)
 *  - chat streaming, token rendering, thought indicator
 *  - redirect banner when resolved elsewhere
 *  - citations display (notebook cells & lecture slides)
 *  - clarification candidate selection & resend
 *  - error handling (SSE error event, incomplete stream, API failure)
 *  - practice quiz generation, submission & result rendering
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { UserRead } from "@/lib/api";
import { streamStudentChat, generateQuiz, submitQuiz } from "@/lib/api";

const streamStudentChatMock = vi.fn();
const generateQuizMock = vi.fn();
const generateQuizFromUploadMock = vi.fn();
const submitQuizMock = vi.fn();
const getQuizHistoryMock = vi.fn();
const listSessionsMock = vi.fn();

let mockPathname = "/student";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    streamStudentChat: (...a: unknown[]) => streamStudentChatMock(...a),
    generateQuiz: (...a: unknown[]) => generateQuizMock(...a),
    generateQuizFromUpload: (...a: unknown[]) => generateQuizFromUploadMock(...a),
    submitQuiz: (...a: unknown[]) => submitQuizMock(...a),
    getQuizHistory: (...a: unknown[]) => getQuizHistoryMock(...a),
    listSessions: (...a: unknown[]) => listSessionsMock(...a),
  };
});

let currentUser: UserRead | null = {
  id: 2,
  name: "Demo Student",
  email: "student@demo.com",
  role: "student",
};

vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({
    user: currentUser,
    status: currentUser ? "authenticated" : "unauthenticated",
  }),
}));

import { StudentChatWidget } from "./student-chat-widget";

const user = userEvent.setup();

beforeEach(() => {
  vi.clearAllMocks();
  mockPathname = "/student";
  currentUser = {
    id: 2,
    name: "Demo Student",
    email: "student@demo.com",
    role: "student",
  };
  getQuizHistoryMock.mockResolvedValue({
    attempts: [],
    not_a_real_grade: true,
    notice: "Notice",
  });
});

describe("<StudentChatWidget /> — visibility & collapse/expand", () => {
  it("renders nothing for instructors or unauthenticated users", () => {
    currentUser = { id: 1, name: "Prof", email: "prof@demo.com", role: "instructor" };
    const { container } = render(<StudentChatWidget />);
    expect(container).toBeEmptyDOMElement();

    currentUser = null;
    const { container: unauthContainer } = render(<StudentChatWidget />);
    expect(unauthContainer).toBeEmptyDOMElement();
  });

  it("renders the floating button, opens on click, and closes on x", async () => {
    render(<StudentChatWidget />);
    const openBtn = screen.getByRole("button", { name: /open course assistant/i });
    expect(openBtn).toBeInTheDocument();

    await user.click(openBtn);
    expect(screen.getByRole("region", { name: /course assistant/i })).toBeInTheDocument();
    expect(screen.getByText(/ask about lectures or assignments/i)).toBeInTheDocument();

    const closeBtn = screen.getByRole("button", { name: /close course assistant/i });
    await user.click(closeBtn);
    expect(screen.getByTestId("student-chat-transcript", { hidden: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open course assistant/i })).toBeInTheDocument();
  });

  it("preserves conversation history when closed and reopened", async () => {
    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "Persistent question?");

    const closeBtn = screen.getByRole("button", { name: /close course assistant/i });
    await user.click(closeBtn);

    const openBtn = screen.getByRole("button", { name: /open course assistant/i });
    await user.click(openBtn);

    expect(textarea).toHaveValue("Persistent question?");
  });
});

describe("<StudentChatWidget /> — session detection & tabs", () => {
  it("detects session id from url when on /student/sessions/5", async () => {
    mockPathname = "/student/sessions/5";
    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    expect(screen.getByText(/scoped to session #5 by default/i)).toBeInTheDocument();
  });

  it("switches to quiz history tab", async () => {
    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const historyTab = screen.getByRole("button", { name: /^quiz history$/i });
    await user.click(historyTab);

    expect(getQuizHistoryMock).toHaveBeenCalled();
  });
});

describe("<StudentChatWidget /> — streaming & interaction", () => {
  it("streams a question and displays tokens and citations", async () => {
    streamStudentChatMock.mockImplementation(async (_req, handlers) => {
      handlers.onResolved?.({
        event: "resolved",
        session_id: 3,
        session_title: "Week 2 Day 1",
        resolution: "current_session",
      });
      handlers.onToken?.({ event: "token", text: "Pandas " });
      handlers.onToken?.({ event: "token", text: "DataFrames are tabular." });
      handlers.onCitations?.({
        event: "citations",
        citations: [
          {
            source_type: "notebook",
            cell_index: 2,
            cell_type: "markdown",
            filename: "pandas.ipynb",
            source_file_id: 10,
            session_id: 3,
            similarity: 0.85,
            snippet: "Intro to DataFrames preview text",
          },
          {
            source_type: "lecture",
            slide_number: 4,
            source: "slide_text",
            filename: "lecture2.pptx",
            source_file_id: 2,
            session_id: 3,
            similarity: 0.75,
            snippet: "Slide content here",
          },
        ],
      });
      handlers.onDone?.({
        event: "done",
        thread_id: 1,
        user_message_id: 1,
        assistant_message_id: 2,
      });
      return { outcome: "completed", unknownEventCount: 0 };
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "What is a DataFrame?");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() => {
      expect(screen.getByTestId("student-user-message")).toHaveTextContent("What is a DataFrame?");
      expect(screen.getByTestId("student-assistant-message")).toHaveTextContent("Pandas DataFrames are tabular.");
    });
    expect(screen.getByText("pandas.ipynb")).toBeInTheDocument();
    expect(screen.getByText(/Cell 2/)).toBeInTheDocument();
    expect(screen.getByText("lecture2.pptx")).toBeInTheDocument();
    expect(screen.getByText(/Slide 4/)).toBeInTheDocument();
  });

  it("shows redirect banner when resolved to a different session from current URL", async () => {
    mockPathname = "/student/sessions/5";
    streamStudentChatMock.mockImplementation(async (_req, handlers) => {
      handlers.onResolved?.({
        event: "resolved",
        session_id: 2,
        session_title: "Week 1 Day 2",
        resolution: "redirected",
      });
      handlers.onToken?.({ event: "token", text: "Answer from week 1." });
      handlers.onDone?.({
        event: "done",
        thread_id: 1,
        user_message_id: 1,
        assistant_message_id: 2,
      });
      return { outcome: "completed", unknownEventCount: 0 };
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "How does basic Python work?");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByTestId("student-session-banner")).toHaveTextContent(
      "Answering about Week 1 Day 2 instead.",
    );
    expect(screen.getByTestId("student-assistant-message")).toHaveTextContent("Answer from week 1.");
  });

  it("shows broad search banner when resolved via broad_search", async () => {
    streamStudentChatMock.mockImplementation(async (_req, handlers) => {
      handlers.onResolved?.({
        event: "resolved",
        session_id: 3,
        session_title: "Week 2 Day 1",
        resolution: "broad_search",
      });
      handlers.onToken?.({ event: "token", text: "Broad answer." });
      handlers.onDone?.({
        event: "done",
        thread_id: 1,
        user_message_id: 1,
        assistant_message_id: 2,
      });
      return { outcome: "completed", unknownEventCount: 0 };
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "General question");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByTestId("student-session-banner")).toHaveTextContent(
      "Searched across your sessions to answer this.",
    );
  });

  it("shows no banner when resolved via current_session", async () => {
    streamStudentChatMock.mockImplementation(async (_req, handlers) => {
      handlers.onResolved?.({
        event: "resolved",
        session_id: 3,
        session_title: "Week 2 Day 1",
        resolution: "current_session",
      });
      handlers.onToken?.({ event: "token", text: "Same session answer." });
      handlers.onDone?.({
        event: "done",
        thread_id: 1,
        user_message_id: 1,
        assistant_message_id: 2,
      });
      return { outcome: "completed", unknownEventCount: 0 };
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "Specific question");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() => {
      expect(screen.getByTestId("student-assistant-message")).toHaveTextContent("Same session answer.");
    });
    expect(screen.queryByTestId("student-session-banner")).not.toBeInTheDocument();
  });

  it("handles clarification events and allows student to pick candidate", async () => {
    streamStudentChatMock.mockImplementationOnce(async (_req, handlers) => {
      handlers.onClarification?.({
        event: "clarification_needed",
        message: "Which session do you mean?",
        candidates: [
          { session_id: 5, session_title: "Week 3 Day 1", best_similarity: 0.75 },
          { session_id: 6, session_title: "Week 3 Day 2", best_similarity: 0.72 },
        ],
      });
      return { outcome: "completed", unknownEventCount: 0 };
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "Help with indexing");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Which session do you mean?")).toBeInTheDocument();
    const candidateBtn = screen.getByRole("button", { name: /Week 3 Day 1/i });
    expect(candidateBtn).toBeInTheDocument();

    streamStudentChatMock.mockImplementationOnce(async (_req, handlers) => {
      handlers.onToken?.({ event: "token", text: "Here is help with indexing." });
      return { outcome: "completed", unknownEventCount: 0 };
    });

    await user.click(candidateBtn);
    expect(streamStudentChatMock).toHaveBeenCalledWith(
      { question: "Help with indexing", currentSessionId: 5 },
      expect.any(Object),
      expect.any(Object),
    );
  });
});

describe("<StudentChatWidget /> — error handling", () => {
  it("renders an error event cleanly as an alert", async () => {
    streamStudentChatMock.mockImplementation(async (_req, handlers) => {
      handlers.onError?.({
        event: "error",
        message: "The course assistant is temporarily unavailable. Please try again later.",
      });
      return { outcome: "error", unknownEventCount: 0 };
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "Any question");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The course assistant is temporarily unavailable.");
  });

  it("flags a stream that ended prematurely without done as incomplete", async () => {
    streamStudentChatMock.mockImplementation(async (_req, handlers) => {
      handlers.onToken?.({ event: "token", text: "Half of an answer..." });
      return { outcome: "incomplete", unknownEventCount: 0 };
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "Incomplete question");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Half of an answer...")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/incomplete/i);
  });

  it("renders an API transport error with its detail", async () => {
    streamStudentChatMock.mockRejectedValue(new ApiError(403, "Student role required."));

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    const textarea = screen.getByPlaceholderText(/ask a question/i);
    await user.type(textarea, "Auth test");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Student role required.");
  });
});

describe("<StudentChatWidget /> — practice quiz", () => {
  it("opens Quiz me form, generates topic quiz, submits answers, and shows result", async () => {
    generateQuizMock.mockResolvedValue({
      id: 101,
      scope_type: "topic",
      scope_detail: { topic_text: "NumPy" },
      questions: [
        {
          question: "What is an array?",
          options: ["A list", "A grid", "A dict", "A set"],
          source_citation: "numpy.ipynb: cell 1",
        },
      ],
      max_score: 5,
      submitted: false,
      created_at: "2026-09-12T10:00:00",
      notice: "Practice quiz only",
    });

    submitQuizMock.mockResolvedValue({
      attempt_id: 101,
      scope_type: "topic",
      scope_detail: { topic_text: "NumPy" },
      score: 5,
      max_score: 5,
      score_label: "5/5 (practice quiz)",
      not_a_real_grade: true,
      notice: "Practice quiz — does not affect your real grade.",
      questions: [
        {
          question: "What is an array?",
          options: ["A list", "A grid", "A dict", "A set"],
          source_citation: "numpy.ipynb: cell 1",
          correct_option_index: 1,
          student_answer_index: 1,
          is_correct: true,
        },
      ],
      created_at: "2026-09-12T10:00:00",
      submitted_at: "2026-09-12T10:05:00",
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    await user.click(screen.getByRole("button", { name: /quiz me/i }));
    expect(screen.getByRole("form", { name: /start a practice quiz/i })).toBeInTheDocument();

    const topicInput = screen.getByLabelText(/^topic$/i);
    await user.type(topicInput, "NumPy");
    await user.click(screen.getByRole("button", { name: /generate quiz/i }));

    expect(await screen.findByText(/What is an array\?/)).toBeInTheDocument();
    expect(generateQuizMock).toHaveBeenCalledWith({
      scope_type: "topic",
      topic_text: "NumPy",
    });

    // Select option "A grid" (option 1)
    const radioGrid = screen.getByLabelText("A grid");
    await user.click(radioGrid);

    // Submit answers
    const submitBtn = screen.getByRole("button", { name: /submit answers/i });
    await user.click(submitBtn);

    await waitFor(() => {
      expect(submitQuizMock).toHaveBeenCalledWith(101, [1]);
      expect(screen.getByText("5/5 (practice quiz)")).toBeInTheDocument();
      expect(screen.getByText("Practice quiz — does not affect your real grade.")).toBeInTheDocument();
    });
  });

  it("submitting a second quiz does not reset the first quiz's result", async () => {
    // Quiz A
    generateQuizMock.mockResolvedValueOnce({
      id: 201,
      scope_type: "topic",
      scope_detail: { topic_text: "Pandas" },
      questions: [
        {
          question: "What is a DataFrame?",
          options: ["A table", "A dict", "A list", "A set"],
          source_citation: "pandas.ipynb: cell 1",
        },
      ],
      max_score: 5,
      submitted: false,
      created_at: "2026-09-12T10:00:00",
      notice: "Practice only",
    });
    submitQuizMock.mockResolvedValueOnce({
      attempt_id: 201,
      scope_type: "topic",
      scope_detail: { topic_text: "Pandas" },
      score: 5,
      max_score: 5,
      score_label: "5/5 — Quiz A",
      not_a_real_grade: true,
      notice: "Practice only",
      questions: [
        {
          question: "What is a DataFrame?",
          options: ["A table", "A dict", "A list", "A set"],
          source_citation: "pandas.ipynb: cell 1",
          correct_option_index: 0,
          student_answer_index: 0,
          is_correct: true,
        },
      ],
      created_at: "2026-09-12T10:00:00",
      submitted_at: "2026-09-12T10:05:00",
    });

    // Quiz B
    generateQuizMock.mockResolvedValueOnce({
      id: 202,
      scope_type: "topic",
      scope_detail: { topic_text: "NumPy" },
      questions: [
        {
          question: "What is an ndarray?",
          options: ["A matrix", "A dict", "A list", "A set"],
          source_citation: "numpy.ipynb: cell 1",
        },
      ],
      max_score: 5,
      submitted: false,
      created_at: "2026-09-12T10:10:00",
      notice: "Practice only",
    });
    submitQuizMock.mockResolvedValueOnce({
      attempt_id: 202,
      scope_type: "topic",
      scope_detail: { topic_text: "NumPy" },
      score: 0,
      max_score: 5,
      score_label: "0/5 — Quiz B",
      not_a_real_grade: true,
      notice: "Practice only",
      questions: [
        {
          question: "What is an ndarray?",
          options: ["A matrix", "A dict", "A list", "A set"],
          source_citation: "numpy.ipynb: cell 1",
          correct_option_index: 0,
          student_answer_index: 1,
          is_correct: false,
        },
      ],
      created_at: "2026-09-12T10:10:00",
      submitted_at: "2026-09-12T10:15:00",
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));

    // Generate and submit Quiz A
    await user.click(screen.getByRole("button", { name: /quiz me/i }));
    await user.type(screen.getByLabelText(/^topic$/i), "Pandas");
    await user.click(screen.getByRole("button", { name: /generate quiz/i }));
    expect(await screen.findByText(/What is a DataFrame\?/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("A table"));
    await user.click(screen.getByRole("button", { name: /submit answers/i }));
    await waitFor(() => expect(screen.getByText("5/5 — Quiz A")).toBeInTheDocument());

    // Generate and submit Quiz B
    await user.click(screen.getByRole("button", { name: /quiz me/i }));
    await user.type(screen.getByLabelText(/^topic$/i), "NumPy");
    await user.click(screen.getByRole("button", { name: /generate quiz/i }));
    expect(await screen.findByText(/What is an ndarray\?/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("A dict"));
    await user.click(screen.getByRole("button", { name: /submit answers/i }));
    await waitFor(() => expect(screen.getByText("0/5 — Quiz B")).toBeInTheDocument());

    // Quiz A's score must still be visible
    expect(screen.getByText("5/5 — Quiz A")).toBeInTheDocument();
  });
});

describe("<StudentChatWidget /> — multi-session quiz", () => {
  it("shows session checkboxes, sends multiple_sessions scope, and generates a quiz", async () => {
    listSessionsMock.mockResolvedValue({
      items: [
        { id: 1, title: "Week 1 Day 1", unsolved_files: [], resource_files: [], created_at: "" },
        { id: 2, title: "Week 1 Day 2", unsolved_files: [], resource_files: [], created_at: "" },
        { id: 3, title: "Week 2 Day 1", unsolved_files: [], resource_files: [], created_at: "" },
      ],
      total: 3,
      skip: 0,
      limit: 200,
    });

    generateQuizMock.mockResolvedValue({
      id: 301,
      scope_type: "multiple_sessions",
      scope_detail: { session_ids: [1, 2] },
      questions: [
        {
          question: "What is a variable?",
          options: ["A label", "A loop", "A class", "A module"],
          source_citation: "Week 1 Day 1: cell 1",
        },
      ],
      max_score: 5,
      submitted: false,
      created_at: "2026-09-12T10:00:00",
      notice: "Practice only",
    });

    render(<StudentChatWidget />);
    await user.click(screen.getByRole("button", { name: /open course assistant/i }));
    await user.click(screen.getByRole("button", { name: /quiz me/i }));

    // Switch to "Several sessions"
    await user.click(screen.getByLabelText(/several sessions/i));

    // Session list should load and show checkboxes
    expect(await screen.findByLabelText("Week 1 Day 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Week 1 Day 2")).toBeInTheDocument();

    // Generate button disabled until 2 selected
    expect(screen.getByRole("button", { name: /generate quiz/i })).toBeDisabled();

    // Pick two sessions
    await user.click(screen.getByLabelText("Week 1 Day 1"));
    await user.click(screen.getByLabelText("Week 1 Day 2"));

    // Now enabled
    expect(screen.getByRole("button", { name: /generate quiz/i })).not.toBeDisabled();

    await user.click(screen.getByRole("button", { name: /generate quiz/i }));

    // Quiz generated with correct scope
    await waitFor(() =>
      expect(generateQuizMock).toHaveBeenCalledWith({
        scope_type: "multiple_sessions",
        session_ids: [1, 2],
      }),
    );

    expect(await screen.findByText(/What is a variable\?/)).toBeInTheDocument();
  });
});
