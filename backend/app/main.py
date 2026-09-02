"""
FastAPI application entry point.

Startup sequence
────────────────
1. Create all database tables (idempotent — skips existing tables).
2. Seed the two demo users (idempotent — skips if they already exist).
3. Register all routers under the /api/v1 prefix.

Run with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, SessionLocal
# Import models so SQLAlchemy metadata is populated before create_all().
import app.models  # noqa: F401

from app.routers import auth, sessions, assignments, submissions, grades
from app.services.auth import seed_demo_users

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan (replaces deprecated @app.on_event) ─────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("Creating database tables…")
    from app.database import Base
    Base.metadata.create_all(bind=engine)

    logger.info("Seeding demo users…")
    db = SessionLocal()
    try:
        seed_demo_users(db)
    finally:
        db.close()

    logger.info("Application ready.")
    yield
    # ── Shutdown (nothing to clean up for SQLite/local FS) ───────────────────
    logger.info("Application shutting down.")


# ── App instance ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Assistant for LMS — Grading API",
    description=(
        "Instructor-directed AI grading assistant for Jupyter notebook assignments. "
        "Instructors create Sessions, upload unsolved files, and trigger grading via "
        "a chatbot. Students upload submissions and view their feedback."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ── CORS ──────────────────────────────────────────────────────────────────────
# Allow the Next.js dev server (port 3000) and any same-origin requests.
# Tighten this to the specific deployed frontend URL in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routers ───────────────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"

app.include_router(auth.router,        prefix=API_PREFIX)
app.include_router(sessions.router,    prefix=API_PREFIX)
app.include_router(assignments.router, prefix=API_PREFIX)
app.include_router(submissions.router, prefix=API_PREFIX)
app.include_router(grades.router,      prefix=API_PREFIX)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"], summary="Health check")
def health() -> dict:
    return {"status": "ok", "version": app.version}
