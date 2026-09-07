/** Student dashboard: all-sessions scope, states, and the enrolment note. */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { ApiError } from "@/lib/api";
import type { SessionRead } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/student",
  useSearchParams: () => new URLSearchParams(),
}));

const listSessionsMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, listSessions: (...a: unknown[]) => listSessionsMock(...a) };
});

import { StudentDashboard } from "./student-dashboard";

function session(overrides: Partial<SessionRead> = {}): SessionRead {
  return {
    id: 5,
    title: "Week 3 Day 1",
    instructor_id: 1,
    created_at: "2026-09-06T15:43:53.245057",
    unsolved_files: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listSessionsMock.mockResolvedValue({ total: 0, items: [] });
});

describe("<StudentDashboard />", () => {
  it("shows a loading state first", () => {
    listSessionsMock.mockReturnValue(new Promise(() => {}));
    render(<StudentDashboard />);
    expect(screen.getByText(/loading sessions/i)).toBeInTheDocument();
  });

  it("shows an empty state when no sessions exist", async () => {
    render(<StudentDashboard />);
    expect(await screen.findByText(/no sessions have been created yet/i)).toBeInTheDocument();
  });

  it("lists sessions with file counts, linking to the student route", async () => {
    listSessionsMock.mockResolvedValue({
      total: 2,
      items: [
        session({ id: 5, title: "Week 3 Day 1", unsolved_files: [] }),
        session({
          id: 3,
          title: "Week 2 Day 1",
          unsolved_files: [
            {
              id: 8,
              session_id: 3,
              original_filename: "Numpy_and_Plotting.ipynb",
              rubric_generated: true,
              uploaded_at: "2026-09-06T15:40:00",
            },
          ],
        }),
      ],
    });

    render(<StudentDashboard />);

    const first = await screen.findByRole("link", { name: /week 3 day 1/i });
    expect(first).toHaveAttribute("href", "/student/sessions/5");
    expect(within(first).getByText(/0 files/)).toBeInTheDocument();

    const second = screen.getByRole("link", { name: /week 2 day 1/i });
    expect(second).toHaveAttribute("href", "/student/sessions/3");
    expect(within(second).getByText(/1 file$/)).toBeInTheDocument();
  });

  it("shows EVERY session, including other instructors' — the confirmed scope", async () => {
    // No enrolment concept exists, so nothing is filtered out.
    listSessionsMock.mockResolvedValue({
      total: 2,
      items: [
        session({ id: 5, title: "Instructor one's session", instructor_id: 1 }),
        session({ id: 9, title: "Instructor two's session", instructor_id: 42 }),
      ],
    });

    render(<StudentDashboard />);

    expect(await screen.findByText("Instructor one's session")).toBeInTheDocument();
    expect(screen.getByText("Instructor two's session")).toBeInTheDocument();
  });

  it("requests one full page rather than the default 50", async () => {
    render(<StudentDashboard />);
    await screen.findByText(/no sessions have been created yet/i);
    expect(listSessionsMock).toHaveBeenCalledWith({ limit: 200 });
  });

  it("states the enrolment gap on both the empty and populated list", async () => {
    render(<StudentDashboard />);
    expect(await screen.findByText(/no enrolment in this system/i)).toBeInTheDocument();

    listSessionsMock.mockResolvedValue({ total: 1, items: [session()] });
    render(<StudentDashboard />);
    const notes = await screen.findAllByText(/no enrolment in this system/i);
    expect(notes.length).toBeGreaterThan(0);
  });

  it("surfaces a load failure rather than showing a false empty list", async () => {
    listSessionsMock.mockRejectedValue(new ApiError(500, "Database unavailable."));
    render(<StudentDashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable.");
  });
});
