"use client";

import { RequireAuth } from "@/components/require-auth";
import { PlaceholderPanel, SignedInShell } from "@/components/signed-in-shell";

/** Placeholder student home. Real content lands in 5.3-5.6. */
export default function StudentHomePage() {
  return (
    <RequireAuth role="student">
      <SignedInShell>
        <PlaceholderPanel
          heading="Student home"
          upcoming={[
            "Browse sessions and download assignment files (5.4)",
            "Upload a solved notebook or zip (5.4)",
            "See your grades and per-criterion feedback (5.6)",
          ]}
        />
      </SignedInShell>
    </RequireAuth>
  );
}
