/**
 * Submission upload -- additive, any file type, no confirm step.
 *
 * Upload can no longer destroy data (uploads are additive), so there is
 * nothing here to warn-and-confirm about any more; that moved to delete.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { SubmissionRead } from "@/lib/api";

const uploadSubmissionMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, uploadSubmission: (...a: unknown[]) => uploadSubmissionMock(...a) };
});

import { SubmissionUploadPanel, describeUpload } from "./submission-upload-panel";

function submission(overrides: Partial<SubmissionRead> = {}): SubmissionRead {
  return {
    id: 16,
    session_id: 3,
    student_id: 2,
    submitted_at: "2026-09-07T10:00:00",
    uploads: [
      { id: 1, submission_id: 16, original_filename: "old.ipynb", content_type: null, uploaded_at: "2026-09-07T10:00:00" },
    ],
    files: [
      { id: 49, original_filename: "old.ipynb", matched_unsolved_file_id: 6, graded: false, source_upload_id: 1 },
    ],
    ...overrides,
  };
}

function file(name = "solution.ipynb") {
  return new File(['{"cells":[]}'], name, { type: "application/json" });
}

beforeEach(() => {
  vi.clearAllMocks();
  uploadSubmissionMock.mockResolvedValue(submission());
});

describe("describeUpload", () => {
  it("names a single file", () => {
    expect(describeUpload([file("a.ipynb")])).toBe("Uploaded a.ipynb.");
  });

  it("lists multiple files", () => {
    const text = describeUpload([file("a.ipynb"), file("b.pdf")]);
    expect(text).toContain("Uploaded 2 files");
    expect(text).toContain("a.ipynb");
    expect(text).toContain("b.pdf");
  });
});

describe("<SubmissionUploadPanel />", () => {
  it("first upload: no notice, uploads straight through", async () => {
    render(
      <SubmissionUploadPanel sessionId={3} submission={null} onUploaded={vi.fn()} />,
    );

    expect(screen.getByText(/upload your submission/i)).toBeInTheDocument();

    await userEvent.upload(screen.getByLabelText(/your solved file/i), file());
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(uploadSubmissionMock).toHaveBeenCalledTimes(1));
    const [sessionId, files] = uploadSubmissionMock.mock.calls[0];
    expect(sessionId).toBe(3);
    expect((files as File[]).map((f) => f.name)).toEqual(["solution.ipynb"]);
  });

  it("accepts any file type -- no client-side extension restriction", async () => {
    render(
      <SubmissionUploadPanel sessionId={3} submission={null} onUploaded={vi.fn()} />,
    );
    await userEvent.upload(screen.getByLabelText(/your solved file/i), file("notes.txt"));
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));
    await waitFor(() => expect(uploadSubmissionMock).toHaveBeenCalledTimes(1));
  });

  it("uploading again with an existing submission is additive, labelled 'Add another file'", async () => {
    const onUploaded = vi.fn();
    render(
      <SubmissionUploadPanel sessionId={3} submission={submission()} onUploaded={onUploaded} />,
    );

    expect(screen.getByText(/add another file/i)).toBeInTheDocument();
    // No "this destroys your current submission" call-to-action any more.
    expect(screen.queryByRole("button", { name: /replace submission/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/continue\?/i)).not.toBeInTheDocument();

    await userEvent.upload(screen.getByLabelText(/your solved file/i), file("second.ipynb"));
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(uploadSubmissionMock).toHaveBeenCalledTimes(1));
    expect(onUploaded).toHaveBeenCalled();
  });

  it("refuses an empty selection", async () => {
    render(
      <SubmissionUploadPanel sessionId={3} submission={null} onUploaded={vi.fn()} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/choose at least one file/i);
    expect(uploadSubmissionMock).not.toHaveBeenCalled();
  });

  it("reports an upload failure", async () => {
    uploadSubmissionMock.mockRejectedValue(new ApiError(422, "At least one file must be uploaded."));
    render(
      <SubmissionUploadPanel sessionId={3} submission={null} onUploaded={vi.fn()} />,
    );
    await userEvent.upload(screen.getByLabelText(/your solved file/i), file());
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/at least one file/i);
  });
});
