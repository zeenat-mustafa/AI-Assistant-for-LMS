"use client";

/**
 * Route guard for protected pages.
 *
 * Placed inside each protected *page*, not in a shared layout. The Next.js
 * docs are explicit about why: because of Partial Rendering, layouts do not
 * re-render on navigation, so a layout-level check would not run on every
 * route change -- and a layout cannot stop the rest of the route from
 * rendering anyway. The guidance is to check close to the component being
 * conditionally rendered, which is what this component is for.
 *
 * `proxy.ts` (Next 16's renamed Middleware) is not usable here either: it
 * runs on the server and can only read cookies, while our JWT lives in
 * localStorage. The docs also limit Proxy to optimistic checks rather than
 * real authorization.
 *
 * This guard is therefore UX, not security. Enforcement stays where it
 * already is -- every protected FastAPI endpoint requires the Bearer token,
 * so a user who bypasses this component still gets 401/403 from the API.
 */

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";

import { homePathForRole, useAuth } from "@/lib/auth/auth-context";
import type { UserRole } from "@/lib/api";

export function RequireAuth({
  role,
  children,
}: {
  /** When set, also require this exact role. */
  role?: UserRole;
  children: ReactNode;
}) {
  const { user, status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  const wrongRole = status === "authenticated" && role !== undefined && user?.role !== role;

  // Distinguishes "arrived here without a session" from "signed out while
  // here". Only the former should come back to this page after logging in --
  // tacking ?next= onto a logout would send the *next* person who signs in on
  // this browser to the previous user's page.
  const hadSession = useRef(false);
  useEffect(() => {
    if (status === "authenticated") hadSession.current = true;
  }, [status]);

  useEffect(() => {
    if (status === "loading") return;

    if (status === "anonymous") {
      // Remember where they were headed so /login can send them back.
      router.replace(
        hadSession.current ? "/login" : `/login?next=${encodeURIComponent(pathname)}`,
      );
      return;
    }

    // Signed in, but this page belongs to the other role -- send them home
    // rather than to /login, which would look like a broken session.
    if (wrongRole && user) router.replace(homePathForRole(user.role));
  }, [status, wrongRole, user, pathname, router]);

  if (status === "loading") return <GuardMessage>Checking your session…</GuardMessage>;
  if (status === "anonymous") return <GuardMessage>Redirecting to sign in…</GuardMessage>;
  if (wrongRole) return <GuardMessage>Redirecting…</GuardMessage>;

  return <>{children}</>;
}

function GuardMessage({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <p className="text-sm text-slate-500" role="status">
        {children}
      </p>
    </div>
  );
}
