"use client";

/**
 * Lecture files for one session (Phase 7.1).
 *
 * Used on both the instructor page (canUpload=true) and student page
 * (read-only). No delete control — no delete endpoint exists.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError, downloadLecture, listLectures, uploadLecture } from "@/lib/api";
import type { LectureFileRead } from "@/lib/api";
import {
  EmptyState,
  FormError,
  FormNotice,
  Loading,
  SmallButton,
  SubmitButton,
} from "@/components/ui";
import { formatDate } from "@/lib/format";
import { triggerBlobDownload } from "@/lib/download";

/** The backend's own LEGACY_PPT_ERROR (lecture_extraction.py), verbatim. */
export const LEGACY_PPT_MESSAGE =
  "Legacy .ppt format is not supported — please save as .pptx and re-upload.";

/** Shown after a 409 — there is no delete endpoint, so the app cannot remove the existing file. */
export const LECTURE_RENAME_HINT =
  "Lecture files can't be removed in the app, so rename your file and upload it again.";

export function checkLectureFilename(filename: string): string | null {
  const dot = filename.lastIndexOf(".");
  const ext = dot === -1 ? "" : filename.slice(dot).toLowerCase();
  if (ext === ".pptx") return null;
  if (ext === ".ppt") return LEGACY_PPT_MESSAGE;
  return `Only .pptx lecture files are accepted — '${filename}' is not a .pptx file.`;
}

export function LectureFilesPanel({
  sessionId,
  canUpload,
}: {
  sessionId: number;
  canUpload: boolean;
}) {
  const [lectures, setLectures] = useState<LectureFileRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listLectures(sessionId).then(
      (rows) => {
        if (cancelled) return;
        setLectures(rows);
        setLoadError(null);
      },
      (error: unknown) => {
        if (cancelled) return;
        setLoadError(
          error instanceof ApiError ? error.detail : "Could not load lecture files.",
        );
      },
    );
    return () => { cancelled = true; };
  }, [sessionId]);

  return (
    <div className="lms-card">
      <h2 className="text-base font-semibold text-neutral-900">Lecture files</h2>
      <p className="mt-1 mb-4 text-sm text-neutral-500">
        {canUpload
          ? "Upload .pptx lecture slides. Slide text and speaker notes become searchable by the student assistant."
          : "Lecture slides uploaded for this session."}
      </p>

      {canUpload ? (
        <LectureUploadForm
          sessionId={sessionId}
          onUploaded={(created) => setLectures((current) => [...(current ?? []), created])}
        />
      ) : null}

      <LectureList sessionId={sessionId} lectures={lectures} loadError={loadError} />
    </div>
  );
}

function LectureUploadForm({
  sessionId,
  onUploaded,
}: {
  sessionId: number;
  onUploaded: (created: LectureFileRead) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function handleSelect(event: React.ChangeEvent<HTMLInputElement>) {
    setNotice(null);
    const file = event.target.files?.[0] ?? null;
    setSelected(file);
    setError(file ? checkLectureFilename(file.name) : null);
  }

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (!selected) { setError("Choose a .pptx file first."); return; }
    const typeError = checkLectureFilename(selected.name);
    if (typeError) { setError(typeError); return; }

    setPending(true);
    try {
      const created = await uploadLecture(sessionId, selected);
      onUploaded(created);
      setNotice(`Uploaded ${created.original_filename}.`);
      setSelected(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (uploadError) {
      if (uploadError instanceof ApiError) {
        setError(
          uploadError.status === 409
            ? `${uploadError.detail} ${LECTURE_RENAME_HINT}`
            : uploadError.detail,
        );
      } else {
        setError("Upload failed.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={handleUpload} noValidate className="mb-6">
      {error ? <FormError>{error}</FormError> : null}
      {notice ? <FormNotice>{notice}</FormNotice> : null}

      <label htmlFor="lecture-file" className="mb-1 block text-sm font-medium text-neutral-700">
        Lecture file (.pptx)
      </label>
      <input
        id="lecture-file"
        ref={inputRef}
        type="file"
        name="file"
        accept=".pptx"
        onChange={handleSelect}
        className="mb-4 block w-full text-sm text-neutral-700 file:mr-3 file:rounded file:border file:border-neutral-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-neutral-700"
      />

      <SubmitButton pending={pending} pendingLabel="Uploading...">
        Upload lecture
      </SubmitButton>
    </form>
  );
}

function LectureList({
  sessionId,
  lectures,
  loadError,
}: {
  sessionId: number;
  lectures: LectureFileRead[] | null;
  loadError: string | null;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  async function handleDownload(lecture: LectureFileRead) {
    setDownloadError(null);
    setBusyId(lecture.id);
    try {
      const blob = await downloadLecture(sessionId, lecture.id);
      triggerBlobDownload(blob, lecture.original_filename);
    } catch (error) {
      setDownloadError(
        error instanceof ApiError
          ? error.detail
          : `Could not download ${lecture.original_filename}.`,
      );
    } finally {
      setBusyId(null);
    }
  }

  if (loadError) return <FormError>{loadError}</FormError>;
  if (lectures === null) return <Loading>Loading lecture files...</Loading>;
  if (lectures.length === 0) return <EmptyState>No lecture files uploaded yet</EmptyState>;

  return (
    <>
      {downloadError ? <FormError>{downloadError}</FormError> : null}
      <ul className="divide-y divide-neutral-100">
        {lectures.map((lecture) => (
          <li key={lecture.id} className="py-3">
            <div className="flex items-center justify-between gap-4">
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-neutral-900">
                  {lecture.original_filename}
                </span>
                <span className="block text-xs text-neutral-400">
                  Uploaded {formatDate(lecture.uploaded_at)}
                  {lecture.content_type ? ` - ${lecture.content_type}` : ""}
                </span>
              </span>
              <SmallButton
                onClick={() => void handleDownload(lecture)}
                disabled={busyId === lecture.id}
              >
                {busyId === lecture.id ? "Downloading..." : "Download"}
              </SmallButton>
            </div>

            {!lecture.extracted ? (
              <div className="mt-2 lms-alert lms-alert-warning text-xs">
                <p>
                  This file was stored, but its content could not be extracted, so it is
                  not searchable by the course assistant.
                </p>
                {lecture.extraction_error ? (
                  <p className="mt-1">{lecture.extraction_error}</p>
                ) : null}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  );
}
