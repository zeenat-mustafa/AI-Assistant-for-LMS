"use client";

/**
 * Shared student layout -- mounts the floating student course-assistant widget
 * once for every page under /student, so it persists across navigation between
 * the student dashboard, session detail pages, and the quiz history page
 * without remounting per page.
 *
 * Mirrors the instructor layout (app/instructor/layout.tsx) exactly:
 *  - Not an auth boundary -- auth checks stay per-page in require-auth.tsx.
 *  - StudentChatWidget renders nothing unless the signed-in user is a student.
 */

import type { ReactNode } from "react";

import { StudentChatWidget } from "@/components/student-chat-widget";

export default function StudentLayout({ children }: { children: ReactNode }) {
  return (
    <>
      {children}
      <StudentChatWidget />
    </>
  );
}
