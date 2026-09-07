"use client";

/**
 * Submission upload (5.6, Step 1).
 *
 * The backend REPLACES a previous submission for the same (session, student):
 * it deletes the old Submission and its SubmissionFile rows, and Grade rows
 * cascade away with them. There is no versioning and no undo.
 *
 * What this component does about that is a UX guardrail and nothing more.
 * The endpoint is unchanged and still replaces-and-deletes for any caller —
 * Swagger, curl, or any future client — so nothing here protects the data;
 * it only makes the cost visible and asks for consent before this UI does it.
 *
 * Three states, decided from the per-file `graded` flag on the student's own
 * submission (never from combined_score, which is 0.0 for both a genuine zero
 * and an ungraded submitter):
 *
 *   nothing submitted        -> upload freely, no notice
 *   submitted, none graded   -> mild inline notice, no confirm step
 *   submitted, >=1 graded    -> explicit confirm naming the real score(s),
 *                               which blocks the upload call until accepted
 */

import { useRef, useState } from "react";

import { ApiError, uploadSubmission } from "@/lib/api";
import type { GradeSummary, SubmissionRead } from "@/lib/api";
import { FormError, FormNotice, Panel, SmallButton, SubmitButton } from "@/components/ui";

/** Mirrors the backend's own `_ALLOWED_EXTENSIONS` in routers/submissions.py. */
const ACCEPTED_EXTENSIONS = [".ipynb", ".zip"];

/**
 * Build the sentence naming exactly what a re-upload will destroy.
 * Exported for tests — the specific score has to appear, not a generic
 * "are you sure?".
 */
export function describeGradeLoss(grades: GradeSummary | undefined): string {
  const files = grades?.per_file ?? [];
  if (files.length === 0) {
    // Reachable only if the grade fetch failed while `graded` flags say
    // otherwise. Say so plainly rather than inventing a score.
    return "You already have at least one grade for this session, but its score could not be loaded. Uploading a new file will permanently delete that grade and its feedback.";
  }
  if (files.length === 1) {
    return `You already have a grade of ${files[0].score}/10 for this session. Uploading a new file will permanently delete this grade and its feedback.`;
  }
  const combined = grades?.combined_score;
  const combinedText = combined === null || combined === undefined ? "" : ` (combined ${combined}/10)`;
  return `You already have grades for ${files.length} files in this session${combinedText}. Uploading a new file will permanently delete them and their feedback.`;
}

export function SubmissionUploadPanel({
  sessionId,
  submission,
  grades,
  onUploaded,
}: {
  sessionId: number;
  /** undefined = loading; null = nothing submitted. */
  submission: SubmissionRead | null | undefined;
  grades: GradeSummary | undefined;
  /** Refresh submission status AND grades — the old grades are now gone. */
  onUploaded: (created: SubmissionRead) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const gradedFiles = submission?.files.filter((f) => f.graded) ?? [];
  const hasGradedFiles = gradedFiles.length > 0;
  const hasSubmission = submission != null;
  // Can't name the score yet; don't offer the confirm until we can.
  const awaitingGradeDetail = hasGradedFiles && grades === undefined;

  function handleSelect(event: React.ChangeEvent<HTMLInputElement>) {
    setError(null);
    setNotice(null);
    setConfirming(false);
    setSelected(event.target.files?.[0] ?? null);
  }

  /** Validate, then either upload or hand off to the confirm step. */
  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (!selected) {
      setError("Choose a .ipynb or .zip file first.");
      return;
    }
    const name = selected.name.toLowerCase();
    if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
      setError(`Only .ipynb or .zip files are accepted. Got: ${selected.name}`);
      return;
    }

    if (hasGradedFiles) {
      // Stop here. The upload call is not made until the student accepts.
      setConfirming(true);
      return;
    }
    void doUpload(selected);
  }

  async function doUpload(file: File) {
    setPending(true);
    setConfirming(false);
    try {
      const created = await uploadSubmission(sessionId, file);
      onUploaded(created);
      setNotice(`Uploaded ${created.original_filename}.`);
      setSelected(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (uploadError) {
      setError(
        uploadError instanceof ApiError
          ? uploadError.detail
          : "Upload failed. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <Panel
      title={hasSubmission ? "Replace your submission" : "Upload your submission"}
      description="One solved .ipynb notebook, or a .zip containing your notebooks."
    >
      {error ? <FormError>{error}</FormError> : null}
      {notice ? <FormNotice>{notice}</FormNotice> : null}

      {/* Mild notice: a replacement is coming, but no grades are at stake. */}
      {hasSubmission && !hasGradedFiles ? (
        <p className="mb-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
          Uploading again will replace your current submission.
        </p>
      ) : null}

      <form onSubmit={handleSubmit} noValidate>
        <label
          htmlFor="submission-file"
          className="mb-1 block text-sm font-medium text-slate-700"
        >
          Your solved file
        </label>
        <input
          id="submission-file"
          ref={inputRef}
          type="file"
          name="file"
          accept=".ipynb,.zip"
          onChange={handleSelect}
          className="mb-4 block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700"
        />

        {confirming ? (
          <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 px-3 py-3">
            <p className="text-sm text-amber-900">{describeGradeLoss(grades)}</p>
            <p className="mt-2 text-sm font-medium text-amber-900">Continue?</p>
            <div className="mt-3 flex items-center gap-2">
              <SmallButton
                tone="danger"
                onClick={() => selected && void doUpload(selected)}
                disabled={pending}
              >
                Replace and delete my grade
              </SmallButton>
              <SmallButton onClick={() => setConfirming(false)} disabled={pending}>
                Cancel
              </SmallButton>
            </div>
          </div>
        ) : null}

        <SubmitButton
          pending={pending || awaitingGradeDetail}
          pendingLabel={pending ? "Uploading…" : "Checking your existing grade…"}
        >
          {hasSubmission ? "Replace submission" : "Upload"}
        </SubmitButton>
      </form>
    </Panel>
  );
}
