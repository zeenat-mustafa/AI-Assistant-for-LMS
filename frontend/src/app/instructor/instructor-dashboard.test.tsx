/**
 * Instructor dashboard: create-session form and session list.
 * API client and auth context are mocked; no real network.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listSessions: (...args: unknown[]) => listSessionsMock(...args),
    createSession: (...args: unknown[]) => createSessionMock(...args),
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
    created_at: "2026-09-06T15:43:53.245057",
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
    expect(screen.getByText(/loading your sessions/i)).toBeInTheDocument();
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

  it("hides sessions owned by another instructor", async () => {
    // GET /sessions has no server-side owner filter, so the narrowing is ours.
    listSessionsMock.mockResolvedValue({
      total: 2,
      items: [
        session({ id: 5, title: "Mine", instructor_id: 1 }),
        session({ id: 6, title: "Someone else's", instructor_id: 99 }),
      ],
    });

    render(<InstructorDashboard />);

    expect(await screen.findByText("Mine")).toBeInTheDocument();
    expect(screen.queryByText("Someone else's")).not.toBeInTheDocument();
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
