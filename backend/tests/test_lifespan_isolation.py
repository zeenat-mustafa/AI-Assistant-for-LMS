"""
Guards tests/conftest.py's lifespan isolation — the app's startup
create_all() + demo-user seeding must never run against the real lms.db
during tests.

Both tests check isolation BEFORE starting the app, so if the conftest
fixture is ever removed or broken they fail without touching the real DB
(confirm with `python -m pytest tests/test_lifespan_isolation.py --noconftest`).

Run with:
    cd backend
    python -m pytest tests/test_lifespan_isolation.py -v
"""

from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, Table, inspect

from app.config import settings
from app.database import Base
from app.database import engine as real_engine
from app.models.user import User


def _is_in_memory(engine) -> bool:
    return engine.url.database in (None, "", ":memory:")


def test_lifespan_uses_an_isolated_in_memory_engine():
    import app.main as app_main

    assert app_main.engine is not real_engine
    assert _is_in_memory(app_main.engine), f"lifespan engine points at {app_main.engine.url}"
    assert str(app_main.engine.url) != settings.database_url
    assert app_main.SessionLocal.kw["bind"] is app_main.engine


def test_lifespan_create_all_and_seeding_land_in_the_isolated_engine():
    import app.main as app_main

    # Safety gate: never start the app unless the lifespan is already
    # isolated — otherwise the probe table below would be created on lms.db.
    assert _is_in_memory(app_main.engine), f"refusing to start app: lifespan engine is {app_main.engine.url}"

    probe = Table("_lifespan_isolation_probe", Base.metadata, Column("id", Integer, primary_key=True))
    try:
        with TestClient(app_main.app):
            table_names = inspect(app_main.engine).get_table_names()
            # create_all ran here: a table that exists only in metadata, plus a real model table.
            assert "_lifespan_isolation_probe" in table_names
            assert "conversation_threads" in table_names

            # seed_demo_users ran here too.
            with app_main.SessionLocal() as session:
                emails = {user.email for user in session.query(User)}
            assert "student@demo.com" in emails
            assert "instructor@demo.com" in emails
    finally:
        Base.metadata.remove(probe)
