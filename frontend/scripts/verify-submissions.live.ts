/**
 * LIVE verification of the submission-side rework (Step 7) against a real
 * running backend, using ONLY the documented demo accounts.
 *
 *   cd backend && uvicorn app.main:app --reload --port 8000
 *   cd frontend && npm run verify:api -- scripts/verify-submissions.live.ts
 *
 * Creates its own sessions (titled "Week 97 Day N") so it never touches real
 * seeded student data, and cleans them up (DELETE /sessions) at the end
 * regardless of pass/fail. Real Gemini spend: two grading runs (rubric +
 * evaluation each), matching this project's minimal-real-call convention.
 */

import { createHash } from "node:crypto";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { login } from "@/lib/api/auth";
import { createSession, deleteSession } from "@/lib/api/sessions";
import { uploadAssignment } from "@/lib/api/assignments";
import {
  uploadSubmission,
  getMySubmission,
  listSubmissions,
  downloadSubmissionUpload,
  downloadMySubmissionUpload,
  deleteSubmissionUpload,
  gradeSubmissionFile,
} from "@/lib/api/submissions";
import { postChat } from "@/lib/api/chat";
import { ApiError } from "@/lib/api/client";

const INSTRUCTOR_EMAIL = process.env.VERIFY_INSTRUCTOR_EMAIL ?? "instructor@demo.com";
const INSTRUCTOR_PASSWORD = process.env.VERIFY_INSTRUCTOR_PASSWORD ?? "instructor123";
const STUDENT_EMAIL = process.env.VERIFY_STUDENT_EMAIL ?? "student@demo.com";
const STUDENT_PASSWORD = process.env.VERIFY_STUDENT_PASSWORD ?? "student123";
// A second seeded demo student (backend/app/services/auth.py's
// seed_demo_users), used ONLY to prove cross-student download rejection —
// never a real seeded student's personal credentials.
const STUDENT2_EMAIL = process.env.VERIFY_STUDENT2_EMAIL ?? "student2@demo.com";
const STUDENT2_PASSWORD = process.env.VERIFY_STUDENT2_PASSWORD ?? "student2pass";

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function log(label: string, value: unknown) {
  console.log(`\n--- ${label} ---`);
  console.log(typeof value === "string" ? value : JSON.stringify(value, null, 2));
}

/** A minimal, valid nbformat 4.5 notebook as a File, cheap for Gemini to grade. */
function makeNotebook(name: string, opts: { solved: boolean }): File {
  const nb = {
    cells: [
      {
        cell_type: "markdown",
        id: "md1",
        metadata: {},
        source: [
          "Assignment: define `answer` as the integer 42 in the cell below and run it.",
        ],
      },
      {
        cell_type: "code",
        id: "code1",
        metadata: {},
        execution_count: opts.solved ? 1 : null,
        outputs: opts.solved
          ? [{ output_type: "execute_result", data: { "text/plain": ["42"] }, execution_count: 1, metadata: {} }]
          : [],
        source: opts.solved ? ["answer = 42\nanswer"] : ["# TODO: define answer = 42\n"],
      },
    ],
    metadata: {},
    nbformat: 4,
    nbformat_minor: 5,
  };
  return new File([JSON.stringify(nb)], name, { type: "application/octet-stream" });
}

function makeZip(entries: Record<string, Uint8Array | string>): File {
  // Build a minimal, valid (uncompressed, STORE method) ZIP by hand -- no
  // dependency needed for a couple of small entries.
  const encoder = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  function crc32(buf: Uint8Array): number {
    let crc = ~0;
    for (const byte of buf) {
      crc ^= byte;
      for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
    return ~crc >>> 0;
  }

  function u16(n: number) {
    return new Uint8Array([n & 0xff, (n >> 8) & 0xff]);
  }
  function u32(n: number) {
    return new Uint8Array([n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >> 24) & 0xff]);
  }
  function concat(...arrs: Uint8Array[]): Uint8Array {
    const total = arrs.reduce((n, a) => n + a.length, 0);
    const out = new Uint8Array(total);
    let o = 0;
    for (const a of arrs) {
      out.set(a, o);
      o += a.length;
    }
    return out;
  }

  for (const [name, content] of Object.entries(entries)) {
    const nameBytes = encoder.encode(name);
    const data = typeof content === "string" ? encoder.encode(content) : content;
    const crc = crc32(data);
    const localHeader = concat(
      u32(0x04034b50),
      u16(20), u16(0), u16(0), u16(0), u16(0),
      u32(crc), u32(data.length), u32(data.length),
      u16(nameBytes.length), u16(0),
      nameBytes,
    );
    parts.push(localHeader, data);

    const centralHeader = concat(
      u32(0x02014b50),
      u16(20), u16(20), u16(0), u16(0), u16(0), u16(0),
      u32(crc), u32(data.length), u32(data.length),
      u16(nameBytes.length), u16(0), u16(0), u16(0), u16(0),
      u32(0), u32(offset),
      nameBytes,
    );
    central.push(centralHeader);
    offset += localHeader.length + data.length;
  }

  const centralStart = offset;
  const centralBlob = concat(...central);
  const eocd = concat(
    u32(0x06054b50),
    u16(0), u16(0),
    u16(Object.keys(entries).length), u16(Object.keys(entries).length),
    u32(centralBlob.length), u32(centralStart),
    u16(0),
  );

  const zipBytes = concat(...parts, centralBlob, eocd);
  return new File([zipBytes], "bundle.zip", { type: "application/zip" });
}

let instructorToken = "";
let studentToken = "";
let student2Token = "";
const sessionIds: number[] = [];

describe("live submission-side verification", () => {
  beforeAll(async () => {
    const i = await login(INSTRUCTOR_EMAIL, INSTRUCTOR_PASSWORD, { persist: false });
    instructorToken = i.access_token;
    const s = await login(STUDENT_EMAIL, STUDENT_PASSWORD, { persist: false });
    studentToken = s.access_token;
    const s2 = await login(STUDENT2_EMAIL, STUDENT2_PASSWORD, { persist: false });
    student2Token = s2.access_token;
    log("logged in", { instructor: INSTRUCTOR_EMAIL, student: STUDENT_EMAIL, student2: STUDENT2_EMAIL });
  });

  afterAll(async () => {
    for (const id of sessionIds) {
      try {
        await deleteSession(id, { token: instructorToken });
        log("cleanup: deleted session", id);
      } catch (err) {
        log("cleanup FAILED for session", { id, err: String(err) });
      }
    }
  });

  let sessionAId = 0;
  let uploadAId = 0; // the zip (notebook + png)
  let uploadBId = 0; // the second, bare file
  let notebookAFileId = 0;
  let zipBytesForComparison: Uint8Array | null = null;

  it("1. zip with a notebook + a non-notebook file: both preserved, whole zip is one item", async () => {
    const session = await createSession(`Week 97 Day 1 (verify ${Date.now()})`, {
      token: instructorToken,
    });
    sessionAId = session.id;
    sessionIds.push(sessionAId);
    log("created session A", { id: sessionAId, title: session.title });

    await uploadAssignment(sessionAId, [makeNotebook("assignment.ipynb", { solved: false })], {
      token: instructorToken,
    });

    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3, 4, 5]);
    const zipFile = makeZip({
      "solution.ipynb": await makeNotebook("solution.ipynb", { solved: true }).text(),
      "screenshot.png": png,
    });
    zipBytesForComparison = new Uint8Array(await zipFile.arrayBuffer());

    const result = await uploadSubmission(sessionAId, [zipFile], { token: studentToken });
    log("POST submissions (zip: notebook + png)", {
      uploads: result.uploads,
      files: result.files,
    });

    expect(result.uploads).toHaveLength(1);
    expect(result.uploads[0].original_filename).toBe("bundle.zip");
    uploadAId = result.uploads[0].id;

    // The notebook was extracted and is gradeable...
    expect(result.files).toHaveLength(1);
    expect(result.files[0].original_filename).toBe("solution.ipynb");
    notebookAFileId = result.files[0].id;

    // ...and the whole zip (including the PNG, never separately listed)
    // downloads as one byte-exact item.
    const downloaded = await downloadSubmissionUpload(sessionAId, uploadAId, {
      token: instructorToken,
    });
    const downloadedBytes = new Uint8Array(await downloaded.arrayBuffer());
    expect(downloadedBytes).toEqual(zipBytesForComparison);
    log("download byte-exact check", {
      uploaded_bytes: zipBytesForComparison.length,
      downloaded_bytes: downloadedBytes.length,
      identical: downloadedBytes.length === zipBytesForComparison.length,
    });
  });

  it("1b. student self-download is SHA-256/size identical; another student's download is rejected", async () => {
    const downloaded = await downloadMySubmissionUpload(sessionAId, uploadAId, {
      token: studentToken,
    });
    const downloadedBytes = new Uint8Array(await downloaded.arrayBuffer());
    const uploadedHash = sha256(zipBytesForComparison!);
    const downloadedHash = sha256(downloadedBytes);
    log("student self-download SHA-256/size check", {
      uploaded_size: zipBytesForComparison!.length,
      downloaded_size: downloadedBytes.length,
      uploaded_sha256: uploadedHash,
      downloaded_sha256: downloadedHash,
      identical: uploadedHash === downloadedHash,
    });
    expect(downloadedBytes.length).toBe(zipBytesForComparison!.length);
    expect(downloadedHash).toBe(uploadedHash);

    let caught: ApiError | null = null;
    try {
      await downloadMySubmissionUpload(sessionAId, uploadAId, { token: student2Token });
    } catch (err) {
      caught = err as ApiError;
    }
    log("student2 attempting to download student's upload (direct API call)", {
      status: caught?.status,
      detail: caught?.detail,
    });
    expect(caught).not.toBeNull();
    expect([403, 404]).toContain(caught!.status);
  });

  it("2. a second, separate upload is ADDED, not a replacement", async () => {
    const before = await getMySubmission(sessionAId, { token: studentToken });
    log("submission before second upload", before);
    expect(before?.uploads).toHaveLength(1);

    const second = await uploadSubmission(
      sessionAId,
      [new File(["just some notes, not gradeable"], "notes.txt", { type: "text/plain" })],
      { token: studentToken },
    );
    log("POST submissions (second, separate upload)", second.uploads);

    expect(second.uploads).toHaveLength(2);
    expect(second.uploads.map((u) => u.original_filename).sort()).toEqual(
      ["bundle.zip", "notes.txt"].sort(),
    );
    uploadBId = second.uploads.find((u) => u.original_filename === "notes.txt")!.id;
    // The first upload's notebook is still there, untouched.
    expect(second.files.find((f) => f.id === notebookAFileId)).toBeTruthy();
  });

  it("3. grading + delete: warn-and-confirm fires, names the real score, only proceeds on confirm", async () => {
    const graded = await gradeSubmissionFile(sessionAId, notebookAFileId, {
      token: instructorToken,
    });
    log("POST submissions/files/{id}/grade (real Gemini call)", graded);
    expect((graded as { success: boolean }).success).toBe(true);

    let caught: ApiError | null = null;
    try {
      await deleteSubmissionUpload(sessionAId, uploadAId, { confirm: false }, { token: studentToken });
    } catch (err) {
      caught = err as ApiError;
    }
    log("DELETE without confirm", { status: caught?.status, detail: caught?.detail });
    expect(caught).not.toBeNull();
    expect(caught!.status).toBe(409);
    const realScore = (graded as { score: number }).score;
    expect(caught!.detail).toContain(String(realScore));

    // Not deleted yet.
    const stillThere = await getMySubmission(sessionAId, { token: studentToken });
    expect(stillThere?.uploads.some((u) => u.id === uploadAId)).toBe(true);

    await deleteSubmissionUpload(sessionAId, uploadAId, { confirm: true }, { token: studentToken });
    const afterConfirm = await getMySubmission(sessionAId, { token: studentToken });
    log("submission after confirmed delete", afterConfirm);
    expect(afterConfirm?.uploads.some((u) => u.id === uploadAId)).toBe(false);
    expect(afterConfirm?.files.some((f) => f.id === notebookAFileId)).toBe(false);
  });

  it("4. deleting an ungraded upload succeeds immediately, no warning", async () => {
    await deleteSubmissionUpload(sessionAId, uploadBId, {}, { token: studentToken });
    const after = await getMySubmission(sessionAId, { token: studentToken });
    log("submission after deleting the ungraded upload", after);
    expect(after === null || after.uploads.length === 0).toBe(true);
  });

  // ── Session B: multi-upload submission graded end-to-end via the chat ──────

  let sessionBId = 0;
  let sessionBTitle = "";
  let realNotebookFileId = 0;

  it("5/6. multi-upload submission graded correctly end-to-end via /chat, matching the right file", async () => {
    sessionBTitle = `Week 97 Day 2 (verify ${Date.now()})`;
    const session = await createSession(sessionBTitle, { token: instructorToken });
    sessionBId = session.id;
    sessionIds.push(sessionBId);

    await uploadAssignment(sessionBId, [makeNotebook("assignment.ipynb", { solved: false })], {
      token: instructorToken,
    });

    // Upload 1: an irrelevant file first...
    const first = await uploadSubmission(
      sessionBId,
      [new File(["irrelevant"], "readme.txt", { type: "text/plain" })],
      { token: studentToken },
    );
    // ...then a SEPARATE, later upload with the real, gradeable notebook.
    const second = await uploadSubmission(
      sessionBId,
      [makeNotebook("solved.ipynb", { solved: true })],
      { token: studentToken },
    );
    log("session B submission after two separate uploads", { first: first.uploads, second: second.uploads });

    expect(second.uploads).toHaveLength(2);
    expect(second.files).toHaveLength(1); // only the notebook upload produced a gradeable file
    realNotebookFileId = second.files[0].id;
    expect(second.files[0].original_filename).toBe("solved.ipynb");

    const chatResult = await postChat(`grade ${sessionBTitle}`, { token: instructorToken });
    log("POST /chat (real Gemini grading call, multi-upload submission)", chatResult);

    expect(chatResult.status).toBe("graded");
    if (chatResult.status === "graded") {
      expect(chatResult.summary.graded).toBeGreaterThanOrEqual(1);
      expect(chatResult.summary.failed).toBe(0);
    }

    const subs = await listSubmissions(sessionBId, { token: instructorToken });
    const gradedFile = subs.flatMap((s) => s.files).find((f) => f.id === realNotebookFileId);
    log("the file grading actually landed on", gradedFile);
    // The right file (from the SECOND upload, not the irrelevant first one)
    // is the one that ended up graded.
    expect(gradedFile?.graded).toBe(true);
  });
});
