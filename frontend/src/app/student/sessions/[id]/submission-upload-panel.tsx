"use client";

/**
 * Submission upload (submission-side rework).
 *
 * Uploads are ADDITIVE: each call adds new SubmissionUpload row(s) alongside
 * whatever the student already submitted for this session -- nothing is ever
 * replaced or destroyed by uploading. Any file type is accepted, single or
 * inside a `.zip` with no requirement that it contain a notebook. Because
 * upload can no longer destroy a grade, there is no warn-and-confirm step
 * here any more -- that concern now lives entirely on delete (see
 * `student-session-detail.tsx`'s uploads list), which the backend itself
 * enforces with a real 409 + confirm, not just a client-side guardrail.
 */

import { useRef, useState } from "react";

import { ApiError, uploadSubmission } from "@/lib/api";
import type { SubmissionRead } from "@/lib/api";
import { FormError, FormNotice, SubmitButton } from "@/components/ui";

/** Summarise an upload response -- one entry per file uploaded, whatever it was. */
export function describeUpload(files: File[]): string {
  if (files.length === 1) return `Uploaded ${files[0].name}.`;
  return `Uploaded ${files.length} files: ${files.map((f) => f.name).join(", ")}.`;
}

export function SubmissionUploadPanel({
  sessionId,
  submission,
  onUploaded,
}: {
  sessionId: number;
  /** undefined = loading; null = nothing submitted yet. */
  submission: SubmissionRead | null | undefined;
  onUploaded: (updated: SubmissionRead) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const hasSubmission = submission != null && submission.uploads.length > 0;

  function handleSelect(event: React.ChangeEvent<HTMLInputElement>) {
    setError(null);
    setNotice(null);
    setSelected(Array.from(event.target.files ?? []));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (selected.length === 0) {
      setError("Choose at least one file first.");
      return;
    }

    setPending(true);
    try {
      const updated = await uploadSubmission(sessionId, selected);
      onUploaded(updated);
      setNotice(describeUpload(selected));
      setSelected([]);
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
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">
        {hasSubmission ? "Add another file" : "Upload your submission"}
      </h2>

      {error ? <FormError>{error}</FormError> : null}
      {notice ? <FormNotice>{notice}</FormNotice> : null}

      <form onSubmit={handleSubmit} noValidate>
        <label
          htmlFor="submission-files"
          className="mb-1 block text-sm font-medium text-neutral-700"
        >
          Your solved file(s)
        </label>
        <input
          id="submission-files"
          ref={inputRef}
          type="file"
          name="files"
          multiple
          onChange={handleSelect}
          className="sr-only"
        />
        <div className="mb-4 flex items-center gap-3">
          <label
            htmlFor="submission-files"
            className="lms-btn-secondary cursor-pointer text-sm"
          >
            Choose file(s)
          </label>
          {selected.length > 0 ? (
            <span className="text-sm text-neutral-600">
              {selected.length === 1 ? selected[0].name : `${selected.length} files selected`}
            </span>
          ) : (
            <span className="text-sm text-neutral-400">No file chosen</span>
          )}
        </div>

        {selected.length > 0 ? (
          <ul className="mb-4 list-inside list-disc text-xs text-neutral-600">
            {selected.map((file) => (
              <li key={file.name}>{file.name}</li>
            ))}
          </ul>
        ) : null}

        <SubmitButton pending={pending} pendingLabel="Uploading…">
          {selected.length > 1 ? `Upload ${selected.length} files` : "Upload"}
        </SubmitButton>
      </form>
    </div>
  );
}
