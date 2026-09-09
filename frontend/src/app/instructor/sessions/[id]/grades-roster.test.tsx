/** Roster rendering: loading, empty, populated, and the zero-submission caveat. */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { GradeRead, GradeSummary, SessionGradeReport } from "@/lib/api";

import { GradesRoster } from "./grades-roster";

function grade(overrides: Partial<GradeRead> = {}): GradeRead {
  return {
    id: 1,
    submission_file_id: 10,
    original_filename: "GPU_Acceleration.ipynb",
    score: 8.5,
    feedback_text: "Solid work on the kernel timing section.",
    rationale: null,
    graded_at: "2026-09-06T16:00:00",
    ...overrides,
  };
}

function student(overrides: Partial<GradeSummary> = {}): GradeSummary {
  return {
    student_id: 2,
    student_name: "Fiza",
    per_file: [grade()],
    combined_score: 9,
    ...overrides,
  };
}

function report(students: GradeSummary[]): SessionGradeReport {
  return { session_id: 5, session_title: "Week 3 Day 1", students };
}

describe("<GradesRoster />", () => {
  it("shows a loading state before the report arrives", () => {
    render(<GradesRoster report={null} error={null} totalAssignmentFiles={4} />);
    expect(screen.getByText(/loading grades/i)).toBeInTheDocument();
  });

  it("shows an empty state when nobody has submitted", () => {
    render(<GradesRoster report={report([])} error={null} totalAssignmentFiles={4} />);
    expect(screen.getByText(/no submissions for this session yet/i)).toBeInTheDocument();
  });

  it("surfaces a load failure", () => {
    render(
      <GradesRoster report={null} error="Session 5 not found." totalAssignmentFiles={4} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Session 5 not found.");
  });

  it("renders each student with their combined score and graded count", () => {
    render(
      <GradesRoster
        report={report([
          student({ student_id: 2, student_name: "Fiza", combined_score: 9 }),
          student({
            student_id: 3,
            student_name: "Soph",
            combined_score: 5,
            per_file: [grade({ id: 2 }), grade({ id: 3 })],
          }),
        ])}
        error={null}
        totalAssignmentFiles={4}
      />,
    );

    expect(screen.getByText("Fiza")).toBeInTheDocument();
    expect(screen.getByText("9 / 10")).toBeInTheDocument();
    expect(screen.getByText("1 of 4 files graded")).toBeInTheDocument();

    expect(screen.getByText("Soph")).toBeInTheDocument();
    expect(screen.getByText("5 / 10")).toBeInTheDocument();
    expect(screen.getByText("2 of 4 files graded")).toBeInTheDocument();
  });

  it("shows 'Not graded yet' rather than a score of 0 for an ungraded submitter", () => {
    // The backend returns combined_score 0.0 (not null) for a student who has
    // submitted but has nothing graded -- rendering that as "0 / 10" would
    // read as a genuine zero.
    render(
      <GradesRoster
        report={report([student({ student_name: "Nami", per_file: [], combined_score: 0 })])}
        error={null}
        totalAssignmentFiles={4}
      />,
    );

    expect(screen.getByText("Not graded yet")).toBeInTheDocument();
    expect(screen.queryByText("0 / 10")).not.toBeInTheDocument();
    expect(screen.getByText("0 of 4 files graded")).toBeInTheDocument();
    // Nothing to expand.
    expect(screen.queryByRole("button", { name: /show files/i })).not.toBeInTheDocument();
  });

  it("handles the null combined_score case (session with no assignment files)", () => {
    render(
      <GradesRoster
        report={report([student({ per_file: [], combined_score: null })])}
        error={null}
        totalAssignmentFiles={0}
      />,
    );
    expect(screen.getByText("Not graded yet")).toBeInTheDocument();
  });

  it("expands to a per-file breakdown with scores and feedback", async () => {
    render(
      <GradesRoster
        report={report([
          student({
            per_file: [
              grade({ id: 1, original_filename: "a.ipynb", score: 8.5 }),
              grade({
                id: 2,
                original_filename: "b.ipynb",
                score: 6,
                feedback_text: "Missing the plotting step.",
              }),
            ],
          }),
        ])}
        error={null}
        totalAssignmentFiles={2}
      />,
    );

    expect(screen.queryByText("a.ipynb")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /show files/i }));

    expect(screen.getByText("a.ipynb")).toBeInTheDocument();
    expect(screen.getByText("8.5 / 10")).toBeInTheDocument();
    expect(screen.getByText("b.ipynb")).toBeInTheDocument();
    expect(screen.getByText("6 / 10")).toBeInTheDocument();
    // Fix D: the per-file feedback is now collapsed behind the file's own
    // toggle, so it is present but not visible until that file is opened.
    expect(screen.getByText("Missing the plotting step.")).not.toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /hide files/i }));
    expect(screen.queryByText("a.ipynb")).not.toBeInTheDocument();
  });

  it("states the zero-submission limitation on both the empty and populated table", () => {
    const { rerender } = render(
      <GradesRoster report={report([])} error={null} totalAssignmentFiles={4} />,
    );
    expect(
      screen.getByText(/only students with at least one submission appear here/i),
    ).toBeInTheDocument();

    rerender(
      <GradesRoster report={report([student()])} error={null} totalAssignmentFiles={4} />,
    );
    expect(
      screen.getByText(/only students with at least one submission appear here/i),
    ).toBeInTheDocument();
  });

  it("uses singular wording for a one-file session", () => {
    render(
      <GradesRoster
        report={report([student({ per_file: [] })])}
        error={null}
        totalAssignmentFiles={1}
      />,
    );
    expect(screen.getByText("0 of 1 file graded")).toBeInTheDocument();
  });

  it("keeps each student's expansion independent", async () => {
    render(
      <GradesRoster
        report={report([
          student({ student_id: 2, student_name: "Fiza", per_file: [grade({ id: 1, original_filename: "fiza.ipynb" })] }),
          student({ student_id: 3, student_name: "Soph", per_file: [grade({ id: 2, original_filename: "soph.ipynb" })] }),
        ])}
        error={null}
        totalAssignmentFiles={2}
      />,
    );

    const fizaRow = screen.getByText("Fiza").closest("li")!;
    await userEvent.click(within(fizaRow).getByRole("button", { name: /show files/i }));

    expect(screen.getByText("fiza.ipynb")).toBeInTheDocument();
    expect(screen.queryByText("soph.ipynb")).not.toBeInTheDocument();
  });
});

/**
 * Fix D — the roster's per-file rows collapse too.
 *
 * There are now two levels: a student expands to reveal their files, and each
 * file expands to reveal its detail. Assertions are on VISIBILITY, since the
 * detail stays mounted while collapsed.
 */
describe("<GradesRoster /> — collapsible per-file detail", () => {
  const TWO_FILES = report([
    student({
      per_file: [
        grade({ id: 1, original_filename: "a.ipynb", score: 8.5, feedback_text: "Good work on a." }),
        grade({ id: 2, original_filename: "b.ipynb", score: 6, feedback_text: "Missing the plot in b." }),
      ],
    }),
  ]);

  /** Expand the student so their file rows are on screen. */
  async function showFiles() {
    render(<GradesRoster report={TWO_FILES} error={null} totalAssignmentFiles={2} />);
    await userEvent.click(screen.getByRole("button", { name: /show files/i }));
  }

  it("keeps the combined score prominent on the student row", async () => {
    await showFiles();
    expect(screen.getByText("9 / 10")).toBeVisible();
  });

  it("collapses each file to filename and score by default", async () => {
    await showFiles();

    expect(screen.getByText("a.ipynb")).toBeVisible();
    expect(screen.getByText("8.5 / 10")).toBeVisible();
    expect(screen.getByText("b.ipynb")).toBeVisible();
    expect(screen.getByText("6 / 10")).toBeVisible();

    expect(screen.getByText("Good work on a.")).not.toBeVisible();
    expect(screen.getByText("Missing the plot in b.")).not.toBeVisible();
  });

  it("expands one file in place without touching the other", async () => {
    await showFiles();

    await userEvent.click(screen.getByRole("button", { name: /a\.ipynb/ }));
    expect(screen.getByText("Good work on a.")).toBeVisible();
    expect(screen.getByText("Missing the plot in b.")).not.toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /b\.ipynb/ }));
    expect(screen.getByText("Good work on a.")).toBeVisible();
    expect(screen.getByText("Missing the plot in b.")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /b\.ipynb/ }));
    expect(screen.getByText("Good work on a.")).toBeVisible();
    expect(screen.getByText("Missing the plot in b.")).not.toBeVisible();
  });

  it("shows a genuine zero as a score in the collapsed row", async () => {
    render(
      <GradesRoster
        report={report([
          student({
            student_name: "Nami",
            per_file: [grade({ id: 1, original_filename: "z.ipynb", score: 0 })],
            combined_score: 0,
          }),
        ])}
        error={null}
        totalAssignmentFiles={1}
      />,
    );

    // The student scored zero -- that must read as a score, not as ungraded.
    expect(screen.getByText("0 / 10")).toBeVisible();
    expect(screen.queryByText(/not graded yet/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /show files/i }));
    expect(screen.getByText("z.ipynb")).toBeVisible();
    expect(screen.getAllByText("0 / 10").length).toBeGreaterThan(0);
  });

  it("still distinguishes an ungraded submitter, whose 0.0 is not a score", () => {
    render(
      <GradesRoster
        report={report([
          student({ student_name: "Soph", per_file: [], combined_score: 0 }),
        ])}
        error={null}
        totalAssignmentFiles={1}
      />,
    );

    // Same combined_score of 0 as the case above, opposite meaning. The
    // collapse must not have blurred the distinction.
    expect(screen.getByText(/not graded yet/i)).toBeVisible();
    expect(screen.queryByText("0 / 10")).not.toBeInTheDocument();
    // No files to expand, so no toggle at all.
    expect(screen.queryByRole("button", { name: /show files/i })).not.toBeInTheDocument();
  });
});
