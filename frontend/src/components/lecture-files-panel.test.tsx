/** Lecture files panel (7.7): .pptx-only upload, verbatim errors, list states, no delete. */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { LectureFileRead } from "@/lib/api";

const listLecturesMock = vi.fn();
const uploadLectureMock = vi.fn();
const downloadLectureMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listLectures: (...a: unknown[]) => listLecturesMock(...a),
    uploadLecture: (...a: unknown[]) => uploadLectureMock(...a),
    downloadLecture: (...a: unknown[]) => downloadLectureMock(...a),
  };
});

import { LEGACY_PPT_MESSAGE, LECTURE_RENAME_HINT, LectureFilesPanel } from "./lecture-files-panel";

function lecture(overrides: Partial<LectureFileRead> = {}): LectureFileRead {
  return {
    id: 3,
    session_id: 5,
    instructor_id: 1,
    original_filename: "Week10_Lecture.pptx",
    content_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    extracted: true,
    extraction_error: null,
    uploaded_at: "2026-09-12T10:00:00",
    ...overrides,
  };
}

// The input's accept=".pptx" would otherwise make userEvent drop a .ppt before
// our own check ever sees it; a real user can still pick "All files".
const user = userEvent.setup({ applyAccept: false });

async function renderInstructor() {
  render(<LectureFilesPanel sessionId={5} canUpload />);
  await waitFor(() => expect(listLecturesMock).toHaveBeenCalledWith(5));
}

beforeEach(() => {
  vi.clearAllMocks();
  listLecturesMock.mockResolvedValue([]);
});

describe("<LectureFilesPanel /> — list states", () => {
  it("shows a loading state, then the empty state", async () => {
    render(<LectureFilesPanel sessionId={5} canUpload />);
    expect(screen.getByText(/loading lecture files/i)).toBeInTheDocument();
    expect(await screen.findByText("No lecture files uploaded yet")).toBeInTheDocument();
  });

  it("lists the real rows in the backend's order with their real fields", async () => {
    listLecturesMock.mockResolvedValue([
      lecture({ id: 3, original_filename: "b.pptx" }),
      lecture({ id: 1, original_filename: "a.pptx", content_type: null }),
    ]);
    await renderInstructor();
    const names = (await screen.findAllByText(/\.pptx$/)).map((el) => el.textContent);
    expect(names).toEqual(["b.pptx", "a.pptx"]);
    expect(screen.getAllByText(/presentationml\.presentation/)).toHaveLength(1);
  });

  it("surfaces a load failure", async () => {
    listLecturesMock.mockRejectedValue(new ApiError(404, "Session 5 not found."));
    await renderInstructor();
    expect(await screen.findByRole("alert")).toHaveTextContent("Session 5 not found.");
  });

  it("shows the real extraction_error and a not-searchable warning for extracted: false", async () => {
    listLecturesMock.mockResolvedValue([
      lecture({ extracted: false, extraction_error: "File is not a zip file" }),
    ]);
    await renderInstructor();
    expect(await screen.findByText("File is not a zip file")).toBeInTheDocument();
    expect(screen.getByText(/stored, but its content could not be extracted/i)).toBeInTheDocument();
    expect(screen.getByText(/not searchable/i)).toBeInTheDocument();
  });

  it("never renders a delete control, in any state", async () => {
    listLecturesMock.mockResolvedValue([lecture(), lecture({ id: 4, extracted: false })]);
    await renderInstructor();
    await screen.findAllByText("Week10_Lecture.pptx");
    expect(screen.queryByRole("button", { name: /remove|delete/i })).not.toBeInTheDocument();
  });

  it("downloads through the authenticated client", async () => {
    listLecturesMock.mockResolvedValue([lecture()]);
    const blob = new Blob(["PK"]);
    downloadLectureMock.mockResolvedValue(blob);
    const createObjectURL = vi.fn(() => "blob:lecture");
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL: vi.fn() });

    await renderInstructor();
    await user.click(await screen.findByRole("button", { name: /^download$/i }));
    await waitFor(() => expect(downloadLectureMock).toHaveBeenCalledWith(5, 3));
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    vi.unstubAllGlobals();
  });
});

describe("<LectureFilesPanel /> — upload", () => {
  it("uploads a .pptx (one file, one request) and appends the created row", async () => {
    uploadLectureMock.mockResolvedValue(lecture({ id: 8, original_filename: "New.pptx" }));
    await renderInstructor();

    const file = new File(["PK"], "New.pptx");
    await user.upload(screen.getByLabelText("Lecture file (.pptx)"), file);
    await user.click(screen.getByRole("button", { name: /upload lecture/i }));

    await waitFor(() => expect(uploadLectureMock).toHaveBeenCalledTimes(1));
    expect(uploadLectureMock).toHaveBeenCalledWith(5, file);
    expect(await screen.findByText("New.pptx")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Uploaded New.pptx.");
  });

  it("blocks a .ppt with the backend's exact legacy-format wording, calling nothing", async () => {
    await renderInstructor();
    await user.upload(screen.getByLabelText("Lecture file (.pptx)"), new File(["x"], "Old.ppt"));
    expect(await screen.findByRole("alert")).toHaveTextContent(LEGACY_PPT_MESSAGE);
    expect(LEGACY_PPT_MESSAGE).toBe(
      "Legacy .ppt format is not supported — please save as .pptx and re-upload.",
    );

    await user.click(screen.getByRole("button", { name: /upload lecture/i }));
    expect(uploadLectureMock).not.toHaveBeenCalled();
  });

  it("blocks any other extension, naming .pptx as the only accepted format", async () => {
    await renderInstructor();
    await user.upload(screen.getByLabelText("Lecture file (.pptx)"), new File(["x"], "notes.pdf"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/only \.pptx lecture files are accepted/i);
    await user.click(screen.getByRole("button", { name: /upload lecture/i }));
    expect(uploadLectureMock).not.toHaveBeenCalled();
  });

  it("renders a 409 detail verbatim plus the rename line", async () => {
    const detail =
      "A lecture file named 'Week10_Lecture.pptx' already exists in session 5. Use a different name.";
    uploadLectureMock.mockRejectedValue(new ApiError(409, detail));
    await renderInstructor();

    await user.upload(screen.getByLabelText("Lecture file (.pptx)"), new File(["PK"], "Week10_Lecture.pptx"));
    await user.click(screen.getByRole("button", { name: /upload lecture/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(detail);
    expect(alert).toHaveTextContent(LECTURE_RENAME_HINT);
  });

  it("renders a 422 detail verbatim, without the rename line", async () => {
    uploadLectureMock.mockRejectedValue(new ApiError(422, "Unsupported file type '.pptx ' — whatever the backend says."));
    await renderInstructor();

    await user.upload(screen.getByLabelText("Lecture file (.pptx)"), new File(["PK"], "x.pptx"));
    await user.click(screen.getByRole("button", { name: /upload lecture/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Unsupported file type '.pptx ' — whatever the backend says.");
    expect(alert).not.toHaveTextContent(LECTURE_RENAME_HINT);
  });

  it("shows no upload control in the student (read-only) mode", async () => {
    render(<LectureFilesPanel sessionId={5} canUpload={false} />);
    expect(await screen.findByText("No lecture files uploaded yet")).toBeInTheDocument();
    expect(screen.queryByLabelText("Lecture file (.pptx)")).not.toBeInTheDocument();
  });
});
