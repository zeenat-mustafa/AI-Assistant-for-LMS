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

from app.routers import auth, sessions, assignments, submissions, grades, chat, lectures, student_chat, quiz
from app.services.auth import seed_demo_users

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Warmup ────────────────────────────────────────────────────────────────────

async def _warmup():
    """
    Warm up models and API connections at startup to reduce first-request latency.

    This runs once during server startup, after database setup but before serving
    requests. It:
    1. Loads the embedding model (sentence-transformers) — normally lazy-loaded
       on first use, moving that cost to startup instead.
    2. Fires a single throwaway call to the primary Gemini API to establish the
       TLS connection and warm up the client, discarding the result.

    Both operations are logged clearly as startup warmup, not real usage. Any
    failure here is logged but does not prevent the server from starting — the
    first real request will retry and succeed (or fail with a proper error).
    """
    import asyncio

    # ── Embedding model ───────────────────────────────────────────────────────
    def _load_embedding_model():
        """Load the sentence-transformers model synchronously."""
        from app.services.embeddings import _get_model
        logger.info("[warmup] Loading embedding model (first use moved to startup)...")
        try:
            model = _get_model()
            logger.info(
                "[warmup] Embedding model loaded: %s",
                model.__class__.__name__,
            )
        except Exception as exc:
            logger.warning(
                "[warmup] Embedding model load failed (will retry on first real use): %s",
                exc,
            )

    # ── Gemini API connection ─────────────────────────────────────────────────
    def _warmup_gemini():
        """Fire a single cheap call to Gemini to warm up the TLS connection."""
        from app.services.llm_provider import call_llm, LLMProviderError
        logger.info("[warmup] Warming up Gemini API connection...")
        try:
            # Single-word prompt, result discarded — this is purely to establish
            # the network/TLS connection on the client so the first real request
            # doesn't pay that cost.
            _ = call_llm("Hi", purpose="startup_warmup")
            logger.info("[warmup] Gemini API connection established.")
        except LLMProviderError as exc:
            logger.warning(
                "[warmup] Gemini warmup failed (both models unavailable): %s",
                exc,
            )
        except Exception as exc:
            logger.warning(
                "[warmup] Gemini warmup failed (will retry on first real use): %s",
                exc,
            )

    # Run both synchronous warmup steps in the asyncio executor so they don't
    # block other async tasks during startup. They run in parallel for speed.
    await asyncio.gather(
        asyncio.to_thread(_load_embedding_model),
        asyncio.to_thread(_warmup_gemini),
    )


# ── Lifespan (replaces deprecated @app.on_event) ─────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    # create_all() only ever matters for a brand-new, empty database (fresh
    # local setup convenience) -- it CANNOT alter tables that already exist.
    # Any schema change to an existing DB must go through an Alembic
    # migration (see README.md "Database Migrations"); never rely on this
    # call to apply it.
    logger.info("Creating database tables…")
    from app.database import Base
    Base.metadata.create_all(bind=engine)

    logger.info("Seeding demo users…")
    db = SessionLocal()
    try:
        seed_demo_users(db)
    finally:
        db.close()

    logger.info("Warming up models and API connections…")
    await _warmup()

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
app.include_router(lectures.router,    prefix=API_PREFIX)
app.include_router(submissions.router, prefix=API_PREFIX)
app.include_router(grades.router,      prefix=API_PREFIX)
app.include_router(chat.router,        prefix=API_PREFIX)
app.include_router(student_chat.router, prefix=API_PREFIX)
app.include_router(quiz.router,        prefix=API_PREFIX)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"], summary="Health check")
def health() -> dict:
    return {"status": "ok", "version": app.version}
