/**
 * Student session detail: assignment list + downloads, and the submitted vs
 * not-submitted states (including that `null` is not an error).
 *
 * bugfix-original-upload-preservation: assignment files are one list of
 * AssignmentUpload rows -- exactly what the instructor uploaded, one row per
 * upload event. There is no separate notebooks/resources split in the UI
 * anymore; a zip downloads as that zip.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type {
  AssignmentUploadRead,
  SessionRead,
  SubmissionRead,
  UserRead,
} from "@/lib/api";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/student/sessions/3",
  useSearchParams: () => new URLSearchParams(),
}));

const getSessionMock = vi.fn();
const getMySubmissionMock = vi.fn();
const downloadAssignmentMock = vi.fn();
const deleteSubmissionUploadMock = vi.fn();
const downloadMySubmissionUploadMock = vi.fn();
// 5.6 added the grades panel to this page; it must resolve, or its own error
// banner becomes a second role="alert" and these assertions turn ambiguous.
const getMyGradesMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getSession: (...a: unknown[]) => getSessionMock(...a),
    getMySubmission: (...a: unknown[]) => getMySubmissionMock(...a),
    downloadAssignment: (...a: unknown[]) => downloadAssignmentMock(...a),
    deleteSubmissionUpload: (...a: unknown[]) => deleteSubmissionUploadMock(...a),
    downloadMySubmissionUpload: (...a: unknown[]) => downloadMySubmissionUploadMock(...a),
    getMyGrades: (...a: unknown[]) => getMyGradesMock(...a),
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

import { StudentSessionDetail } from "./student-session-detail";

function upload(overrides: Partial<AssignmentUploadRead> = {}): AssignmentUploadRead {
  return {
    id: 8,
    session_id: 3,
    original_filename: "Numpy_and_Plotting.ipynb",
    content_type: "application/octet-stream",
    uploaded_at: "2026-09-06T15:40:00",
    ...overrides,
  };
}

function sessionWith(uploads: AssignmentUploadRead[]): SessionRead {
  return {
    id: 3,
    title: "Week 2 Day 1",
    instructor_id: 1,
    instructor_name: "Demo Instructor",
    created_at: "2026-09-06T15:35:00",
    assignment_uploads: uploads,
    unsolved_files: [],
    resource_files: [],
  };
}

function submission(overrides: Partial<SubmissionRead> = {}): SubmissionRead {
  return {
    id: 12,
    session_id: 3,
    student_id: 2,
    submitted_at: "2026-09-07T10:00:00",
    uploads: [
      {
        id: 20,
        submission_id: 12,
        original_filename: "my-solution.zip",
        content_type: "application/zip",
        uploaded_at: "2026-09-07T10:00:00",
      },
    ],
    files: [
      {
        id: 30,
        original_filename: "Numpy_and_Plotting.ipynb",
        matched_unsolved_file_id: 8,
        graded: true,
        source_upload_id: 20,
      },
    ],
    ...overrides,
  };
}

async function renderDetail() {
  render(<StudentSessionDetail sessionId={3} />);
  await screen.findByRole("heading", { name: "Week 2 Day 1" });
}

beforeEach(() => {
  vi.clearAllMocks();
  currentUser = STUDENT;
  getSessionMock.mockResolvedValue(sessionWith([upload()]));
  getMySubmissionMock.mockResolvedValue(null);
  deleteSubmissionUploadMock.mockResolvedValue(undefined);
  downloadMySubmissionUploadMock.mockResolvedValue(new Blob(["bytes"]));
  getMyGradesMock.mockResolvedValue({
    student_id: 2,
    student_name: "Demo Student",
    per_file: [],
    combined_score: 0,
  });
});

describe("<StudentSessionDetail /> — session and assignment files", () => {
  it("shows the session's real fields", async () => {
    render(<StudentSessionDetail sessionId={3} />);
    expect(screen.getByText(/loading session/i)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Week 2 Day 1" })).toBeInTheDocument();
    expect(getSessionMock).toHaveBeenCalledWith(3);
  });

  it("lists exactly what was uploaded, one row per upload", async () => {
    getSessionMock.mockResolvedValue(
      sessionWith([
        upload({ id: 8, original_filename: "Numpy_and_Plotting.ipynb" }),
        upload({ id: 9, original_filename: "week2.zip" }),
      ]),
    );
    await renderDetail();

    expect(screen.getByText("Numpy_and_Plotting.ipynb")).toBeInTheDocument();
    expect(screen.getByText("week2.zip")).toBeInTheDocument();
  });

  it("shows an empty state when the instructor has uploaded nothing", async () => {
    getSessionMock.mockResolvedValue(sessionWith([]));
    await renderDetail();
    expect(screen.getByText(/hasn't uploaded any assignment files/i)).toBeInTheDocument();
  });

  it("downloads through the authenticated client, not a bare href", async () => {
    const blob = new Blob(['{"cells":[]}'], { type: "application/octet-stream" });
    downloadAssignmentMock.mockResolvedValue(blob);
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /download/i }));

    await waitFor(() => expect(downloadAssignmentMock).toHaveBeenCalledWith(3, 8));
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    vi.unstubAllGlobals();
  });

  it("downloads a zip exactly as uploaded -- one download, no browsing inside it", async () => {
    getSessionMock.mockResolvedValue(sessionWith([upload({ id: 9, original_filename: "week2.zip" })]));
    const blob = new Blob(["PK\x03\x04"], { type: "application/zip" });
    downloadAssignmentMock.mockResolvedValue(blob);
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:zip"), revokeObjectURL: vi.fn() });

    await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /download/i }));

    await waitFor(() => expect(downloadAssignmentMock).toHaveBeenCalledWith(3, 9));
    vi.unstubAllGlobals();
  });

  it("shows a download failure", async () => {
    downloadAssignmentMock.mockRejectedValue(new ApiError(404, "File not found on disk."));
    await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /download/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("File not found on disk.");
  });

  it("now offers the 5.6 upload control", async () => {
    // This asserted the ABSENCE of upload UI while 5.6 was still unbuilt.
    // 5.6 is what it was guarding against, so it now checks the opposite.
    await renderDetail();
    expect(screen.getByLabelText(/your solved file/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^upload$/i })).toBeInTheDocument();
  });
});

describe("<StudentSessionDetail /> — submission status", () => {
  it("treats a null submission as 'not submitted yet', NOT an error", async () => {
    // The endpoint answers 200 with a null body in this case.
    getMySubmissionMock.mockResolvedValue(null);
    await renderDetail();

    expect(screen.getByText(/haven't submitted anything for this session yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a loading state before the status resolves", async () => {
    // The panel only mounts once the session itself has loaded, so wait for
    // the heading before asserting on the submission panel's own state.
    getMySubmissionMock.mockReturnValue(new Promise(() => {}));
    await renderDetail();
    expect(screen.getByText(/checking your submission/i)).toBeInTheDocument();
  });

  it("shows every upload with its own filename and produced-notebook counts", async () => {
    getMySubmissionMock.mockResolvedValue(
      submission({
        uploads: [
          { id: 20, submission_id: 12, original_filename: "bundle.zip", content_type: "application/zip", uploaded_at: "2026-09-07T10:00:00" },
          { id: 21, submission_id: 12, original_filename: "extra.ipynb", content_type: "application/octet-stream", uploaded_at: "2026-09-07T11:00:00" },
        ],
        files: [
          { id: 30, original_filename: "a.ipynb", matched_unsolved_file_id: 8, graded: true, source_upload_id: 20 },
          { id: 31, original_filename: "b.ipynb", matched_unsolved_file_id: 9, graded: false, source_upload_id: 20 },
          { id: 32, original_filename: "extra.ipynb", matched_unsolved_file_id: null, graded: false, source_upload_id: 21 },
        ],
      }),
    );
    await renderDetail();

    // Additive: BOTH uploads are shown, not just the latest.
    expect(screen.getByText("bundle.zip")).toBeInTheDocument();
    expect(screen.getByText("extra.ipynb")).toBeInTheDocument();
    // Overall summary line.
    expect(screen.getByText(/2 uploads · 3 notebooks · 1 graded/)).toBeInTheDocument();
    // Per-upload notebook breakdown -- bundle.zip produced 2 notebooks, 1 graded.
    expect(screen.getByText(/2 notebooks \(1 graded\)/)).toBeInTheDocument();
    expect(screen.queryByText(/haven't submitted/i)).not.toBeInTheDocument();
  });

  it("offers a Remove control on each upload", async () => {
    getMySubmissionMock.mockResolvedValue(submission());
    await renderDetail();
    expect(screen.getByRole("button", { name: /remove/i })).toBeInTheDocument();
  });

  it("renders the 5.6 grades panel alongside the submission status", async () => {
    getMySubmissionMock.mockResolvedValue(submission());
    await renderDetail();
    expect(screen.getByRole("heading", { name: "Your grade" })).toBeInTheDocument();
    // Nothing graded in this fixture, so no score is shown.
    expect(screen.getByText(/hasn't been graded yet/i)).toBeInTheDocument();
  });

  it("surfaces a real submission-status failure as an error", async () => {
    getMySubmissionMock.mockRejectedValue(new ApiError(500, "Database unavailable."));
    await renderDetail();
    expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable.");
  });

  it("uses singular wording for a one-notebook submission", async () => {
    getMySubmissionMock.mockResolvedValue(submission());
    await renderDetail();
    expect(screen.getByText(/1 notebook · 1 graded/)).toBeInTheDocument();
  });
});

describe("<StudentSessionDetail /> — per-upload download", () => {
  it("offers a Download control on each upload, calling the student-scoped endpoint", async () => {
    const blob = new Blob(["exact bytes"], { type: "application/octet-stream" });
    downloadMySubmissionUploadMock.mockResolvedValue(blob);
    const createObjectURL = vi.fn(() => "blob:mine");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    getMySubmissionMock.mockResolvedValue(submission());
    await renderDetail();

    const uploadRow = screen.getByText("my-solution.zip").closest("li")!;
    await userEvent.click(within(uploadRow).getByRole("button", { name: /^download$/i }));

    await waitFor(() =>
      expect(downloadMySubmissionUploadMock).toHaveBeenCalledWith(3, 20),
    );
    // The student-scoped endpoint, not the instructor one.
    expect(downloadAssignmentMock).not.toHaveBeenCalledWith(3, 20);
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    vi.unstubAllGlobals();
  });

  it("reports a download failure without disturbing the delete controls", async () => {
    downloadMySubmissionUploadMock.mockRejectedValue(new ApiError(404, "File not found on disk."));
    getMySubmissionMock.mockResolvedValue(submission());
    await renderDetail();

    const uploadRow = screen.getByText("my-solution.zip").closest("li")!;
    await userEvent.click(within(uploadRow).getByRole("button", { name: /^download$/i }));

    expect(await within(uploadRow).findByRole("alert")).toHaveTextContent(
      "File not found on disk.",
    );
    expect(within(uploadRow).getByRole("button", { name: /remove/i })).toBeEnabled();
  });
});

describe("<StudentSessionDetail /> — per-upload delete", () => {
  it("deletes an ungraded upload immediately, no confirm step", async () => {
    getMySubmissionMock.mockResolvedValue(submission());
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: /remove/i }));

    await waitFor(() =>
      expect(deleteSubmissionUploadMock).toHaveBeenCalledWith(3, 20, { confirm: false }),
    );
    expect(screen.queryByText(/permanently delete/i)).not.toBeInTheDocument();
    // Re-reads submission (and grades) after a successful delete.
    expect(getMySubmissionMock).toHaveBeenCalledTimes(2);
  });

  it("shows the backend's real 409 message and requires explicit confirmation", async () => {
    getMySubmissionMock.mockResolvedValue(submission());
    deleteSubmissionUploadMock.mockRejectedValueOnce(
      new ApiError(
        409,
        "This upload includes a graded file ('Numpy_and_Plotting.ipynb') scoring 8.5/10. Pass confirm=true to delete it and its grade permanently.",
      ),
    );
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: /remove/i }));

    const warning = await screen.findByText(/scoring 8\.5\/10/i);
    expect(warning).toBeInTheDocument();
    // Not deleted yet -- only the first (no-confirm) call was made.
    expect(deleteSubmissionUploadMock).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: /remove and delete my grade/i }));

    await waitFor(() =>
      expect(deleteSubmissionUploadMock).toHaveBeenNthCalledWith(2, 3, 20, { confirm: true }),
    );
  });

  it("cancelling the confirm step calls nothing further", async () => {
    getMySubmissionMock.mockResolvedValue(submission());
    deleteSubmissionUploadMock.mockRejectedValueOnce(new ApiError(409, "Scoring 8.5/10."));
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: /remove/i }));
    await screen.findByText(/scoring 8\.5\/10/i);

    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(screen.queryByText(/scoring 8\.5\/10/i)).not.toBeInTheDocument();
    expect(deleteSubmissionUploadMock).toHaveBeenCalledTimes(1);
  });
});

describe("<StudentSessionDetail /> — route protection", () => {
  it("redirects an instructor to their own home", async () => {
    currentUser = { ...STUDENT, id: 1, name: "Demo Instructor", role: "instructor" };
    render(<StudentSessionDetail sessionId={3} />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/instructor"));
    expect(screen.queryByRole("heading", { name: "Week 2 Day 1" })).not.toBeInTheDocument();
  });
});
