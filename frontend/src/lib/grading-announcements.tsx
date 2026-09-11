"use client";

/**
 * Cross-component "a grading run just completed" announcement.
 *
 * Bridges the floating chat widget (mounted once, page-agnostic -- see
 * components/floating-chat-widget.tsx) and whichever session-detail page
 * happens to be mounted, so that page can refetch its own roster after a
 * same-tab, self-triggered grading run that named it -- restoring the one
 * behaviour that was lost when the old embedded per-session panel (which had
 * a direct onGraded callback) was replaced by the widget.
 *
 * The widget only ever calls announceGradingCompletion with the raw summary
 * message; it has no idea whether anything is listening, or what for. This
 * keeps the widget exactly as page-agnostic as the locked decision requires.
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

export interface GradingCompletion {
  /** The conversational summary message from POST /chat/stream's `summary` event. */
  message: string;
  /** Monotonic, so effects can react even if the same message text repeats. */
  id: number;
}

export interface GradingAnnouncementContextValue {
  lastCompletion: GradingCompletion | null;
  announceGradingCompletion: (message: string) => void;
}

const noop: GradingAnnouncementContextValue = {
  lastCompletion: null,
  announceGradingCompletion: () => {},
};

/**
 * Default value (rather than null + a throwing hook, as useAuth does) so
 * components using this hook keep working standalone in tests/pages that
 * don't mount <GradingAnnouncementProvider> -- they simply never get notified.
 */
const GradingAnnouncementContext = createContext<GradingAnnouncementContextValue>(noop);

export function GradingAnnouncementProvider({ children }: { children: ReactNode }) {
  const [lastCompletion, setLastCompletion] = useState<GradingCompletion | null>(null);
  const nextId = useRef(1);

  const announceGradingCompletion = useCallback((message: string) => {
    setLastCompletion({ message, id: nextId.current++ });
  }, []);

  const value = useMemo(
    () => ({ lastCompletion, announceGradingCompletion }),
    [lastCompletion, announceGradingCompletion],
  );

  return (
    <GradingAnnouncementContext.Provider value={value}>
      {children}
    </GradingAnnouncementContext.Provider>
  );
}

export function useGradingAnnouncements(): GradingAnnouncementContextValue {
  return useContext(GradingAnnouncementContext);
}

/**
 * Did this summary's conversational message name the given session?
 *
 * Carried over unchanged from the old embedded grading-chat panel (removed
 * in 61e4a4d). The summary event carries no session_id, only the 3.5
 * conversational message, which interpolates the session title verbatim
 * ("...in {session_title}."). If that wording ever changes this fails
 * toward "no match" -- never toward silently claiming a refresh happened.
 */
export function summaryNamesSession(message: string | undefined, sessionTitle: string): boolean {
  if (!message) return false;
  return message.includes(`in ${sessionTitle}`);
}
