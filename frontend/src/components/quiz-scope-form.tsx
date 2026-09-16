"use client";

/**
 * Starts a practice quiz -- the ONLY way one starts (no intent detection on
 * chat text). All five real scope modes; each sends exactly one scope field.
 */

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  generateQuiz,
  generateQuizFromUpload,
  listSessions,
} from "@/lib/api";
import type { QuizAttemptOut, QuizGenerateRequest, SessionRead } from "@/lib/api";
import { EmptyState, FormError, Loading, SmallButton } from "@/components/ui";
import { LEGACY_PPT_MESSAGE } from "@/components/lecture-files-panel";

type Mode = "assignment_file" | "session" | "multiple_sessions" | "topic" | "upload";

const MODES: { value: Mode; label: string }[] = [
  { value: "assignment_file", label: "One assignment file" },
  { value: "session", label: "This whole session" },
  { value: "multiple_sessions", label: "Several sessions" },
  { value: "topic", label: "A topic" },
  { value: "upload", label: "A file I upload now" },
];

/** Client-side convenience check; the backend enforces the same rule. */
export function checkQuizUploadFilename(filename: string): string | null {
  const dot = filename.lastIndexOf(".");
  const ext = dot === -1 ? "" : filename.slice(dot).toLowerCase();
  if (ext === ".pptx" || ext === ".ipynb") return null;
  if (ext === ".ppt") return LEGACY_PPT_MESSAGE;
  return `Only .pptx lecture files and .ipynb notebooks can be used for a quiz — '${filename}' is neither.`;
}

export function QuizScopeForm({
  session,
  onGenerated,
  onCancel,
}: {
  session: SessionRead;
  onGenerated: (attempt: QuizAttemptOut) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = useState<Mode>("session");
  const [fileId, setFileId] = useState<number | null>(session.unsolved_files[0]?.id ?? null);
  const [topic, setTopic] = useState("");
  const [chosenSessions, setChosenSessions] = useState<number[]>([]);
  const [allSessions, setAllSessions] = useState<SessionRead[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [upload, setUpload] = useState<File | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The session list is only needed for the multi-session mode.
  useEffect(() => {
    if (mode !== "multiple_sessions" || allSessions !== null) return;
    let cancelled = false;
    listSessions({ limit: 200 }).then(
      (page) => {
        if (!cancelled) setAllSessions(page.items);
      },
      (loadError: unknown) => {
        if (cancelled) return;
        setSessionsError(
          loadError instanceof ApiError ? loadError.detail : "Could not load sessions.",
        );
        setAllSessions([]);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [mode, allSessions]);

  function readyToGenerate(): boolean {
    switch (mode) {
      case "assignment_file":
        return fileId !== null;
      case "session":
        return true;
      case "multiple_sessions":
        return chosenSessions.length >= 2;
      case "topic":
        return topic.trim().length > 0;
      case "upload":
        return upload !== null && checkQuizUploadFilename(upload.name) === null;
    }
  }

  async function handleGenerate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !readyToGenerate()) return;
    setError(null);
    setPending(true);
    try {
      let attempt: QuizAttemptOut;
      if (mode === "upload") {
        attempt = await generateQuizFromUpload(upload!);
      } else {
        let payload: QuizGenerateRequest;
        if (mode === "assignment_file") {
          payload = { scope_type: "assignment_file", unsolved_file_id: fileId! };
        } else if (mode === "session") {
          payload = { scope_type: "session", session_id: session.id };
        } else if (mode === "multiple_sessions") {
          payload = { scope_type: "multiple_sessions", session_ids: chosenSessions };
        } else {
          payload = { scope_type: "topic", topic_text: topic };
        }
        attempt = await generateQuiz(payload);
      }
      onGenerated(attempt);
    } catch (generateError) {
      setError(
        generateError instanceof ApiError ? generateError.detail : "Could not generate a quiz.",
      );
    } finally {
      setPending(false);
    }
  }

  const uploadError = upload ? checkQuizUploadFilename(upload.name) : null;

  return (
    <form
      onSubmit={handleGenerate}
      noValidate
      className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3"
      aria-label="Start a practice quiz"
    >
      <p className="text-sm font-semibold text-slate-900">Start a practice quiz</p>
      <p className="mb-3 text-xs text-slate-500">
        5 multiple-choice questions. Practice only — it never changes your real grades.
      </p>

      <fieldset disabled={pending} className="space-y-1">
        <legend className="mb-1 text-xs font-medium text-slate-700">Quiz me on</legend>
        {MODES.map((m) => (
          <label key={m.value} className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="radio"
              name="quiz-mode"
              value={m.value}
              checked={mode === m.value}
              onChange={() => {
                setMode(m.value);
                setError(null);
              }}
            />
            {m.label}
          </label>
        ))}
      </fieldset>

      <div className="mt-3">
        {mode === "assignment_file" ? (
          session.unsolved_files.length === 0 ? (
            <EmptyState>This session has no assignment notebooks to quiz on.</EmptyState>
          ) : (
            <>
              <label htmlFor="quiz-file" className="mb-1 block text-xs font-medium text-slate-700">
                Assignment file
              </label>
              <select
                id="quiz-file"
                value={fileId ?? ""}
                onChange={(e) => setFileId(Number(e.target.value))}
                disabled={pending}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900"
              >
                {session.unsolved_files.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.original_filename}
                  </option>
                ))}
              </select>
            </>
          )
        ) : null}

        {mode === "session" ? (
          <p className="text-sm text-slate-700">Questions from all of {session.title}.</p>
        ) : null}

        {mode === "multiple_sessions" ? (
          sessionsError ? (
            <FormError>{sessionsError}</FormError>
          ) : allSessions === null ? (
            <Loading>Loading sessions...</Loading>
          ) : (
            <fieldset disabled={pending}>
              <legend className="mb-1 text-xs font-medium text-slate-700">
                Choose at least 2 sessions
              </legend>
              <div className="max-h-40 space-y-1 overflow-y-auto">
                {allSessions.map((s) => (
                  <label key={s.id} className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={chosenSessions.includes(s.id)}
                      onChange={(e) =>
                        setChosenSessions((current) =>
                          e.target.checked
                            ? [...current, s.id]
                            : current.filter((id) => id !== s.id),
                        )
                      }
                    />
                    {s.title}
                  </label>
                ))}
              </div>
            </fieldset>
          )
        ) : null}

        {mode === "topic" ? (
          <>
            <label htmlFor="quiz-topic" className="mb-1 block text-xs font-medium text-slate-700">
              Topic
            </label>
            <input
              id="quiz-topic"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              disabled={pending}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900"
            />
          </>
        ) : null}

        {mode === "upload" ? (
          <>
            <label htmlFor="quiz-upload" className="mb-1 block text-xs font-medium text-slate-700">
              File (.pptx or .ipynb)
            </label>
            <div className="flex items-center gap-2">
              <input
                id="quiz-upload"
                ref={uploadRef}
                type="file"
                accept=".pptx,.ipynb"
                disabled={pending}
                onChange={(e) => setUpload(e.target.files?.[0] ?? null)}
                className="sr-only"
              />
              <label
                htmlFor="quiz-upload"
                className={`lms-btn-secondary cursor-pointer text-sm${pending ? " opacity-50 cursor-not-allowed pointer-events-none" : ""}`}
              >
                Choose file
              </label>
              {upload ? (
                <span className="truncate text-sm text-slate-600">{upload.name}</span>
              ) : (
                <span className="text-sm text-slate-400">No file chosen</span>
              )}
            </div>
            <p className="mt-1 text-xs text-slate-500">
              This file is used for this quiz only. It is not saved as a submission, an assignment
              file, or a lecture file.
            </p>
            {uploadError ? <p role="alert" className="mt-1 text-xs text-red-600">{uploadError}</p> : null}
          </>
        ) : null}
      </div>

      {error ? (
        <div className="mt-3">
          <FormError>{error}</FormError>
        </div>
      ) : null}

      <div className="mt-3 flex items-center gap-2">
        <button
          type="submit"
          disabled={pending || !readyToGenerate()}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
        >
          {pending ? "Generating quiz..." : "Generate quiz"}
        </button>
        <SmallButton onClick={onCancel} disabled={pending}>
          Cancel
        </SmallButton>
      </div>
    </form>
  );
}
