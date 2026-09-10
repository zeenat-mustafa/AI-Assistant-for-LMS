/** Own-grades display: graded / ungraded / not-submitted, and no raw rationale. */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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

  it("renders the structured rationale as the primary detail when the API returns it", async () => {
    // bugfix-structured-rationale-display: rationale is now the primary
    // per-file detail; feedback_text is only shown when rationale is absent.
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

    await userEvent.click(screen.getByRole("button", { name: /Numpy_and_Plotting/ }));

    expect(screen.getByText("Array reshaping")).toBeInTheDocument();
    expect(screen.getByText("2.5 / 3")).toBeInTheDocument();
    expect(screen.getByText("INTERNAL-RATIONALE-MARKER")).toBeInTheDocument();
    // Rationale takes over -- the flat feedback text is not also shown.
    expect(screen.queryByText(/strong array work/i)).not.toBeInTheDocument();
    // No raw internal field names leak into the UI as literal text.
    expect(document.body.textContent).not.toContain("points_awarded");
    expect(document.body.textContent).not.toContain("points_possible");
  });

  it("falls back to feedback_text when rationale is null", () => {
    render(
      <MyGradesPanel
        grades={summary({ per_file: [grade({ rationale: null })] })}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );
    expect(screen.getByText(/strong array work/i)).toBeInTheDocument();
  });

  it("falls back to feedback_text when rationale is an empty array", () => {
    render(
      <MyGradesPanel
        grades={summary({ per_file: [grade({ rationale: [] })] })}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );
    expect(screen.getByText(/strong array work/i)).toBeInTheDocument();
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

/**
 * Fix D — summary first, per-file detail collapsed until asked for.
 *
 * These assert VISIBILITY, not presence. The detail stays mounted and is
 * toggled with the `hidden` attribute so find-in-page still reaches it, which
 * means `toBeInTheDocument` would pass while collapsed and prove nothing.
 */
describe("<MyGradesPanel /> — collapsible per-file detail", () => {
  const twoFiles = summary({
    per_file: [
      grade({ id: 1, original_filename: "a.ipynb", score: 8.5, feedback_text: "Good work on a." }),
      grade({ id: 2, original_filename: "b.ipynb", score: 6, feedback_text: "Missing the plot in b." }),
    ],
  });

  function renderTwo() {
    render(
      <MyGradesPanel
        grades={twoFiles}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={2}
      />,
    );
  }

  it("keeps the combined score prominent at the top", () => {
    renderTwo();
    expect(screen.getByText(/combined score/i)).toBeVisible();
    expect(screen.getByText("4.5 / 10")).toBeVisible();
  });

  it("collapses each file to filename and score by default", () => {
    renderTwo();

    // The summary line of each row is visible...
    expect(screen.getByText("a.ipynb")).toBeVisible();
    expect(screen.getByText("8.5 / 10")).toBeVisible();
    expect(screen.getByText("b.ipynb")).toBeVisible();
    expect(screen.getByText("6 / 10")).toBeVisible();

    // ...but its detail is not.
    expect(screen.getByText("Good work on a.")).not.toBeVisible();
    expect(screen.getByText("Missing the plot in b.")).not.toBeVisible();
    for (const button of screen.getAllByRole("button")) {
      expect(button).toHaveAttribute("aria-expanded", "false");
    }
  });

  it("expands one file in place to reveal its detail", async () => {
    renderTwo();

    await userEvent.click(screen.getByRole("button", { name: /a\.ipynb/ }));

    expect(screen.getByText("Good work on a.")).toBeVisible();
    // Both files share a graded date, so scope to the row that was opened.
    const openRow = screen.getByRole("button", { name: /a\.ipynb/ }).closest("li")!;
    expect(within(openRow).getByText(/Graded Sep 7, 2026/)).toBeVisible();
    // Still in place -- the score did not move or disappear.
    expect(screen.getByText("8.5 / 10")).toBeVisible();
  });

  it("expands files independently", async () => {
    renderTwo();

    await userEvent.click(screen.getByRole("button", { name: /a\.ipynb/ }));
    expect(screen.getByText("Good work on a.")).toBeVisible();
    expect(screen.getByText("Missing the plot in b.")).not.toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /b\.ipynb/ }));
    // Opening the second must not close the first.
    expect(screen.getByText("Good work on a.")).toBeVisible();
    expect(screen.getByText("Missing the plot in b.")).toBeVisible();

    // And collapsing one leaves the other open.
    await userEvent.click(screen.getByRole("button", { name: /a\.ipynb/ }));
    expect(screen.getByText("Good work on a.")).not.toBeVisible();
    expect(screen.getByText("Missing the plot in b.")).toBeVisible();
  });

  it("shows a genuine zero as a score, collapsed", () => {
    render(
      <MyGradesPanel
        grades={summary({
          per_file: [grade({ id: 1, original_filename: "z.ipynb", score: 0 })],
          combined_score: 0,
        })}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );

    // A real 0 must read as a score, never as "not graded".
    expect(screen.getByText("0 / 10")).toBeVisible();
    expect(screen.queryByText(/hasn't been graded yet/i)).not.toBeInTheDocument();
  });

  it("still shows ungraded as ungraded, with no collapsed rows at all", () => {
    render(
      <MyGradesPanel
        grades={summary({ per_file: [], combined_score: 0 })}
        error={null}
        submission={SUBMISSION}
        totalAssignmentFiles={1}
      />,
    );

    // combined_score is 0.0 here too -- the distinction is per_file, and the
    // collapse must not have blurred it.
    expect(screen.getByText(/hasn't been graded yet/i)).toBeVisible();
    expect(screen.queryByText("0 / 10")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
