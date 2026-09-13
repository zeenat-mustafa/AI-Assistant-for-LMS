"use client";

import { RequireAuth } from "@/components/require-auth";
import { SignedInShell } from "@/components/signed-in-shell";

import { QuizHistory } from "./quiz-history";

export default function StudentQuizHistoryPage() {
  return (
    <RequireAuth role="student">
      <SignedInShell>
        <QuizHistory />
      </SignedInShell>
    </RequireAuth>
  );
}
