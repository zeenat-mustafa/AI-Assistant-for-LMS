/**
 * Session detail: upload form (single / multiple / zip) and the file list.
 * All API calls are mocked; no real network.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type {
  AssignmentUploadItem,
  ResourceFileRead,
  SessionRead,
  UnsolvedFileRead,
  UserRead,
} from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/instructor/sessions/5",
  useSearchParams: () => new URLSearchParams(),
}));

const getSessionMock = vi.fn();
const listAssignmentsMock = vi.fn();
const listResourcesMock = vi.fn();
const uploadAssignmentMock = vi.fn();
const downloadAssignmentMock = vi.fn();
const downloadResourceMock = vi.fn();
const deleteAssignmentMock = vi.fn();
const deleteResourceMock = vi.fn();
// 5.4 added the roster to this page; it must resolve, or its own error
// banner becomes a second role="alert" and every assertion here is ambiguous.
const getGradeReportMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getSession: (...a: unknown[]) => getSessionMock(...a),
    listAssignments: (...a: unknown[]) => listAssignmentsMock(...a),
    listResources: (...a: unknown[]) => listResourcesMock(...a),
    uploadAssignment: (...a: unknown[]) => uploadAssignmentMock(...a),
    downloadAssignment: (...a: unknown[]) => downloadAssignmentMock(...a),
    downloadResource: (...a: unknown[]) => downloadResourceMock(...a),
    deleteAssignment: (...a: unknown[]) => deleteAssignmentMock(...a),
    deleteResource: (...a: unknown[]) => deleteResourceMock(...a),
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

/**
 * POST /assignments returns AssignmentUploadItem, NOT UnsolvedFileRead -- it
 * carries the derived `file_role` that says which table the row landed in.
 * Verified against the live backend's OpenAPI schema.
 */
function uploaded(
  overrides: Partial<AssignmentUploadItem> = {},
): AssignmentUploadItem {
  return {
    id: 14,
    session_id: 5,
    original_filename: "GPU_Acceleration.ipynb",
    file_role: "notebook",
    rubric_generated: false,
    uploaded_at: "2026-09-06T15:44:38.287039",
    ...overrides,
  };
}

function resource(overrides: Partial<ResourceFileRead> = {}): ResourceFileRead {
  return {
    id: 71,
    session_id: 5,
    original_filename: "titanic.csv",
    uploaded_at: "2026-09-06T15:44:40.100000",
    ...overrides,
  };
}

function sessionWith(
  files: UnsolvedFileRead[],
  resources: ResourceFileRead[] = [],
): SessionRead {
  return {
    id: 5,
    title: "Week 3 Day 1",
    instructor_id: 1,
    created_at: "2026-09-06T15:43:53.245057",
    unsolved_files: files,
    resource_files: resources,
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
    expect(screen.getByRole("status")).toHaveTextContent("Uploaded solved.ipynb as a notebook.");
  });

  it("sends every file of a multi-file selection in one call", async () => {
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

  it("accepts a .zip and lists every notebook the backend extracted from it", async () => {
    // One zip in, three rows back -- the backend flattens nested notebooks.
    uploadAssignmentMock.mockResolvedValue([
      uploaded({ id: 31, original_filename: "nested-a.ipynb" }),
      uploaded({ id: 32, original_filename: "nested-b.ipynb" }),
      uploaded({ id: 33, original_filename: "nested-c.ipynb" }),
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

/**
 * Fix A — resource files alongside notebooks.
 *
 * A `.zip` may carry non-notebook material (datasets, slides, PDFs). The
 * backend stores those in a structurally separate `resource_files` table and
 * returns them as their own field, never mixed into `unsolved_files`. These
 * tests pin the two-list rendering, the derived-`file_role` split on upload,
 * and (bugfix-resource-file-delete) that resources can be removed the same
 * way notebooks can, even when ids collide across the two tables.
 */
describe("<SessionDetail /> — resource files", () => {
  it("uploads a zip containing only resources and reports them as resources", async () => {
    uploadAssignmentMock.mockResolvedValue([
      uploaded({ id: 71, original_filename: "titanic.csv", file_role: "resource" }),
      uploaded({ id: 72, original_filename: "slides.pdf", file_role: "resource" }),
    ]);
    await renderDetail();

    const zip = new File(["PK"], "datasets.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Files"), zip);
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText("titanic.csv")).toBeInTheDocument());
    expect(screen.getByText("slides.pdf")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Uploaded 2 resource files.");

    // A resource-only session has nothing gradeable, and must say so rather
    // than silently showing an empty notebook list.
    expect(screen.getByText(/no gradeable notebooks yet/i)).toBeInTheDocument();
  });

  it("splits a mixed zip into notebooks and resources by file_role", async () => {
    uploadAssignmentMock.mockResolvedValue([
      uploaded({ id: 31, original_filename: "analysis.ipynb", file_role: "notebook" }),
      uploaded({ id: 71, original_filename: "titanic.csv", file_role: "resource" }),
    ]);
    await renderDetail();

    const zip = new File(["PK"], "week3.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Files"), zip);
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    await waitFor(() => expect(screen.getByText("analysis.ipynb")).toBeInTheDocument());
    expect(screen.getByText("titanic.csv")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Uploaded 1 notebook and 1 resource file.",
    );

    // Categorisation, not just presence: each name must sit under its own
    // heading, so a regression that lumps both into one list would fail here.
    const notebooks = screen.getByRole("region", { name: /notebooks \(1\)/i });
    const resources = screen.getByRole("region", { name: /resource files \(1\)/i });
    expect(within(notebooks).getByText("analysis.ipynb")).toBeInTheDocument();
    expect(within(notebooks).queryByText("titanic.csv")).not.toBeInTheDocument();
    expect(within(resources).getByText("titanic.csv")).toBeInTheDocument();
    expect(within(resources).queryByText("analysis.ipynb")).not.toBeInTheDocument();
  });

  it("labels notebooks as gradeable and resources as not graded", async () => {
    getSessionMock.mockResolvedValue(
      sessionWith([file({ id: 14, original_filename: "a.ipynb" })], [resource({ id: 71 })]),
    );
    await renderDetail();

    await waitFor(() => expect(screen.getByText("a.ipynb")).toBeInTheDocument());
    expect(screen.getByText("Gradeable notebook")).toBeInTheDocument();
    expect(screen.getByText(/Resource · not graded/)).toBeInTheDocument();
  });

  it("asks for confirmation before removing a resource, and refreshes the list after", async () => {
    getSessionMock.mockResolvedValue(sessionWith([], [resource({ id: 71 })]));
    deleteResourceMock.mockResolvedValue(undefined);
    listResourcesMock.mockResolvedValue([]);

    await renderDetail();
    await waitFor(() => expect(screen.getByText("titanic.csv")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
    expect(deleteResourceMock).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /confirm remove/i }));

    await waitFor(() => expect(deleteResourceMock).toHaveBeenCalledWith(5, 71));
    expect(deleteAssignmentMock).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText("titanic.csv")).not.toBeInTheDocument());
  });

  it("can back out of removing a resource", async () => {
    getSessionMock.mockResolvedValue(sessionWith([], [resource({ id: 71 })]));
    await renderDetail();
    await waitFor(() => expect(screen.getByText("titanic.csv")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(deleteResourceMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /^remove$/i })).toBeInTheDocument();
  });

  it("removes the correct row when a notebook and a resource share the same id", async () => {
    // Both tables start their ids at 1, so id 14 can name a notebook AND a
    // resource in the same session -- confirming one must not remove the other.
    getSessionMock.mockResolvedValue(
      sessionWith(
        [file({ id: 14, original_filename: "a.ipynb" })],
        [resource({ id: 14, original_filename: "data.csv" })],
      ),
    );
    deleteResourceMock.mockResolvedValue(undefined);
    listResourcesMock.mockResolvedValue([]);

    await renderDetail();
    await waitFor(() => expect(screen.getByText("a.ipynb")).toBeInTheDocument());

    const resources = screen.getByRole("region", { name: /resource files \(1\)/i });
    await userEvent.click(within(resources).getByRole("button", { name: /^remove$/i }));
    await userEvent.click(within(resources).getByRole("button", { name: /confirm remove/i }));

    await waitFor(() => expect(deleteResourceMock).toHaveBeenCalledWith(5, 14));
    expect(deleteAssignmentMock).not.toHaveBeenCalled();
    // The notebook row is untouched.
    expect(screen.getByText("a.ipynb")).toBeInTheDocument();
  });

  it("downloads a resource through its own authenticated endpoint", async () => {
    getSessionMock.mockResolvedValue(sessionWith([], [resource({ id: 71 })]));
    const blob = new Blob(["a,b\n1,2\n"], { type: "application/octet-stream" });
    downloadResourceMock.mockResolvedValue(blob);

    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    await renderDetail();
    await userEvent.click(await screen.findByRole("button", { name: /download/i }));

    // The resource route, not the notebook route -- the ids overlap across the
    // two tables, so hitting the wrong one would silently return a notebook.
    await waitFor(() => expect(downloadResourceMock).toHaveBeenCalledWith(5, 71));
    expect(downloadAssignmentMock).not.toHaveBeenCalled();
    expect(createObjectURL).toHaveBeenCalledWith(blob);

    vi.unstubAllGlobals();
  });

  it("keeps notebook and resource downloads apart when ids collide", async () => {
    // Both tables start their ids at 1, so id 14 can name a notebook AND a
    // resource in the same session. Each row must call its own endpoint.
    getSessionMock.mockResolvedValue(
      sessionWith(
        [file({ id: 14, original_filename: "a.ipynb" })],
        [resource({ id: 14, original_filename: "data.csv" })],
      ),
    );
    downloadAssignmentMock.mockResolvedValue(new Blob(["nb"]));
    downloadResourceMock.mockResolvedValue(new Blob(["csv"]));
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });

    await renderDetail();
    await waitFor(() => expect(screen.getByText("a.ipynb")).toBeInTheDocument());

    const notebooks = screen.getByRole("region", { name: /notebooks \(1\)/i });
    const resources = screen.getByRole("region", { name: /resource files \(1\)/i });

    await userEvent.click(within(notebooks).getByRole("button", { name: /download/i }));
    await waitFor(() => expect(downloadAssignmentMock).toHaveBeenCalledWith(5, 14));

    await userEvent.click(within(resources).getByRole("button", { name: /download/i }));
    await waitFor(() => expect(downloadResourceMock).toHaveBeenCalledWith(5, 14));

    vi.unstubAllGlobals();
  });

  it("reports a batch rejection as atomic across both file types", async () => {
    // The backend's duplicate guard spans notebooks AND resources and runs
    // before any write, so a resource collision must still report that the
    // whole batch -- notebooks included -- was discarded.
    uploadAssignmentMock.mockRejectedValue(
      new ApiError(
        409,
        "A file named 'titanic.csv' already exists in session 5. Delete it first or use a different name.",
      ),
    );
    await renderDetail();

    const zip = new File(["PK"], "week3.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Files"), zip);
    await userEvent.click(screen.getByRole("button", { name: /^upload$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/titanic\.csv/);
    expect(alert).toHaveTextContent(/nothing was uploaded/);
    // Nothing was added to either list.
    expect(screen.getByText(/no assignment files yet/i)).toBeInTheDocument();
  });
});

describe("describeUpload", () => {
  it("names the role of a single uploaded file", () => {
    expect(describeUpload([uploaded({ original_filename: "a.ipynb" })])).toBe(
      "Uploaded a.ipynb as a notebook.",
    );
    expect(
      describeUpload([
        uploaded({ original_filename: "d.csv", file_role: "resource" }),
      ]),
    ).toBe("Uploaded d.csv as a resource file.");
  });

  it("counts each kind separately for a batch", () => {
    expect(
      describeUpload([
        uploaded({ id: 1, original_filename: "a.ipynb" }),
        uploaded({ id: 2, original_filename: "b.ipynb" }),
        uploaded({ id: 3, original_filename: "d.csv", file_role: "resource" }),
      ]),
    ).toBe("Uploaded 2 notebooks and 1 resource file.");
  });

  it("mentions only the kind that is present", () => {
    expect(
      describeUpload([
        uploaded({ id: 1, original_filename: "x.csv", file_role: "resource" }),
        uploaded({ id: 2, original_filename: "y.pdf", file_role: "resource" }),
      ]),
    ).toBe("Uploaded 2 resource files.");
  });
});
