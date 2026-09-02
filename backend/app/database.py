from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from pathlib import Path

from app.config import settings


# ── Engine ────────────────────────────────────────────────────────────────────
# connect_args is SQLite-specific: enables WAL mode for concurrent reads during
# a grading run, and check_same_thread=False is required by FastAPI's thread model.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,  # set True temporarily to debug SQL queries
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record):
    """Enable WAL journal mode and foreign key enforcement on every connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ── Session factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Base class for all ORM models ─────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency for FastAPI route handlers ─────────────────────────────────────
def get_db():
    """Yield a database session and ensure it is closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
