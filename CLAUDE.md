# AI Assistant for LMS — Project Memory

## What this is
Capstone project (Purelogics Bootcamp): instructor-directed AI grading assistant for Jupyter notebooks. Instructors create Sessions ("Week X Day Y"), upload unsolved assignments, students download/solve/upload back, instructor triggers grading via chat instruction. Standalone app, no real LMS integration. Team: Zeenat Mustafa + Laiba Afreen. Repo: https://github.com/zeenat-mustafa/AI-Assistant-for-LMS (public).

## Working method (always follow this)
When asked for the next feature/sub-feature prompt, always give a FULLY DETAILED, A-to-Z prompt: exact files to create/edit, exact function names, exact internal logic step-by-step, exact testing requirements, and explicit scope boundaries (a "Do NOT" list). Never leave architecture/library/approach decisions to guesswork. For any resume/handoff situation, verify actual current file contents against a checklist before continuing — never trust a prior summary alone.

## Tech stack
FastAPI (Python) + SQLite/SQLAlchemy + Alembic migrations + JWT/bcrypt auth + local filesystem storage. AI: Gemini primary, Groq fallback (on quota/rate-limit only), Ollama local last-resort fallback — all behind one shared `app/services/llm_provider.py`. Conda env: `base` (not `ai-bootcamp` — confirmed via pip show). Next.js frontend (not built yet, Phase 5). MCP server (not built yet, Phase 4).

## Core design decisions (locked in — apply to every future feature)
- Sessions have 3 structural areas: Assignment Files, Submissions, Grades & Feedback — file role never guessed from filename.
- No instructor rubric — AI generates its own per unsolved file, worth 10 marks, cached & reused per student. Completion-aware: scaffolding capped ~1-2 marks, majority of marks on real student work. Completion points can be TODO keywords, informal comments, OR markdown-only, OR code-comment-only (no markdown at all) — all supported styles.
- File/session matching is content-based first, filename-based fallback, confidence-threshold gated (0.55) — never force-match on ambiguity, always leave for instructor review instead of guessing.
- Scoring: 0.5-point increments only, everywhere. Multi-file Session combined score = sum of per-file scores / TOTAL assignment count in the session (not just files the student submitted) — missing/unmatched/ungraded files count as 0 in the denominator, for fairness across students.
- Every grade stores full criterion-by-criterion rationale (`rationale_json`) for the future "why this grade" student chatbot (Extended Goal).
- Grading only happens on explicit instructor trigger, never automatic.
- Any schema change MUST go through an Alembic migration (`alembic revision --autogenerate`) — never rely on `create_all()` for an existing non-empty DB; it only creates missing tables, never alters existing ones. `create_all()` is still fine for a brand-new empty dev DB.
- Evaluator must verify claimed execution against real `execution_count`/output evidence — never credit a criterion claiming a cell "ran/executed/produced X" without real evidence.
- Evaluator must penalize substituting a custom implementation for an explicitly NAMED required tool/library (not just a goal/outcome) — cap at ~20-30% of that criterion's points, excluding failed/abandoned install attempts from counting as partial use.

## Current status (update this section as phases complete)
- **Phase 1 (Foundation): COMPLETE.** Auth, session CRUD, assignment/submission upload, grade-report endpoints.
- **Phase 2 (AI Grading Pipeline, sub-features 2.1-2.9): COMPLETE, merged to `main`.** Full real-data validation done (not just mocked tests) — 3 genuine grading-quality bugs found and fixed (file-matching exclusion of code-comment-only assignments, execution-hallucination in evaluator, named-tool-substitution under-penalization), plus a live-DB schema gap resolved via Alembic. 223 tests passing. Full details: `Phase2_Completion_Record.pdf` (kept outside repo) and `phase2-known-gaps-record.txt`.
- **Phase 3 (Instructor Chatbot + Session Matching, sub-features 3.1-3.5): COMPLETE, merged to `main`.** 3.1 (session matching) → 3.2 (filter parsing) → 3.3 (`POST /chat`) → 3.4 (`POST /chat/stream`, SSE) → 3.5 (conversational summary messages); 3.6 (never guess on ambiguity) was folded into 3.1 as planned, not built separately. Required one prereq schema change first: `LMSSession` had no instructor ownership at all, so `instructor_id` + per-instructor title uniqueness were added via Alembic migration `b824df1246e5`. Full real-data validation done (not just mocked tests) — 8 genuine bugs found and fixed, 5 of them only surfacing through real Swagger/dev-DB testing (single-item "ambiguous" candidate list, swapped week/day numbers tying at confidence 1.0, digit near-misses still fuzzy-scored, `combined_score` rounding to 0.01 instead of 0.5, and an incomplete dev-DB reset that left 25 orphaned files in `storage/sessions/`). SSE streaming was verified live with timestamped `curl -N` output, not assumed. 273 tests passing. Full details: `phase3-known-gaps-record.txt`.
- **Phases 4-6:** Not started. Phase 4 = MCP server. Phase 5 = Next.js frontend (thin UI, not the focus). Phase 6 = integration testing/polish/demo prep.
- **Extended Goals (build only after Phase 6 fully complete):** Student chatbot (query-only against existing `rationale_json`, no new grading logic) + complaint routing via email/Discord (new integration work). Quiz generation from a course file + auto-grading + student-visible publish page.

## Extended Goals (build only after Phase 6 fully complete)
- **Student Chatbot:** students ask why they got a specific grade, grounded in the already-stored `rationale_json` (query interface only — data already exists from Phase 2, nothing new to store). Students can also ask general questions about their assignments/topics. Students can raise a complaint/concern routed to the relevant instructor via email or Discord (this part is new integration work, not just a query).
- **Instructor-Triggered Quiz Generation:** instructor asks chatbot to generate a quiz from a specific course file; published to a student-visible page once released; responses auto-graded.

## Future Work (explicitly out of scope for a long time — do NOT build, but design current work so these stay possible)
These are not on the roadmap yet, but no current phase should make an architectural choice that accidentally blocks them later:
- Full RAG pipeline (chunked, embedded course material in a vector DB) for broader topic/concept Q&A beyond specific grading rationale — don't hardcode assumptions that grading rationale is the only kind of retrievable context the system will ever have.
- Instructor-triggered release of lecture/lab files for a specific week/day (on-demand, never automatically scheduled) — keep assignment-file visibility/release logic separable from upload logic, not fused together.
- Support for additional submission formats beyond `.ipynb`/`.zip` — keep notebook-parsing logic in its own service layer (already true via `notebook.py`), don't scatter `.ipynb`-specific assumptions into routers/pipeline code.
- Instructor-facing analytics chatbot for cohort-wide progress tracking — keep grade/rationale data structured and queryable (already true via `rationale_json`), don't lock reporting logic into single-student-only shapes.

## Known gaps (by design, not bugs — do not "fix" without being asked)
Phase 1/2:
- No LLM fallback for ambiguous/altered file matches yet (file_matcher.py's own TODO).
- Students with zero submissions don't appear in `session_grade_report` at all.
- Rubric criteria count (3-6) isn't strictly validated, only checked non-empty.
- A handful of cosmetic items (dead imports, stale docstrings, notebooks parsed twice) — see `phase2-known-gaps-record.txt` for the full list.

Phase 3 (see `phase3-known-gaps-record.txt`):
- A student whose real name is an ordinary English word ("Grace", "Will") can false-positive match that word used ordinarily in an instruction. Unlike other misses, nothing catches this — it isn't routed to `ambiguous`/`not_found`, it silently narrows scope.
- No LLM fallback in `instruction_filter.py` — deliberate: the closed 5-shape output contract has nowhere to route "the second student" or exclusion queries.
- Exclusionary filters ("everyone except Ali") are detected and surfaced as `unsupported_filter` rather than silently inverting intent; force-regrading already-graded files is likewise not built.
- When an instruction under-specifies (e.g. "grade week 10" where two Week 10 sessions exist), the matcher returns `ambiguous` rather than guessing — working as designed, not a bug.
- No auth/token-refresh handling for long-lived SSE connections on `/chat/stream`.

## Testing philosophy
Every sub-feature needs both automated tests AND real-data verification before being called done — mocked tests alone have already missed real bugs (Phase 2's 3 grading-quality bugs, and 5 of Phase 3's 8, which surfaced only through real Swagger/dev-DB testing after the mocked tests were already green). For anything involving LLM judgment calls, get exact wording/approach approved before implementing, then re-verify against the real production model (Gemini), not just the fallback (Groq) — a fix is not confirmed until proven on the primary model.

## Resuming after an agent runs out of context/credits
Never assume a prior session's summary is accurate. Re-verify actual file contents against whatever checklist applies before continuing any incomplete work.
