# AI Assistant for LMS — Automated Grading, RAG Chatbot & Practice Quizzes

An instructor-directed AI grading assistant for Jupyter notebook assignments, plus a retrieval-grounded student course-assistant chatbot and non-grade-affecting practice quizzes. Built as a capstone project for the Purelogics Bootcamp. Instructors create a **Session** (e.g. "Week 8 Day 4"), upload the assignment notebook and lecture slides; students download, solve, and submit their work; the instructor triggers AI grading with a single natural-language chat instruction, and students can ask a chatbot questions about lectures/assignments or generate a short practice quiz — all grounded in the instructor's own uploaded material, never a generic answer.

<!--
  SCREENSHOT — add before submitting:
  1. Run the app (see "Run It" below) and sign in as instructor@demo.com / instructor123.
  2. Screenshot the session dashboard (http://localhost:3000/instructor).
  3. Save it as docs/screenshot.png and uncomment the line below.
-->
<!-- ![AI Assistant for LMS — instructor dashboard](docs/screenshot.png) -->
**[ Screenshot pending — see the comment in this file's source for the exact steps to add one before submission ]**

## Table of Contents

- [What It Does](#what-it-does)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Install](#install)
- [Environment Variables](#environment-variables)
- [Run It](#run-it)
- [Example Usage](#example-usage)
- [Demo Video](#demo-video)
- [Project Status](#project-status)
- [MCP Server](#mcp-server)
- [Database Migrations](#database-migrations)
- [Demo Accounts](#demo-accounts)
- [Security Notes](#security-notes)
- [Testing](#testing)
- [Team](#team)

## What It Does

- **AI grading, not a rubric you have to write.** For every unsolved assignment file, the system generates its own 10-point rubric that is *completion-aware* — pre-written scaffolding earns little, the sections a student actually had to complete earn most — then evaluates every submission against it with 0.5-point granularity and a full, criterion-by-criterion rationale.
- **A RAG-grounded student chatbot.** Instructors upload `.pptx` lectures; the system chunks and embeds them (plus every assignment notebook's own instructional text) into a local vector store. Students ask questions and get answers grounded in that real, cited material — with a strict "explain, never solve" rule that refuses to hand over the answer to an incomplete assignment.
- **Ungraded practice quizzes.** Students can generate a 5-question multiple-choice quiz from an assignment, a session, several sessions, a free-text topic, or a one-off uploaded file — always clearly labeled as practice and never touching a real grade.
- **Two doors, one engine.** Every capability above is also exposed as an MCP (Model Context Protocol) tool for MCP-compatible clients (Claude Desktop, an IDE), sharing the exact same backend service code as the REST API — not a second, drifting implementation.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.13 + FastAPI |
| Database | SQLite (SQLAlchemy ORM), schema changes via Alembic migrations |
| File storage | Local filesystem, structured per-session |
| Auth | JWT (bcrypt password hashing) |
| AI — primary / fallback | Gemini API (`gemini-3.5-flash-lite` → `gemini-3.1-flash-lite` on quota/rate-limit) |
| Vector store | Chroma (embedded, file-based — no separate server process) |
| Embeddings | Local `sentence-transformers` (`all-MiniLM-L6-v2`) — never Gemini's own embedding API |
| Lecture parsing | `python-pptx` (`.pptx` only; legacy `.ppt` is explicitly rejected) |
| Notebook parsing | `nbformat`, recursive `.zip` extraction |
| Session/chat matching | `rapidfuzz` + retrieval-similarity resolution |
| MCP server | Python `mcp` SDK (2.x), stdio transport, 11 tools |
| Frontend | Next.js 16.3.4 (App Router, TypeScript, Tailwind CSS 4) |
| Testing | `pytest` (backend, 760+ tests) · Vitest + Testing Library (frontend, 240+ tests) |

## Prerequisites

- Python 3.11+ (developed and tested against Python 3.13 in a conda `base` environment)
- Node.js 18+ (frontend developed against Next.js 16.3.4)
- A Google Gemini API key — [Google AI Studio](https://aistudio.google.com/app/apikey)
- ~500 MB free disk (local embedding model + Chroma vector store are downloaded/created on first use)

## Install

```bash
git clone https://github.com/zeenat-mustafa/AI-Assistant-for-LMS.git
cd AI-Assistant-for-LMS

# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env          # then fill in real values — see below

# Frontend
cd ../frontend
npm install
cp .env.example .env.local    # adjust only if the backend runs elsewhere
```

## Environment Variables

**`backend/.env`** (copy from `backend/.env.example`):

| Variable | Required | Default / example | Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | **Yes** | *(none)* | From [Google AI Studio](https://aistudio.google.com/app/apikey). Never commit this — `.env` is already in `.gitignore`. |
| `SECRET_KEY` | **Yes** for anything beyond local demo | placeholder string | Generate a real one: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | No | `sqlite:///./lms.db` | Relative to `backend/` — must run the app from that directory (`start-all.ps1` and the commands below already do). |
| `STORAGE_ROOT` | No | `storage/sessions` | Where uploaded assignment/submission/lecture files are written. |
| `ALGORITHM` | No | `HS256` | JWT signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `480` | JWT lifetime. |
| `GEMINI_PRIMARY_MODEL` | No | `gemini-3.5-flash-lite` | Serves every AI call unless it hits quota/rate-limit. |
| `GEMINI_FALLBACK_MODEL` | No | `gemini-3.1-flash-lite` | Used automatically on the primary's quota/rate-limit error. |
| `DEMO_INSTRUCTOR_EMAIL` / `DEMO_INSTRUCTOR_PASSWORD` | No | `instructor@demo.com` / `instructor123` | Seeded on first startup. |
| `DEMO_STUDENT_EMAIL` / `DEMO_STUDENT_PASSWORD` | No | `student@demo.com` / `student123` | Seeded on first startup. |

**`frontend/.env.local`** (copy from `frontend/.env.example`):

| Variable | Required | Default | Notes |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | **Yes** | `http://127.0.0.1:8000/api/v1` | Where the frontend expects the FastAPI backend. The backend's CORS config allows `localhost:3000` / `127.0.0.1:3000` by default. |

## Run It

**Option A — one command (recommended for demos):**
```powershell
.\start-all.ps1
```
Starts both services with clearly prefixed output; the backend runs without auto-reload for demo stability. Press `Ctrl+C` to stop both cleanly.

**Option B — run separately (recommended for development, gives auto-reload):**

Terminal 1:
```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Terminal 2:
```bash
cd frontend
npm run dev
```

Either way:
- Backend: **http://127.0.0.1:8000** (interactive API docs at `/docs`)
- Frontend: **http://localhost:3000**

On first run, the backend creates the SQLite database, seeds the demo accounts below, and warms up the embedding model and the Gemini connection so the first real request isn't slow.

## Example Usage

1. Sign in at `http://localhost:3000` as `instructor@demo.com` / `instructor123`.
2. Create a session (e.g. "Week 1 Day 1"), then upload an unsolved `.ipynb` file as its assignment.
3. Sign in as `student@demo.com` / `student123` in a different browser tab (or a private window — sessions are per-tab), download the assignment, and upload a solved copy back.
4. Back as the instructor, open the floating grading-chat widget and type:
   ```
   grade Week 1 Day 1
   ```
   Grading progress streams live, then a summary is shown; the grade roster now shows the student's score and full rationale.
5. As the student, open the floating course-assistant widget and ask a real question about the uploaded material, e.g.:
   ```
   What is this assignment asking me to build in the first section?
   ```
   The answer streams back with citations to the real lecture slide or notebook cell it came from. Click **Quiz me** in the same widget to generate a short, ungraded practice quiz on the same material.

## Demo Video

**[ Add the demo video link here before submission — e.g. an unlisted YouTube/Drive link showing the flow above end to end ]**

## Project Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Authentication, session management, assignment/submission upload and download, database schema | ✅ Complete |
| 2 | AI grading pipeline — notebook parsing, submission matching, rubric generation, evaluation, feedback, dual-provider AI layer, batch grading | ✅ Complete |
| 3 | Instructor chatbot — natural-language session resolution and live-updating grading runs | ✅ Complete |
| 4 | MCP server — grading pipeline exposed as standardized callable tools | ✅ Complete |
| 5 | Next.js web dashboard for instructors and students | ✅ Complete |
| 6 | Integration testing, polish, and demo preparation | ✅ Complete |
| 7.1–7.6 | RAG pipeline — lecture ingestion, Chroma embeddings, "explain never solve" scope safety, chat memory, student Q&A chatbot, practice quizzes | ✅ Complete |
| 7.7 | Student chat & quiz frontend, instructor lecture-upload UI | ✅ Complete |
| 7.8 | Full UI/UX redesign, MCP tool expansion (11 tools total), demo-readiness hardening | ✅ Complete |

See `docs/phase*-known-gaps-record.txt` for the full, evidence-based record of every known gap, deliberate scope decision, and later resolution across each phase.

## MCP Server

Alongside the REST API, the project exposes a [Model Context Protocol](https://modelcontextprotocol.io) server so an MCP client (Claude Desktop, an IDE, or the SDK's own client) can drive grading, retrieval, and quizzes directly. It is a **separate process** from the FastAPI app, not a replacement for it — both talk to the same database, and every tool below calls the exact same service-layer function the REST API uses (no logic is duplicated between the two interfaces).

**Start it:**
```bash
cd backend
python -m app.mcp
```

It speaks JSON-RPC over **stdio**, so it prints no banner, binds no port, and blocks waiting for a client — that is expected, not a hang. Example client config:

```json
{
  "mcpServers": {
    "ai-assistant-for-lms": {
      "command": "python",
      "args": ["-m", "app.mcp"],
      "cwd": "/absolute/path/to/backend"
    }
  }
}
```

`cwd` matters: the SQLite path in `DATABASE_URL` is relative, so a client launching the server from elsewhere would silently create a different, empty database.

**Available tools (11 + `ping`)**

| Tool | Purpose |
|---|---|
| `ping` | Connectivity check. |
| `match_session` | Resolves a free-text instruction to a session — same matcher the REST `/chat` endpoint uses. |
| `generate_rubric` | Generates (or returns the cached) 10-point rubric for one assignment file. |
| `evaluate_submission` | Evaluates one submission against its rubric without recording a grade. |
| `grade_submission_file` | Grades one submission and records the grade. |
| `grade_session` | Grades every ungraded submission in a session (optionally one student), draining the full pipeline before returning. |
| `list_lecture_files` | Lists lecture files uploaded to a session. |
| `upload_lecture_file` | Uploads a `.pptx` lecture file — same extraction/chunking/embedding path as the REST upload. |
| `ask_course_assistant` | The student chatbot, collected into one response (the streaming REST endpoint's SSE events, drained). |
| `generate_quiz` | Generates a 5-question practice quiz from any of four scope types (a fifth, one-off file upload, is REST-only). |
| `submit_quiz` | Scores a practice quiz attempt once — never touches the real `Grade` table. |

`ambiguous` / `clarification_needed` results are normal outcomes, not errors — the system never force-matches on a close call.

> **SDK note:** built against `mcp` 2.x, where the high-level server class is `MCPServer`. Most tutorials still show 1.x's `FastMCP`, which will not run as-is against 2.x.

## Database Migrations

Schema changes are managed with [Alembic](https://alembic.sqlalchemy.org/), configured in `backend/alembic/`. `Base.metadata.create_all()` still runs on every startup, but it only ever matters for a brand-new, empty database — it cannot alter tables that already exist. **Any schema change to a database that already has data in it must go through a migration, never through `create_all()` alone.**

```bash
cd backend
alembic upgrade head        # apply pending migrations (run after pulling)
alembic revision --autogenerate -m "short description"   # after changing a model
```

Review every autogenerated migration before committing — Alembic is good but not infallible, especially around renames and SQLite's limited `ALTER TABLE` support.

## Demo Accounts

| Role | Email | Password |
|---|---|---|
| Instructor | `instructor@demo.com` | `instructor123` |
| Instructor (multi-tenant testing) | `instructor2@demo.com` | `instructor2123` |
| Instructor (multi-tenant testing) | `instructor3@demo.com` | `instructor3123` |
| Student | `student@demo.com` | `student123` |
| Student (multi-tenant testing) | `student2@demo.com` – `student4@demo.com` | see `app/services/auth.py` |

These are seeded automatically on first startup. **Never use real, non-demo credentials for testing.** Replace `SECRET_KEY` with a securely generated value before any shared or hosted deployment.

## Security Notes

- **API keys are never committed.** `backend/.env` (which holds `GEMINI_API_KEY` and `SECRET_KEY`) is listed in `.gitignore`; only `backend/.env.example`, with placeholder values, is tracked.
- **Dependencies are pinned.** Every package in `backend/requirements.txt` is pinned to an exact version; `frontend/package.json` pins exact versions for its core dependencies.
- The auth token lives in the browser's `sessionStorage`, not `localStorage`, so separate tabs can hold independent instructor/student sessions side by side without one overwriting the other.

## Testing

```bash
cd backend && pytest -q          # 760+ tests
cd frontend && npm test          # 240+ tests
```

Every sub-feature in this project was verified two ways before being considered done: automated tests, and real-data/real-Gemini verification (real notebooks, real lecture files, real adversarial prompts against production Gemini) — mocked tests alone have repeatedly missed real grading-quality and safety bugs during development, so neither is treated as sufficient on its own.

Before submitting, clone the repository into a fresh folder and run it from scratch following the steps above, to confirm nothing depends on local machine state.

## Team

- **Zeenat Mustafa**
- **Laiba Afreen**

Repository: [github.com/zeenat-mustafa/AI-Assistant-for-LMS](https://github.com/zeenat-mustafa/AI-Assistant-for-LMS)
