/**
 * Typed client for the FastAPI backend.
 *
 * Import from `@/lib/api` -- e.g. `import { login, listSessions } from "@/lib/api";`
 * Nothing outside this folder should call `fetch` against the backend directly.
 */

export { API_BASE_URL, buildUrl } from "./config";
export { ApiError, apiFetch, buildRequestInit, type RequestOptions } from "./client";
export { getToken, setToken, clearToken } from "./token-storage";

export { login, register, getCurrentUser, logout } from "./auth";
export {
  createSession,
  listSessions,
  getSession,
  deleteSession,
  gradeSession,
} from "./sessions";
export {
  uploadAssignment,
  listAssignments,
  assignmentDownloadUrl,
  downloadAssignment,
  deleteAssignment,
  generateRubric,
} from "./assignments";
export {
  uploadSubmission,
  listSubmissions,
  getMySubmission,
  gradeSubmissionFile,
} from "./submissions";
export { getGradeReport, getMyGrades, getStudentGrades } from "./grades";
export { postChat, streamChat, parseSseFrame } from "./chat";

export * from "./types";
