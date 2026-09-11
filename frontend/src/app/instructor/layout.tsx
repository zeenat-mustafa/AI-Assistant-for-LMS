"use client";

/**
 * Shared instructor layout -- mounts the floating grading-chat widget once
 * for every page under /instructor (the dashboard and every session detail
 * page), so it persists across navigation between them instead of
 * remounting per page. Next's App Router does this naturally: a layout
 * doesn't re-render on navigation within its own segment, only `children`
 * does, so `FloatingChatWidget` here stays mounted (and its open/closed
 * state stays put) while the instructor moves between instructor pages.
 *
 * Not an auth boundary -- see require-auth.tsx for why redirect checks stay
 * per-page. FloatingChatWidget itself checks the signed-in user's role via
 * useAuth() and renders nothing unless they're an authenticated instructor.
 *
 * GradingAnnouncementProvider sits at this same level (mirroring how
 * AuthProvider sits at the root layout) so the widget can announce "a
 * grading run just completed" without knowing who, if anyone, is listening
 * -- see lib/grading-announcements.tsx.
 */

import type { ReactNode } from "react";

import { FloatingChatWidget } from "@/components/floating-chat-widget";
import { GradingAnnouncementProvider } from "@/lib/grading-announcements";

export default function InstructorLayout({ children }: { children: ReactNode }) {
  return (
    <GradingAnnouncementProvider>
      {children}
      <FloatingChatWidget />
    </GradingAnnouncementProvider>
  );
}
