"use client";

/**
 * Header + logout chrome shared by the signed-in placeholder pages.
 * Real dashboard content arrives in 5.3-5.6; this only proves the session.
 */

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth/auth-context";

export function SignedInShell({ children }: { children: ReactNode }) {
  const { user, signOut } = useAuth();
  const router = useRouter();

  function handleLogout() {
    signOut();
    router.replace("/login");
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-6 py-4">
          <span className="text-sm font-semibold text-slate-900">AI Assistant for LMS</span>
          <div className="flex items-center gap-4">
            {user ? (
              <span className="text-sm text-slate-600">
                {user.name}{" "}
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                  {user.role}
                </span>
              </span>
            ) : null}
            <button
              type="button"
              onClick={handleLogout}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
            >
              Log out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
    </div>
  );
}

/** Placeholder body for a role home page until 5.3-5.6 fill them in. */
export function PlaceholderPanel({ heading, upcoming }: { heading: string; upcoming: string[] }) {
  const { user } = useAuth();
  return (
    <>
      <h1 className="text-2xl font-semibold text-slate-900">{heading}</h1>
      {user ? (
        <p className="mt-2 text-sm text-slate-600">
          Signed in as <strong className="text-slate-900">{user.name}</strong> ({user.email}) —
          role <strong className="text-slate-900">{user.role}</strong>.
        </p>
      ) : null}
      <div className="mt-8 rounded-xl border border-dashed border-slate-300 bg-white p-6">
        <p className="text-sm font-medium text-slate-700">Coming in later sub-features:</p>
        <ul className="mt-2 list-inside list-disc text-sm text-slate-500">
          {upcoming.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
    </>
  );
}
