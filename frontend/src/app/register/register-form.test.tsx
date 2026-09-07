/**
 * Register form tests — validation, backend error display, and the
 * auto-login-then-redirect flow (including its fallback).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { UserRead } from "@/lib/api";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/register",
}));

const registerMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, register: (...args: unknown[]) => registerMock(...args) };
});

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

import { MIN_PASSWORD_LENGTH, RegisterForm, validateRegistration } from "./register-form";

const STUDENT: UserRead = {
  id: 7,
  name: "New Student",
  email: "new@demo.com",
  role: "student",
  created_at: "2026-09-07T09:00:00",
};

async function fillValidForm() {
  await userEvent.type(screen.getByLabelText("Name"), "New Student");
  await userEvent.type(screen.getByLabelText("Email"), "new@demo.com");
  await userEvent.type(screen.getByLabelText("Password"), "notebook123");
  await userEvent.type(screen.getByLabelText("Confirm password"), "notebook123");
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("validateRegistration", () => {
  const valid = {
    name: "A",
    email: "a@b.com",
    password: "longenough",
    confirmPassword: "longenough",
  };

  it("accepts a complete form", () => {
    expect(validateRegistration(valid)).toEqual({});
  });

  it("requires every field", () => {
    const errors = validateRegistration({
      name: "",
      email: "",
      password: "",
      confirmPassword: "",
    });
    expect(errors.name).toBe("Name is required.");
    expect(errors.email).toBe("Email is required.");
    expect(errors.password).toBe("Password is required.");
  });

  it("enforces the minimum password length", () => {
    const short = "a".repeat(MIN_PASSWORD_LENGTH - 1);
    const errors = validateRegistration({ ...valid, password: short, confirmPassword: short });
    expect(errors.password).toContain(String(MIN_PASSWORD_LENGTH));
  });

  it("catches a mismatched confirmation", () => {
    expect(validateRegistration({ ...valid, confirmPassword: "different" }).confirmPassword).toBe(
      "Passwords do not match.",
    );
  });
});

describe("<RegisterForm />", () => {
  it("blocks submission and calls no API when fields are empty", async () => {
    render(<RegisterForm />);
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByText("Name is required.")).toBeInTheDocument();
    expect(registerMock).not.toHaveBeenCalled();
    expect(signIn).not.toHaveBeenCalled();
  });

  it("shows a mismatch error without hitting the API", async () => {
    render(<RegisterForm />);
    await userEvent.type(screen.getByLabelText("Name"), "New Student");
    await userEvent.type(screen.getByLabelText("Email"), "new@demo.com");
    await userEvent.type(screen.getByLabelText("Password"), "notebook123");
    await userEvent.type(screen.getByLabelText("Confirm password"), "notebook124");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByText("Passwords do not match.")).toBeInTheDocument();
    expect(registerMock).not.toHaveBeenCalled();
  });

  it("registers, auto-logs in, and lands on /student", async () => {
    registerMock.mockResolvedValue(STUDENT);
    signIn.mockResolvedValue(STUDENT);

    render(<RegisterForm />);
    await fillValidForm();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/student"));
    expect(registerMock).toHaveBeenCalledWith({
      name: "New Student",
      email: "new@demo.com",
      password: "notebook123",
    });
    expect(signIn).toHaveBeenCalledWith("new@demo.com", "notebook123");
  });

  it("shows the backend's 409 for a duplicate email and does not log in", async () => {
    registerMock.mockRejectedValue(
      new ApiError(409, "An account with email 'new@demo.com' already exists."),
    );

    render(<RegisterForm />);
    await fillValidForm();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/i);
    expect(signIn).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("falls back to /login?registered=1 when the account is made but auto-login fails", async () => {
    registerMock.mockResolvedValue(STUDENT);
    signIn.mockRejectedValue(new ApiError(500, "boom"));

    render(<RegisterForm />);
    await fillValidForm();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login?registered=1"));
  });
});
