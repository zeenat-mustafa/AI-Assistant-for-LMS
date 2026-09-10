/**
 * Session detail: upload form (single / multiple / zip / any file type) and
 * the file list.
 *
 * bugfix-original-upload-preservation: every upload is now ONE row per file
 * submitted -- a zip is one row with its own filename, never a list of what
 * was extracted from it. All API calls are mocked; no real network.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { AssignmentUploadRead, SessionRead, UserRead } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/instructor/sessions/5",
  useSearchParams: () => new URLSearchParams(),
}));

const getSessionMock = vi.fn();
const listAssignmentsMock = vi.fn();
const uploadAssignmentMock = vi.fn();
const downloadAssignmentMock = vi.fn();
const deleteAssignmentMock = vi.fn();
// 5.4 added the roster to this page; it must resolve, or its own error
// banner becomes a second role="alert" and every assertion here is ambiguous.
const getGradeReportMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getSession: (...a: unknown[]) => getSessionMock(...a),
    listAssignments: (...a: unknown[]) => listAssignmentsMock(...a),
    uploadAssignment: (...a: unknown[]) => uploadAssignmentMock(...a),
    downloadAssignment: (...a: unknown[]) => downloadAssignmentMock(...a),
    deleteAssignment: (...a: unknown[]) => deleteAssignmentMock(...a),
    getGradeReport: (...a: unknown[]) => getGradeReportMock(...a),
  };
});

const INSTRUCTOR: UserRead = {
  id: 1,
  name: "Demo Instructor",
  email: "instructor@demo.com",
  role: "instructor",
  created_at: "2026-09-06T15:26:49.014311",
};

vi.mock("@/lib/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/auth-context")>();
  return {
    ...actual,
    useAuth: () => ({
      user: INSTRUCTOR,
      status: "authenticated" as const,
      signIn: vi.fn(),
      signOut: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});

import { SessionDetail, describeUpload } from "./session-detail";

function uploaded(overrides: Partial<AssignmentUploadRead> = {}): AssignmentUploadRead {
  return {
    id: 14,
    session_id: 5,
    original_filename: "GPU_Acceleration.ipynb",
    content_type: "application/octet-stream",
    uploaded_at: "2026-09-06T15:44:38.287039",
    ...overrides,
  };
}

function sessionWith(uploads: AssignmentUploadRead[]): SessionRead {
  return {
    id: 5,
    title: "Week 3 Day 1",
    instructor_id: 1,
    instructor_name: "Demo Instructor",
    created_at: "2026-09-06T15:43:53.245057",
    assignment_uploads: uploads,
    unsolved_files: [],
    resource_files: [],
  };
}

function notebook(name: string) {
  return new File(['{"cells":[]}'], name, { type: "application/json" });
}

/** Wait for the initial load to settle before interacting. */
async function renderDetail() {
  render(<SessionDetail sessionId={5} />);
  await screen.findByRole("heading", { name: "Week 3 Day 1" });
}

beforeEach(() => {
  vi.clearAllMocks();
  getSessionMock.mockResolvedValue(sessionWith([]));
  getGradeReportMock.mockResolvedValue({
    session_id: 5,
    session_title: "Week 3 Day 1",
    students: [],
  });
});

describe("<SessionDetail /> — loading and header", () => {
  it("shows a loading state, then the session's real fields", async () => {
    render(<SessionDetail sessionId={5} />);
    expect(screen.getByText(/loading session/i)).toBeInTheDocument();

    expect(await screen.findByRole("heading", { name: "Week 3 Day 1" })).toBeInTheDocument();
    expect(getSessionMock).toHaveBeenCalledWith(5);
  });

  it("surfaces a load failure", async () => {
    getSessionMock.mockRejectedValue(new ApiError(404, "Session 5 not found."));
    render(<SessionDetail sessionId={5} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Session 5 not found.");
  });
});

describe("<SessionDetail /> — file list", () => {
  it("shows an empty state when the session has no files", async () => {
    await renderDetail();
    expect(screen.getByText(/no assignment files yet/i)).toBeInTheDocument();
  });

  it("lists exactly what was uploaded, one row per upload", async () => {
    getSessionMock.mockResolvedValue(
      sessionWith([
        uploaded({ id: 14, original_filename: "GPU_Acceleration.ipynb" }),
        uploaded({ id: 15, original_filename: "week3.zip" }),
      ]),
    );
    await renderDetail();

    expect(screen.getByText("GPU_Acceleration.ipynb")).toBeInTheDocument();
    expect(screen.getByText("week3.zip")).toBeInTheDocument();
  });
});

describe("<SessionDetail /> — upload", () => {
  it("refuses to submit with nothing selected", async () => {
    await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/choose at least one/i);
    expect(uploadAssignmentMock).not.toHaveBeenCalled();
  });

  it("uploads a single file and shows it in the list", async () => {
    const created = uploaded({ id: 20, original_filename: "solved.ipynb" });
    uploadAssignmentMock.mockResolvedValue([created]);
    await renderDetail();

    const input = screen.getByLabelText("Files");
    await userEvent.upload(input, notebook("solved.ipynb"));
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText("solved.ipynb")).toBeInTheDocument());
    expect(uploadAssignmentMock).toHaveBeenCalledTimes(1);
    const [sessionId, sent] = uploadAssignmentMock.mock.calls[0];
    expect(sessionId).toBe(5);
    expect(sent.map((f: File) => f.name)).toEqual(["solved.ipynb"]);
    expect(screen.getByRole("status")).toHaveTextContent("Uploaded solved.ipynb.");
  });

  it("sends every file of a multi-file selection in one call, one row each", async () => {
    uploadAssignmentMock.mockResolvedValue([
      uploaded({ id: 21, original_filename: "one.ipynb" }),
      uploaded({ id: 22, original_filename: "two.ipynb" }),
    ]);
    await renderDetail();

    await userEvent.upload(screen.getByLabelText("Files"), [
      notebook("one.ipynb"),
      notebook("two.ipynb"),
    ]);
    // The button reflects the selection count.
    await userEvent.click(screen.getByRole("button", { name: /upload 2 files/i }));

    await waitFor(() => expect(uploadAssignmentMock).toHaveBeenCalledTimes(1));
    const [, sent] = uploadAssignmentMock.mock.calls[0];
    expect(sent.map((f: File) => f.name)).toEqual(["one.ipynb", "two.ipynb"]);
    expect(screen.getByText("one.ipynb")).toBeInTheDocument();
    expect(screen.getByText("two.ipynb")).toBeInTheDocument();
  });

  it("uploads a zip as ONE row, never the notebooks extracted from it", async () => {
    // The backend extracts internally for grading, but the response is one
    // AssignmentUploadRead for the zip itself -- never one per notebook found.
    uploadAssignmentMock.mockResolvedValue([uploaded({ id: 31, original_filename: "week3.zip" })]);
    await renderDetail();

    const zip = new File(["PK"], "week3.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Files"), zip);
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText("week3.zip")).toBeInTheDocument());
    const [, sent] = uploadAssignmentMock.mock.calls[0];
    expect(sent.map((f: File) => f.name)).toEqual(["week3.zip"]);
    expect(screen.getByRole("status")).toHaveTextContent("Uploaded week3.zip.");
    // No exploded per-notebook rows -- only the one upload row exists.
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("accepts any file type -- no client-side extension gate", async () => {
    uploadAssignmentMock.mockResolvedValue([uploaded({ id: 40, original_filename: "notes.pdf" })]);
    await renderDetail();

    const pdf = new File(["%PDF-1.4"], "notes.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Files"), pdf);
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText("notes.pdf")).toBeInTheDocument());
    expect(uploadAssignmentMock).toHaveBeenCalledTimes(1);
  });

  it("says nothing was uploaded when the batch is rejected", async () => {
    // The backend checks the whole batch before writing, so a 409 means no
    // partial write -- the message must not leave that ambiguous.
    uploadAssignmentMock.mockRejectedValue(
      new ApiError(
        409,
        "A file named 'dup.ipynb' already exists in session 5. Delete it first or use a different name.",
      ),
    );
    await renderDetail();

    await userEvent.upload(screen.getByLabelText("Files"), notebook("dup.ipynb"));
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/already exists in session 5/);
    expect(alert).toHaveTextContent(/nothing was uploaded/);
  });
});

describe("<SessionDetail /> — download and remove", () => {
  it("downloads through the authenticated client, not a bare href", async () => {
    getSessionMock.mockResolvedValue(sessionWith([uploaded({ id: 14 })]));
    const blob = new Blob(['{"cells":[]}'], { type: "application/octet-stream" });
    downloadAssignmentMock.mockResolvedValue(blob);

    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /download/i }));

    await waitFor(() => expect(downloadAssignmentMock).toHaveBeenCalledWith(5, 14));
    expect(createObjectURL).toHaveBeenCalledWith(blob);

    vi.unstubAllGlobals();
  });

  it("shows an error if the download fails", async () => {
    getSessionMock.mockResolvedValue(sessionWith([uploaded({ id: 14 })]));
    downloadAssignmentMock.mockRejectedValue(
      new ApiError(404, "File is recorded in the database but not found on disk."),
    );

    await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /download/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not found on disk/);
  });

  it("asks for confirmation before removing, and refreshes the list after", async () => {
    getSessionMock.mockResolvedValue(sessionWith([uploaded({ id: 14 })]));
    deleteAssignmentMock.mockResolvedValue(undefined);
    listAssignmentsMock.mockResolvedValue([]);

    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
    expect(deleteAssignmentMock).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /confirm remove/i }));

    await waitFor(() => expect(deleteAssignmentMock).toHaveBeenCalledWith(5, 14));
    expect(await screen.findByText(/no assignment files yet/i)).toBeInTheDocument();
  });

  it("can back out of a remove", async () => {
    getSessionMock.mockResolvedValue(sessionWith([uploaded({ id: 14 })]));
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(deleteAssignmentMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /^remove$/i })).toBeInTheDocument();
  });
});

describe("describeUpload", () => {
  it("names a single uploaded file", () => {
    expect(describeUpload([uploaded({ original_filename: "a.ipynb" })])).toBe(
      "Uploaded a.ipynb.",
    );
  });

  it("lists every filename for a batch", () => {
    expect(
      describeUpload([
        uploaded({ id: 1, original_filename: "a.ipynb" }),
        uploaded({ id: 2, original_filename: "b.zip" }),
      ]),
    ).toBe("Uploaded 2 files: a.ipynb, b.zip.");
  });
});
