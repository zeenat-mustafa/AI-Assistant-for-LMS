/** Quiz history page (7.7): real attempts, verbatim score labels, per-attempt view, states. */



import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "@testing-library/react";

import userEvent from "@testing-library/user-event";



import { ApiError } from "@/lib/api";

import type { QuizHistoryOut, QuizResultOut } from "@/lib/api";



vi.mock("next/navigation", () => ({

  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),

  usePathname: () => "/student/quizzes",

  useSearchParams: () => new URLSearchParams(),

}));



const getQuizHistoryMock = vi.fn();

const getQuizAttemptMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {

  const actual = await importOriginal<typeof import("@/lib/api")>();

  return {

    ...actual,

    getQuizHistory: (...a: unknown[]) => getQuizHistoryMock(...a),

    getQuizAttempt: (...a: unknown[]) => getQuizAttemptMock(...a),

  };

});



import { QuizHistory } from "./quiz-history";



const NOTICE = "Practice quiz — this score does not affect your real grades.";



const HISTORY: QuizHistoryOut = {

  attempts: [

    {

      attempt_id: 12,

      scope_type: "topic",

      scope_detail: { topic_text: "tool calling" },

      score: 4,

      max_score: 5,

      score_label: "4/5 (practice quiz — does not affect your real grades)",

      not_a_real_grade: true,

      created_at: "2026-09-13T10:00:00",

      submitted_at: "2026-09-13T10:05:00",

    },

  ],

  not_a_real_grade: true,

  notice: NOTICE,

};



const RESULT: QuizResultOut = {

  attempt_id: 12,

  scope_type: "topic",

  scope_detail: { topic_text: "tool calling" },

  score: 4,

  max_score: 5,

  score_label: "4/5 (practice quiz — does not affect your real grades)",

  not_a_real_grade: true,

  notice: NOTICE,

  questions: Array.from({ length: 5 }, (_, i) => ({

    question: `Stored question ${i + 1}?`,

    options: ["A", "B", "C", "D"],

    source_citation: "Week 10 Day 3 | lecture.pptx | slide 3",

    correct_option_index: 1,

    student_answer_index: i === 0 ? 0 : 1,

    is_correct: i !== 0,

  })),

  created_at: "2026-09-13T10:00:00",

  submitted_at: "2026-09-13T10:05:00",

};



beforeEach(() => {

  vi.clearAllMocks();

  getQuizHistoryMock.mockResolvedValue(HISTORY);

  getQuizAttemptMock.mockResolvedValue(RESULT);

});



describe("<QuizHistory />", () => {

  it("lists submitted attempts with the requested scope and the backend's score label", async () => {

    render(<QuizHistory />);

    expect(await screen.findByText("4/5 (practice quiz — does not affect your real grades)")).toBeInTheDocument();

    expect(screen.getByText("Topic (topic_text: tool calling)")).toBeInTheDocument();

    expect(screen.getByText(/practice only.*never changes your real grades/i)).toBeInTheDocument();

    expect(screen.getByText(/only submitted quizzes appear here/i)).toBeInTheDocument();

  });



  it("opens one attempt's full stored result", async () => {

    render(<QuizHistory />);

    await userEvent.click(await screen.findByRole("button", { name: /topic_text: tool calling/i }));

    expect(getQuizAttemptMock).toHaveBeenCalledWith(12);

    expect(await screen.findByText(/Stored question 1\?/)).toBeInTheDocument();

    expect(screen.getByTestId("quiz-score")).toHaveTextContent("4 / 5");

  });



  it("shows an empty state", async () => {

    getQuizHistoryMock.mockResolvedValue({ ...HISTORY, attempts: [] });

    render(<QuizHistory />);

    expect(await screen.findByText(/haven't submitted any practice quizzes/i)).toBeInTheDocument();

  });



  it("shows a load error", async () => {

    getQuizHistoryMock.mockRejectedValue(new ApiError(403, "Student role required."));

    render(<QuizHistory />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Student role required.");

  });

});

