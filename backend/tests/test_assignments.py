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
  5.  .zip with mixed files → notebooks (UnsolvedFile) + resources (ResourceFile) (Fix 2)
  6.  .zip with only resources → accepted; only junk → 422 (Fix 2)
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

    def test_zip_non_notebook_files_stored_as_resources(self, client, db):
        """
        bugfix-post-phase5, Fix 2: non-notebook entries in the archive are now
        STORED as resource files (in the separate ResourceFile table), not
        silently dropped. The .ipynb is a gradeable notebook; the rest are
        resources. All are returned, each tagged with its derived file_role.
        """
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# Only notebook"])
        zip_bytes = _make_zip({
            "assignment.ipynb": nb,
            "helper.py":        b"def foo(): pass",
            "data.csv":         b"col1,col2\n1,2",
            "README.txt":       b"read me",
        })

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("mixed.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )

        assert res.status_code == 201
        by_name = {d["original_filename"]: d for d in res.json()}
        assert by_name["assignment.ipynb"]["file_role"] == "notebook"
        assert by_name["helper.py"]["file_role"] == "resource"
        assert by_name["data.csv"]["file_role"] == "resource"
        assert by_name["README.txt"]["file_role"] == "resource"
        assert by_name["data.csv"]["rubric_generated"] is False

        # Structural separation: exactly one gradeable notebook in UnsolvedFile...
        notebooks = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert [n.original_filename for n in notebooks] == ["assignment.ipynb"]
        # ...and the three resources live in the separate ResourceFile table.
        resources = db.query(ResourceFile).filter(ResourceFile.session_id == 10).all()
        assert {r.original_filename for r in resources} == {"helper.py", "data.csv", "README.txt"}

    # ── 6. .zip with only resources (no notebooks) → accepted ─────────────────

    def test_zip_with_only_resources_is_accepted(self, client, db):
        """
        bugfix-post-phase5, Fix 2: a zip with zero notebooks but at least one
        other file is now accepted, storing the non-notebook files as
        resources rather than rejecting the whole upload.
        """
        c, instr_token, _ = client
        zip_bytes = _make_zip({"script.py": b"pass", "notes.md": b"# notes"})

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("resources_only.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )

        assert res.status_code == 201
        data = res.json()
        assert {d["original_filename"] for d in data} == {"script.py", "notes.md"}
        assert all(d["file_role"] == "resource" for d in data)
        # No gradeable notebooks were created.
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all() == []
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).count() == 2

    def test_zip_with_nothing_usable_returns_422(self, client, db):
        """A zip with only skippable entries (__MACOSX metadata) still 422s."""
        c, instr_token, _ = client
        zip_bytes = _make_zip({"__MACOSX/._x": b"junk"})

        res = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("empty.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )

        assert res.status_code == 422
        assert "no usable files" in res.json()["detail"].lower()
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all() == []
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).all() == []

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


# ===========================================================================
# bugfix-post-phase5, Fix 2 (ResourceFile table): resources are stored in a
# structurally separate table, so they never enter grading paths.
# ===========================================================================

class TestResourceFileHandling:
    def test_resources_excluded_from_assignment_count_structurally(self, client, db):
        """The combined-score DENOMINATOR counts notebooks only. With resources
        in their own table, _get_total_assignment_count is UNCHANGED from before
        Fix 2 (queries UnsolvedFile) and simply never sees them."""
        from app.routers.grades import _get_total_assignment_count

        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        zip_bytes = _make_zip({"hw.ipynb": nb, "dataset.csv": b"a,b\n1,2"})
        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("bundle.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )
        assert res.status_code == 201
        # One notebook + one resource stored across the two tables.
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count() == 1
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).count() == 1
        # The denominator is 1 — the resource is not in the table it queries.
        assert _get_total_assignment_count(10, db) == 1

    def test_resource_not_in_match_candidate_pool(self, client, db):
        """The matcher builds its candidate pool from UnsolvedFile; a resource
        is in a different table, so it can never be a match candidate."""
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        zip_bytes = _make_zip({"hw.ipynb": nb, "notes.pdf": b"%PDF-1.4 fake"})
        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("bundle.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )
        candidates = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).all()
        assert [c_.original_filename for c_ in candidates] == ["hw.ipynb"]

    def test_duplicate_name_across_tables_rejected_atomically(self, client, db):
        """The duplicate guard spans BOTH tables: re-uploading a name already
        used by a notebook OR a resource rejects the whole batch, atomically."""
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])

        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            first = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("r.zip", _make_zip({"data.csv": b"a,b"}), "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )
        assert first.status_code == 201
        nb_before = db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count()
        res_before = db.query(ResourceFile).filter(ResourceFile.session_id == 10).count()

        # New notebook + a resource name that collides with the stored data.csv.
        clash = _make_zip({"new.ipynb": nb, "data.csv": b"x,y"})
        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("clash.zip", clash, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )
        assert res.status_code == 409
        assert "data.csv" in res.json()["detail"]
        # Nothing new written to EITHER table — new.ipynb must not have landed.
        assert db.query(UnsolvedFile).filter(UnsolvedFile.session_id == 10).count() == nb_before
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).count() == res_before

    def test_resource_download_and_listing(self, client, db):
        """Resources are downloadable and listed via their own endpoints."""
        c, instr_token, _ = client
        zip_bytes = _make_zip({"dataset.csv": b"a,b\n1,2"})
        # Use the real save so the file exists on disk for download.
        res = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("r.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 201
        rid = res.json()[0]["id"]

        listed = c.get(
            "/api/v1/sessions/10/assignments/resources",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert listed.status_code == 200
        assert [r["original_filename"] for r in listed.json()] == ["dataset.csv"]

        dl = c.get(
            f"/api/v1/sessions/10/assignments/resources/{rid}/download",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert dl.status_code == 200
        assert dl.content == b"a,b\n1,2"

    def test_session_detail_splits_notebooks_and_resources(self, client, db):
        """SessionRead exposes unsolved_files (notebooks) and resource_files
        (resources) separately."""
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        zip_bytes = _make_zip({"hw.ipynb": nb, "slides.pdf": b"%PDF fake", "data.csv": b"a,b"})
        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("bundle.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )
        detail = c.get(
            "/api/v1/sessions/10",
            headers={"Authorization": f"Bearer {instr_token}"},
        ).json()
        assert [f["original_filename"] for f in detail["unsolved_files"]] == ["hw.ipynb"]
        assert {f["original_filename"] for f in detail["resource_files"]} == {"slides.pdf", "data.csv"}

    def test_pure_notebook_zip_unchanged(self, client, db):
        """Regression: a zip of only notebooks behaves exactly as before —
        every entry a gradeable notebook, no resource rows created."""
        c, instr_token, _ = client
        nb1 = _make_notebook_bytes(markdown_cells=["# One"])
        nb2 = _make_notebook_bytes(markdown_cells=["# Two"])
        zip_bytes = _make_zip({"a.ipynb": nb1, "b.ipynb": nb2})
        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            res = c.post(
                "/api/v1/sessions/10/assignments",
                files=[("files", ("nbs.zip", zip_bytes, "application/zip"))],
                headers={"Authorization": f"Bearer {instr_token}"},
            )
        assert res.status_code == 201
        data = res.json()
        assert {d["original_filename"] for d in data} == {"a.ipynb", "b.ipynb"}
        assert all(d["file_role"] == "notebook" for d in data)
        assert db.query(ResourceFile).filter(ResourceFile.session_id == 10).all() == []


# ===========================================================================
# bugfix-resource-file-delete: DELETE /sessions/{id}/assignments/resources/{id}
# ===========================================================================

class TestDeleteResource:
    """
    Mirrors the notebook delete route (DELETE /sessions/{id}/assignments/{id})
    exactly: instructor-only, 204 on success, 404 if the resource doesn't
    exist or belongs to a different session, DB row + on-disk file both
    removed.
    """

    def test_delete_removes_db_row_and_disk_file(self, client, db):
        c, instr_token, _ = client
        zip_bytes = _make_zip({"dataset.csv": b"a,b\n1,2"})
        # Real save (not mocked) so there is an actual file on disk to delete.
        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("r.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert upload.status_code == 201
        rid = upload.json()[0]["id"]

        row = db.query(ResourceFile).filter(ResourceFile.id == rid).one()
        from app.services.storage import absolute_path
        abs_path = absolute_path(row.file_path)
        assert abs_path.exists()

        res = c.delete(
            f"/api/v1/sessions/10/assignments/resources/{rid}",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 204
        assert res.content == b""

        assert db.query(ResourceFile).filter(ResourceFile.id == rid).first() is None
        assert not abs_path.exists()

    def test_delete_nonexistent_resource_404(self, client, db):
        c, instr_token, _ = client
        res = c.delete(
            "/api/v1/sessions/10/assignments/resources/9999",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 404

    def test_delete_resource_from_wrong_session_404(self, client, db):
        """A resource that exists but belongs to a different session must
        404, same as the notebook route's equivalent case."""
        c, instr_token, _ = client
        other_session = LMSSession(id=11, title="Week 2 Day 1")
        db.add(other_session)
        db.commit()

        zip_bytes = _make_zip({"dataset.csv": b"a,b\n1,2"})
        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("r.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        rid = upload.json()[0]["id"]

        res = c.delete(
            f"/api/v1/sessions/11/assignments/resources/{rid}",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 404
        # Untouched — still exists under its real session.
        assert db.query(ResourceFile).filter(ResourceFile.id == rid).first() is not None

    def test_student_cannot_delete_resource(self, client, db):
        c, instr_token, student_token = client
        zip_bytes = _make_zip({"dataset.csv": b"a,b\n1,2"})
        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("r.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        rid = upload.json()[0]["id"]

        res = c.delete(
            f"/api/v1/sessions/10/assignments/resources/{rid}",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert res.status_code == 403
        assert db.query(ResourceFile).filter(ResourceFile.id == rid).first() is not None

    def test_unauthenticated_cannot_delete_resource(self, client, db):
        c, instr_token, _ = client
        zip_bytes = _make_zip({"dataset.csv": b"a,b\n1,2"})
        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("r.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        rid = upload.json()[0]["id"]

        res = c.delete(f"/api/v1/sessions/10/assignments/resources/{rid}")
        assert res.status_code == 401
        assert db.query(ResourceFile).filter(ResourceFile.id == rid).first() is not None

    def test_deleting_a_resource_does_not_touch_a_same_id_notebook(self, client, db):
        """Regression: notebook and resource ids can collide (both tables
        start at 1) — deleting one must never remove or affect the other."""
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            nb_res = _post(c, instr_token, 10, ("hw.ipynb", nb))
        nb_id = nb_res.json()[0]["id"]

        zip_bytes = _make_zip({"dataset.csv": b"a,b\n1,2"})
        upload = c.post(
            "/api/v1/sessions/10/assignments",
            files=[("files", ("r.zip", zip_bytes, "application/zip"))],
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        rid = upload.json()[0]["id"]
        assert rid == nb_id, "test assumes colliding ids across the two tables"

        res = c.delete(
            f"/api/v1/sessions/10/assignments/resources/{rid}",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 204

        # The notebook with the same numeric id is untouched.
        assert db.query(UnsolvedFile).filter(UnsolvedFile.id == nb_id).first() is not None

    def test_notebook_delete_route_still_unaffected(self, client, db):
        """Regression: the existing notebook delete route is untouched by
        this change."""
        c, instr_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["# HW"])
        with patch("app.routers.assignments.save_assignment_file", side_effect=_mock_save):
            nb_res = _post(c, instr_token, 10, ("hw.ipynb", nb))
        nb_id = nb_res.json()[0]["id"]

        res = c.delete(
            f"/api/v1/sessions/10/assignments/{nb_id}",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 204
        assert db.query(UnsolvedFile).filter(UnsolvedFile.id == nb_id).first() is None
