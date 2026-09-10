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
 */

import type { ReactNode } from "react";

import { FloatingChatWidget } from "@/components/floating-chat-widget";

export default function InstructorLayout({ children }: { children: ReactNode }) {
  return (
    <>
      {children}
      <FloatingChatWidget />
    </>
  );
}
