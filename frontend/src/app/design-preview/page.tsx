/**
 * Design system preview — Phase 7.8
 * Route: /design-preview
 *
 * Static page (no auth, no API calls) that renders every token and
 * primitive so the design system can be verified in the browser.
 * Delete this file once Phase 7.8 is complete.
 */

export default function DesignPreviewPage() {
  return (
    <div className="min-h-screen bg-page-bg p-8 font-sans">
      <div className="mx-auto max-w-3xl space-y-10">

        {/* Header */}
        <header>
          <h1 className="text-3xl font-bold text-neutral-900">
            LMS Design System
          </h1>
          <p className="mt-1 text-sm text-neutral-500">
            Phase 7.8 — visual token reference. Not a real page.
          </p>
        </header>

        {/* ── Color palette ── */}
        <section className="lms-card space-y-4">
          <h2 className="text-lg font-semibold text-neutral-900">Color palette</h2>

          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-400">Primary</p>
            <div className="flex gap-2 flex-wrap">
              {[
                ["bg-primary-50",  "50"],
                ["bg-primary-100", "100"],
                ["bg-primary-200", "200"],
                ["bg-primary-500", "500"],
                ["bg-primary-600", "600"],
                ["bg-primary-700", "700"],
                ["bg-primary-800", "800"],
              ].map(([cls, label]) => (
                <div key={cls} className="flex flex-col items-center gap-1">
                  <div className={`h-10 w-16 rounded ${cls} border border-neutral-200`} />
                  <span className="text-[11px] text-neutral-500">{label}</span>
                </div>
              ))}
            </div>
          </div>

          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-400">Neutral</p>
            <div className="flex gap-2 flex-wrap">
              {[
                ["bg-neutral-50",  "50"],
                ["bg-neutral-100", "100"],
                ["bg-neutral-200", "200"],
                ["bg-neutral-300", "300"],
                ["bg-neutral-400", "400"],
                ["bg-neutral-500", "500"],
                ["bg-neutral-600", "600"],
                ["bg-neutral-700", "700"],
                ["bg-neutral-800", "800"],
                ["bg-neutral-900", "900"],
              ].map(([cls, label]) => (
                <div key={cls} className="flex flex-col items-center gap-1">
                  <div className={`h-10 w-12 rounded ${cls} border border-neutral-200`} />
                  <span className="text-[11px] text-neutral-500">{label}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="flex gap-6 flex-wrap">
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-400">Success</p>
              <div className="flex gap-2">
                {[["bg-success-50","50"],["bg-success-200","200"],["bg-success-600","600"],["bg-success-700","700"]].map(([cls,l])=>(
                  <div key={cls} className="flex flex-col items-center gap-1">
                    <div className={`h-10 w-12 rounded ${cls} border border-neutral-200`} />
                    <span className="text-[11px] text-neutral-500">{l}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-400">Danger</p>
              <div className="flex gap-2">
                {[["bg-danger-50","50"],["bg-danger-200","200"],["bg-danger-600","600"],["bg-danger-700","700"]].map(([cls,l])=>(
                  <div key={cls} className="flex flex-col items-center gap-1">
                    <div className={`h-10 w-12 rounded ${cls} border border-neutral-200`} />
                    <span className="text-[11px] text-neutral-500">{l}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ── Typography ── */}
        <section className="lms-card space-y-3">
          <h2 className="text-lg font-semibold text-neutral-900">Typography</h2>
          <p className="text-3xl font-bold text-neutral-900">Page title (text-3xl / 30px)</p>
          <p className="text-2xl font-semibold text-neutral-900">Display heading (text-2xl / 24px)</p>
          <p className="text-xl font-semibold text-neutral-800">Card title (text-xl / 20px)</p>
          <p className="text-lg font-semibold text-neutral-800">Section heading (text-lg / 18px)</p>
          <p className="text-base text-neutral-800">Body text (text-base / 16px) — The quick brown fox jumps over the lazy dog.</p>
          <p className="text-sm text-neutral-700">Small body (text-sm / 14px) — Used for most UI labels and table content.</p>
          <p className="text-xs text-neutral-500">Caption / muted (text-xs / 12px) — Timestamps, helper text, badges.</p>
          <p className="font-mono text-sm text-neutral-700">Monospace (font-mono) — code snippets, IDs.</p>
        </section>

        {/* ── Buttons ── */}
        <section className="lms-card space-y-4">
          <h2 className="text-lg font-semibold text-neutral-900">Buttons</h2>
          <div className="flex flex-wrap gap-3 items-center">
            <button className="lms-btn-primary">Primary action</button>
            <button className="lms-btn-primary" disabled>Primary disabled</button>
            <button className="lms-btn-secondary">Secondary</button>
            <button className="lms-btn-secondary" disabled>Secondary disabled</button>
            <button className="lms-btn-danger">Danger</button>
          </div>
          <div className="flex flex-wrap gap-3 items-center">
            {/* SmallButton equivalents */}
            <button className="inline-flex items-center rounded border border-neutral-300 px-2.5 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-100 transition">
              Small neutral
            </button>
            <button className="inline-flex items-center rounded border border-danger-200 px-2.5 py-1 text-xs font-medium text-danger-700 hover:bg-danger-50 transition">
              Small danger
            </button>
          </div>
        </section>

        {/* ── Form input ── */}
        <section className="lms-card space-y-4">
          <h2 className="text-lg font-semibold text-neutral-900">Form input</h2>
          <div className="max-w-sm space-y-3">
            <div>
              <label className="mb-1 block text-sm font-medium text-neutral-700">Normal</label>
              <input className="lms-input" defaultValue="Some value" />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-neutral-700">Error state</label>
              <input className="lms-input" aria-invalid="true" defaultValue="Bad input" />
              <p className="mt-1 text-xs text-danger-600">This field has an error.</p>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-neutral-700">Disabled</label>
              <input className="lms-input" disabled defaultValue="Cannot edit" />
            </div>
          </div>
        </section>

        {/* ── Badges ── */}
        <section className="lms-card space-y-3">
          <h2 className="text-lg font-semibold text-neutral-900">Badges</h2>
          <div className="flex flex-wrap gap-2 items-center">
            <span className="lms-badge lms-badge-primary">Primary</span>
            <span className="lms-badge lms-badge-success">Success</span>
            <span className="lms-badge lms-badge-neutral">Neutral</span>
            <span className="lms-badge lms-badge-success">Gradeable notebook</span>
            <span className="lms-badge lms-badge-neutral">Resource - not graded</span>
          </div>
        </section>

        {/* ── Alert banners ── */}
        <section className="lms-card space-y-3">
          <h2 className="text-lg font-semibold text-neutral-900">Alert banners</h2>
          <p className="lms-alert lms-alert-error">Error: something went wrong. Please try again.</p>
          <p className="lms-alert lms-alert-success">Success: your changes have been saved.</p>
          <p className="lms-alert lms-alert-warning">Warning: this action cannot be undone.</p>
          <p className="lms-alert lms-alert-info">Info: the session has no assignment files yet.</p>
        </section>

        {/* ── Card / Panel ── */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-neutral-900">Cards</h2>
          <div className="lms-card">
            <h3 className="text-base font-semibold text-neutral-900">Panel title</h3>
            <p className="mt-1 text-sm text-neutral-500">Panel description — secondary text at text-sm, muted.</p>
            <div className="mt-4 text-sm text-neutral-700">Panel body content goes here.</div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="lms-card">
              <p className="text-sm font-semibold text-neutral-800">Card A</p>
              <p className="mt-1 text-xs text-neutral-500">Consistent padding and radius.</p>
            </div>
            <div className="lms-card">
              <p className="text-sm font-semibold text-neutral-800">Card B</p>
              <p className="mt-1 text-xs text-neutral-500">Same shadow and border.</p>
            </div>
          </div>
        </section>

        {/* ── Spacing scale reference ── */}
        <section className="lms-card space-y-2">
          <h2 className="text-lg font-semibold text-neutral-900">Spacing scale</h2>
          <div className="space-y-1 text-xs text-neutral-600 font-mono">
            {[
              ["--space-1","4px"],["--space-2","8px"],["--space-3","12px"],
              ["--space-4","16px"],["--space-5","20px"],["--space-6","24px"],
              ["--space-8","32px"],["--space-10","40px"],["--space-12","48px"],
            ].map(([v,px])=>(
              <div key={v} className="flex items-center gap-3">
                <span className="w-24 text-neutral-400">{v}</span>
                <span className="w-12">{px}</span>
                <div className="h-3 bg-primary-200 rounded" style={{width:px}} />
              </div>
            ))}
          </div>
        </section>

        <footer className="pb-8 text-xs text-neutral-400 text-center">
          /design-preview — delete this page after Phase 7.8 is complete
        </footer>
      </div>
    </div>
  );
}
