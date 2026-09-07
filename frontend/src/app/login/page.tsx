import { Suspense } from "react";
import type { Metadata } from "next";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in — AI Assistant for LMS" };

/**
 * `LoginForm` reads `?next=` / `?registered=` with `useSearchParams`, which
 * must sit under a Suspense boundary or it opts the whole route out of
 * prerendering.
 */
export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
