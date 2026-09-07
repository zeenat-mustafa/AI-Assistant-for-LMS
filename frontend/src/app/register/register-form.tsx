"use client";

/**
 * Student self-registration.
 *
 * There is no role selector and no instructor path: POST /auth/register is
 * public and always creates a `student` (the role cannot be set by the
 * caller). Instructors are seeded by the backend.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { ApiError, register } from "@/lib/api";
import { homePathForRole, useAuth } from "@/lib/auth/auth-context";
import { AuthCard, Field, FormError, SubmitButton } from "@/components/ui";

/** Client-side minimum; the backend imposes no password rules of its own. */
export const MIN_PASSWORD_LENGTH = 8;

interface FieldErrors {
  name?: string;
  email?: string;
  password?: string;
  confirmPassword?: string;
}

export function validateRegistration(values: {
  name: string;
  email: string;
  password: string;
  confirmPassword: string;
}): FieldErrors {
  const errors: FieldErrors = {};
  if (!values.name.trim()) errors.name = "Name is required.";
  if (!values.email.trim()) errors.email = "Email is required.";
  else if (!values.email.includes("@")) errors.email = "Enter a valid email address.";
  if (!values.password) errors.password = "Password is required.";
  else if (values.password.length < MIN_PASSWORD_LENGTH) {
    errors.password = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (values.confirmPassword !== values.password) {
    errors.confirmPassword = "Passwords do not match.";
  }
  return errors;
}

export function RegisterForm() {
  const { signIn } = useAuth();
  const router = useRouter();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const errors = validateRegistration({ name, email, password, confirmPassword });
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setPending(true);
    try {
      await register({ name: name.trim(), email: email.trim(), password });
    } catch (error) {
      setFormError(
        error instanceof ApiError
          ? error.detail // e.g. the backend's 409 "email already exists"
          : "Something went wrong creating your account. Please try again.",
      );
      setPending(false);
      return;
    }

    // Auto-login: /auth/register returns the user but no token, so a login
    // call is needed either way -- and we already hold valid credentials.
    // If it fails for any reason the account still exists, so fall back to
    // /login rather than stranding them on a form that would now 409.
    try {
      const user = await signIn(email.trim(), password);
      router.replace(homePathForRole(user.role));
    } catch {
      router.replace("/login?registered=1");
    }
  }

  return (
    <AuthCard title="Create a student account" subtitle="AI Assistant for LMS">
      {formError ? <FormError>{formError}</FormError> : null}

      <form onSubmit={handleSubmit} noValidate>
        <Field
          label="Name"
          name="name"
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={fieldErrors.name}
        />
        <Field
          label="Email"
          type="email"
          name="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={fieldErrors.email}
        />
        <Field
          label="Password"
          type="password"
          name="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={fieldErrors.password}
        />
        <Field
          label="Confirm password"
          type="password"
          name="confirmPassword"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          error={fieldErrors.confirmPassword}
        />
        <SubmitButton pending={pending}>Create account</SubmitButton>
      </form>

      <p className="mt-6 text-center text-sm text-slate-500">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-slate-900 underline">
          Sign in
        </Link>
      </p>
    </AuthCard>
  );
}
