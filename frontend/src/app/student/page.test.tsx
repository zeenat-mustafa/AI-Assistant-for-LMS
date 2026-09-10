/**
 * The floating grading-chat widget is instructor-only. It is mounted at
 * app/instructor/layout.tsx, and there is no equivalent student layout --
 * this renders the real student page tree end to end to prove nothing there
 * pulls it in by accident.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import type { UserRead } from "@/lib/api";

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

const STUDENT: UserRead = {
  id: 2,
  name: "Demo Student",
  email: "student@demo.com",
  role: "student",
  created_at: "2026-09-06T15:26:49.014311",
};

vi.mock("@/lib/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/auth-context")>();
  return {
    ...actual,
    useAuth: () => ({
      user: STUDENT,
      status: "authenticated" as const,
      signIn: vi.fn(),
      signOut: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});

import StudentHomePage from "./page";

beforeEach(() => {
  vi.clearAllMocks();
  listSessionsMock.mockResolvedValue({ total: 0, items: [] });
});

describe("<StudentHomePage /> — floating chat widget is never present", () => {
  it("does not render the grading chat widget for a signed-in student", async () => {
    render(<StudentHomePage />);
    expect(await screen.findByText(/no sessions have been created yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open grading chat/i })).not.toBeInTheDocument();
  });
});
