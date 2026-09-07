"use client";

/**
 * Entry route: sends the visitor wherever their session says they belong.
 * Client-side because the session lives in localStorage (see auth-context).
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { homePathForRole, useAuth } from "@/lib/auth/auth-context";

export default function RootPage() {
  const { user, status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "loading") return;
    router.replace(status === "authenticated" && user ? homePathForRole(user.role) : "/login");
  }, [status, user, router]);

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <p className="text-sm text-slate-500" role="status">
        Loading…
      </p>
    </main>
  );
}
