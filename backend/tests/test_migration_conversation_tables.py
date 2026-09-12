"""
Migration tests for 9d9376585d4a (add conversation_threads and
conversation_messages) — Phase 7, Sub-feature 7.4.

Mirrors 7.2's test_migration_embedding_fields.py: runs the real `alembic`
CLI as a subprocess against a scratch SQLite copy of the real dev DB (made via
sqlite3's backup API — never the real lms.db directly), confirming the
forward migration's schema and a real, reversible downgrade().

Run with:
    cd backend
    python -m pytest tests/test_migration_conversation_tables.py -v
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REAL_DB_PATH = BACKEND_DIR / "lms.db"
PRIOR_HEAD = "b0bae8c363e6"  # 7.2's head, immediately before this migration
THIS_REVISION = "9d9376585d4a"
NEW_TABLES = ("conversation_threads", "conversation_messages")


def _run_alembic(*args: str, db_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )


def _query(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _tables(db_path: Path) -> set[str]:
    return {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}


def _table_info(db_path: Path, table: str) -> dict[str, tuple]:
    # name -> (type, notnull, dflt_value)
    return {r[1]: (r[2], r[3], r[4]) for r in _query(db_path, f"PRAGMA table_info({table})")}


def _row_counts(db_path: Path) -> dict[str, int]:
    return {
        t: _query(db_path, f'SELECT COUNT(*) FROM "{t}"')[0][0]
        for t in _tables(db_path)
        if t not in NEW_TABLES and t != "alembic_version"
    }


def _current_heads(db_path: Path) -> str:
    result = _run_alembic("current", db_path=db_path)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture()
def scratch_db_from_real(tmp_path):
    """A real backup-API copy of backend/lms.db. The real lms.db is never
    opened for writing by this suite."""
    if not REAL_DB_PATH.exists():
        pytest.skip("backend/lms.db not present in this environment")

    scratch = tmp_path / "scratch_lms.db"
    src = sqlite3.connect(str(REAL_DB_PATH))
    dst = sqlite3.connect(str(scratch))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return scratch


class TestUpgradeCreatesConversationTables:

    def test_fresh_db_upgrade_head_creates_both_tables(self, tmp_path):
        fresh_db = tmp_path / "fresh.db"
        result = _run_alembic("upgrade", "head", db_path=fresh_db)
        assert result.returncode == 0, result.stderr
        assert THIS_REVISION in _current_heads(fresh_db)

        threads = _table_info(fresh_db, "conversation_threads")
        assert set(threads) == {
            "id", "student_id", "lms_session_id", "rolling_summary",
            "summarized_through_message_id", "created_at", "updated_at",
        }
        assert threads["rolling_summary"][1] == 0, "rolling_summary must be nullable"
        assert threads["summarized_through_message_id"][1] == 0
        assert threads["student_id"][1] == 1
        assert threads["lms_session_id"][1] == 1

        messages = _table_info(fresh_db, "conversation_messages")
        assert set(messages) == {"id", "thread_id", "role", "content", "created_at"}
        assert messages["content"][1] == 1

        fks = {(r[2], r[3], r[6]) for r in _query(fresh_db, "PRAGMA foreign_key_list(conversation_messages)")}
        assert ("conversation_threads", "thread_id", "CASCADE") in fks
        thread_fks = {(r[2], r[3], r[6]) for r in _query(fresh_db, "PRAGMA foreign_key_list(conversation_threads)")}
        assert ("users", "student_id", "CASCADE") in thread_fks
        assert ("lms_sessions", "lms_session_id", "CASCADE") in thread_fks

    def test_unique_student_lms_session_enforced(self, tmp_path):
        fresh_db = tmp_path / "fresh.db"
        assert _run_alembic("upgrade", "head", db_path=fresh_db).returncode == 0

        conn = sqlite3.connect(str(fresh_db))
        try:
            conn.execute(
                "INSERT INTO users (id, name, email, hashed_password, role, created_at) "
                "VALUES (1, 's', 's@x.com', 'h', 'student', '2026-09-13')"
            )
            conn.execute(
                "INSERT INTO lms_sessions (id, title, created_at) VALUES (1, 'Week 1 Day 1', '2026-09-13')"
            )
            insert = (
                "INSERT INTO conversation_threads (student_id, lms_session_id, created_at, updated_at) "
                "VALUES (1, 1, '2026-09-13', '2026-09-13')"
            )
            conn.execute(insert)
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(insert)
        finally:
            conn.close()


class TestDowngradeIsReversible:

    def test_downgrade_then_reupgrade_on_real_db_copy(self, scratch_db_from_real):
        db = scratch_db_from_real

        # The scratch copy starts at whatever the real DB is at; bring it to
        # this revision first (a no-op if the real DB is already upgraded).
        assert _run_alembic("upgrade", "head", db_path=db).returncode == 0
        assert set(NEW_TABLES) <= _tables(db)
        counts_before = _row_counts(db)

        result = _run_alembic("downgrade", "-1", db_path=db)
        assert result.returncode == 0, result.stderr
        assert PRIOR_HEAD in _current_heads(db)
        assert not (set(NEW_TABLES) & _tables(db))
        assert _row_counts(db) == counts_before

        result = _run_alembic("upgrade", "head", db_path=db)
        assert result.returncode == 0, result.stderr
        assert THIS_REVISION in _current_heads(db)
        assert set(NEW_TABLES) <= _tables(db)
        assert _row_counts(db) == counts_before
