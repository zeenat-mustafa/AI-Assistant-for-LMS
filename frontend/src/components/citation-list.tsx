"use client";



/**

 * Citations under a student-chat answer (Phase 7.7, surfacing 7.2/7.5).

 *

 * Renders only what the backend really sent:

 *  - a null field renders nothing -- never "undefined", "N/A", or a guess;

 *  - `cell_index` is shown raw (0-indexed) with a legend, never +1'd, so it

 *    can be cross-checked against the notebook JSON;

 *  - `snippet` is the backend's 200-character preview, shown as exactly that;

 *  - `similarity` is omitted: it is a raw retrieval score with no scale the

 *    UI could state without inventing one.

 *

 * These are the chunks GIVEN to the model, so the heading says so rather than

 * claiming the answer used every one of them.

 */



import { useState } from "react";



import { ApiError, downloadLecture } from "@/lib/api";

import type { Citation } from "@/lib/api";

import { triggerBlobDownload } from "@/lib/download";



/** `ChunkSource` values -- which part of a slide a lecture chunk came from. */

const SOURCE_LABELS: Record<string, string> = {

  slide_text: "slide text",

  notes: "speaker notes",

};



export function CitationList({ citations }: { citations: Citation[] }) {

  if (citations.length === 0) return null;



  return (

    <div className="mt-2 space-y-1" data-testid="citation-list">

      <ul className="space-y-1">

        {citations.map((citation, index) => (

          <CitationEntry key={index} citation={citation} />

        ))}

      </ul>

    </div>

  );

}



function locatorParts(citation: Citation): string[] {

  const parts: string[] = [];

  if (citation.source_type === "lecture") {

    if (citation.slide_number !== null && citation.slide_number !== undefined) {

      parts.push(`Slide ${citation.slide_number}`);

    }

  } else if (citation.source_type === "notebook") {

    if (citation.cell_index !== null && citation.cell_index !== undefined) {

      parts.push(`Cell ${citation.cell_index}`);

    }

  }

  return parts;

}



function CitationEntry({ citation }: { citation: Citation }) {

  const [expanded, setExpanded] = useState(false);

  const [downloading, setDownloading] = useState(false);

  const [error, setError] = useState<string | null>(null);



  const locator = locatorParts(citation).join(" | ");

  const canDownload =

    citation.source_type === "lecture" &&

    citation.session_id !== null &&

    citation.source_file_id !== null &&

    Boolean(citation.filename);



  async function handleDownload() {

    if (!canDownload) return;

    setError(null);

    setDownloading(true);

    try {

      const blob = await downloadLecture(citation.session_id!, citation.source_file_id!);

      triggerBlobDownload(blob, citation.filename!);

    } catch (downloadError) {

      setError(

        downloadError instanceof ApiError ? downloadError.detail : "Could not download this lecture.",

      );

    } finally {

      setDownloading(false);

    }

  }



  return (

    <li className="text-[11px] text-slate-500">

      <span className="flex flex-wrap items-center gap-x-2">

        {citation.filename ? <span className="font-medium text-slate-600">{citation.filename}</span> : null}

        {locator ? <span className="text-slate-400">{locator}</span> : null}

        {citation.snippet ? (

          <button

            type="button"

            onClick={() => setExpanded((v) => !v)}

            className="text-slate-500 underline"

            aria-expanded={expanded}

          >

            {expanded ? "Hide preview" : "Show preview"}

          </button>

        ) : null}

        {canDownload ? (

          <button

            type="button"

            onClick={() => void handleDownload()}

            disabled={downloading}

            className="text-slate-500 underline disabled:opacity-50"

          >

            {downloading ? "Downloading..." : "Download lecture"}

          </button>

        ) : null}

      </span>

      {expanded ? (

        <span className="mt-1 block rounded border border-slate-200 bg-white px-2 py-1">

          <span className="block whitespace-pre-wrap text-slate-700">{citation.snippet}</span>

          <span className="mt-1 block text-[11px] text-slate-400">

            Truncated preview — only the first 200 characters are available.

          </span>

        </span>

      ) : null}

      {error ? (

        <span role="alert" className="block text-red-600">

          {error}

        </span>

      ) : null}

    </li>

  );

}



/**

 * A quiz question's `source_citation`: a display string the backend builds

 * itself (with 1-indexed cells). Rendered verbatim in the same style -- it has

 * no structured fields to split, so none are invented.

 */

export function QuizSourceLine({ text }: { text: string }) {

  if (!text) return null;

  return <p className="mt-1 text-xs text-slate-500">Source: {text}</p>;

}

