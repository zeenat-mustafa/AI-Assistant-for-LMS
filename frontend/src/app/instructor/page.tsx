"use client";

import { RequireAuth } from "@/components/require-auth";
import { PlaceholderPanel, SignedInShell } from "@/components/signed-in-shell";

/** Placeholder instructor home. Real content lands in 5.3-5.6. */
export default function InstructorHomePage() {
  return (
    <RequireAuth role="instructor">
      <SignedInShell>
        <PlaceholderPanel
          heading="Instructor home"
          upcoming={[
            "Create sessions and upload assignment files (5.3)",
            "Review submissions per session (5.4)",
            "Grading chat, with live progress over /chat/stream (5.5)",
            "Session grade report (5.6)",
          ]}
        />
      </SignedInShell>
    </RequireAuth>
  );
}
