/**
 * Submission upload: the three re-upload paths.
 *
 * The confirm step is a UX guardrail, not protection — the backend still
 * replaces-and-deletes for any caller. These tests check the guardrail
 * behaves as specified, nothing stronger.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { GradeSummary, SubmissionRead } from "@/lib/api";

const uploadSubmissionMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, uploadSubmission: (...a: unknown[]) => uploadSubmissionMock(...a) };
});

import { SubmissionUploadPanel, describeGradeLoss } from "./submission-upload-panel";

function submission(overrides: Partial<SubmissionRead> = {}): SubmissionRead {
  return {
    id: 16,
    session_id: 3,
    student_id: 2,
    original_filename: "old.ipynb",
    submitted_at: "2026-09-07T10:00:00",
    files: [
      { id: 49, original_filename: "old.ipynb", matched_unsolved_file_id: 6, graded: false },
    ],
    ...overrides,
  };
}

function grades(overrides: Partial<GradeSummary> = {}): GradeSummary {
  return {
    student_id: 2,
    student_name: "Demo Student",
    per_file: [
      {
        id: 1,
        submission_file_id: 49,
        original_filename: "old.ipynb",
        score: 7.5,
        feedback_text: "Good work.",
        rationale: null,
        graded_at: "2026-09-07T11:00:00",
      },
    ],
    combined_score: 3.5,
    ...overrides,
  };
}

function notebook(name = "solution.ipynb") {
  return new File(['{"cells":[]}'], name, { type: "application/json" });
}

async function pick(name = "solution.ipynb") {
  await userEvent.upload(screen.getByLabelText("Your solved file"), notebook(name));
}

beforeEach(() => {
  vi.clearAllMocks();
  uploadSubmissionMock.mockResolvedValue(submission({ original_filename: "solution.ipynb" }));
});

describe("describeGradeLoss", () => {
  it("names the actual score for a single graded file", () => {
    expect(describeGradeLoss(grades())).toContain("grade of 7.5/10");
  });

  it("names the count and combined score for several graded files", () => {
    const many = grades({
      per_file: [grades().per_file[0], { ...grades().per_file[0], id: 2, score: 9 }],
      combined_score: 8,
    });
    const text = describeGradeLoss(many);
    expect(text).toContain("2 files");
    expect(text).toContain("combined 8/10");
  });

  it("does not invent a score when the grade data is missing", () => {
    const text = describeGradeLoss(undefined);
    expect(text).toMatch(/could not be loaded/i);
    expect(text).not.toMatch(/\d+\/10/);
  });
});

describe("<SubmissionUploadPanel /> — first submission", () => {
  it("uploads with no notice and no confirm step", async () => {
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={null}
        grades={undefined}
        onUploaded={vi.fn()}
      />,
    );

    expect(screen.queryByText(/will replace your current submission/i)).not.toBeInTheDocument();

    await pick();
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(uploadSubmissionMock).toHaveBeenCalledTimes(1));
    const [sessionId, file] = uploadSubmissionMock.mock.calls[0];
    expect(sessionId).toBe(3);
    expect((file as File).name).toBe("solution.ipynb");
    expect(screen.queryByText(/continue\?/i)).not.toBeInTheDocument();
  });

  it("refuses an empty selection and a disallowed extension", async () => {
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={null}
        grades={undefined}
        onUploaded={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/choose a .ipynb or .zip/i);
    expect(uploadSubmissionMock).not.toHaveBeenCalled();

    // accept=".ipynb,.zip" is only a picker hint, so bypass it to exercise
    // the explicit check the way a drag-drop would.
    await userEvent.upload(
      screen.getByLabelText("Your solved file"),
      new File(["x"], "notes.txt", { type: "text/plain" }),
      { applyAccept: false },
    );
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/only .ipynb or .zip/i);
    expect(uploadSubmissionMock).not.toHaveBeenCalled();
  });

  it("reports an upload failure", async () => {
    uploadSubmissionMock.mockRejectedValue(new ApiError(422, "Only .ipynb or .zip files are accepted."));
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={null}
        grades={undefined}
        onUploaded={vi.fn()}
      />,
    );
    await pick();
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/only .ipynb or .zip/i);
  });
});

describe("<SubmissionUploadPanel /> — submitted but nothing graded", () => {
  it("shows the mild notice and uploads without a confirm step", async () => {
    const onUploaded = vi.fn();
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={submission()}
        grades={grades({ per_file: [], combined_score: 0 })}
        onUploaded={onUploaded}
      />,
    );

    expect(screen.getByText(/uploading again will replace your current submission/i)).toBeInTheDocument();

    await pick();
    await userEvent.click(screen.getByRole("button", { name: /replace submission/i }));

    await waitFor(() => expect(uploadSubmissionMock).toHaveBeenCalledTimes(1));
    // Straight through — no confirm gate for this case.
    expect(screen.queryByText(/continue\?/i)).not.toBeInTheDocument();
    expect(onUploaded).toHaveBeenCalled();
  });
});

describe("<SubmissionUploadPanel /> — submitted WITH a graded file", () => {
  const graded = submission({
    files: [
      { id: 49, original_filename: "old.ipynb", matched_unsolved_file_id: 6, graded: true },
    ],
  });

  it("blocks the upload call until the student confirms, naming the real score", async () => {
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={graded}
        grades={grades()}
        onUploaded={vi.fn()}
      />,
    );

    await pick();
    await userEvent.click(screen.getByRole("button", { name: /replace submission/i }));

    // The API must NOT have been called yet.
    expect(uploadSubmissionMock).not.toHaveBeenCalled();

    const warning = await screen.findByText(/grade of 7\.5\/10/i);
    expect(warning).toBeInTheDocument();
    expect(warning).toHaveTextContent(/permanently delete this grade and its feedback/i);
    expect(screen.getByText(/continue\?/i)).toBeInTheDocument();
    // The mild notice is not used for this case.
    expect(screen.queryByText(/^Uploading again will replace your current submission\.$/)).not.toBeInTheDocument();
  });

  it("cancelling leaves the grade alone and calls nothing", async () => {
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={graded}
        grades={grades()}
        onUploaded={vi.fn()}
      />,
    );

    await pick();
    await userEvent.click(screen.getByRole("button", { name: /replace submission/i }));
    await screen.findByText(/continue\?/i);

    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(uploadSubmissionMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/continue\?/i)).not.toBeInTheDocument();
  });

  it("confirming performs the upload", async () => {
    const onUploaded = vi.fn();
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={graded}
        grades={grades()}
        onUploaded={onUploaded}
      />,
    );

    await pick();
    await userEvent.click(screen.getByRole("button", { name: /replace submission/i }));
    await userEvent.click(screen.getByRole("button", { name: /replace and delete my grade/i }));

    await waitFor(() => expect(uploadSubmissionMock).toHaveBeenCalledTimes(1));
    expect(onUploaded).toHaveBeenCalled();
  });

  it("waits for the score before offering the confirm", () => {
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={graded}
        grades={undefined}
        onUploaded={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /checking your existing grade/i })).toBeDisabled();
  });

  it("decides from the graded flag, not from combined_score", async () => {
    // combined_score is 0 here, but a file IS graded -- the confirm must fire.
    render(
      <SubmissionUploadPanel
        sessionId={3}
        submission={graded}
        grades={grades({ per_file: [{ ...grades().per_file[0], score: 0 }], combined_score: 0 })}
        onUploaded={vi.fn()}
      />,
    );

    await pick();
    await userEvent.click(screen.getByRole("button", { name: /replace submission/i }));

    expect(uploadSubmissionMock).not.toHaveBeenCalled();
    expect(await screen.findByText(/grade of 0\/10/i)).toBeInTheDocument();
  });
});
