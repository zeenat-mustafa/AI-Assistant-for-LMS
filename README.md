# AI Assistant for LMS — Automated Grading & Feedback

An instructor-directed AI grading assistant for Jupyter notebook assignments, built as a capstone project for the Purelogics Bootcamp.

Instructors create grading sessions, upload unsolved assignment notebooks, and trigger grading via a natural-language chatbot. The AI pipeline matches each student's submission to the correct assignment, generates a rubric, evaluates the work, and writes specific per-student feedback — all on demand.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.13 + FastAPI |
| Database | SQLite (SQLAlchemy ORM) |
| File storage | Local filesystem (storage abstraction — swappable) |
| Auth | JWT (python-jose + bcrypt) |
| LLM — primary | Gemini API (`gemini-flash-latest` / `gemini-3.1-pro-preview`) |
| LLM — fallback | Groq API (auto-fallback on Gemini quota errors) |
| Notebook handling | nbformat + recursive zip extraction |
| Session matching | rapidfuzz fuzzy match |
| MCP server | Python MCP SDK |
| Frontend | Next.js (Phase 5 — not yet built) |

---

## Setup

### 1. Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env   # Linux/macOS
# or: Copy-Item .env.example .env   (PowerShell)
```

Open `.env` and fill in:
- `GEMINI_API_KEY` — from [Google AI Studio](https://aistudio.google.com/app/apikey)
- `GROQ_API_KEY` — from [Groq console](https://console.groq.com/keys)
- `SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`

Everything else can stay as the defaults for local development.

### 3. Run the server

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The server will automatically:
- Create the SQLite database and all tables on first run
- Seed two demo users: `instructor@demo.com` / `instructor123` and `student@demo.com` / `student123`

Interactive API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Project Status

| Phase | Description | Status |
|---|---|---|
| 1 | Auth (JWT), Session CRUD, assignment upload/download, student submission upload, DB schema | ✅ Complete |
| 2 | AI grading pipeline: notebook parsing, zip extraction, rubric generation, evaluation, feedback + rationale, LLM provider abstraction (Gemini + Groq fallback) | 🔄 In progress |
| 3 | Instructor chatbot — natural-language instruction → session matching → grading run with live SSE progress updates | ⏳ Upcoming |
| 4 | MCP server — chatbot + grading agent exposed as callable MCP tools | ⏳ Upcoming |
| 5 | Minimal Next.js dashboard — two-role UI (instructor + student) | ⏳ Upcoming |

---

## Roles & Demo Users

| Role | Email | Password |
|---|---|---|
| Instructor | instructor@demo.com | instructor123 |
| Student | student@demo.com | student123 |

> These are seeded automatically on first server start. For any shared or hosted deployment, replace `SECRET_KEY` with a real random value.
