"use client";

/**
 * Client-side auth session state.
 *
 * Why a client context rather than Next's server-side session patterns:
 * the backend is a separate FastAPI service that issues a Bearer JWT, and
 * 5.1 stores that token in `localStorage`. Nothing on the Next server can
 * see it -- not `proxy.ts` (Next 16's renamed Middleware), not Server
 * Components -- so the session has to be resolved in the browser.
 *
 * `status` is deliberately three-valued. Treating "not loaded yet" as
 * "logged out" would bounce an authenticated user to /login on every hard
 * refresh, before the token has even been read.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { ApiError, clearToken, getCurrentUser, getToken, login, logout } from "@/lib/api";
import type { UserRead } from "@/lib/api";

export type AuthStatus = "loading" | "authenticated" | "anonymous";

export interface AuthContextValue {
  user: UserRead | null;
  status: AuthStatus;
  /** Log in and resolve with the user, so callers can route by role. */
  signIn: (email: string, password: string) => Promise<UserRead>;
  /** Drop the token and reset to anonymous. Does not navigate. */
  signOut: () => void;
  /** Re-read the session from the backend. */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

type ResolvedSession = { user: UserRead | null; status: Exclude<AuthStatus, "loading"> };

/**
 * Resolve the stored token against the backend.
 *
 * The presence of a token string is not treated as proof of a session -- it
 * can be expired, or issued by a since-reset dev database. Only a real
 * GET /auth/me counts, and a 401 clears the stale token.
 *
 * Returns the next state instead of setting it, so the caller decides
 * whether it is still wanted (see the cancellation guard below).
 */
async function resolveSession(): Promise<ResolvedSession> {
  if (!getToken()) return { user: null, status: "anonymous" };
  try {
    return { user: await getCurrentUser(), status: "authenticated" };
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) clearToken();
    return { user: null, status: "anonymous" };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserRead | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  const refresh = useCallback(async () => {
    const next = await resolveSession();
    setUser(next.user);
    setStatus(next.status);
  }, []);

  useEffect(() => {
    // `cancelled` stops a slow /auth/me from overwriting a newer session --
    // e.g. the user signs in, or signs out, while the request is in flight.
    let cancelled = false;
    void resolveSession().then((next) => {
      if (cancelled) return;
      setUser(next.user);
      setStatus(next.status);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    // 5.1's login() stores the token; getCurrentUser() then picks it up.
    await login(email, password);
    const me = await getCurrentUser();
    setUser(me);
    setStatus("authenticated");
    return me;
  }, []);

  const signOut = useCallback(() => {
    logout();
    setUser(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, signIn, signOut, refresh }),
    [user, status, signIn, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>.");
  return context;
}

/** Where a user lands after logging in, by role. */
export function homePathForRole(role: UserRead["role"]): "/instructor" | "/student" {
  return role === "instructor" ? "/instructor" : "/student";
}
