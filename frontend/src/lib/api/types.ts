/**
 * TypeScript mirrors of the backend's Pydantic schemas.
 *
 * Every type here corresponds 1:1 to a schema in `backend/app/schemas/`
 * (or, for the /chat endpoints, to the response shapes documented in
 * `backend/app/routers/chat.py`'s module docstring). Datetimes arrive as
 * ISO-8601 strings over JSON, so they are typed `string`, not `Date`.
 *
 * If a backend schema changes, change it here too — nothing enforces this
 * automatically.
 */

// -- Auth / user (backend/app/schemas/user.py) -------------------------------

/** `UserRole` -- backend/app/models/user.py */
export type UserRole = "instructor" | "student";

/** `Token` -- returned by POST /auth/login */
export interface Token {
  access_token: string;
  token_type: string;
}

/** `UserRead` -- safe outward-facing User (no password) */
export interface UserRead {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  created_at: string;
}

/** `UserRegister` -- body for POST /auth/register (public, students only) */
export interface UserRegister {
  name: string;
  email: string;
  password: string;
}

// -- Assignment files (backend/app/schemas/unsolved_file.py) -----------------

/**
 * `UnsolvedFileRead` -- one instructor-uploaded assignment file.
 * `file_path` and `rubric_json` are deliberately not exposed by the backend.
 */
export interface UnsolvedFileRead {
  id: number;
  session_id: number;
  original_filename: string;
  /** True once the rubric has been generated. */
  rubric_generated: boolean;
  uploaded_at: string;
}

// -- Resource files (backend/app/schemas/resource_file.py) -------------------

/**
 * `ResourceFileRead` -- one non-notebook supporting file (dataset, slides,
 * PDF) uploaded inside a `.zip` alongside or instead of notebooks.
 *
 * Structurally separate from `UnsolvedFileRead`, not a variant of it: the
 * backend stores these in their own `resource_files` table. A resource is
 * downloadable only -- it is never parsed, never file-matched, never given a
 * rubric, and never counted toward a session's assignment total. It therefore
 * has NO `rubric_generated` field, which is why the two are kept as distinct
 * types rather than one type with a role flag.
 */
export interface ResourceFileRead {
  id: number;
  session_id: number;
  original_filename: string;
  uploaded_at: string;
}

/**
 * `AssignmentUploadItem` -- one entry in POST /sessions/{id}/assignments'
 * response, which is a unified view over BOTH tables.
 *
 * `file_role` is derived by the backend from which table the row landed in;
 * it is not a stored column on either model. `rubric_generated` is always
 * false for a resource.
 */
export interface AssignmentUploadItem {
  id: number;
  session_id: number;
  original_filename: string;
  file_role: "notebook" | "resource";
  rubric_generated: boolean;
  uploaded_at: string;
}

// -- Sessions (backend/app/schemas/session.py) -------------------------------

/** `SessionRead` -- full session detail including its assignment files. */
export interface SessionRead {
  id: number;
  title: string;
  instructor_id: number | null;
  created_at: string;
  /** Gradeable notebooks only -- resources never appear here. */
  unsolved_files: UnsolvedFileRead[];
  /**
   * Downloadable-only supporting files. Defaults to `[]` on the backend, so
   * a session with no resource uploads returns an empty array, not null.
   */
  resource_files: ResourceFileRead[];
}

/** `SessionList` -- paginated session listing. */
export interface SessionList {
  total: number;
  items: SessionRead[];
}

// -- Submissions (backend/app/schemas/submission.py) -------------------------

/** `SubmissionFileRead` -- one notebook inside a submission. */
export interface SubmissionFileRead {
  id: number;
  original_filename: string;
  /** null when file matching could not confidently pick an assignment file. */
  matched_unsolved_file_id: number | null;
  /** True once a Grade row exists for this submission file. */
  graded: boolean;
}

/** `SubmissionRead` -- a student's submission for one session. */
export interface SubmissionRead {
  id: number;
  session_id: number;
  student_id: number;
  original_filename: string;
  submitted_at: string;
  files: SubmissionFileRead[];
}

// -- Grades (backend/app/schemas/grade.py) -----------------------------------

/** `RationaleEntry` -- one row of the criterion-by-criterion breakdown. */
export interface RationaleEntry {
  criterion: string;
  points_possible: number;
  points_awarded: number;
  explanation: string;
}

/** `GradeRead` -- full grade detail for one SubmissionFile. Score is out of 10. */
export interface GradeRead {
  id: number;
  submission_file_id: number;
  original_filename: string;
  score: number;
  feedback_text: string;
  /** null when `rationale_json` was absent or malformed. */
  rationale: RationaleEntry[] | null;
  graded_at: string;
}

/**
 * `GradeSummary` -- one student's results for a session.
 *
 * `combined_score` is the sum of per-file scores divided by the session's
 * TOTAL assignment count, rounded to the nearest 0.5.
 *
 * CAUTION: it is null ONLY when the session has no assignment files at all.
 * A student who has submitted but has nothing graded yet scores 0.0, not
 * null -- indistinguishable from a genuine zero unless you also check
 * `per_file.length`. (Both this schema's docstring and an earlier comment
 * here claimed null meant "not graded yet"; `_build_grade_summary` in
 * backend/app/routers/grades.py shows otherwise.)
 */
export interface GradeSummary {
  student_id: number;
  student_name: string;
  per_file: GradeRead[];
  combined_score: number | null;
}

/** `SessionGradeReport` -- one GradeSummary per student in a session. */
export interface SessionGradeReport {
  session_id: number;
  session_title: string;
  students: GradeSummary[];
}

// -- Rubric / single-file grading (untyped dicts on the backend) -------------

/**
 * Return value of POST /sessions/{id}/assignments/{file_id}/generate-rubric.
 * The backend returns `generate_rubric_for_unsolved_file`'s raw dict, which is
 * not a Pydantic schema -- kept loose on purpose rather than guessing at it.
 */
export type GenerateRubricResult = Record<string, unknown>;

/**
 * Return value of POST /sessions/{id}/submissions/files/{file_id}/grade.
 * Also an untyped dict on the backend.
 */
export type GradeSubmissionFileResult = Record<string, unknown>;

// -- Batch grading events (backend/app/services/grading_pipeline.py) ---------

export interface GradingCheckingEvent {
  event: "checking";
  student_id: number;
  student_name: string;
  filename: string;
}

export interface GradingGradedEvent {
  event: "graded";
  student_id: number;
  student_name: string;
  filename: string;
  score: number;
}

export interface GradingFailedEvent {
  event: "failed";
  student_id: number;
  student_name: string;
  filename: string;
  error: string;
}

export interface GradingFailureRecord {
  student_id: number;
  filename: string;
  error: string;
}

/** Always the final event of a batch. */
export interface GradingSummaryEvent {
  event: "summary";
  total: number;
  graded: number;
  failed: number;
  failures: GradingFailureRecord[];
  /**
   * Present only on the SSE stream (POST /chat/stream), where the summary
   * event carries the conversational message. Absent on the plain batch
   * endpoint POST /sessions/{id}/grade.
   */
  message?: string;
}

export type GradingEvent =
  | GradingCheckingEvent
  | GradingGradedEvent
  | GradingFailedEvent
  | GradingSummaryEvent;

/** Response of POST /sessions/{session_id}/grade (generator drained eagerly). */
export interface BatchGradeResult {
  events: GradingEvent[];
  summary: GradingSummaryEvent;
  [key: string]: unknown;
}

// -- Chat (backend/app/routers/chat.py) --------------------------------------

/** Candidate returned when an instruction matches more than one session. */
export interface SessionCandidate {
  session_id: number;
  session_title: string;
  confidence: number;
}

/** Candidate returned when a student name matches more than one student. */
export interface StudentCandidate {
  student_id: number;
  student_name: string;
}

export interface ChatNoSessionMatch {
  status: "no_session_match";
  message: string;
}

export interface ChatAmbiguousSession {
  status: "ambiguous_session";
  message: string;
  candidates: SessionCandidate[];
}

export interface ChatStudentNotFound {
  status: "student_not_found";
  message: string;
  attempted_name: string;
}

export interface ChatAmbiguousStudent {
  status: "ambiguous_student";
  message: string;
  session_id: number;
  session_title: string;
  candidates: StudentCandidate[];
}

export interface ChatUnsupportedFilter {
  status: "unsupported_filter";
  message: string;
  reason: string;
}

/**
 * The instruction was not a grading instruction at all ("what is a tensor?").
 * Previously such a question fell through to a student-name lookup and came
 * back as "no such student"; the backend now answers honestly instead.
 */
export interface ChatUnrecognizedInstruction {
  status: "unrecognized_instruction";
  message: string;
}

export interface ChatGraded {
  status: "graded";
  message: string;
  session_id: number;
  session_title: string;
  scope: "all" | "student";
  /** Present only when `scope === "student"`. */
  student_name?: string;
  events: GradingEvent[];
  summary: GradingSummaryEvent;
}

/** Every POST /chat outcome returns HTTP 200 -- discriminate on `status`. */
export type ChatResponse =
  | ChatNoSessionMatch
  | ChatAmbiguousSession
  | ChatStudentNotFound
  | ChatAmbiguousStudent
  | ChatUnsupportedFilter
  | ChatUnrecognizedInstruction
  | ChatGraded;

/** The non-graded ("early exit") outcomes, which /chat/stream emits as one event. */
export type ChatEarlyExit = Exclude<ChatResponse, ChatGraded>;

/**
 * One decoded SSE event from POST /chat/stream.
 *
 * The stream is either exactly one early-exit outcome (then it closes), or a
 * live sequence of the pipeline's own checking/graded/failed events ending in
 * `summary` (which alone carries the conversational `message`).
 */
export type ChatStreamEvent = ChatEarlyExit | GradingEvent;

/** Narrowing helper: is this stream event one of the early-exit outcomes? */
export function isChatEarlyExit(e: ChatStreamEvent): e is ChatEarlyExit {
  return "status" in e;
}

/** Narrowing helper: is this stream event a grading-pipeline progress event? */
export function isGradingEvent(e: ChatStreamEvent): e is GradingEvent {
  return "event" in e;
}
