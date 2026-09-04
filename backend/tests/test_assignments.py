"""
Tests for the updated POST /api/v1/sessions/{session_id}/assignments endpoint.

Phase 1/2 fix: the endpoint now accepts one .ipynb, multiple .ipynb files in
a single multipart request, or a .zip archive (recursively extracted via the
same extract_notebooks_from_zip already used for student submissions).
The response is always list[UnsolvedFileRead].

Run with:
    cd backend
    python -m pytest tests/test_assignments.py -v

Cases covered
─────────────
Upload behaviour
  1.  Single .ipynb → list with exactly one UnsolvedFileRead (backward compat)
  2.  Multiple .ipynb files in one call → list with all records created
  3.  .zip with notebooks at archive root → all extracted and returned
  4.  .zip with notebooks 2+ folders deep → recursive extraction
  5.  .zip with mixed files (non-.ipynb ignored) → only notebooks returned
  6.  .zip with no .ipynb files → 422 with clear message
  7.  Duplicate filename within session → 409 (whole request rejected atomically)
  8.  Invalid extension (.py) → 422
  9.  Student cannot upload → 403
  10. Unauthenticated request → 401
  11. Nonexistent session → 404

DB / response correctness
  12. Each created UnsolvedFile row has parsed_requirements_text set
  13. created_count rows appear in DB after a multi-file upload
  14. Response list order and field values match what's in the DB
"""

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import nbformat
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
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


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def db():
    """In-memory SQLite database wired to the real ORM models."""
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
# Tests
# ===========================================================================

class TestUploadAssignment:

    # ── 1. Single .ipynb (backward compatibility) ─────────────────────────────

    def test_single_ipynb_returns_list_with_one_item(self, client, db):
        """
        A single .ipynb upload now returns a list[UnsolvedFileRead] with one
        element — preserving the same creation logic as before.
        """
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW1\nDo task A."])

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = _post(c, instr_token, 10, ("hw1.ipynb", nb))

        assert res.status_code == 201
        data = res.json()
        assert isinstance(data, list), "Response must be a list"
        assert len(data) == 1
        assert data[0]["original_filename"] == "hw1.ipynb"
        assert data[0]["id"] is not None
        assert data[0]["session_id"] == 10
        assert data[0]["rubric_generated"] is False

        # Exactly one DB row created.
        rows = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert len(rows) == 1
        assert rows[0].original_filename == "hw1.ipynb"

    # ── 2. Multiple .ipynb files in one multipart call ─────────────────────────

    def test_multiple_ipynb_files_in_one_call(self, client, db):
        """All three notebooks are created in a single request; list has three items."""
        c, instr_token, _ = client
        nb1 = _make_notebook_bytes(markdown_cells=["# Lab 1"])
        nb2 = _make_notebook_bytes(markdown_cells=["# Lab 2"])
        nb3 = _make_notebook_bytes(markdown_cells=["# Lab 3"])

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = _post(
                c, instr_token, 10,
                ("lab1.ipynb", nb1),
                ("lab2.ipynb", nb2),
                ("lab3.ipynb", nb3),
            )

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 3
        returned_names = {item["original_filename"] for item in data}
        assert returned_names == {"lab1.ipynb", "lab2.ipynb", "lab3.ipynb"}

        rows = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert len(rows) == 3

    # ── 3. .zip with notebooks at archive root ─────────────────────────────────

    def test_zip_flat_extracts_all_notebooks(self, client, db):
        """Notebooks at the zip root are extracted and two records are created."""
        c, instr_token, _ = client
        nb1 = _make_notebook_bytes(markdown_cells=["# NB1"])
        nb2 = _make_notebook_bytes(markdown_cells=["# NB2"])
        zip_bytes = _make_zip({"notebook1.ipynb": nb1, "notebook2.ipynb": nb2})

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("labs.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 2
        returned_names = {item["original_filename"] for item in data}
        assert returned_names == {"notebook1.ipynb", "notebook2.ipynb"}

        rows = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert len(rows) == 2

    # ── 4. .zip with nested folders (recursive extraction) ─────────────────────

    def test_zip_nested_folders_extracted_recursively(self, client, db):
        """
        Notebooks at the zip root, one folder deep, and three folders deep are
        all found — the recursive extraction logic is applied just as it is for
        student .zip submissions.
        """
        c, instr_token, _ = client
        nb_top    = _make_notebook_bytes(markdown_cells=["# Top level"])
        nb_deep   = _make_notebook_bytes(markdown_cells=["# One level deep"])
        nb_deeper = _make_notebook_bytes(markdown_cells=["# Three levels deep"])
        zip_bytes = _make_zip({
            "top.ipynb":                         nb_top,
            "subdir/deep.ipynb":                 nb_deep,
            "subdir/level2/level3/deeper.ipynb": nb_deeper,
        })

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("nested.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 3
        returned_names = {item["original_filename"] for item in data}
        assert "top.ipynb"    in returned_names
        assert "deep.ipynb"   in returned_names
        assert "deeper.ipynb" in returned_names

    # ── 5. .zip with mixed files — non-.ipynb silently ignored ─────────────────

    def test_zip_non_notebook_files_ignored(self, client, db):
        """
        .py, .csv, .png, .txt entries in the archive are silently skipped;
        only the single .ipynb is returned.
        """
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# Only notebook"])
        zip_bytes = _make_zip({
            "assignment.ipynb": nb,
            "helper.py":        b"def foo(): pass",
            "data.csv":         b"col1,col2\n1,2",
            "image.png":        bytes(range(50)),
            "README.txt":       b"ignore me",
        })

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("mixed.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )

        assert res.status_code == 201
        data = res.json()
        assert len(data) == 1
        assert data[0]["original_filename"] == "assignment.ipynb"

    # ── 6. .zip with no .ipynb files → 422 ────────────────────────────────────

    def test_zip_with_no_notebooks_returns_422(self, client, db):
        """A zip that has only .py / .md files is rejected with a clear 422."""
        c, instr_token, _ = client
        zip_bytes = _make_zip({"script.py": b"pass", "README.md": b"# readme"})

        res = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("empty_notebooks.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )

        assert res.status_code == 422
        detail = res.json()["detail"].lower()
        # message must mention the zip name and the absence of notebooks
        assert "no .ipynb" in detail or "contains no" in detail

        # No DB rows should have been created
        rows = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert rows == []

    # ── 7. Duplicate filename → 409 (whole request rejected atomically) ─────────

    def test_duplicate_filename_returns_409_and_no_partial_write(self, client, db):
        """
        Uploading a file whose name already exists in the session returns 409.
        The check is performed before any disk write, so a multi-file request
        that includes one duplicate rejects the entire batch.
        """
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW1"])

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            # First upload succeeds
            res1 = _post(c, instr_token, 10, ("hw1.ipynb", nb))
            assert res1.status_code == 201

            # Second upload of same filename → 409
            res2 = _post(c, instr_token, 10, ("hw1.ipynb", nb))

        assert res2.status_code == 409
        assert "hw1.ipynb" in res2.json()["detail"]

        # Only the one row from the first successful upload remains.
        rows = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert len(rows) == 1

    def test_multi_file_request_with_one_duplicate_rejected_atomically(self, client, db):
        """
        If a multi-file request includes a name that already exists, the whole
        request is rejected — the new files are NOT partially saved.
        """
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# Existing"])
        nb_new = _make_notebook_bytes(markdown_cells=["# New"])

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            # Pre-seed one existing file
            r = _post(c, instr_token, 10, ("existing.ipynb", nb))
            assert r.status_code == 201

            # Batch that includes the existing name plus a new one → rejected
            r2 = _post(c, instr_token, 10, ("existing.ipynb", nb), ("new.ipynb", nb_new))

        assert r2.status_code == 409
        # "new.ipynb" must NOT have been created
        rows = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert len(rows) == 1  # only the pre-seeded file

    # ── 8. Invalid extension → 422 ────────────────────────────────────────────

    def test_invalid_extension_returns_422(self, client, db):
        c, instr_token, _ = client

        res = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("script.py", b"print('hi')", "text/plain"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )

        assert res.status_code == 422

    # ── 9. Student cannot upload → 403 ────────────────────────────────────────

    def test_student_upload_returns_403(self, client, db):
        c, _, student_token = client
        nb = _make_notebook_bytes()

        res = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("hw.ipynb", nb, "application/octet-stream"))],
            headers={"Authorization": f"Bearer {student_token}"},
        )

        assert res.status_code == 403

    # ── 10. Unauthenticated → 401 ─────────────────────────────────────────────

    def test_unauthenticated_returns_401(self, client, db):
        c, _, _ = client
        nb = _make_notebook_bytes()

        res = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("hw.ipynb", nb, "application/octet-stream"))],
            # No Authorization header
        )

        assert res.status_code == 401

    # ── 11. Nonexistent session → 404 ─────────────────────────────────────────

    def test_nonexistent_session_returns_404(self, client, db):
        c, instr_token, _ = client
        nb = _make_notebook_bytes()

        res = c.post(
            "/api/v1/sessions/999/assignments",
            files=[("files", ("hw.ipynb", nb, "application/octet-stream"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )

        assert res.status_code == 404

    # ── 12. parsed_requirements_text set on each row ──────────────────────────

    def test_parsed_requirements_text_stored_per_file(self, client, db):
        """
        Each created UnsolvedFile has parsed_requirements_text set.
        Because save_assignment_file is mocked (no real disk write), the
        extract_requirements_text call gracefully returns "" for the missing
        path — confirming the extraction attempt is made and errors are handled
        without crashing, exactly as in the original single-file flow.
        """
        c, instr_token, _ = client
        nb1 = _make_notebook_bytes(markdown_cells=["# Task 1\nImplement linear regression."])
        nb2 = _make_notebook_bytes(markdown_cells=["# Task 2\nImplement logistic regression."])

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = _post(c, instr_token, 10, ("hw1.ipynb", nb1), ("hw2.ipynb", nb2))

        assert res.status_code == 201

        rows = (
            db.query(UnsolvedFile)
            .filter(UnsolvedFile.session_id == 10)
            .order_by(UnsolvedFile.original_filename)
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            # Column is set (to "" when file not physically on disk) — never None
            # because extract_requirements_text never raises.
            assert row.parsed_requirements_text is not None

    # ── 13. Correct count in DB after multi-file upload ───────────────────────

    def test_db_row_count_matches_uploaded_file_count(self, client, db):
        """After uploading N files, exactly N UnsolvedFile rows exist."""
        c, instr_token, _ = client
        notebooks = [(f"hw{i}.ipynb", _make_notebook_bytes(markdown_cells=[f"# HW {i}"])) for i in range(1, 6)]

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = _post(c, instr_token, 10, *notebooks)

        assert res.status_code == 201
        assert len(res.json()) == 5

        rows = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert len(rows) == 5

    # ── 14. Response fields match DB state ────────────────────────────────────

    def test_response_fields_match_db(self, client, db):
        """
        Every item in the response list has an id, session_id=10,
        rubric_generated=False, and an original_filename matching a DB row.
        """
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# Assignment"])

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = _post(c, instr_token, 10, ("assign.ipynb", nb))

        assert res.status_code == 201
        item = res.json()[0]

        db_row = db.query(UnsolvedFile).filter(UnsolvedFile.id == item["id"]).first()
        assert db_row is not None
        assert db_row.session_id == item["session_id"] == 10
        assert db_row.original_filename == item["original_filename"] == "assign.ipynb"
        assert item["rubric_generated"] is False
        assert db_row.rubric_json is None
