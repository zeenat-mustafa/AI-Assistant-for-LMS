"""
Tests for POST/GET/DELETE /api/v1/sessions/{session_id}/assignments.

bugfix-original-upload-preservation: every upload now creates exactly one
AssignmentUpload row per file submitted — a zip is one row with its own
filename, never a list of what's inside it. The response is
list[AssignmentUploadRead]; downloading an upload returns the original bytes
byte-for-byte; deleting one cascades to whatever it produced internally.

Notebook/resource extraction for the grading pipeline is UNCHANGED — a zip
still explodes into UnsolvedFile (gradeable) and ResourceFile (download-only,
historical) rows exactly as before. Those rows are simply no longer
independently listed, downloaded, or deleted; tests that verify extraction
itself check the DB directly instead of the old per-piece API responses.

Run with:
    cd backend
    python -m pytest tests/test_assignments.py -v

Cases covered
─────────────
Upload — any file type, one row per upload event
  1.  Single .ipynb → one AssignmentUploadRead; one UnsolvedFile row too
  2.  Multiple files in one call → one AssignmentUploadRead per file
  3.  .zip with notebooks at archive root → ONE upload row; N UnsolvedFile rows
  4.  .zip with notebooks 2+ folders deep → recursive extraction unaffected
  5.  .zip with mixed files → ONE upload row; notebooks + resources extracted internally
  6.  .zip with only resources (no notebooks) → accepted, ONE upload row
  7.  .zip with NOTHING usable inside → still accepted as one upload row (no 422 anymore)
  8.  A bare non-notebook, non-zip file (e.g. .pdf) → accepted directly, no extraction
  9.  Duplicate filename within session → 409 (whole request rejected atomically)
  10. Student cannot upload → 403
  11. Unauthenticated request → 401
  12. Nonexistent session → 404

Grading-pipeline preservation (Option (a)'s whole point)
  13. Resources still excluded from the assignment-count denominator
  14. Resources still never enter the file-matcher's candidate pool
  15. Duplicate guard still spans notebooks/resources/uploads together
  16. parsed_requirements_text still set per extracted notebook

Display / download / delete — the new contract
  17. GET lists AssignmentUploadRead, not extracted pieces
  18. Download returns the original bytes exactly, byte-for-byte
  19. SessionRead exposes assignment_uploads (display) separately from
      unsolved_files/resource_files (internal-only counts)
  20. DELETE removes the upload AND cascades to its extracted notebooks/
      resources, in the DB and on disk
  21. DELETE 404s for a nonexistent id or one from a different session
  22. Access control on download/delete (401/403)
"""

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import nbformat
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.assignment_upload import AssignmentUpload
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.resource_file import ResourceFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token


# ===========================================================================
# Shared helpers
# ===========================================================================

def _make_notebook_bytes(
    markdown_cells: list[str] | None = None,
    code_cells: list[str] | None = None,
) -> bytes:
    """Build a minimal valid .ipynb as bytes."""
    nb = nbformat.v4.new_notebook()
    for md in (markdown_cells or []):
        nb.cells.append(nbformat.v4.new_markdown_cell(md))
    for src in (code_cells or []):
        nb.cells.append(nbformat.v4.new_code_cell(src))
    buf = io.StringIO()
    nbformat.write(nb, buf)
    return buf.getvalue().encode("utf-8")


def _make_zip(members: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP from {archive_path: file_bytes} mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in members.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


# Mock for save_assignment_file — avoids touching the real filesystem.
# Returns a predictable relative path so absolute_path() can be constructed.
async def _mock_save(session_id: int, filename: str, data: bytes) -> str:
    return f"{session_id}/assignments/{filename}"


# Mock for save_original_upload_file — same idea, separate directory.
async def _mock_save_original(session_id: int, filename: str, data: bytes) -> str:
    return f"{session_id}/assignments/originals/{filename}"


def _post(client, token, session_id, *file_tuples):
    """
    Convenience: POST multipart upload to the assignments endpoint.
    file_tuples: one or more (filename, bytes) pairs.
    """
    files = [
        ("files", (name, data, "application/octet-stream"))
        for name, data in file_tuples
    ]
    return client.post(
        f"/api/v1/sessions/{session_id}/assignments",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )


def _post_mocked(client, token, session_id, *file_tuples):
    """Same as _post but with both save helpers mocked (no real disk I/O)."""
    with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save), \
         patch("app.routers.assignments.save_original_upload_file", side_effect=_mock_save_original):
        return _post(client, token, session_id, *file_tuples)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def db():
    """
    In-memory SQLite database wired to the real ORM models.

    Mirrors app/database.py's own connect-time pragma: SQLite does not
    enforce foreign keys (and therefore never runs an ondelete=CASCADE) by
    default, unlike Postgres/MySQL. The real engine turns this on for every
    connection; without doing the same here, this fixture would silently
    under-test any FK cascade (e.g. deleting an AssignmentUpload cascading
    to the UnsolvedFile/ResourceFile rows it produced).
    """
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db):
    """
    TestClient backed by in-memory DB.
    Seeded with:
      - instructor (id=1, email=prof@test.com)
      - student    (id=2, email=stu@test.com)
      - LMS session (id=10, title='Week 1 Day 1')
    Yields (TestClient, instructor_token, student_token).
    """

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    instructor = User(
        id=1, name="Prof Test", email="prof@test.com",
        hashed_password="hash", role=UserRole.instructor,
    )
    student = User(
        id=2, name="Stu Test", email="stu@test.com",
        hashed_password="hash", role=UserRole.student,
    )
    lms_session = LMSSession(id=10, title="Week 1 Day 1")
    db.add_all([instructor, student, lms_session])
    db.commit()

    instructor_token = create_access_token(1, UserRole.instructor)
    student_token = create_access_token(2, UserRole.student)

    with TestClient(app) as c:
        yield c, instructor_token, student_token

    app.dependency_overrides.clear()


# ===========================================================================
# Upload — any file type, one row per upload event
# ===========================================================================

class TestUploadAssignment:

    # ── 1. Single .ipynb ───────────────────────────────────────────────────────

    def test_single_ipynb_creates_one_upload_and_one_notebook(self, client, db):
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW1\nDo task A."])

        res = _post_mocked(c, instr_token, 10, ("hw1.ipynb", nb))

        assert res.status_code == 201
        data = res.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["original_filename"] == "hw1.ipynb"
        assert data[0]["id"] is not None
        assert data[0]["session_id"] == 10
        # No file_role/rubric_generated on the upload response anymore.
        assert "file_role" not in data[0]
        assert "rubric_generated" not in data[0]

        uploads = db.query(AssignmentUpload).filter(AssignmentUpload.session_id == 10).all()
        assert len(uploads) == 1
        assert uploads[0].original_filename == "hw1.ipynb"

        notebooks = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert len(notebooks) == 1
        assert notebooks[0].original_filename == "hw1.ipynb"
        assert notebooks[0].source_upload_id == uploads[0].id

    # ── 2. Multiple files in one multipart call ────────────────────────────────

    def test_multiple_files_in_one_call_creates_one_upload_row_each(self, client, db):
        c, instr_token, _ = client
        nb1 = _make_notebook_bytes(markdown_cells=["# Lab 1"])
        nb2 = _make_notebook_bytes(markdown_cells=["# Lab 2"])

        res = _post_mocked(c, instr_token, 10, ("lab1.ipynb", nb1), ("lab2.ipynb", nb2))

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 2
        returned_names = {item["original_filename"] for item in data}
        assert returned_names == {"lab1.ipynb", "lab2.ipynb"}

        assert db.query(AssignmentUpload).filter(AssignmentUpload.session_id == 10).count() == 2
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count() == 2

    # ── 3. .zip with notebooks at archive root → ONE upload row ───────────────

    def test_zip_flat_is_one_upload_row_but_extracts_all_notebooks(self, client, db):
        c, instr_token, _ = client
        nb1 = _make_notebook_bytes(markdown_cells=["# NB1"])
        nb2 = _make_notebook_bytes(markdown_cells=["# NB2"])
        zip_bytes = _make_zip({"notebook1.ipynb": nb1, "notebook2.ipynb": nb2})

        res = _post_mocked(c, instr_token, 10, ("labs.zip", zip_bytes))

        assert res.status_code == 201
        data = res.json()
        # ONE row for the zip itself — not one per notebook inside it.
        assert len(data) == 1
        assert data[0]["original_filename"] == "labs.zip"

        # But both notebooks were still extracted internally for grading.
        notebooks = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert {n.original_filename for n in notebooks} == {"notebook1.ipynb", "notebook2.ipynb"}
        upload_id = data[0]["id"]
        assert all(n.source_upload_id == upload_id for n in notebooks)

    # ── 4. .zip with nested folders (recursive extraction unaffected) ─────────

    def test_zip_nested_folders_extracted_recursively(self, client, db):
        c, instr_token, _ = client
        nb_top    = _make_notebook_bytes(markdown_cells=["# Top level"])
        nb_deep   = _make_notebook_bytes(markdown_cells=["# One level deep"])
        nb_deeper = _make_notebook_bytes(markdown_cells=["# Three levels deep"])
        zip_bytes = _make_zip({
            "top.ipynb":                         nb_top,
            "subdir/deep.ipynb":                 nb_deep,
            "subdir/level2/level3/deeper.ipynb": nb_deeper,
        })

        res = _post_mocked(c, instr_token, 10, ("nested.zip", zip_bytes))

        assert res.status_code == 201
        assert len(res.json()) == 1  # one upload row for the zip

        notebooks = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        names = {n.original_filename for n in notebooks}
        assert names == {"top.ipynb", "deep.ipynb", "deeper.ipynb"}

    # ── 5. .zip with mixed files ────────────────────────────────────────────────

    def test_zip_mixed_files_is_one_upload_row_extraction_unchanged(self, client, db):
        """The zip displays/downloads as ONE row; internally it still splits
        into a gradeable UnsolvedFile and downloadable ResourceFile rows,
        exactly as bugfix-post-phase5 built it."""
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# Only notebook"])
        zip_bytes = _make_zip({
            "assignment.ipynb": nb,
            "helper.py":        b"def foo(): pass",
            "data.csv":         b"col1,col2\n1,2",
            "README.txt":       b"read me",
        })

        res = _post_mocked(c, instr_token, 10, ("mixed.zip", zip_bytes))

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 1
        assert data[0]["original_filename"] == "mixed.zip"

        notebooks = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert [n.original_filename for n in notebooks] == ["assignment.ipynb"]
        resources = db.query(ResourceFile).filter(ResourceFile.session_id == 10).all()
        assert {r.original_filename for r in resources} == {"helper.py", "data.csv", "README.txt"}
        assert all(r.source_upload_id == data[0]["id"] for r in resources)

    # ── 6. .zip with only resources (no notebooks) → accepted ─────────────────

    def test_zip_with_only_resources_is_accepted_as_one_upload(self, client, db):
        c, instr_token, _ = client
        zip_bytes = _make_zip({"script.py": b"pass", "notes.md": b"# notes"})

        res = _post_mocked(c, instr_token, 10, ("resources_only.zip", zip_bytes))

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 1
        assert data[0]["original_filename"] == "resources_only.zip"
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all() == []
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).count() == 2

    # ── 7. .zip with NOTHING usable inside → still accepted (Fix 5 changes this) ─

    def test_zip_with_nothing_usable_is_still_accepted_as_one_upload(self, client, db):
        """Before this fix, a zip with only skippable entries (__MACOSX
        metadata) 422'd. Now the original zip is always a valid upload in its
        own right, regardless of what extraction finds inside it."""
        c, instr_token, _ = client
        zip_bytes = _make_zip({"__MACOSX/._x": b"junk"})

        res = _post_mocked(c, instr_token, 10, ("empty.zip", zip_bytes))

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 1
        assert data[0]["original_filename"] == "empty.zip"
        # Nothing extracted, but the upload itself is real.
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all() == []
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).all() == []
        assert db.query(AssignmentUpload).filter(AssignmentUpload.session_id == 10).count() == 1

    # ── 8. A bare non-notebook, non-zip file → accepted directly ───────────────

    def test_bare_non_notebook_file_is_accepted_directly(self, client, db):
        """Before this fix this 422'd outright. Any file type must now be
        uploadable directly — it's just an upload with nothing extracted."""
        c, instr_token, _ = client

        res = _post_mocked(c, instr_token, 10, ("notes.pdf", b"%PDF-1.4 fake pdf bytes"))

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 1
        assert data[0]["original_filename"] == "notes.pdf"
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all() == []
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).all() == []
        assert db.query(AssignmentUpload).filter(AssignmentUpload.session_id == 10).count() == 1

    # ── 9. Duplicate filename → 409 (whole request rejected atomically) ────────

    def test_duplicate_filename_returns_409_and_no_partial_write(self, client, db):
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW1"])

        res1 = _post_mocked(c, instr_token, 10, ("hw1.ipynb", nb))
        assert res1.status_code == 201

        res2 = _post_mocked(c, instr_token, 10, ("hw1.ipynb", nb))
        assert res2.status_code == 409
        assert "hw1.ipynb" in res2.json()["detail"]

        assert db.query(AssignmentUpload).filter(AssignmentUpload.session_id == 10).count() == 1
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count() == 1

    def test_multi_file_request_with_one_duplicate_rejected_atomically(self, client, db):
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# Existing"])
        nb_new = _make_notebook_bytes(markdown_cells=["# New"])

        r = _post_mocked(c, instr_token, 10, ("existing.ipynb", nb))
        assert r.status_code == 201

        r2 = _post_mocked(c, instr_token, 10, ("existing.ipynb", nb), ("new.ipynb", nb_new))
        assert r2.status_code == 409
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count() == 1

    def test_duplicate_against_extracted_notebook_name_also_rejected(self, client, db):
        """A direct upload whose name collides with a notebook PREVIOUSLY
        extracted from a zip is still caught — the guard spans notebooks,
        resources, and original-upload filenames together."""
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        zip_bytes = _make_zip({"hw.ipynb": nb})
        r1 = _post_mocked(c, instr_token, 10, ("bundle.zip", zip_bytes))
        assert r1.status_code == 201

        # Direct upload named exactly like the extracted notebook.
        r2 = _post_mocked(c, instr_token, 10, ("hw.ipynb", nb))
        assert r2.status_code == 409

    def test_duplicate_against_original_upload_filename(self, client, db):
        """Re-uploading a file with the exact same name as a PREVIOUS
        original upload (e.g. the same zip name) is rejected too — this is
        new: the old guard never checked the container's own filename."""
        c, instr_token, _ = client
        zip_bytes = _make_zip({"note.txt": b"hello"})
        r1 = _post_mocked(c, instr_token, 10, ("week1.zip", zip_bytes))
        assert r1.status_code == 201

        r2 = _post_mocked(c, instr_token, 10, ("week1.zip", _make_zip({"other.txt": b"bye"})))
        assert r2.status_code == 409
        assert "week1.zip" in r2.json()["detail"]

    # ── 10. Student cannot upload → 403 ───────────────────────────────────────

    def test_student_upload_returns_403(self, client, db):
        c, _, student_token = client
        nb = _make_notebook_bytes()
        res = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("hw.ipynb", nb, "application/octet-stream"))],
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert res.status_code == 403

    # ── 11. Unauthenticated → 401 ──────────────────────────────────────────────

    def test_unauthenticated_returns_401(self, client, db):
        c, _, _ = client
        nb = _make_notebook_bytes()
        res = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("hw.ipynb", nb, "application/octet-stream"))],
        )
        assert res.status_code == 401

    # ── 12. Nonexistent session → 404 ──────────────────────────────────────────

    def test_nonexistent_session_returns_404(self, client, db):
        c, instr_token, _ = client
        nb = _make_notebook_bytes()
        res = c.post(
            "/api/v1/sessions/999/assignments",
            files=[("files", ("hw.ipynb", nb, "application/octet-stream"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 404


# ===========================================================================
# Notebook-cell embedding (Phase 7.2) — synchronous, same request as upload,
# never blocks the upload's own success. Every other test in this file mocks
# save_assignment_file to a fake (non-existent) path, so extract_notebook_
# structure naturally fails fast with "file not found" and upsert_chunk is
# never reached — safe, but no coverage of the success path. These tests use
# real (unmocked) storage via tmp_path so embedding genuinely runs, with
# upsert_chunk itself mocked to avoid touching the real model/Chroma store.
# ===========================================================================

class TestNotebookEmbedding:

    def test_successful_upload_embeds_every_non_blank_cell(self, client, db, tmp_path):
        c, instr_token, _ = client
        nb = _make_notebook_bytes(
            markdown_cells=["# HW1\nDo task A.", "   "],  # second cell is blank
            code_cells=["print('hello')", "# TODO: your code here"],
        )

        with patch("app.services.storage._storage_root", return_value=tmp_path), \
             patch("app.routers.assignments.upsert_chunk") as mock_upsert:
            res = _post(c, instr_token, 10, ("hw1.ipynb", nb))

        assert res.status_code == 201
        notebook = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).one()
        assert notebook.embedded is True
        assert notebook.embedding_error is None

        # 4 cells total, 1 blank markdown cell skipped -> 3 embedded.
        assert mock_upsert.call_count == 3
        call_ids = {call.kwargs["chunk_id"] for call in mock_upsert.call_args_list}
        assert call_ids == {
            f"notebook:{notebook.id}:0",
            f"notebook:{notebook.id}:2",
            f"notebook:{notebook.id}:3",
        }
        first_call = mock_upsert.call_args_list[0]
        assert first_call.kwargs["metadata"] == {
            "source_type": "notebook", "source_file_id": notebook.id,
            "session_id": 10, "cell_index": 0, "cell_type": "markdown",
        }

    def test_embedding_failure_marks_file_failed_but_upload_still_succeeds(self, client, db, tmp_path):
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW1\nDo task A."])

        with patch("app.services.storage._storage_root", return_value=tmp_path), \
             patch("app.routers.assignments.upsert_chunk", side_effect=RuntimeError("chroma unavailable")):
            res = _post(c, instr_token, 10, ("hw1.ipynb", nb))

        assert res.status_code == 201  # upload itself still succeeds
        notebook = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).one()
        assert notebook.embedded is False
        assert "chroma unavailable" in notebook.embedding_error


# ===========================================================================
# Grading-pipeline preservation — Option (a)'s whole point: none of this
# changed. These assert on the DB state that file_matcher.py/grading_pipeline
# actually read, independent of the display-facing API response shape.
# ===========================================================================

class TestGradingPipelineUnaffected:

    def test_resources_still_excluded_from_assignment_count(self, client, db):
        from app.routers.grades import _get_total_assignment_count

        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        zip_bytes = _make_zip({"hw.ipynb": nb, "dataset.csv": b"a,b\n1,2"})
        res = _post_mocked(c, instr_token, 10, ("bundle.zip", zip_bytes))
        assert res.status_code == 201

        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count() == 1
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).count() == 1
        assert _get_total_assignment_count(10, db) == 1

    def test_resource_still_never_in_match_candidate_pool(self, client, db):
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        zip_bytes = _make_zip({"hw.ipynb": nb, "notes.pdf": b"%PDF-1.4 fake"})
        _post_mocked(c, instr_token, 10, ("bundle.zip", zip_bytes))

        candidates = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert [cand.original_filename for cand in candidates] == ["hw.ipynb"]

    def test_duplicate_across_extraction_tables_still_atomic(self, client, db):
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])

        first = _post_mocked(c, instr_token, 10, ("r.zip", _make_zip({"data.csv": b"a,b"})))
        assert first.status_code == 201
        nb_before = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count()
        res_before = db.query(ResourceFile).filter(ResourceFile.session_id == 10).count()
        upload_before = db.query(AssignmentUpload).filter(AssignmentUpload.session_id == 10).count()

        clash = _make_zip({"new.ipynb": nb, "data.csv": b"x,y"})
        res = _post_mocked(c, instr_token, 10, ("clash.zip", clash))
        assert res.status_code == 409
        assert "data.csv" in res.json()["detail"]
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count() == nb_before
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).count() == res_before
        assert db.query(AssignmentUpload).filter(AssignmentUpload.session_id == 10).count() == upload_before

    def test_parsed_requirements_text_still_stored_per_notebook(self, client, db):
        c, instr_token, _ = client
        nb1 = _make_notebook_bytes(markdown_cells=["# Task 1\nImplement linear regression."])
        nb2 = _make_notebook_bytes(markdown_cells=["# Task 2\nImplement logistic regression."])

        res = _post_mocked(c, instr_token, 10, ("hw1.ipynb", nb1), ("hw2.ipynb", nb2))
        assert res.status_code == 201

        rows = (
            db.query(UnsolvedFile)
            .filter(UnsolvedFile.session_id == 10)
            .order_by(UnsolvedFile.original_filename)
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.parsed_requirements_text is not None


# ===========================================================================
# Display / download / delete — the new contract
# ===========================================================================

class TestListDownloadDelete:

    def test_list_returns_uploads_not_extracted_pieces(self, client, db):
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        zip_bytes = _make_zip({"hw.ipynb": nb, "data.csv": b"a,b"})
        _post_mocked(c, instr_token, 10, ("bundle.zip", zip_bytes))
        _post_mocked(c, instr_token, 10, ("notes.pdf", b"%PDF fake"))

        listed = c.get(
            "/api/v1/sessions/10/assignments",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert listed.status_code == 200
        names = {item["original_filename"] for item in listed.json()}
        # Exactly the two things uploaded — never "hw.ipynb" or "data.csv".
        assert names == {"bundle.zip", "notes.pdf"}

    def test_download_returns_original_bytes_exactly(self, client, db):
        """Real save (not mocked) so there's an actual file on disk, and the
        original zip's bytes — not a reconstruction — come back untouched."""
        c, instr_token, _ = client
        original_bytes = _make_zip({"hw.ipynb": _make_notebook_bytes(), "data.csv": b"a,b\n1,2"})

        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("bundle.zip", original_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert upload.status_code == 201
        upload_id = upload.json()[0]["id"]

        dl = c.get(
            f"/api/v1/sessions/10/assignments/{upload_id}/download",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert dl.status_code == 200
        # Byte-for-byte identical to what was uploaded — not a re-zip of parts.
        assert dl.content == original_bytes

    def test_download_of_bare_non_notebook_file_returns_its_own_bytes(self, client, db):
        c, instr_token, _ = client
        pdf_bytes = b"%PDF-1.4 totally real pdf content"

        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("notes.pdf", pdf_bytes, "application/pdf"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert upload.status_code == 201
        upload_id = upload.json()[0]["id"]

        dl = c.get(
            f"/api/v1/sessions/10/assignments/{upload_id}/download",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert dl.status_code == 200
        assert dl.content == pdf_bytes

    def test_session_detail_exposes_uploads_separately_from_internal_counts(self, client, db):
        """assignment_uploads is the display list; unsolved_files/
        resource_files still ride along on SessionRead too, but only for
        internal count purposes (e.g. totalAssignmentFiles) — not for a
        separate UI listing anymore."""
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        zip_bytes = _make_zip({"hw.ipynb": nb, "slides.pdf": b"%PDF fake", "data.csv": b"a,b"})
        _post_mocked(c, instr_token, 10, ("bundle.zip", zip_bytes))

        detail = c.get(
            "/api/v1/sessions/10",
            headers={"Authorization": f"Bearer {instr_token}"},
        ).json()
        assert [u["original_filename"] for u in detail["assignment_uploads"]] == ["bundle.zip"]
        assert [f["original_filename"] for f in detail["unsolved_files"]] == ["hw.ipynb"]
        assert {f["original_filename"] for f in detail["resource_files"]} == {"slides.pdf", "data.csv"}

    def test_delete_cascades_to_extracted_notebook_and_its_file_on_disk(self, client, db):
        """Deleting the original upload removes the AssignmentUpload row AND
        the UnsolvedFile it produced, in the DB and on disk — not just the
        original zip."""
        c, instr_token, _ = client
        zip_bytes = _make_zip({"hw.ipynb": _make_notebook_bytes(), "data.csv": b"a,b"})

        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("bundle.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert upload.status_code == 201
        upload_id = upload.json()[0]["id"]

        from app.services.storage import absolute_path
        upload_row = db.query(AssignmentUpload).filter(AssignmentUpload.id == upload_id).one()
        notebook_row = db.query(UnsolvedFile).filter(UnsolvedFile.source_upload_id == upload_id).one()
        resource_row = db.query(ResourceFile).filter(ResourceFile.source_upload_id == upload_id).one()

        original_path = absolute_path(upload_row.file_path)
        notebook_path = absolute_path(notebook_row.file_path)
        resource_path = absolute_path(resource_row.file_path)
        assert original_path.exists() and notebook_path.exists() and resource_path.exists()

        res = c.delete(
            f"/api/v1/sessions/10/assignments/{upload_id}",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 204

        assert db.query(AssignmentUpload).filter(AssignmentUpload.id == upload_id).first() is None
        assert db.query(UnsolvedFile).filter(UnsolvedFile.source_upload_id == upload_id).first() is None
        assert db.query(ResourceFile).filter(ResourceFile.source_upload_id == upload_id).first() is None
        assert not original_path.exists()
        assert not notebook_path.exists()
        assert not resource_path.exists()

    def test_delete_nonexistent_upload_404(self, client, db):
        c, instr_token, _ = client
        res = c.delete(
            "/api/v1/sessions/10/assignments/9999",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 404

    def test_delete_upload_from_wrong_session_404(self, client, db):
        c, instr_token, _ = client
        other_session = LMSSession(id=11, title="Week 2 Day 1")
        db.add(other_session)
        db.commit()

        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("notes.pdf", b"%PDF fake", "application/pdf"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        upload_id = upload.json()[0]["id"]

        res = c.delete(
            f"/api/v1/sessions/11/assignments/{upload_id}",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 404
        assert db.query(AssignmentUpload).filter(AssignmentUpload.id == upload_id).first() is not None

    def test_student_cannot_delete_upload(self, client, db):
        c, instr_token, student_token = client
        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("notes.pdf", b"%PDF fake", "application/pdf"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        upload_id = upload.json()[0]["id"]

        res = c.delete(
            f"/api/v1/sessions/10/assignments/{upload_id}",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert res.status_code == 403
        assert db.query(AssignmentUpload).filter(AssignmentUpload.id == upload_id).first() is not None

    def test_unauthenticated_cannot_delete_upload(self, client, db):
        c, instr_token, _ = client
        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("notes.pdf", b"%PDF fake", "application/pdf"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        upload_id = upload.json()[0]["id"]

        res = c.delete(f"/api/v1/sessions/10/assignments/{upload_id}")
        assert res.status_code == 401
        assert db.query(AssignmentUpload).filter(AssignmentUpload.id == upload_id).first() is not None
