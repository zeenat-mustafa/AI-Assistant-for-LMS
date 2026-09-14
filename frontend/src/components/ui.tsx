/**
 * Shared UI primitives — Phase 7.8 design system.
 *
 * All visual tokens come from globals.css via .lms-* component classes
 * and Tailwind utilities mapped to CSS custom properties.
 * Component APIs (props) are unchanged from 7.7.
 */

import type { ReactNode } from "react";

// ── Auth / page-level shell ───────────────────────────────────────────────────

export function AuthCard({ title, subtitle, children }: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-page-bg p-6">
      <div className="lms-card w-full max-w-sm">
        <h1 className="text-xl font-semibold text-neutral-900">{title}</h1>
        {subtitle ? <p className="mt-1 text-sm text-neutral-500">{subtitle}</p> : null}
        <div className="mt-6">{children}</div>
      </div>
    </main>
  );
}

// ── Form primitives ───────────────────────────────────────────────────────────

export function Field({ label, error, ...props }: {
  label: string;
  error?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const { id, ...rest } = props;
  const inputId = id ?? `field-${label.toLowerCase().replace(/\s+/g, "-")}`;
  const errorId = `${inputId}-error`;
  return (
    <div className="mb-4">
      <label htmlFor={inputId} className="mb-1 block text-sm font-medium text-neutral-700">
        {label}
      </label>
      <input
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        className="lms-input"
        {...rest}
      />
      {error ? (
        <p id={errorId} className="mt-1 text-xs text-danger-600">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Form-level failure (e.g. a rejected login). Announced to screen readers. */
export function FormError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="lms-alert lms-alert-error mb-4">
      {children}
    </p>
  );
}

export function FormNotice({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="lms-alert lms-alert-success mb-4">
      {children}
    </p>
  );
}

// ── Layout containers ─────────────────────────────────────────────────────────

/** Section container used across instructor and student pages. */
export function Panel({ title, description, children }: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">{title}</h2>
      {description ? <p className="mt-1 text-sm text-neutral-500">{description}</p> : null}
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** Shown in place of a list that has loaded but has nothing in it. */
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-neutral-300 px-4 py-6 text-center text-sm text-neutral-500">
      {children}
    </p>
  );
}

export function Loading({ children = "Loading..." }: { children?: ReactNode }) {
  return (
    <p role="status" className="py-4 text-sm text-neutral-500">
      {children}
    </p>
  );
}

// ── Buttons ───────────────────────────────────────────────────────────────────

/** Small neutral/secondary button (download, cancel, secondary actions). */
export function SmallButton({
  tone = "neutral",
  ...props
}: { tone?: "neutral" | "danger" } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const toneClass =
    tone === "danger"
      ? "border-danger-200 text-danger-700 hover:bg-danger-50"
      : "border-neutral-300 text-neutral-700 hover:bg-neutral-100";
  return (
    <button
      type="button"
      className={`inline-flex items-center rounded border px-2.5 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${toneClass}`}
      {...props}
    />
  );
}

export function SubmitButton({
  pending,
  pendingLabel = "Please wait...",
  children,
}: {
  pending: boolean;
  pendingLabel?: ReactNode;
  children: ReactNode;
}) {
  return (
    <button
      type="submit"
      disabled={pending}
      className="lms-btn-primary w-full"
    >
      {pending ? pendingLabel : children}
    </button>
  );
}

// ── Badges ────────────────────────────────────────────────────────────────────

/**
 * Shows whether an assignment file is gradeable or a resource.
 * Role is passed by the caller from which table the row came from —
 * never inferred from the row itself.
 */
export function FileRoleBadge({ role }: { role: "notebook" | "resource" }) {
  return (
    <span className={role === "notebook" ? "lms-badge lms-badge-success" : "lms-badge lms-badge-neutral"}>
      {role === "notebook" ? "Gradeable notebook" : "Resource - not graded"}
    </span>
  );
}
