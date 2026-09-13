"""
Tests for embedded-image stripping — Phase 7.6 pre-commit fix.

strip_embedded_images() lives in the shared notebook parsing core and is
OPT-IN (strip_images=False by default), so grading/rubric callers still see
byte-identical cell content. Only the RAG embedding pipeline (upload flow +
backfill script) and the practice quiz turn it on; both embedding call sites
are covered here.

Run with:
    cd backend
    python -m pytest tests/test_notebook_image_stripping.py -v
"""

import importlib.util
from pathlib import Path

import nbformat
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.services.notebook import (
    IMAGE_PLACEHOLDER,
    extract_notebook_structure,
    extract_notebook_structure_from_bytes,
    strip_embedded_images,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
PAYLOAD = "iVBORw0KGgoAAAANSUhEUgAABuIAAANoCAIAAAC6BGvgAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQ" * 400
MD_IMAGE = f"![group_by_cities.png](data:image/png;base64,{PAYLOAD})"


def _notebook_bytes(*markdown_sources: str) -> bytes:
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_markdown_cell(src) for src in markdown_sources]
    nb.cells.append(nbformat.v4.new_code_cell("# TODO: complete\nresult = ______"))
    return nbformat.writes(nb).encode("utf-8")


class TestStripEmbeddedImages:

    def test_image_only_markdown_cell_becomes_just_the_placeholder(self):
        assert len(MD_IMAGE) > 40_000
        assert strip_embedded_images(MD_IMAGE) == IMAGE_PLACEHOLDER == "[image omitted]"

    def test_markdown_image_inside_text_keeps_the_text(self):
        text = f"**Split-Apply-Combine** is shown below:\n\n{MD_IMAGE}\n\nThen we aggregate."
        assert strip_embedded_images(text) == (
            "**Split-Apply-Combine** is shown below:\n\n[image omitted]\n\nThen we aggregate."
        )

    def test_wrapped_multiline_base64_and_optional_title(self):
        wrapped = "\n".join(PAYLOAD[i:i + 76] for i in range(0, 2000, 76))
        text = f'before ![chart]( data:image/jpeg;base64,{wrapped} "A chart") after'
        assert strip_embedded_images(text) == "before [image omitted] after"

    # Explicit ids: the default id would embed the whole payload, and Windows
    # caps the PYTEST_CURRENT_TEST environment variable at 32,767 characters.
    @pytest.mark.parametrize("tag", [
        f'<img src="data:image/png;base64,{PAYLOAD}">',
        f"<img width='400' src='data:image/gif;base64,{PAYLOAD}' alt=\"x\"/>",
        f'<IMG SRC="data:image/svg+xml;base64,\n{PAYLOAD[:500]}\n" height="800">',
    ], ids=["double-quoted", "single-quoted-with-attrs", "uppercase-wrapped"])
    def test_raw_html_img_tags(self, tag):
        assert strip_embedded_images(f"<p>Figure:</p>{tag}<p>done</p>") == "<p>Figure:</p>[image omitted]<p>done</p>"

    def test_multiple_images_each_replaced(self):
        text = f"{MD_IMAGE}\ntext\n<img src=\"data:image/png;base64,{PAYLOAD[:300]}\">"
        assert strip_embedded_images(text) == "[image omitted]\ntext\n[image omitted]"

    @pytest.mark.parametrize("text", [
        "# Pandas Merge Tutorial\nNo images here at all.",
        '<img src="db_joins.jpg" height="800", width="800">',
        "![diagram](https://example.com/diagram.png)",
        "![image.png](attachment:image.png)",
        "",
    ])
    def test_text_without_embedded_base64_images_is_unchanged(self, text):
        assert strip_embedded_images(text) == text


class TestExtractorFlag:

    def test_default_is_unchanged_and_opt_in_strips_for_path_and_bytes(self, tmp_path):
        data = _notebook_bytes(f"Intro text\n{MD_IMAGE}")
        path = tmp_path / "nb.ipynb"
        path.write_bytes(data)

        raw_path = extract_notebook_structure(str(path))
        raw_bytes = extract_notebook_structure_from_bytes(data)
        assert raw_path["cells"][0]["content"] == raw_bytes["cells"][0]["content"] == f"Intro text\n{MD_IMAGE}"

        for result in (
            extract_notebook_structure(str(path), strip_images=True),
            extract_notebook_structure_from_bytes(data, strip_images=True),
        ):
            assert result["valid"] is True
            assert result["cells"][0]["content"] == "Intro text\n[image omitted]"
            # heuristic_hint and the untouched code cell are unaffected
            assert [c["heuristic_hint"] for c in result["cells"]] == [
                c["heuristic_hint"] for c in raw_path["cells"]
            ]
            assert result["cells"][1] == raw_path["cells"][1]


# ── Embedding call sites (7.2) ────────────────────────────────────────────────

@pytest.fixture()
def db():
    import app.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(LMSSession(id=4, title="Week 2 Day 2"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _capture_upserts(monkeypatch, module):
    calls = []
    monkeypatch.setattr(module, "upsert_chunk", lambda chunk_id, text, metadata: calls.append((chunk_id, text)))
    return calls


def test_upload_embedding_flow_embeds_stripped_text(tmp_path, monkeypatch):
    from app.routers import assignments

    path = tmp_path / "dataframe_basics.ipynb"
    path.write_bytes(_notebook_bytes("A DataFrame is a table.", MD_IMAGE))
    calls = _capture_upserts(monkeypatch, assignments)
    unsolved = UnsolvedFile(id=10, session_id=4, original_filename=path.name, file_path="4/assignments/x.ipynb")

    assignments._embed_unsolved_file_cells(unsolved, path, 4)

    assert unsolved.embedded is True
    assert dict(calls)["notebook:10:1"] == "[image omitted]"
    assert all("base64" not in text for _, text in calls)


def test_backfill_script_embeds_stripped_text(db, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("backfill_embeddings", BACKEND_DIR / "scripts" / "backfill_embeddings.py")
    backfill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill)

    path = tmp_path / "pandas_group_by.ipynb"
    path.write_bytes(_notebook_bytes(MD_IMAGE, "Group by splits the data."))
    monkeypatch.setattr(backfill, "absolute_path", lambda _rel: path)
    calls = _capture_upserts(monkeypatch, backfill)
    db.add(UnsolvedFile(id=11, session_id=4, original_filename=path.name, file_path="4/assignments/g.ipynb"))
    db.commit()

    files_embedded, cells_embedded, _, files_failed = backfill.backfill_unsolved_files(db)

    assert (files_embedded, files_failed) == (1, 0)
    assert dict(calls)["notebook:11:0"] == "[image omitted]"
    assert all("base64" not in text for _, text in calls)
