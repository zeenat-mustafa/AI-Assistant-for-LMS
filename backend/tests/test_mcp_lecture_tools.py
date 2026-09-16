"""
Tests for backend/app/mcp/tools/lecture_tools.py -- Post-7.8 Fix 3 (Prompt 3);
tests added for the Phase 7.8 audit fix (Item 2).

lecture_tools.py doesn't delegate to a separate service function the way
Phase 4's tools do -- both list_lecture_files and upload_lecture_file
inline the same query/upload/extract/chunk/embed logic the REST router
(app/routers/lectures.py) has. So "parity" here means seeding two
identically-populated fresh databases and running one surface against each
with the same mocked extraction boundary, then comparing the real,
persisted results -- not mocking a shared function, since none exists.

Cases covered
-------------
1-2. Both tools registered with the expected schema.
3. list_lecture_files returns [] for a missing session, and real rows
   (including chunk_count) for an existing one -- unaltered, DB closed.
4. upload_lecture_file rejects a non-.pptx filename without touching disk/DB.
5. upload_lecture_file rejects a duplicate filename in the same session.
6. upload_lecture_file rejects a missing session.
7. A DB session is closed even when extraction raises.
8. Parity: list_lecture_files vs REST GET .../lectures on identically-seeded
   data.
9. Parity: upload_lecture_file vs REST POST .../lectures on identically-
   seeded databases and the same mocked extraction/embedding boundary.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.mcp.server import server
from app.mcp.tools.lecture_tools import list_lecture_files, upload_lecture_file
from app.models.lecture_chunk import LectureChunk
from app.models.lecture_file import LectureFile
from app.models.session import LMSSession
from app.models.user import User, UserRole
from app.services.auth import create_access_token


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 1-2. Registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_lecture_files_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "list_lecture_files"), None)

    assert tool is not None
    assert tool.description
    schema = tool.input_schema
    assert schema["properties"]["session_id"]["type"] == "integer"
    assert schema["required"] == ["session_id"]


@pytest.mark.anyio
async def test_upload_lecture_file_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "upload_lecture_file"), None)

    assert tool is not None
    assert tool.description
    schema = tool.input_schema
    assert schema["properties"]["session_id"]["type"] == "integer"
    assert schema["properties"]["filename"]["type"] == "string"
    assert set(schema["required"]) == {"session_id", "filename", "file_bytes"}


# ---------------------------------------------------------------------------
# 3. list_lecture_files
# ---------------------------------------------------------------------------

def _fresh_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_list_lecture_files_empty_for_missing_session():
    engine, db = _fresh_db()
    try:
        with patch("app.mcp.tools.lecture_tools.SessionLocal", return_value=db):
            assert list_lecture_files(999) == []
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_list_lecture_files_returns_real_rows_unaltered():
    import app.models  # noqa: F401

    engine, db = _fresh_db()
    try:
        db.add(LMSSession(id=1, title="Week 1 Day 1"))
        db.commit()
        db.add(LectureFile(
            id=5, session_id=1, instructor_id=1, original_filename="lec.pptx",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            file_path="1/lectures/lec.pptx", extracted=True,
        ))
        db.commit()
        db.add(LectureChunk(lecture_file_id=5, slide_number=1, source="slide_text", chunk_index=0, chunk_text="a"))
        db.add(LectureChunk(lecture_file_id=5, slide_number=2, source="slide_text", chunk_index=0, chunk_text="b"))
        db.commit()

        with patch("app.mcp.tools.lecture_tools.SessionLocal", return_value=db):
            result = list_lecture_files(1)

        assert len(result) == 1
        assert result[0]["id"] == 5
        assert result[0]["original_filename"] == "lec.pptx"
        assert result[0]["extracted"] is True
        assert result[0]["chunk_count"] == 2
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# 4-6. upload_lecture_file validation
# ---------------------------------------------------------------------------

def test_upload_rejects_non_pptx_without_touching_disk():
    engine, db = _fresh_db()
    try:
        db.add(LMSSession(id=1, title="Week 1 Day 1"))
        db.commit()
        with patch("app.mcp.tools.lecture_tools.SessionLocal", return_value=db), \
             patch("app.services.storage.save_lecture_file") as mock_save:
            result = upload_lecture_file(1, "notes.pdf", b"bytes")
        assert "error" in result
        mock_save.assert_not_called()
        assert db.query(LectureFile).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_upload_rejects_duplicate_filename():
    import app.models  # noqa: F401

    engine, db = _fresh_db()
    try:
        db.add(LMSSession(id=1, title="Week 1 Day 1"))
        db.commit()
        db.add(LectureFile(
            id=5, session_id=1, instructor_id=1, original_filename="lec.pptx",
            file_path="1/lectures/lec.pptx", extracted=True,
        ))
        db.commit()

        with patch("app.mcp.tools.lecture_tools.SessionLocal", return_value=db):
            result = upload_lecture_file(1, "lec.pptx", b"PK\x03\x04")

        assert "error" in result
        assert "already exists" in result["error"]
        assert db.query(LectureFile).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_upload_rejects_missing_session():
    engine, db = _fresh_db()
    try:
        with patch("app.mcp.tools.lecture_tools.SessionLocal", return_value=db):
            result = upload_lecture_file(999, "lec.pptx", b"PK\x03\x04")
        assert "error" in result
        assert "not found" in result["error"]
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# 7. Cleanup on failure
# ---------------------------------------------------------------------------

def test_db_session_closed_even_if_extraction_raises():
    fake_db = MagicMock()
    fake_db.get.return_value = MagicMock(id=1)
    fake_db.query.return_value.filter.return_value.first.return_value = None

    with patch("app.mcp.tools.lecture_tools.SessionLocal", return_value=fake_db), \
         patch("app.services.storage.save_lecture_file", side_effect=RuntimeError("disk full")):
        result = upload_lecture_file(1, "lec.pptx", b"PK\x03\x04")

    assert "error" in result
    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 8-9. Parity with the REST lecture endpoints
# ---------------------------------------------------------------------------

def _seed_session(db, session_id=1):
    db.add(LMSSession(id=session_id, title="Week 1 Day 1", instructor_id=1))
    db.add(User(id=1, name="Prof", email="prof@x.com", hashed_password="h", role=UserRole.instructor))
    db.commit()


def test_list_lecture_files_shape_matches_rest_list_endpoint():
    """Both surfaces read the same rows; the MCP dict's fields are a superset
    (it also reports chunk_count) but must agree on every REST-shared field."""
    import app.models  # noqa: F401

    mcp_engine, mcp_db = _fresh_db()
    rest_engine, rest_db = _fresh_db()
    try:
        for db in (mcp_db, rest_db):
            _seed_session(db)
            db.add(LectureFile(
                id=5, session_id=1, instructor_id=1, original_filename="lec.pptx",
                content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                file_path="1/lectures/lec.pptx", extracted=True,
            ))
            db.commit()

        with patch("app.mcp.tools.lecture_tools.SessionLocal", return_value=mcp_db):
            mcp_result = list_lecture_files(1)[0]

        from app.database import get_db
        from app.main import app

        def override_get_db():
            yield rest_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            token = create_access_token(1, UserRole.instructor)
            rest = client.get("/api/v1/sessions/1/lectures", headers={"Authorization": f"Bearer {token}"})
            assert rest.status_code == 200, rest.text
            rest_result = rest.json()[0]
        finally:
            app.dependency_overrides.clear()

        for field in ("id", "session_id", "instructor_id", "original_filename", "content_type", "extracted", "extraction_error"):
            assert mcp_result[field] == rest_result[field], field
    finally:
        mcp_db.close()
        rest_db.close()
        Base.metadata.drop_all(mcp_engine)
        Base.metadata.drop_all(rest_engine)


def test_upload_lecture_file_matches_rest_upload_on_identically_seeded_dbs(monkeypatch, tmp_path):
    """Real extraction/embedding boundary mocked identically for both
    surfaces (matching this project's grading-tools parity precedent);
    compares the persisted row each surface actually created."""
    import app.models  # noqa: F401

    fake_slides = {"valid": True, "slides": [{"slide_number": 1, "texts": {"slide_text": "Hello"}}]}
    fake_chunks = [{"slide_number": 1, "source": "slide_text", "chunk_index": 0, "chunk_text": "Hello"}]

    async def fake_save(session_id, filename, data):
        return f"{session_id}/lectures/{filename}"

    # lecture_tools.py imports these fresh inside the function body, so
    # patching the source module is enough; the REST router imports them at
    # module load time, so its own module namespace needs patching too.
    for target in ("app.services.lecture_extraction", "app.routers.lectures"):
        monkeypatch.setattr(f"{target}.extract_pptx_content", lambda _p: fake_slides)
        monkeypatch.setattr(f"{target}.chunk_slides", lambda _s: fake_chunks)
    monkeypatch.setattr("app.services.embeddings.upsert_chunk", lambda **kw: None)
    monkeypatch.setattr("app.routers.lectures.upsert_chunk", lambda **kw: None)
    monkeypatch.setattr("app.services.storage.save_lecture_file", fake_save)
    monkeypatch.setattr("app.routers.lectures.save_lecture_file", fake_save)

    mcp_engine, mcp_db = _fresh_db()
    rest_engine, rest_db = _fresh_db()
    try:
        _seed_session(mcp_db)
        _seed_session(rest_db)

        with patch("app.mcp.tools.lecture_tools.SessionLocal", return_value=mcp_db):
            mcp_result = upload_lecture_file(1, "lec.pptx", b"PK\x03\x04", instructor_id=1)

        from app.database import get_db
        from app.main import app

        def override_get_db():
            yield rest_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            token = create_access_token(1, UserRole.instructor)
            rest = client.post(
                "/api/v1/sessions/1/lectures",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": ("lec.pptx", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
            )
            assert rest.status_code == 201, rest.text
            rest_result = rest.json()
        finally:
            app.dependency_overrides.clear()

        assert mcp_result["original_filename"] == rest_result["original_filename"] == "lec.pptx"
        assert mcp_result["extracted"] is True and rest_result["extracted"] is True
        assert mcp_db.query(LectureFile).count() == 1
        assert rest_db.query(LectureFile).count() == 1
    finally:
        mcp_db.close()
        rest_db.close()
        Base.metadata.drop_all(mcp_engine)
        Base.metadata.drop_all(rest_engine)
