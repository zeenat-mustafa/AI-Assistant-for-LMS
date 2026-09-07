/**
 * Login form tests — validation, error display, and role-based redirect.
 * The API client and Next's router are both mocked; no real network calls.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { UserRead } from "@/lib/api";

const replace = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => searchParams,
  usePathname: () => "/login",
}));

const signIn = vi.fn();
vi.mock("@/lib/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/auth-context")>();
  return {
    ...actual,
    useAuth: () => ({
      user: null,
      status: "anonymous" as const,
      signIn,
      signOut: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});

import { LoginForm, validateLogin } from "./login-form";

function user(role: UserRead["role"]): UserRead {
  return {
    id: role === "instructor" ? 1 : 2,
    name: role === "instructor" ? "Demo Instructor" : "Demo Student",
    email: `${role}@demo.com`,
    role,
    created_at: "2026-09-06T15:26:49.014311",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  searchParams = new URLSearchParams();
});

describe("validateLogin", () => {
  it("requires both fields", () => {
    expect(validateLogin("", "")).toEqual({
      email: "Email is required.",
      password: "Password is required.",
    });
  });

  it("rejects an address with no @", () => {
    expect(validateLogin("nope", "secret").email).toBe("Enter a valid email address.");
  });

  it("accepts a filled-in pair", () => {
    expect(validateLogin("a@b.com", "secret")).toEqual({});
  });
});

describe("<LoginForm />", () => {
  it("shows field errors and never calls the API when the form is empty", async () => {
    render(<LoginForm />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Email is required.")).toBeInTheDocument();
    expect(screen.getByText("Password is required.")).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("sends an instructor to /instructor", async () => {
    signIn.mockResolvedValue(user("instructor"));
    render(<LoginForm />);

    await userEvent.type(screen.getByLabelText("Email"), "instructor@demo.com");
    await userEvent.type(screen.getByLabelText("Password"), "instructor123");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/instructor"));
    expect(signIn).toHaveBeenCalledWith("instructor@demo.com", "instructor123");
  });

  it("sends a student to /student", async () => {
    signIn.mockResolvedValue(user("student"));
    render(<LoginForm />);

    await userEvent.type(screen.getByLabelText("Email"), "student@demo.com");
    await userEvent.type(screen.getByLabelText("Password"), "student123");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/student"));
  });

  it("displays the backend's message when login is rejected", async () => {
    signIn.mockRejectedValue(new ApiError(401, "Incorrect email or password."));
    render(<LoginForm />);

    await userEvent.type(screen.getByLabelText("Email"), "instructor@demo.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
    expect(replace).not.toHaveBeenCalled();
    // The form stays usable for a retry.
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
  });

  it("falls back to a generic message for a non-API failure", async () => {
    signIn.mockRejectedValue(new Error("boom"));
    render(<LoginForm />);

    await userEvent.type(screen.getByLabelText("Email"), "a@b.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/something went wrong/i);
  });

  it("returns the user to ?next= after signing in", async () => {
    searchParams = new URLSearchParams("next=%2Finstructor%2Fsessions%2F3");
    signIn.mockResolvedValue(user("instructor"));
    render(<LoginForm />);

    await userEvent.type(screen.getByLabelText("Email"), "instructor@demo.com");
    await userEvent.type(screen.getByLabelText("Password"), "instructor123");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/instructor/sessions/3"));
  });

  it("ignores an off-site ?next= instead of redirecting to it", async () => {
    searchParams = new URLSearchParams("next=https%3A%2F%2Fevil.example.com");
    signIn.mockResolvedValue(user("student"));
    render(<LoginForm />);

    await userEvent.type(screen.getByLabelText("Email"), "student@demo.com");
    await userEvent.type(screen.getByLabelText("Password"), "student123");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/student"));
  });

  it("shows the post-registration notice when ?registered=1", () => {
    searchParams = new URLSearchParams("registered=1");
    render(<LoginForm />);
    expect(screen.getByRole("status")).toHaveTextContent(/account created/i);
  });
});
