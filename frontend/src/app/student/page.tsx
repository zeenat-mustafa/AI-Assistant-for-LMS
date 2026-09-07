"use client";

import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";

import { StudentDashboard } from "./student-dashboard";

export default function StudentHomePage() {
  return (
    <RequireAuth role="student">
      <SignedInShell>
        <StudentDashboard />
      </SignedInShell>
    </RequireAuth>
  );
}
