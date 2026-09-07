/**
 * Session detail: upload form (single / multiple / zip) and the file list.
 * All API calls are mocked; no real network.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { SessionRead, UnsolvedFileRead, UserRead } from "@/lib/api";

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

import { SessionDetail } from "./session-detail";

function file(overrides: Partial<UnsolvedFileRead> = {}): UnsolvedFileRead {
  return {
    id: 14,
    session_id: 5,
    original_filename: "GPU_Acceleration.ipynb",
    rubric_generated: false,
    uploaded_at: "2026-09-06T15:44:38.287039",
    ...overrides,
  };
}

function sessionWith(files: UnsolvedFileRead[]): SessionRead {
  return {
    id: 5,
    title: "Week 3 Day 1",
    instructor_id: 1,
    created_at: "2026-09-06T15:43:53.245057",
    unsolved_files: files,
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

  it("lists the session's files with their rubric status", async () => {
    getSessionMock.mockResolvedValue(
      sessionWith([
        file({ id: 14, original_filename: "GPU_Acceleration.ipynb", rubric_generated: true }),
        file({ id: 15, original_filename: "Week_7_Day_3.ipynb", rubric_generated: false }),
      ]),
    );
    await renderDetail();

    expect(screen.getByText("GPU_Acceleration.ipynb")).toBeInTheDocument();
    expect(screen.getByText(/rubric ready/)).toBeInTheDocument();
    expect(screen.getByText("Week_7_Day_3.ipynb")).toBeInTheDocument();
    expect(screen.getByText(/no rubric yet/)).toBeInTheDocument();
  });
});

describe("<SessionDetail /> — upload", () => {
  it("refuses to submit with nothing selected", async () => {
    await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/choose at least one/i);
    expect(uploadAssignmentMock).not.toHaveBeenCalled();
  });

  it("uploads a single .ipynb and shows it in the list", async () => {
    const created = file({ id: 20, original_filename: "solved.ipynb" });
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

  it("sends every file of a multi-file selection in one call", async () => {
    uploadAssignmentMock.mockResolvedValue([
      file({ id: 21, original_filename: "one.ipynb" }),
      file({ id: 22, original_filename: "two.ipynb" }),
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

  it("accepts a .zip and lists every notebook the backend extracted from it", async () => {
    // One zip in, three rows back -- the backend flattens nested notebooks.
    uploadAssignmentMock.mockResolvedValue([
      file({ id: 31, original_filename: "nested-a.ipynb" }),
      file({ id: 32, original_filename: "nested-b.ipynb" }),
      file({ id: 33, original_filename: "nested-c.ipynb" }),
    ]);
    await renderDetail();

    const zip = new File(["PK"], "week3.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Files"), zip);
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText("nested-a.ipynb")).toBeInTheDocument());
    expect(screen.getByText("nested-b.ipynb")).toBeInTheDocument();
    expect(screen.getByText("nested-c.ipynb")).toBeInTheDocument();
    const [, sent] = uploadAssignmentMock.mock.calls[0];
    expect(sent.map((f: File) => f.name)).toEqual(["week3.zip"]);
    expect(screen.getByRole("status")).toHaveTextContent("Uploaded 3 notebooks.");
  });

  it("blocks a disallowed extension before it reaches the API", async () => {
    await renderDetail();

    // `applyAccept: false` deliberately bypasses the input's accept=".ipynb,.zip".
    // That attribute is only a file-picker hint -- a drag-drop or an "All files"
    // pick can still hand over a .py -- so the explicit check has to hold on its
    // own, and this is the only way to exercise it.
    await userEvent.upload(
      screen.getByLabelText("Files"),
      new File(["print(1)"], "script.py", { type: "text/x-python" }),
      { applyAccept: false },
    );
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/only .ipynb or .zip/i);
    expect(screen.getByRole("alert")).toHaveTextContent("script.py");
    expect(uploadAssignmentMock).not.toHaveBeenCalled();
  });

  it("says nothing was uploaded when the batch is rejected", async () => {
    // The backend checks the whole batch before writing, so a 409 means no
    // partial write -- the message must not leave that ambiguous.
    uploadAssignmentMock.mockRejectedValue(
      new ApiError(
        409,
        "An assignment file named 'dup.ipynb' already exists in session 5. Delete it first or use a different name.",
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
    getSessionMock.mockResolvedValue(sessionWith([file({ id: 14 })]));
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
    getSessionMock.mockResolvedValue(sessionWith([file({ id: 14 })]));
    downloadAssignmentMock.mockRejectedValue(
      new ApiError(404, "File is recorded in the database but not found on disk."),
    );

    await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /download/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not found on disk/);
  });

  it("asks for confirmation before removing, and refreshes the list after", async () => {
    getSessionMock.mockResolvedValue(sessionWith([file({ id: 14 })]));
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
    getSessionMock.mockResolvedValue(sessionWith([file({ id: 14 })]));
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(deleteAssignmentMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /^remove$/i })).toBeInTheDocument();
  });
});
