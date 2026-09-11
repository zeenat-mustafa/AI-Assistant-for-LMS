"""
Tests for POST/GET/DELETE /api/v1/sessions/{session_id}/submissions.

Submission-side rework: every upload creates one SubmissionUpload row per
file submitted (ADDITIVE — a new upload never replaces a previous one), any
file type accepted, single or inside a .zip with zero required notebooks.
Notebook extraction into SubmissionFile (matched via file_matcher.py) is
otherwise unchanged; the only new link is SubmissionFile.source_upload_id.

Run with:
    cd backend
    python -m pytest tests/test_submissions.py -v

Cases covered
─────────────
Upload — any file type, additive
  1.  Single .ipynb -> one SubmissionUploadRead; one SubmissionFile, matched
  2.  A second, separate upload call -> ADDS to the first, does not replace it
  3.  .zip with a notebook + a non-notebook file -> one upload row; notebook
      extracted and gradeable; non-notebook content preserved via the zip itself
  4.  .zip with zero notebooks -> still accepted as one upload row (no failure)
  5.  A bare non-notebook, non-zip file (e.g. .pdf) -> accepted, no extraction
  6.  Student cannot upload as another student (token drives student_id)
  7.  Unauthenticated request -> 401
  8.  Nonexistent session -> 404

Download (instructor)
  9.  Byte-exact content
  10. Student (non-instructor) -> 403
  11. Nonexistent upload / wrong session -> 404

Download (owning student, via /mine/uploads/{id}/download)
  9b. Byte-exact content, same bytes as the instructor endpoint serves
  10b. Another student's upload -> 404 (not 403 -- matches delete's own-upload convention)
  11b. Nonexistent upload -> 404

Delete (owning student only, warn-and-confirm if graded)
  12. Ungraded upload deletes immediately, no confirm needed
  13. Graded upload without confirm=true -> 409, names the real score
  14. Graded upload with confirm=true -> deletes and cascades (file + Grade)
  15. Another student cannot delete this upload -> 404
  16. Nonexistent upload -> 404

Grading-pipeline preservation (multi-upload indifference)
  17. Grading a notebook that came from the SECOND of two separate uploads
      works correctly via grade_single_submission_file, end to end
"""

import io
import zipfile
from unittest.mock import patch

import nbformat
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.grade import Grade
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.submission_upload import SubmissionUpload
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token


# ===========================================================================
# Shared helpers
# ===========================================================================

def _make_notebook_bytes(markdown_cells: list[str] | None = None) -> bytes:
    nb = nbformat.v4.new_notebook()
    for md in (markdown_cells or []):
        nb.cells.append(nbformat.v4.new_markdown_cell(md))
    buf = io.StringIO()
    nbformat.write(nb, buf)
    return buf.getvalue().encode("utf-8")


def _make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in members.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


def _post(client, token, session_id, *file_tuples):
    files = [("files", (name, data, "application/octet-stream")) for name, data in file_tuples]
    return client.post(
        f"/api/v1/sessions/{session_id}/submissions",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def db():
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
def client(db, tmp_path, monkeypatch):
    """
    TestClient backed by the in-memory DB, with real file I/O redirected to
    a throwaway tmp_path so uploads/extraction/download genuinely exercise
    the real storage code path without touching backend/storage/.
    """
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    instructor = User(
        id=1, name="Prof Test", email="prof-sub@test.com",
        hashed_password="hash", role=UserRole.instructor,
    )
    student = User(
        id=2, name="Stu Test", email="stu-sub@test.com",
        hashed_password="hash", role=UserRole.student,
    )
    student2 = User(
        id=3, name="Stu Two", email="stu2-sub@test.com",
        hashed_password="hash", role=UserRole.student,
    )
    lms_session = LMSSession(id=10, title="Week 1 Day 1 Sub")
    db.add_all([instructor, student, student2, lms_session])
    db.commit()

    instructor_token = create_access_token(1, UserRole.instructor)
    student_token = create_access_token(2, UserRole.student)
    student2_token = create_access_token(3, UserRole.student)

    with TestClient(app) as c:
        yield c, instructor_token, student_token, student2_token

    app.dependency_overrides.clear()


# ===========================================================================
# Upload — any file type, additive
# ===========================================================================

class TestUploadSubmission:

    def test_single_ipynb_creates_one_upload_and_one_matched_file(self, client, db):
        c, _, stu_token, _ = client
        unsolved = UnsolvedFile(
            id=100, session_id=10, original_filename="hw1.ipynb",
            file_path="10/assignments/hw1.ipynb",
            parsed_requirements_text="Do task A about pandas dataframes.",
        )
        db.add(unsolved)
        db.commit()

        nb = _make_notebook_bytes(markdown_cells=["Do task A about pandas dataframes, solved."])
        res = _post(c, stu_token, 10, ("hw1_solved.ipynb", nb))

        assert res.status_code == 201
        data = res.json()
        assert len(data["uploads"]) == 1
        assert data["uploads"][0]["original_filename"] == "hw1_solved.ipynb"
        assert len(data["files"]) == 1
        assert data["files"][0]["source_upload_id"] == data["uploads"][0]["id"]
        # Single unsolved candidate -> always matched (file_matcher shortcut).
        assert data["files"][0]["matched_unsolved_file_id"] == 100

        upload_row = db.query(SubmissionUpload).filter(SubmissionUpload.submission_id == data["id"]).one()
        from app.services.storage import absolute_path
        assert absolute_path(upload_row.file_path).exists()

    def test_second_separate_upload_adds_not_replaces(self, client, db):
        c, _, stu_token, _ = client
        nb1 = _make_notebook_bytes(markdown_cells=["First"])
        nb2 = _make_notebook_bytes(markdown_cells=["Second"])

        first = _post(c, stu_token, 10, ("first.ipynb", nb1))
        assert first.status_code == 201
        sub_id = first.json()["id"]

        second = _post(c, stu_token, 10, ("second.ipynb", nb2))
        assert second.status_code == 201
        assert second.json()["id"] == sub_id  # same Submission container reused

        data = second.json()
        assert len(data["uploads"]) == 2
        assert {u["original_filename"] for u in data["uploads"]} == {"first.ipynb", "second.ipynb"}
        assert len(data["files"]) == 2

        # Only one Submission row ever exists for this student+session.
        assert db.query(Submission).filter(
            Submission.session_id == 10, Submission.student_id == 2
        ).count() == 1
        assert db.query(SubmissionUpload).filter(SubmissionUpload.submission_id == sub_id).count() == 2

    def test_zip_with_notebook_and_non_notebook_preserves_both(self, client, db):
        c, _, stu_token, _ = client
        nb = _make_notebook_bytes(markdown_cells=["Lab work"])
        zip_bytes = _make_zip({"lab.ipynb": nb, "screenshot.png": b"\x89PNG fake bytes"})

        res = _post(c, stu_token, 10, ("bundle.zip", zip_bytes))

        assert res.status_code == 201
        data = res.json()
        assert len(data["uploads"]) == 1
        assert data["uploads"][0]["original_filename"] == "bundle.zip"
        # Notebook extracted and gradeable...
        assert len(data["files"]) == 1
        assert data["files"][0]["original_filename"] == "lab.ipynb"
        # ...the PNG is not a separate DB row, but is preserved inside the
        # zip itself, which downloads byte-exact (verified in download tests).
        from app.services.storage import absolute_path
        upload_row = db.query(SubmissionUpload).filter(SubmissionUpload.submission_id == data["id"]).one()
        raw_zip = absolute_path(upload_row.file_path).read_bytes()
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            assert set(zf.namelist()) == {"lab.ipynb", "screenshot.png"}

    def test_zip_with_zero_notebooks_still_succeeds(self, client, db):
        c, _, stu_token, _ = client
        zip_bytes = _make_zip({"notes.txt": b"just some notes", "data.csv": b"a,b\n1,2"})

        res = _post(c, stu_token, 10, ("extras.zip", zip_bytes))

        assert res.status_code == 201
        data = res.json()
        assert len(data["uploads"]) == 1
        assert data["uploads"][0]["original_filename"] == "extras.zip"
        assert data["files"] == []  # nothing gradeable, and that's fine

    def test_bare_non_notebook_file_accepted_directly(self, client, db):
        c, _, stu_token, _ = client
        res = _post(c, stu_token, 10, ("readme.pdf", b"%PDF fake bytes"))

        assert res.status_code == 201
        data = res.json()
        assert len(data["uploads"]) == 1
        assert data["uploads"][0]["original_filename"] == "readme.pdf"
        assert data["files"] == []

    def test_unauthenticated_upload_401(self, client):
        c, *_ = client
        res = c.post(
            "/api/v1/sessions/10/submissions",
            files=[("files", ("x.ipynb", b"{}", "application/octet-stream"))],
        )
        assert res.status_code == 401

    def test_nonexistent_session_404(self, client):
        c, _, stu_token, _ = client
        res = _post(c, stu_token, 9999, ("x.ipynb", _make_notebook_bytes()))
        assert res.status_code == 404

    def test_no_files_422(self, client):
        c, _, stu_token, _ = client
        res = c.post(
            "/api/v1/sessions/10/submissions",
            files=[],
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        assert res.status_code == 422


# ===========================================================================
# Download (instructor)
# ===========================================================================

class TestDownloadSubmissionUpload:

    def test_download_is_byte_exact(self, client, db):
        c, instr_token, stu_token, _ = client
        raw = b"exact bytes, nothing reconstructed"
        upload = _post(c, stu_token, 10, ("thing.bin", raw))
        upload_id = upload.json()["uploads"][0]["id"]

        res = c.get(
            f"/api/v1/sessions/10/submissions/uploads/{upload_id}/download",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 200
        assert res.content == raw

    def test_download_student_forbidden(self, client, db):
        c, _, stu_token, _ = client
        upload = _post(c, stu_token, 10, ("thing.bin", b"data"))
        upload_id = upload.json()["uploads"][0]["id"]

        res = c.get(
            f"/api/v1/sessions/10/submissions/uploads/{upload_id}/download",
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        assert res.status_code == 403

    def test_download_nonexistent_upload_404(self, client):
        c, instr_token, *_ = client
        res = c.get(
            "/api/v1/sessions/10/submissions/uploads/9999/download",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 404


class TestDownloadMySubmissionUpload:
    """The student-facing counterpart: GET .../submissions/mine/uploads/{id}/download."""

    def test_download_own_upload_byte_exact(self, client, db):
        c, _, stu_token, _ = client
        raw = b"exact bytes, nothing reconstructed"
        upload = _post(c, stu_token, 10, ("thing.bin", raw))
        upload_id = upload.json()["uploads"][0]["id"]

        res = c.get(
            f"/api/v1/sessions/10/submissions/mine/uploads/{upload_id}/download",
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        assert res.status_code == 200
        assert res.content == raw

    def test_download_matches_instructor_endpoint_byte_for_byte(self, client, db):
        c, instr_token, stu_token, _ = client
        raw = b"same bytes either way"
        upload = _post(c, stu_token, 10, ("thing.bin", raw))
        upload_id = upload.json()["uploads"][0]["id"]

        mine = c.get(
            f"/api/v1/sessions/10/submissions/mine/uploads/{upload_id}/download",
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        instructor = c.get(
            f"/api/v1/sessions/10/submissions/uploads/{upload_id}/download",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert mine.content == instructor.content == raw

    def test_cannot_download_another_students_upload(self, client, db):
        c, _, stu_token, stu2_token = client
        upload = _post(c, stu_token, 10, ("thing.bin", b"private"))
        upload_id = upload.json()["uploads"][0]["id"]

        res = c.get(
            f"/api/v1/sessions/10/submissions/mine/uploads/{upload_id}/download",
            headers={"Authorization": f"Bearer {stu2_token}"},
        )
        # 404, not 403 -- matches delete_submission_upload's own convention
        # of never confirming another student's upload even exists.
        assert res.status_code == 404

    def test_download_nonexistent_own_upload_404(self, client):
        c, _, stu_token, _ = client
        res = c.get(
            "/api/v1/sessions/10/submissions/mine/uploads/9999/download",
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        assert res.status_code == 404

    def test_download_unauthenticated_401(self, client, db):
        c, _, stu_token, _ = client
        upload = _post(c, stu_token, 10, ("thing.bin", b"data"))
        upload_id = upload.json()["uploads"][0]["id"]

        res = c.get(f"/api/v1/sessions/10/submissions/mine/uploads/{upload_id}/download")
        assert res.status_code == 401


# ===========================================================================
# Delete (owning student only, warn-and-confirm if graded)
# ===========================================================================

class TestDeleteSubmissionUpload:

    def test_delete_ungraded_upload_immediately(self, client, db):
        c, _, stu_token, _ = client
        upload = _post(c, stu_token, 10, ("x.ipynb", _make_notebook_bytes()))
        upload_id = upload.json()["uploads"][0]["id"]

        res = c.delete(
            f"/api/v1/sessions/10/submissions/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        assert res.status_code == 200
        assert db.query(SubmissionUpload).filter(SubmissionUpload.id == upload_id).first() is None

    def test_delete_graded_upload_requires_confirm(self, client, db):
        c, _, stu_token, _ = client
        upload = _post(c, stu_token, 10, ("x.ipynb", _make_notebook_bytes()))
        upload_id = upload.json()["uploads"][0]["id"]
        sub_file_id = upload.json()["files"][0]["id"]

        grade = Grade(submission_file_id=sub_file_id, score=8.5, feedback_text="Nice work.")
        db.add(grade)
        db.commit()

        res = c.delete(
            f"/api/v1/sessions/10/submissions/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        assert res.status_code == 409
        assert "8.5" in res.json()["detail"]
        # Not deleted yet.
        assert db.query(SubmissionUpload).filter(SubmissionUpload.id == upload_id).first() is not None

        res2 = c.delete(
            f"/api/v1/sessions/10/submissions/uploads/{upload_id}?confirm=true",
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        assert res2.status_code == 200
        assert db.query(SubmissionUpload).filter(SubmissionUpload.id == upload_id).first() is None
        assert db.query(SubmissionFile).filter(SubmissionFile.id == sub_file_id).first() is None
        assert db.query(Grade).filter(Grade.submission_file_id == sub_file_id).first() is None

    def test_delete_another_students_upload_404(self, client, db):
        c, _, stu_token, stu2_token = client
        upload = _post(c, stu_token, 10, ("x.ipynb", _make_notebook_bytes()))
        upload_id = upload.json()["uploads"][0]["id"]

        res = c.delete(
            f"/api/v1/sessions/10/submissions/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {stu2_token}"},
        )
        assert res.status_code == 404
        assert db.query(SubmissionUpload).filter(SubmissionUpload.id == upload_id).first() is not None

    def test_delete_nonexistent_upload_404(self, client):
        c, _, stu_token, _ = client
        res = c.delete(
            "/api/v1/sessions/10/submissions/uploads/9999",
            headers={"Authorization": f"Bearer {stu_token}"},
        )
        assert res.status_code == 404


# ===========================================================================
# Grading-pipeline preservation — indifferent to multi-upload shape
# ===========================================================================

class TestGradingPipelineIndifferentToUploadShape:

    def test_grades_notebook_from_second_of_two_uploads(self, client, db):
        c, instr_token, stu_token, _ = client
        from app.services.storage import absolute_path

        unsolved_path = absolute_path("10/assignments/hw.ipynb")
        unsolved_path.parent.mkdir(parents=True, exist_ok=True)
        unsolved_path.write_bytes(_make_notebook_bytes(markdown_cells=["Solve the pandas exercise."]))

        unsolved = UnsolvedFile(
            id=100, session_id=10, original_filename="hw.ipynb",
            file_path="10/assignments/hw.ipynb",
            parsed_requirements_text="Solve the pandas exercise.",
            rubric_json='{"criteria": [{"criterion": "Completion", "points_possible": 10.0}]}',
        )
        db.add(unsolved)
        db.commit()

        # First upload: an unrelated non-notebook file.
        first = _post(c, stu_token, 10, ("notes.txt", b"just notes"))
        assert first.status_code == 201
        assert first.json()["files"] == []

        # Second, separate upload: the actual gradeable notebook.
        nb = _make_notebook_bytes(markdown_cells=["Solve the pandas exercise, solved."])
        second = _post(c, stu_token, 10, ("hw_solved.ipynb", nb))
        assert second.status_code == 201
        files = second.json()["files"]
        assert len(files) == 1
        assert files[0]["matched_unsolved_file_id"] == 100
        sub_file_id = files[0]["id"]

        with patch(
            "app.services.evaluator.call_gemini_for_evaluation",
            return_value='{"criteria": [{"criterion": "Completion", "points_possible": 10.0, "points_awarded": 10.0, "explanation": "Done."}]}',
        ):
            res = c.post(
                f"/api/v1/sessions/10/submissions/files/{sub_file_id}/grade",
                headers={"Authorization": f"Bearer {instr_token}"},
            )
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert res.json()["score"] == 10.0

        grade = db.query(Grade).filter(Grade.submission_file_id == sub_file_id).one()
        assert grade.score == 10.0
