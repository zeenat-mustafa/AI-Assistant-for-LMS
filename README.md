# AI Assistant for LMS — Automated Grading & Feedback

An instructor-directed AI grading assistant for Jupyter notebook assignments, built as a capstone project for the Purelogics Bootcamp.

Instructors create a **Session** for a given day (e.g. "Week 8 Day 4") and upload the unsolved assignment. Students download it, solve it, and submit their work back to the same session. When an instructor is ready, they trigger grading with a plain-English chat instruction — the system locates the right submissions, matches each one to the correct assignment, generates its own grading rubric, evaluates the work, and writes specific, individualized feedback. No manual grading, no instructor-authored rubric.

## How Grading Works

- **No pre-written rubric.** The AI reads each unsolved assignment's own instructions and generates its own grading criteria (out of 10 marks), once per assignment, reused consistently for every student.
- **Content-based matching, not filename matching.** Students typically edit the original notebook in place rather than starting fresh, so their submission still carries the original instructions. The system matches submissions to assignments by comparing that retained content — filename is only a fast first check.
- **Structurally separated files.** Every session has three distinct areas — assignment files, student submissions, and grades/feedback — so a file's role is never guessed from its name.
- **Resilient by design.** LLM calls run through a shared provider layer: Gemini is primary, with automatic fallback to Groq if a quota or rate-limit error occurs, so a single provider hiccup doesn't stop a whole grading run.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.13 + FastAPI |
| Database | SQLite (SQLAlchemy ORM) |
| File storage | Local filesystem, structured per-session (swappable abstraction) |
| Auth | JWT (bcrypt password hashing) |
| LLM — primary | Gemini API (`gemini-flash-latest` / `gemini-3.1-pro-preview`) |
| LLM — fallback | Groq API (automatic fallback on Gemini quota errors) |
| Notebook parsing | `nbformat`, recursive `.zip` extraction |
| Session matching | Fuzzy text matching |
| MCP server | Python MCP SDK |
| Frontend | Next.js *(Phase 5 — not yet built)* |

## Getting Started

**1. Install dependencies**
```bash
cd backend
pip install -r requirements.txt
```

**2. Configure environment**
```bash
cp .env.example .env
```
Fill in `.env` with:
- `GEMINI_API_KEY` — from [Google AI Studio](https://aistudio.google.com/app/apikey)
- `GROQ_API_KEY` — from [Groq Console](https://console.groq.com/keys)
- `SECRET_KEY` — generate one with `python -c "import secrets; print(secrets.token_hex(32))"`

**3. Run the server**
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
On first run, the server creates the SQLite database and seeds two demo accounts (see below). Interactive API docs are available at **http://localhost:8000/docs**.

## Demo Accounts

| Role | Email | Password |
|---|---|---|
| Instructor | `instructor@demo.com` | `instructor123` |
| Student | `student@demo.com` | `student123` |

These are seeded automatically for local development only. Replace `SECRET_KEY` with a real random value before any shared or hosted deployment.

## Project Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Auth, Session CRUD, assignment upload/download, submission upload, database schema | ✅ Complete |
| 2 | AI grading pipeline — notebook parsing, file matching, rubric generation, evaluation, feedback + rationale, Gemini/Groq fallback layer | 🔄 In progress |
| 3 | Instructor chatbot — natural-language instruction → session matching → live-updating grading run | ⏳ Upcoming |
| 4 | MCP server — chatbot and grading agent exposed as standardized callable tools | ⏳ Upcoming |
| 5 | Minimal Next.js dashboard for both roles | ⏳ Upcoming |
| 6 | Integration testing, polish, live demo prep | ⏳ Upcoming |

## Contributors

- Zeenat Mustafa
- Laiba Afreen
