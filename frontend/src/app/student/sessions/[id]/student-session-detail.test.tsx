/**
 * Student session detail: assignment list + downloads, and the submitted vs
 * not-submitted states (including that `null` is not an error).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type {
  ResourceFileRead,
  SessionRead,
  SubmissionRead,
  UnsolvedFileRead,
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

function file(overrides: Partial<UnsolvedFileRead> = {}): UnsolvedFileRead {
  return {
    id: 8,
    session_id: 3,
    original_filename: "Numpy_and_Plotting.ipynb",
    rubric_generated: true,
    uploaded_at: "2026-09-06T15:40:00",
    ...overrides,
  };
}

function sessionWith(
  files: UnsolvedFileRead[],
  resources: ResourceFileRead[] = [],
): SessionRead {
  return {
    id: 3,
    title: "Week 2 Day 1",
    instructor_id: 1,
    created_at: "2026-09-06T15:35:00",
    unsolved_files: files,
    resource_files: resources,
  };
}

function submission(overrides: Partial<SubmissionRead> = {}): SubmissionRead {
  return {
    id: 12,
    session_id: 3,
    student_id: 2,
    original_filename: "my-solution.zip",
    submitted_at: "2026-09-07T10:00:00",
    files: [
      {
        id: 30,
        original_filename: "Numpy_and_Plotting.ipynb",
        matched_unsolved_file_id: 8,
        graded: true,
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
  getSessionMock.mockResolvedValue(sessionWith([file()]));
  getMySubmissionMock.mockResolvedValue(null);
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

  it("lists the assignment files", async () => {
    getSessionMock.mockResolvedValue(
      sessionWith([
        file({ id: 8, original_filename: "Numpy_and_Plotting.ipynb" }),
        file({ id: 9, original_filename: "Pandas_Hands_on.ipynb" }),
      ]),
    );
    await renderDetail();

    expect(screen.getByText("Numpy_and_Plotting.ipynb")).toBeInTheDocument();
    expect(screen.getByText("Pandas_Hands_on.ipynb")).toBeInTheDocument();
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
    expect(screen.getByLabelText("Your solved file")).toBeInTheDocument();
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

  it("shows the submitted state with filename, counts and per-file status", async () => {
    getMySubmissionMock.mockResolvedValue(
      submission({
        files: [
          { id: 30, original_filename: "a.ipynb", matched_unsolved_file_id: 8, graded: true },
          { id: 31, original_filename: "b.ipynb", matched_unsolved_file_id: 9, graded: false },
          { id: 32, original_filename: "c.ipynb", matched_unsolved_file_id: null, graded: false },
        ],
      }),
    );
    await renderDetail();

    expect(screen.getByText(/my-solution\.zip/)).toBeInTheDocument();
    expect(screen.getByText(/3 notebooks · 1 graded/)).toBeInTheDocument();
    expect(screen.getByText("graded")).toBeInTheDocument();
    expect(screen.getByText("awaiting grading")).toBeInTheDocument();
    expect(screen.getByText("not matched to an assignment")).toBeInTheDocument();
    expect(screen.queryByText(/haven't submitted/i)).not.toBeInTheDocument();
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

describe("<StudentSessionDetail /> — route protection", () => {
  it("redirects an instructor to their own home", async () => {
    currentUser = { ...STUDENT, id: 1, name: "Demo Instructor", role: "instructor" };
    render(<StudentSessionDetail sessionId={3} />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/instructor"));
    expect(screen.queryByRole("heading", { name: "Week 2 Day 1" })).not.toBeInTheDocument();
  });
});
