/**
 * Instructor dashboard: create-session form and session list.
 * API client and auth context are mocked; no real network.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { SessionRead, UserRead } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/instructor",
  useSearchParams: () => new URLSearchParams(),
}));

const listSessionsMock = vi.fn();
const createSessionMock = vi.fn();
const renameSessionMock = vi.fn();
const deleteSessionMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listSessions: (...args: unknown[]) => listSessionsMock(...args),
    createSession: (...args: unknown[]) => createSessionMock(...args),
    renameSession: (...args: unknown[]) => renameSessionMock(...args),
    deleteSession: (...args: unknown[]) => deleteSessionMock(...args),
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

import { InstructorDashboard } from "./instructor-dashboard";

function session(overrides: Partial<SessionRead> = {}): SessionRead {
  return {
    id: 5,
    title: "Week 3 Day 1",
    instructor_id: 1,
    instructor_name: "Demo Instructor",
    created_at: "2026-09-06T15:43:53.245057",
    assignment_uploads: [],
    unsolved_files: [],
    resource_files: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listSessionsMock.mockResolvedValue({ total: 0, items: [] });
});

describe("<InstructorDashboard /> — session list", () => {
  it("shows an empty state, not a blank screen, when there are no sessions", async () => {
    render(<InstructorDashboard />);
    expect(await screen.findByText(/no sessions yet/i)).toBeInTheDocument();
  });

  it("shows a loading state before the list arrives", () => {
    listSessionsMock.mockReturnValue(new Promise(() => {}));
    render(<InstructorDashboard />);
    expect(screen.getByText(/loading sessions/i)).toBeInTheDocument();
  });

  it("renders sessions with their file counts, linking to the detail route", async () => {
    listSessionsMock.mockResolvedValue({
      total: 2,
      items: [
        session({ id: 5, title: "Week 3 Day 1", unsolved_files: [] }),
        session({
          id: 2,
          title: "Week 1 Day 2",
          unsolved_files: [
            {
              id: 4,
              session_id: 2,
              original_filename: "a.ipynb",
              rubric_generated: false,
              uploaded_at: "2026-09-06T15:36:04.937382",
            },
          ],
        }),
      ],
    });

    render(<InstructorDashboard />);

    const first = await screen.findByRole("link", { name: /week 3 day 1/i });
    expect(first).toHaveAttribute("href", "/instructor/sessions/5");
    expect(within(first).getByText(/0 files/)).toBeInTheDocument();

    const second = screen.getByRole("link", { name: /week 1 day 2/i });
    expect(second).toHaveAttribute("href", "/instructor/sessions/2");
    // Singular, not "1 files".
    expect(within(second).getByText(/1 file$/)).toBeInTheDocument();
  });

  it("shows sessions from every instructor, naming who created each one", async () => {
    // Shared faculty workspace, deliberately: GET /sessions has no owner
    // filter and the dashboard no longer applies one client-side either.
    listSessionsMock.mockResolvedValue({
      total: 2,
      items: [
        session({
          id: 5,
          title: "Mine",
          instructor_id: 1,
          instructor_name: "Demo Instructor",
        }),
        session({
          id: 6,
          title: "Someone else's",
          instructor_id: 99,
          instructor_name: "Demo Instructor 2",
        }),
      ],
    });

    render(<InstructorDashboard />);

    const mine = await screen.findByRole("link", { name: /mine/i });
    expect(within(mine).getByText(/Demo Instructor ·/)).toBeInTheDocument();

    const someoneElses = screen.getByRole("link", { name: /someone else's/i });
    expect(within(someoneElses).getByText(/Demo Instructor 2 ·/)).toBeInTheDocument();
  });

  it("surfaces a load failure instead of pretending the list is empty", async () => {
    listSessionsMock.mockRejectedValue(new ApiError(500, "Database unavailable."));
    render(<InstructorDashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable.");
  });
});

describe("<InstructorDashboard /> — create session", () => {
  it("rejects an empty title without calling the API", async () => {
    render(<InstructorDashboard />);
    await screen.findByText(/no sessions yet/i);

    await userEvent.click(screen.getByRole("button", { name: /create session/i }));

    expect(await screen.findByText("Session title is required.")).toBeInTheDocument();
    expect(createSessionMock).not.toHaveBeenCalled();
  });

  it("rejects a whitespace-only title", async () => {
    render(<InstructorDashboard />);
    await screen.findByText(/no sessions yet/i);

    await userEvent.type(screen.getByLabelText("Session title"), "   ");
    await userEvent.click(screen.getByRole("button", { name: /create session/i }));

    expect(await screen.findByText("Session title is required.")).toBeInTheDocument();
    expect(createSessionMock).not.toHaveBeenCalled();
  });

  it("adds the new session to the list without refetching", async () => {
    const created = session({ id: 9, title: "Week 4 Day 1" });
    createSessionMock.mockResolvedValue(created);

    render(<InstructorDashboard />);
    await screen.findByText(/no sessions yet/i);

    await userEvent.type(screen.getByLabelText("Session title"), "  Week 4 Day 1  ");
    await userEvent.click(screen.getByRole("button", { name: /create session/i }));

    const link = await screen.findByRole("link", { name: /week 4 day 1/i });
    expect(link).toHaveAttribute("href", "/instructor/sessions/9");
    // Title is trimmed before it is sent.
    expect(createSessionMock).toHaveBeenCalledWith("Week 4 Day 1");
    // No reload: the list was fetched once, on mount.
    expect(listSessionsMock).toHaveBeenCalledTimes(1);
    // The field is cleared, ready for the next one.
    expect(screen.getByLabelText("Session title")).toHaveValue("");
  });

  it("shows the backend's duplicate-title 409 verbatim", async () => {
    createSessionMock.mockRejectedValue(
      new ApiError(409, "A session titled 'Week 3 Day 1' already exists (id=5)."),
    );

    render(<InstructorDashboard />);
    await screen.findByText(/no sessions yet/i);

    await userEvent.type(screen.getByLabelText("Session title"), "Week 3 Day 1");
    await userEvent.click(screen.getByRole("button", { name: /create session/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists \(id=5\)/);
    // Still usable for a retry, and the typed title is preserved.
    expect(screen.getByRole("button", { name: /create session/i })).toBeEnabled();
    expect(screen.getByLabelText("Session title")).toHaveValue("Week 3 Day 1");
  });
});

describe("<InstructorDashboard /> — rename", () => {
  it("renames a session in place and reflects the new title", async () => {
    listSessionsMock.mockResolvedValue({
      total: 1,
      items: [session({ id: 5, title: "Week 3 Day 1" })],
    });
    renameSessionMock.mockResolvedValue(
      session({ id: 5, title: "Week 3 Day 1 (renamed)" }),
    );

    render(<InstructorDashboard />);
    await screen.findByRole("link", { name: /week 3 day 1/i });

    await userEvent.click(screen.getByRole("button", { name: /^rename$/i }));
    const input = screen.getByDisplayValue("Week 3 Day 1");
    await userEvent.clear(input);
    await userEvent.type(input, "Week 3 Day 1 (renamed)");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(renameSessionMock).toHaveBeenCalledWith(5, "Week 3 Day 1 (renamed)"),
    );
    expect(
      await screen.findByRole("link", { name: /week 3 day 1 \(renamed\)/i }),
    ).toBeInTheDocument();
  });

  it("can cancel a rename without calling the API", async () => {
    listSessionsMock.mockResolvedValue({
      total: 1,
      items: [session({ id: 5, title: "Week 3 Day 1" })],
    });

    render(<InstructorDashboard />);
    await screen.findByRole("link", { name: /week 3 day 1/i });

    await userEvent.click(screen.getByRole("button", { name: /^rename$/i }));
    await userEvent.clear(screen.getByDisplayValue("Week 3 Day 1"));
    await userEvent.type(screen.getByLabelText(/rename/i), "Something else");
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(renameSessionMock).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /week 3 day 1/i })).toBeInTheDocument();
  });

  it("shows the backend's global-conflict 409 verbatim on rename, naming the other owner", async () => {
    listSessionsMock.mockResolvedValue({
      total: 1,
      items: [session({ id: 5, title: "Week 3 Day 1" })],
    });
    renameSessionMock.mockRejectedValue(
      new ApiError(
        409,
        "A session titled 'Week 2 Day 1' already exists (id=9 by Demo Instructor 2).",
      ),
    );

    render(<InstructorDashboard />);
    await screen.findByRole("link", { name: /week 3 day 1/i });

    await userEvent.click(screen.getByRole("button", { name: /^rename$/i }));
    await userEvent.clear(screen.getByDisplayValue("Week 3 Day 1"));
    await userEvent.type(screen.getByLabelText(/rename/i), "Week 2 Day 1");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /already exists \(id=9 by Demo Instructor 2\)/,
    );
    // Still in edit mode for a retry -- nothing was silently discarded.
    expect(screen.getByLabelText(/rename/i)).toHaveValue("Week 2 Day 1");
  });
});

describe("<InstructorDashboard /> — delete", () => {
  it("asks for confirmation before deleting, and removes the row after", async () => {
    listSessionsMock.mockResolvedValue({
      total: 1,
      items: [session({ id: 5, title: "Week 3 Day 1" })],
    });
    deleteSessionMock.mockResolvedValue(undefined);

    render(<InstructorDashboard />);
    await screen.findByRole("link", { name: /week 3 day 1/i });

    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(deleteSessionMock).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /confirm delete/i }));

    await waitFor(() => expect(deleteSessionMock).toHaveBeenCalledWith(5));
    expect(screen.queryByRole("link", { name: /week 3 day 1/i })).not.toBeInTheDocument();
  });

  it("can back out of a delete", async () => {
    listSessionsMock.mockResolvedValue({
      total: 1,
      items: [session({ id: 5, title: "Week 3 Day 1" })],
    });

    render(<InstructorDashboard />);
    await screen.findByRole("link", { name: /week 3 day 1/i });

    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(deleteSessionMock).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /week 3 day 1/i })).toBeInTheDocument();
  });
});
