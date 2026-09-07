"use client";

/**
 * Login form — one form for both roles. The backend's /auth/login takes no
 * role, and the role comes back from /auth/me, so there is nothing for the
 * user to pick here.
 *
 * A plain client-side submit handler rather than a Server Action: the JWT
 * has to reach `localStorage` in the browser, and a Server Action runs on
 * the Next server where it could neither read nor write that.
 */

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

import { ApiError } from "@/lib/api";
import { homePathForRole, useAuth } from "@/lib/auth/auth-context";
import { AuthCard, Field, FormError, FormNotice, SubmitButton } from "@/components/ui";

interface FieldErrors {
  email?: string;
  password?: string;
}

/** Client-side sanity checks only — the backend is the real authority. */
export function validateLogin(email: string, password: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!email.trim()) errors.email = "Email is required.";
  else if (!email.includes("@")) errors.email = "Enter a valid email address.";
  if (!password) errors.password = "Password is required.";
  return errors;
}

export function LoginForm() {
  const { signIn } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const justRegistered = searchParams.get("registered") === "1";

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const errors = validateLogin(email, password);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setPending(true);
    try {
      const user = await signIn(email, password);
      // Honour ?next= from RequireAuth, but only for in-app paths — an
      // attacker-supplied absolute URL must not become an open redirect.
      const next = searchParams.get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : null;
      router.replace(safeNext ?? homePathForRole(user.role));
    } catch (error) {
      setFormError(
        error instanceof ApiError
          ? error.detail
          : "Something went wrong signing in. Please try again.",
      );
      setPending(false);
    }
  }

  return (
    <AuthCard title="Sign in" subtitle="AI Assistant for LMS">
      {justRegistered ? (
        <FormNotice>Account created. Sign in with your new details.</FormNotice>
      ) : null}
      {formError ? <FormError>{formError}</FormError> : null}

      <form onSubmit={handleSubmit} noValidate>
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
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={fieldErrors.password}
        />
        <SubmitButton pending={pending}>Sign in</SubmitButton>
      </form>

      <p className="mt-6 text-center text-sm text-slate-500">
        New student?{" "}
        <Link href="/register" className="font-medium text-slate-900 underline">
          Create an account
        </Link>
      </p>
    </AuthCard>
  );
}
