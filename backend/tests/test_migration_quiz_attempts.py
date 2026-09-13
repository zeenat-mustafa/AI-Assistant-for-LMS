"""
Migration tests for af5038c37bc4 (add quiz_attempts) — Phase 7, Sub-feature 7.6.

Mirrors 7.4's test_migration_conversation_tables.py: runs the real `alembic`
CLI as a subprocess against a fresh DB and a scratch SQLite copy of the real
dev DB (made via sqlite3's backup API — never the real lms.db directly),
confirming the forward migration's schema and a real, reversible downgrade().
Pinned to THIS_REVISION throughout, never "head", so later migrations can't
break it.

Run with:
    cd backend
    python -m pytest tests/test_migration_quiz_attempts.py -v
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REAL_DB_PATH = BACKEND_DIR / "lms.db"
PRIOR_HEAD = "9d9376585d4a"  # 7.4's head, immediately before this migration
THIS_REVISION = "af5038c37bc4"
NEW_TABLE = "quiz_attempts"


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
        if t not in (NEW_TABLE, "alembic_version")
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


class TestUpgradeCreatesQuizAttempts:

    def test_fresh_db_upgrade_creates_quiz_attempts_matching_the_model(self, tmp_path):
        fresh_db = tmp_path / "fresh.db"
        result = _run_alembic("upgrade", THIS_REVISION, db_path=fresh_db)
        assert result.returncode == 0, result.stderr
        assert THIS_REVISION in _current_heads(fresh_db)

        columns = _table_info(fresh_db, NEW_TABLE)
        assert {name: (col_type, notnull) for name, (col_type, notnull, _) in columns.items()} == {
            "id": ("INTEGER", 1),
            "student_id": ("INTEGER", 1),
            "scope_type": ("VARCHAR(32)", 1),
            "scope_detail": ("TEXT", 1),
            "questions_json": ("TEXT", 1),
            "student_answers_json": ("TEXT", 0),
            "score": ("INTEGER", 0),
            "max_score": ("INTEGER", 1),
            "created_at": ("DATETIME", 1),
            "submitted_at": ("DATETIME", 0),
        }

        fks = {(r[2], r[3], r[6]) for r in _query(fresh_db, f"PRAGMA foreign_key_list({NEW_TABLE})")}
        assert fks == {("users", "student_id", "CASCADE")}

        indexes = {r[1] for r in _query(fresh_db, f"PRAGMA index_list({NEW_TABLE})")}
        assert {"ix_quiz_attempts_id", "ix_quiz_attempts_student_id"} <= indexes

    def test_migration_does_not_touch_the_grades_table_schema(self, tmp_path):
        fresh_db = tmp_path / "fresh.db"
        assert _run_alembic("upgrade", PRIOR_HEAD, db_path=fresh_db).returncode == 0
        grades_before = _query(fresh_db, "SELECT sql FROM sqlite_master WHERE tbl_name='grades'")
        assert _run_alembic("upgrade", THIS_REVISION, db_path=fresh_db).returncode == 0
        assert _query(fresh_db, "SELECT sql FROM sqlite_master WHERE tbl_name='grades'") == grades_before


class TestDowngradeIsReversible:

    def test_downgrade_then_reupgrade_on_real_db_copy(self, scratch_db_from_real):
        db = scratch_db_from_real

        # Bring the scratch copy to exactly this revision — up if the real DB
        # is behind, down if a later migration has already been applied.
        assert _run_alembic("upgrade", THIS_REVISION, db_path=db).returncode == 0
        result = _run_alembic("downgrade", THIS_REVISION, db_path=db)
        assert result.returncode == 0, result.stderr
        assert THIS_REVISION in _current_heads(db)
        assert NEW_TABLE in _tables(db)
        counts_before = _row_counts(db)

        result = _run_alembic("downgrade", "-1", db_path=db)
        assert result.returncode == 0, result.stderr
        assert PRIOR_HEAD in _current_heads(db)
        assert NEW_TABLE not in _tables(db)
        assert _row_counts(db) == counts_before

        result = _run_alembic("upgrade", THIS_REVISION, db_path=db)
        assert result.returncode == 0, result.stderr
        assert THIS_REVISION in _current_heads(db)
        assert NEW_TABLE in _tables(db)
        assert _row_counts(db) == counts_before
