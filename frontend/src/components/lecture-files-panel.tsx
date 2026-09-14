"use client";



/**

 * Lecture files for one session (Phase 7.7, surfacing 7.1).

 *

 * A structurally separate area from Assignment files: a file is a lecture

 * because it was uploaded HERE, never because of its name. Used on both the

 * instructor page (`canUpload`) and the student page (read-only).

 *

 * What is deliberately absent, because the backend does not provide it:

 *  - no delete control (there is no delete endpoint);

 *  - no size / slide count / chunk count / "embedded" state (`LectureFileRead`

 *    carries none of them);

 *  - no "processing" state (extraction runs inside the upload request).

 */



import { useEffect, useRef, useState } from "react";



import { ApiError, downloadLecture, listLectures, uploadLecture } from "@/lib/api";

import type { LectureFileRead } from "@/lib/api";

import {

  EmptyState,

  FormError,

  FormNotice,

  Loading,

  Panel,

  SmallButton,

  SubmitButton,

} from "@/components/ui";

import { formatDate } from "@/lib/format";

import { triggerBlobDownload } from "@/lib/download";



/** The backend's own LEGACY_PPT_ERROR (lecture_extraction.py), verbatim. */

export const LEGACY_PPT_MESSAGE =

  "Legacy .ppt format is not supported — please save as .pptx and re-upload.";



/** Shown after a 409 -- there is no delete endpoint, so the app cannot remove the existing file. */

export const LECTURE_RENAME_HINT =

  "Lecture files can't be removed in the app, so rename your file and upload it again.";



/**

 * Client-side extension check (convenience only -- the backend enforces it

 * too, and its message wins if it ever disagrees). Returns an error or null.

 */

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

    return () => {

      cancelled = true;

    };

  }, [sessionId]);



  return (

    <Panel

      title="Lecture files"

      description={

        canUpload

          ? "Upload one .pptx lecture at a time. Its slide text and speaker notes become searchable by the student course assistant. Lecture files are kept separate from assignment files and are never graded."

          : "Lecture slides your instructor uploaded for this session."

      }

    >

      {canUpload ? (

        <LectureUploadForm

          sessionId={sessionId}

          onUploaded={(created) => setLectures((current) => [...(current ?? []), created])}

        />

      ) : null}



      <LectureList sessionId={sessionId} lectures={lectures} loadError={loadError} />

    </Panel>

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



    if (!selected) {

      setError("Choose a .pptx lecture file first.");

      return;

    }

    const typeError = checkLectureFilename(selected.name);

    if (typeError) {

      setError(typeError);

      return;

    }



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



      <label htmlFor="lecture-file" className="mb-1 block text-sm font-medium text-slate-700">

        Lecture file (.pptx)

      </label>

      <input

        id="lecture-file"

        ref={inputRef}

        type="file"

        name="file"

        accept=".pptx"

        onChange={handleSelect}

        className="mb-4 block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700"

      />



      <SubmitButton pending={pending} pendingLabel="Uploading and extracting...">

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

      <ul className="divide-y divide-slate-200">

        {lectures.map((lecture) => (

          <li key={lecture.id} className="py-3">

            <div className="flex items-center justify-between gap-4">

              <span className="min-w-0">

                <span className="block truncate text-sm font-medium text-slate-900">

                  {lecture.original_filename}

                </span>

                <span className="block text-xs text-slate-500">

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

              <div className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">

                <p>

                  This file was stored, but its content could not be extracted, so it is not

                  searchable by the course assistant.

                </p>

                {lecture.extraction_error ? (

                  <p className="mt-1 text-xs">{lecture.extraction_error}</p>

                ) : null}

              </div>

            ) : null}

          </li>

        ))}

      </ul>

    </>

  );

}

