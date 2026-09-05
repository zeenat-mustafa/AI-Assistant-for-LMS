# AI Assistant for LMS — Automated Grading & Feedback

An instructor-directed AI grading assistant for Jupyter notebook assignments, built as a capstone project for the Purelogics Bootcamp.

Instructors create a **Session** for a given class day (e.g. "Week 8 Day 4") and upload the assignment notebook. Students download it, complete their work, and submit it back to the same session. When ready, an instructor triggers grading with a single request — the system locates the relevant submissions, matches each one to its assignment, generates a tailored grading rubric, evaluates the work against it, and produces specific, individualized feedback for every student.

## How Grading Works

- **Dynamic, assignment-specific rubrics.** For each assignment, the system reads its instructions and structure to generate a grading rubric out of 10 points, tailored to that specific task. The rubric is generated once and reused consistently across every student's submission, ensuring fair and uniform grading.
- **Rubrics weighted toward genuine student work.** The system distinguishes instructor-provided starter code from sections requiring student completion, and allocates the majority of the available points to the latter — recognizing correct student-authored logic, completed exercises, and answered questions, wherever they appear in the notebook.
- **Context-aware submission matching.** Each submitted notebook is matched to its corresponding assignment by comparing the substance of the work — the retained instructions and structure a student's edits preserve — giving reliable matching even across multiple assignments in the same session.
- **Consistent, granular scoring.** All scores are expressed in clean 0.5-point increments, both at the rubric level and in final grades, for clarity and consistency across a class.
- **Specific, criterion-level feedback.** Every grade includes a breakdown by rubric criterion — what was awarded, what was possible, and why — giving students clear, actionable feedback rather than a single opaque number.
- **Resilient grading pipeline.** AI requests are served through a dual-provider system (Gemini primary, Groq as an automatic backup), and batch grading runs report progress per student and continue through the full class list even if an individual submission needs attention — keeping instructors informed without interrupting the run.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.13 + FastAPI |
| Database | SQLite (SQLAlchemy ORM) |
| File storage | Local filesystem, structured per-session |
| Auth | JWT (bcrypt password hashing) |
| AI — primary | Gemini API (`gemini-flash-latest` / `gemini-3.1-pro-preview`) |
| AI — backup | Groq API |
| Notebook parsing | `nbformat`, recursive `.zip` extraction |
| Session matching | Fuzzy text matching |
| MCP server | Python MCP SDK |
| Frontend | Next.js *(in development)* |

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

These are seeded automatically for local development. Replace `SECRET_KEY` with a securely generated value before any shared or hosted deployment.

## Project Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Authentication, session management, assignment/submission upload and download, database schema | ✅ Complete |
| 2 | AI grading pipeline — notebook parsing, submission matching, rubric generation, evaluation, feedback, dual-provider AI layer, batch grading | ✅ Complete |
| 3 | Instructor chatbot — natural-language session resolution and live-updating grading runs | ⏳ Upcoming |
| 4 | MCP server — grading pipeline exposed as standardized callable tools | ⏳ Upcoming |
| 5 | Web dashboard for instructors and students | ⏳ Upcoming |
| 6 | Integration testing, polish, and demo preparation | ⏳ Upcoming |

## Contributors

- Zeenat Mustafa
- Laiba Afreen