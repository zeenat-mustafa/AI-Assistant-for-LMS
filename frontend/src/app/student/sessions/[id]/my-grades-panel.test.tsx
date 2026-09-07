/** Own-grades display: graded / ungraded / not-submitted, and no raw rationale. */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { GradeRead, GradeSummary, SubmissionRead } from "@/lib/api";

import { MyGradesPanel } from "./my-grades-panel";

function grade(overrides: Partial<GradeRead> = {}): GradeRead {
  return {
    id: 1,
    submission_file_id: 49,
    original_filename: "Numpy_and_Plotting.ipynb",
    score: 8.5,
    feedback_text: "Strong array work; the plotting section is incomplete.",
    rationale: null,
    graded_at: "2026-09-07T11:00:00",
    ...overrides,
  };
}

function summary(overrides: Partial<GradeSummary> = {}): GradeSummary {
  return {
    student_id: 2,
    student_name: "Demo Student",
    per_file: [grade()],
    combined_score: 4.5,
    ...overrides,
  };
}

const SUBMISSION: SubmissionRead = {
  id: 16,
  session_id: 3,
  student_id: 2,
  original_filename: "solution.ipynb",
  submitted_at: "2026-09-07T10:00:00",
  files: [
    { id: 49, original_filename: "solution.ipynb", matched_unsolved_file_id: 6, graded: true },
  ],
};

describe("<MyGradesPanel />", () => {
  it("shows a loading state", () => {
    render(
      <MyGradesPanel
        grades={undefined}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={2}
      />,
    );
    expect(screen.getByText(/loading your grade/i)).toBeInTheDocument();
  });

  it("says nothing to grade when there is no submission", () => {
    render(
      <MyGradesPanel
        grades={summary({ per_file: [], combined_score: 0 })}
        error={null}
        submission={null}
        totalAssignmentFiles={2}
      />,
    );
    expect(screen.getByText(/you haven't submitted for this session/i)).toBeInTheDocument();
    expect(screen.queryByText(/0 \/ 10/)).not.toBeInTheDocument();
  });

  it("shows 'not graded yet' rather than a false zero for an ungraded submission", () => {
    // combined_score is 0.0 here but nothing is graded -- the empty per_file
    // list is the signal, not the score.
    render(
      <MyGradesPanel
        grades={summary({ per_file: [], combined_score: 0 })}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={2}
      />,
    );
    expect(screen.getByText(/hasn't been graded yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/0 \/ 10/)).not.toBeInTheDocument();
  });

  it("shows a genuine zero as a real score", () => {
    render(
      <MyGradesPanel
        grades={summary({ per_file: [grade({ score: 0 })], combined_score: 0 })}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );
    expect(screen.getByText("0 / 10")).toBeInTheDocument();
    expect(screen.queryByText(/hasn't been graded yet/i)).not.toBeInTheDocument();
  });

  it("shows per-file score and the human-readable feedback", () => {
    render(
      <MyGradesPanel
        grades={summary()}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );
    expect(screen.getByText("Numpy_and_Plotting.ipynb")).toBeInTheDocument();
    expect(screen.getByText("8.5 / 10")).toBeInTheDocument();
    expect(
      screen.getByText(/strong array work; the plotting section is incomplete/i),
    ).toBeInTheDocument();
  });

  it("shows the combined score only when the session has several files", () => {
    const { rerender } = render(
      <MyGradesPanel
        grades={summary()}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );
    expect(screen.queryByText("Combined score")).not.toBeInTheDocument();

    rerender(
      <MyGradesPanel
        grades={summary()}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={2}
      />,
    );
    expect(screen.getByText("Combined score")).toBeInTheDocument();
    // Displayed as returned -- never recomputed client-side.
    expect(screen.getByText("4.5 / 10")).toBeInTheDocument();
  });

  it("explains that the combined score counts ungraded files too", () => {
    render(
      <MyGradesPanel
        grades={summary()}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={3}
      />,
    );
    expect(screen.getByText(/1 of 3 assignment files graded so far/i)).toBeInTheDocument();
  });

  it("never renders the structured rationale, even when the API returns it", () => {
    render(
      <MyGradesPanel
        grades={summary({
          per_file: [
            grade({
              rationale: [
                {
                  criterion: "Array reshaping",
                  points_possible: 3,
                  points_awarded: 2.5,
                  explanation: "INTERNAL-RATIONALE-MARKER",
                },
              ],
            }),
          ],
        })}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );

    // The human-readable feedback is shown...
    expect(screen.getByText(/strong array work/i)).toBeInTheDocument();
    // ...but nothing from rationale_json is.
    expect(screen.queryByText(/INTERNAL-RATIONALE-MARKER/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Array reshaping/)).not.toBeInTheDocument();
  });

  it("surfaces a load failure", () => {
    render(
      <MyGradesPanel
        grades={undefined}
        error="Session 3 not found."
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Session 3 not found.");
  });
});
