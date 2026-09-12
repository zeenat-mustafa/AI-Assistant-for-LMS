"""
Migration tests for b0bae8c363e6 (add embedded/embedding_error to
lecture_chunks and unsolved_files) — Phase 7, Sub-feature 7.2.

First migration test file in this project (no prior precedent to mirror) —
runs the real `alembic` CLI as a subprocess against a scratch SQLite copy of
the real dev DB (made via sqlite3's own backup API, never against the real
lms.db directly), confirming both the forward migration's schema/defaults
and a real, reversible downgrade().

Run with:
    cd backend
    python -m pytest tests/test_migration_embedding_fields.py -v
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REAL_DB_PATH = BACKEND_DIR / "lms.db"
PRIOR_HEAD = "e15a0ce634e1"  # 7.1's head, immediately before this migration
THIS_REVISION = "b0bae8c363e6"


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


def _table_info(db_path: Path, table: str) -> dict[str, tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    # name -> (type, notnull, dflt_value)
    return {row[1]: (row[2], row[3], row[4]) for row in rows}


@pytest.fixture()
def scratch_db_from_real(tmp_path):
    """
    A real backup-API copy of the actual dev DB (backend/lms.db), at its
    current real head. Tests run alembic against THIS file only — the real
    lms.db is never opened for writing by this test suite.
    """
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


def _current_heads(db_path: Path) -> str:
    result = _run_alembic("current", db_path=db_path)
    assert result.returncode == 0, result.stderr
    return result.stdout


class TestUpgradeAddsTrackingColumns:

    def test_fresh_db_upgrade_head_adds_embedded_and_embedding_error(self, tmp_path):
        """On an empty DB, upgrading through the full chain (including this
        migration) must leave both tables with the two new columns, NOT NULL
        embedded defaulting to false, nullable embedding_error."""
        fresh_db = tmp_path / "fresh.db"
        # Pinned to THIS_REVISION, not "head" — later migrations (7.4+) move
        # head forward, and this test is only about this one migration.
        result = _run_alembic("upgrade", THIS_REVISION, db_path=fresh_db)
        assert result.returncode == 0, result.stderr
        assert THIS_REVISION in _current_heads(fresh_db)

        lecture_chunks_cols = _table_info(fresh_db, "lecture_chunks")
        unsolved_files_cols = _table_info(fresh_db, "unsolved_files")

        for cols, table in ((lecture_chunks_cols, "lecture_chunks"), (unsolved_files_cols, "unsolved_files")):
            assert "embedded" in cols, table
            _type, notnull, default = cols["embedded"]
            assert notnull == 1, f"{table}.embedded should be NOT NULL"
            assert default == "0", f"{table}.embedded should default to false"

            assert "embedding_error" in cols, table
            _type, notnull, _default = cols["embedding_error"]
            assert notnull == 0, f"{table}.embedding_error should be nullable"


class TestDowngradeIsReversible:

    def test_downgrade_then_reupgrade_on_real_db_copy(self, scratch_db_from_real):
        """
        Real downgrade() tested on a scratch copy of the actual dev DB (via
        sqlite3's backup API — never the real lms.db), confirmed reversible:
        downgrade removes exactly the two columns per table with no other
        side effects, and re-upgrading restores them cleanly.
        """
        db = scratch_db_from_real

        # The real DB may already be past this revision (7.4+); step the
        # scratch copy to exactly THIS_REVISION first so "-1" means this one.
        result = _run_alembic("downgrade", THIS_REVISION, db_path=db)
        assert result.returncode == 0, result.stderr

        before_lc = _table_info(db, "lecture_chunks")
        before_uf = _table_info(db, "unsolved_files")
        assert "embedded" in before_lc and "embedding_error" in before_lc
        assert "embedded" in before_uf and "embedding_error" in before_uf

        # Preserve a real row's other data to confirm downgrade/upgrade never
        # touches unrelated columns or rows.
        conn = sqlite3.connect(str(db))
        try:
            lecture_chunk_count_before = conn.execute("SELECT COUNT(*) FROM lecture_chunks").fetchone()[0]
            unsolved_count_before = conn.execute("SELECT COUNT(*) FROM unsolved_files").fetchone()[0]
        finally:
            conn.close()

        result = _run_alembic("downgrade", "-1", db_path=db)
        assert result.returncode == 0, result.stderr
        assert PRIOR_HEAD in _current_heads(db)

        after_downgrade_lc = _table_info(db, "lecture_chunks")
        after_downgrade_uf = _table_info(db, "unsolved_files")
        assert "embedded" not in after_downgrade_lc
        assert "embedding_error" not in after_downgrade_lc
        assert "embedded" not in after_downgrade_uf
        assert "embedding_error" not in after_downgrade_uf

        conn = sqlite3.connect(str(db))
        try:
            assert conn.execute("SELECT COUNT(*) FROM lecture_chunks").fetchone()[0] == lecture_chunk_count_before
            assert conn.execute("SELECT COUNT(*) FROM unsolved_files").fetchone()[0] == unsolved_count_before
        finally:
            conn.close()

        # Re-upgrade: full round trip must restore both columns cleanly.
        result = _run_alembic("upgrade", THIS_REVISION, db_path=db)
        assert result.returncode == 0, result.stderr
        assert THIS_REVISION in _current_heads(db)

        after_reupgrade_lc = _table_info(db, "lecture_chunks")
        after_reupgrade_uf = _table_info(db, "unsolved_files")
        assert "embedded" in after_reupgrade_lc
        assert "embedding_error" in after_reupgrade_lc
        assert "embedded" in after_reupgrade_uf
        assert "embedding_error" in after_reupgrade_uf
