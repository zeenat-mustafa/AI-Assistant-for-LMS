"use client";

/**
 * Header + logout chrome shared by the signed-in placeholder pages.
 * Real dashboard content arrives in 5.3-5.6; this only proves the session.
 */

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth/auth-context";

export function SignedInShell({
  children,
  fullWidth = false,
}: {
  children: ReactNode;
  /** When true the main area fills the full viewport width (used by the
   *  two-column session shell). When false (default) it keeps the original
   *  max-w-4xl centred layout used by the dashboard/home pages. */
  fullWidth?: boolean;
}) {
  const { user, signOut } = useAuth();
  const router = useRouter();

  function handleLogout() {
    signOut();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen flex-col bg-neutral-50">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-screen-xl items-center justify-between gap-4 px-6 py-3">
          <span className="text-sm font-semibold text-neutral-900">AI Assistant for LMS</span>
          <div className="flex items-center gap-3">
            {user ? (
              <span className="text-sm text-neutral-600">
                {user.name}{" "}
                <span className="lms-badge lms-badge-neutral ml-1">
                  {user.role}
                </span>
              </span>
            ) : null}
            <button
              type="button"
              onClick={handleLogout}
              className="lms-btn-secondary py-1 px-3 text-xs"
            >
              Log out
            </button>
          </div>
        </div>
      </header>
      {fullWidth ? (
        <div className="flex flex-1 flex-col">{children}</div>
      ) : (
        <main className="mx-auto w-full max-w-4xl px-6 py-10">{children}</main>
      )}
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
