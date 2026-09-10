/**
 * The floating chat widget mounts once at the shared instructor layout, so
 * every page under /instructor gets it without remounting per page.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import type { UserRead } from "@/lib/api";

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

import InstructorLayout from "./layout";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("<InstructorLayout />", () => {
  it("renders its children plus the floating chat widget, collapsed by default", () => {
    render(
      <InstructorLayout>
        <p>Page content</p>
      </InstructorLayout>,
    );

    expect(screen.getByText("Page content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open grading chat/i })).toBeInTheDocument();
  });
});
