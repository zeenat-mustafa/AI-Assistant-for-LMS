/** Route-guard behaviour: anonymous, wrong role, loading, and allowed. */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import type { AuthContextValue, AuthStatus } from "@/lib/auth/auth-context";
import type { UserRead } from "@/lib/api";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/instructor",
  useSearchParams: () => new URLSearchParams(),
}));

let auth: AuthContextValue;
vi.mock("@/lib/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/auth-context")>();
  return { ...actual, useAuth: () => auth };
});

import { RequireAuth } from "./require-auth";

const INSTRUCTOR: UserRead = {
  id: 1,
  name: "Demo Instructor",
  email: "instructor@demo.com",
  role: "instructor",
  created_at: "2026-09-06T15:26:49.014311",
};
const STUDENT: UserRead = { ...INSTRUCTOR, id: 2, name: "Demo Student", role: "student" };

function setAuth(status: AuthStatus, user: UserRead | null = null) {
  auth = { user, status, signIn: vi.fn(), signOut: vi.fn(), refresh: vi.fn() };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("<RequireAuth />", () => {
  it("renders the page for a matching role", () => {
    setAuth("authenticated", INSTRUCTOR);
    render(
      <RequireAuth role="instructor">
        <p>secret content</p>
      </RequireAuth>,
    );
    expect(screen.getByText("secret content")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects an anonymous visitor to /login, preserving where they were going", async () => {
    setAuth("anonymous");
    render(
      <RequireAuth role="instructor">
        <p>secret content</p>
      </RequireAuth>,
    );
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/login?next=%2Finstructor"),
    );
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("sends a signed-in student away from an instructor page to their own home", async () => {
    setAuth("authenticated", STUDENT);
    render(
      <RequireAuth role="instructor">
        <p>secret content</p>
      </RequireAuth>,
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/student"));
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("waits instead of bouncing while the session is still loading", () => {
    setAuth("loading");
    render(
      <RequireAuth role="instructor">
        <p>secret content</p>
      </RequireAuth>,
    );
    expect(replace).not.toHaveBeenCalled();
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/checking your session/i);
  });

  it("sends a user who signs out here to plain /login, with no ?next=", async () => {
    // Mount authenticated, then flip to anonymous -- i.e. a logout, not an
    // interrupted visit. Coming back to this page is not what they asked for.
    setAuth("authenticated", INSTRUCTOR);
    const { rerender } = render(
      <RequireAuth role="instructor">
        <p>secret content</p>
      </RequireAuth>,
    );
    expect(screen.getByText("secret content")).toBeInTheDocument();

    setAuth("anonymous");
    rerender(
      <RequireAuth role="instructor">
        <p>secret content</p>
      </RequireAuth>,
    );

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(replace).not.toHaveBeenCalledWith("/login?next=%2Finstructor");
  });

  it("allows any signed-in role when no role is required", () => {
    setAuth("authenticated", STUDENT);
    render(
      <RequireAuth>
        <p>shared content</p>
      </RequireAuth>,
    );
    expect(screen.getByText("shared content")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
