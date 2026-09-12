"""
Shared pytest configuration for the backend test suite.

Isolates app.main's lifespan from the real dev database.

app/main.py's lifespan runs Base.metadata.create_all(bind=engine) and
seed_demo_users(SessionLocal()) against the module-level engine/SessionLocal
it imports from app.database — i.e. the REAL backend/lms.db. Every test that
enters `with TestClient(app)` triggers that lifespan; overriding get_db only
redirects request handlers, not startup. So before this fixture existed, a
plain `pytest` run after adding a new model (but before `alembic upgrade
head`) silently created the new tables on the live DB while alembic_version
stayed behind, breaking the migration (found in Phase 7.4, 2026-09-13).

Fix: for the whole test session, point app.main's two module-level names at
a throwaway in-memory engine. The lifespan still genuinely runs (create_all
+ idempotent seeding), just never against lms.db. No production code
changes; the real startup path is untouched.

Scope note: this covers the lifespan only. Code that opens
app.database.SessionLocal directly (the MCP tools) is already patched per
test. tests/test_lifespan_isolation.py guards this fixture.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="session", autouse=True)
def _isolate_app_lifespan_from_real_db():
    # Imported here, not at module top, so conftest loading doesn't change
    # when app.main (and everything it imports) is first imported.
    import app.main as app_main

    isolated_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    isolated_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=isolated_engine)

    patcher = pytest.MonkeyPatch()
    patcher.setattr(app_main, "engine", isolated_engine)
    patcher.setattr(app_main, "SessionLocal", isolated_session_factory)
    try:
        yield isolated_engine
    finally:
        patcher.undo()
        isolated_engine.dispose()
