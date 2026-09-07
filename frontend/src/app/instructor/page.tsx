"use client";

import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";

import { InstructorDashboard } from "./instructor-dashboard";

export default function InstructorHomePage() {
  return (
    <RequireAuth role="instructor">
      <SignedInShell>
        <InstructorDashboard />
      </SignedInShell>
    </RequireAuth>
  );
}
