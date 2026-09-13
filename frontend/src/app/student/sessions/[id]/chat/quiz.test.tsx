/**
 * In-chat practice quiz (7.7): scope modes, answer-leak marker, submit gate,
 * backend score shown verbatim, 409 handling, non-grade labels, abandonment.
 * All API calls mocked.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { QuizAttemptOut, QuizResultOut, SessionRead, UserRead } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/student/sessions/7/chat",
  useSearchParams: () => new URLSearchParams(),
}));

const getSessionMock = vi.fn();
const listSessionsMock = vi.fn();
const generateQuizMock = vi.fn();
const generateQuizFromUploadMock = vi.fn();
const submitQuizMock = vi.fn();
const getQuizAttemptMock = vi.fn();
const streamStudentChatMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getSession: (...a: unknown[]) => getSessionMock(...a),
    listSessions: (...a: unknown[]) => listSessionsMock(...a),
    generateQuiz: (...a: unknown[]) => generateQuizMock(...a),
    generateQuizFromUpload: (...a: unknown[]) => generateQuizFromUploadMock(...a),
    submitQuiz: (...a: unknown[]) => submitQuizMock(...a),
    getQuizAttempt: (...a: unknown[]) => getQuizAttemptMock(...a),
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
vi.mock("@/lib/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/auth-context")>();
  return {
    ...actual,
    useAuth: () => ({
      user: STUDENT,
      status: "authenticated" as const,
      signIn: vi.fn(),
      signOut: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});

import { StudentChat } from "./student-chat";
import { PRACTICE_ONLY_LINE, QuizCard } from "@/components/quiz-views";

const NOTICE = "Practice quiz — this score does not affect your real grades.";

const SESSION: SessionRead = {
  id: 7,
  title: "Week 10 Day 3",
  instructor_id: 1,
  instructor_name: "Demo Instructor",
  created_at: "2026-09-06T15:43:53",
  assignment_uploads: [],
  unsolved_files: [
    { id: 20, session_id: 7, original_filename: "Week 10_Lab3.ipynb", rubric_generated: true, uploaded_at: "2026-09-06T15:44:00" },
    { id: 21, session_id: 7, original_filename: "Week10_Day2.ipynb", rubric_generated: false, uploaded_at: "2026-09-06T15:45:00" },
  ],
  resource_files: [],
};

function attempt(overrides: Partial<QuizAttemptOut> = {}): QuizAttemptOut {
  return {
    id: 41,
    scope_type: "session",
    scope_detail: { session_id: 7 },
    questions: Array.from({ length: 5 }, (_, i) => ({
      question: `Question text ${i + 1}?`,
      options: [`Q${i + 1} option A`, `Q${i + 1} option B`, `Q${i + 1} option C`, `Q${i + 1} option D`],
      source_citation: `Week 10 Day 3 · Week10_Lecture.pptx · slide ${i + 2}`,
    })),
    max_score: 5,
    submitted: false,
    created_at: "2026-09-13T10:00:00",
    notice: NOTICE,
    ...overrides,
  };
}

function result(overrides: Partial<QuizResultOut> = {}): QuizResultOut {
  const base = attempt();
  return {
    attempt_id: 41,
    scope_type: "session",
    scope_detail: { session_id: 7 },
    score: 3,
    max_score: 5,
    score_label: "3/5 (practice quiz — does not affect your real grades)",
    not_a_real_grade: true,
    notice: NOTICE,
    questions: base.questions.map((q, i) => ({
      ...q,
      correct_option_index: 2,
      student_answer_index: i < 3 ? 2 : 0,
      is_correct: i < 3,
    })),
    created_at: "2026-09-13T10:00:00",
    submitted_at: "2026-09-13T10:02:00",
    ...overrides,
  };
}

async function answerAll(count = 5) {
  const radios = screen.getAllByRole("radio");
  for (let q = 0; q < count; q += 1) await userEvent.click(radios[q * 4]);
}

async function openQuizForm() {
  render(<StudentChat sessionId={7} />);
  await screen.findByRole("heading", { name: /ask about week 10 day 3/i });
  await userEvent.click(screen.getByRole("button", { name: /quiz me/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getSessionMock.mockResolvedValue(SESSION);
  generateQuizMock.mockResolvedValue(attempt());
  generateQuizFromUploadMock.mockResolvedValue(attempt({ scope_type: "uploaded_file" }));
  listSessionsMock.mockResolvedValue({
    total: 3,
    items: [SESSION, { ...SESSION, id: 5, title: "Week 10 Day 1" }, { ...SESSION, id: 6, title: "Week 10 Day 2" }],
  });
});

describe("quiz scope modes — each issues the right call with exactly one scope field", () => {
  it("this whole session", async () => {
    await openQuizForm();
    await userEvent.click(screen.getByRole("button", { name: /generate quiz/i }));
    await waitFor(() => expect(generateQuizMock).toHaveBeenCalledTimes(1));
    expect(generateQuizMock.mock.calls[0][0]).toEqual({ scope_type: "session", session_id: 7 });
    expect(await screen.findByRole("region", { name: "Practice quiz" })).toBeInTheDocument();
  });

  it("one assignment file, picked from the session's real files", async () => {
    await openQuizForm();
    await userEvent.click(screen.getByLabelText("One assignment file"));
    await userEvent.selectOptions(screen.getByLabelText("Assignment file"), "21");
    await userEvent.click(screen.getByRole("button", { name: /generate quiz/i }));
    await waitFor(() =>
      expect(generateQuizMock.mock.calls[0][0]).toEqual({ scope_type: "assignment_file", unsolved_file_id: 21 }),
    );
  });

  it("several sessions -- disabled until at least two are chosen", async () => {
    await openQuizForm();
    await userEvent.click(screen.getByLabelText("Several sessions"));
    await userEvent.click(await screen.findByLabelText("Week 10 Day 3"));
    expect(screen.getByRole("button", { name: /generate quiz/i })).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Week 10 Day 1"));
    await userEvent.click(screen.getByRole("button", { name: /generate quiz/i }));
    await waitFor(() =>
      expect(generateQuizMock.mock.calls[0][0]).toEqual({ scope_type: "multiple_sessions", session_ids: [7, 5] }),
    );
  });

  it("a topic", async () => {
    await openQuizForm();
    await userEvent.click(screen.getByLabelText("A topic"));
    await userEvent.type(screen.getByLabelText("Topic"), "tool calling");
    await userEvent.click(screen.getByRole("button", { name: /generate quiz/i }));
    await waitFor(() =>
      expect(generateQuizMock.mock.calls[0][0]).toEqual({ scope_type: "topic", topic_text: "tool calling" }),
    );
  });

  it("a file uploaded now -- multipart endpoint, stated as not saved", async () => {
    const user = userEvent.setup({ applyAccept: false });
    await openQuizForm();
    await user.click(screen.getByLabelText("A file I upload now"));
    expect(screen.getByText(/used for this quiz only.*not saved as a submission/i)).toBeInTheDocument();

    const file = new File(["{}"], "my_notes.ipynb");
    await user.upload(screen.getByLabelText("File (.pptx or .ipynb)"), file);
    await user.click(screen.getByRole("button", { name: /generate quiz/i }));

    await waitFor(() => expect(generateQuizFromUploadMock).toHaveBeenCalledWith(file));
    expect(generateQuizMock).not.toHaveBeenCalled();
  });

  it("blocks a .ppt upload with the legacy-format wording", async () => {
    const user = userEvent.setup({ applyAccept: false });
    await openQuizForm();
    await user.click(screen.getByLabelText("A file I upload now"));
    await user.upload(screen.getByLabelText("File (.pptx or .ipynb)"), new File(["x"], "old.ppt"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Legacy .ppt format is not supported — please save as .pptx and re-upload.",
    );
    expect(screen.getByRole("button", { name: /generate quiz/i })).toBeDisabled();
  });

  it("shows the backend's generation error verbatim", async () => {
    generateQuizMock.mockRejectedValue(new ApiError(422, "Not enough course material in that scope to build a quiz."));
    await openQuizForm();
    await userEvent.click(screen.getByRole("button", { name: /generate quiz/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Not enough course material in that scope to build a quiz.",
    );
  });

  it("cancelling the form calls nothing", async () => {
    await openQuizForm();
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(generateQuizMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /quiz me/i })).toBeInTheDocument();
  });
});

describe("<QuizCard />", () => {
  it("MARKER: no correct answer reaches the DOM before submission, and it does after", async () => {
    // Simulate a future backend regression that starts sending the answer early.
    const leaky = attempt();
    (leaky.questions as unknown as Array<Record<string, unknown>>).forEach((q) => {
      q.correct_option_index = 2;
    });
    submitQuizMock.mockResolvedValue(result());

    render(<QuizCard attempt={leaky} />);

    expect(document.body.innerHTML).not.toContain("correct_option_index");
    expect(document.body.textContent).not.toMatch(/correct/i);

    await answerAll();
    await userEvent.click(screen.getByRole("button", { name: /submit answers/i }));

    await screen.findByRole("region", { name: "Practice quiz result" });
    expect(screen.getAllByText("(correct answer)")).toHaveLength(5);
    expect(screen.getAllByText(/✓ Correct/)).toHaveLength(3);
    expect(screen.getAllByText(/✕ Incorrect/)).toHaveLength(2);
  });

  it("blocks submit until all five questions are answered, then sends the chosen indices", async () => {
    submitQuizMock.mockResolvedValue(result());
    render(<QuizCard attempt={attempt()} />);
    const submit = screen.getByRole("button", { name: /submit answers/i });
    expect(submit).toBeDisabled();

    const radios = screen.getAllByRole("radio");
    await userEvent.click(radios[1]); // q1 -> B
    await userEvent.click(radios[0]); // q1 changed -> A
    await userEvent.click(radios[4 + 3]); // q2 -> D
    await userEvent.click(radios[8 + 2]); // q3 -> C
    await userEvent.click(radios[12 + 1]); // q4 -> B
    expect(submit).toBeDisabled();
    await userEvent.click(submit);
    expect(submitQuizMock).not.toHaveBeenCalled();

    await userEvent.click(radios[16]); // q5 -> A
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await waitFor(() => expect(submitQuizMock).toHaveBeenCalledWith(41, [0, 3, 2, 1, 0]));
  });

  it("displays exactly the backend's score even when a client-side tally would differ", async () => {
    // is_correct is true for only 1 question, but the backend says 4/5 and a custom label.
    const r = result({ score: 4, score_label: "4/5 (practice quiz — does not affect your real grades)" });
    r.questions = r.questions.map((q, i) => ({ ...q, is_correct: i === 0 }));
    submitQuizMock.mockResolvedValue(r);

    render(<QuizCard attempt={attempt()} />);
    await answerAll();
    await userEvent.click(screen.getByRole("button", { name: /submit answers/i }));

    expect(await screen.findByTestId("quiz-score")).toHaveTextContent("4 / 5");
    expect(screen.getByText("4/5 (practice quiz — does not affect your real grades)")).toBeInTheDocument();
    expect(screen.queryByText(/1 \/ 5|20%|80%/)).not.toBeInTheDocument();
  });

  it("on a 409, fetches and shows the stored result instead of an error", async () => {
    submitQuizMock.mockRejectedValue(new ApiError(409, "This quiz attempt was already submitted and can't be re-scored."));
    getQuizAttemptMock.mockResolvedValue(result({ score: 2, score_label: "2/5 (practice quiz — does not affect your real grades)" }));

    render(<QuizCard attempt={attempt()} />);
    await answerAll();
    await userEvent.click(screen.getByRole("button", { name: /submit answers/i }));

    expect(await screen.findByTestId("quiz-score")).toHaveTextContent("2 / 5");
    expect(getQuizAttemptMock).toHaveBeenCalledWith(41);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a non-409 submit failure as an error", async () => {
    submitQuizMock.mockRejectedValue(new ApiError(403, "You can only access your own quiz attempts."));
    render(<QuizCard attempt={attempt()} />);
    await answerAll();
    await userEvent.click(screen.getByRole("button", { name: /submit answers/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You can only access your own quiz attempts.");
  });

  it("carries the non-grade label on both the card and the result", async () => {
    submitQuizMock.mockResolvedValue(result());
    render(<QuizCard attempt={attempt()} resultFooter="visit-only note" />);

    const card = screen.getByRole("region", { name: "Practice quiz" });
    expect(card).toHaveTextContent(PRACTICE_ONLY_LINE);
    expect(card).toHaveTextContent(NOTICE);

    await answerAll();
    await userEvent.click(screen.getByRole("button", { name: /submit answers/i }));

    const res = await screen.findByRole("region", { name: "Practice quiz result" });
    expect(res).toHaveTextContent(PRACTICE_ONLY_LINE);
    expect(res).toHaveTextContent(NOTICE);
    expect(res).toHaveTextContent("visit-only note");
  });
});

describe("abandoning a quiz", () => {
  it("leaving the chat page with an unsubmitted quiz calls no submit endpoint", async () => {
    const { unmount } = render(<StudentChat sessionId={7} />);
    await screen.findByRole("heading", { name: /ask about week 10 day 3/i });
    await userEvent.click(screen.getByRole("button", { name: /quiz me/i }));
    await userEvent.click(screen.getByRole("button", { name: /generate quiz/i }));
    await screen.findByRole("region", { name: "Practice quiz" });

    const radios = screen.getAllByRole("radio");
    await userEvent.click(radios[0]);

    // Navigating away unmounts the page.
    unmount();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(submitQuizMock).not.toHaveBeenCalled();
    expect(getQuizAttemptMock).not.toHaveBeenCalled();
  });
});
