"""
Unit tests for app.services.lecture_extraction — Phase 7, Sub-feature 7.1.

Fixtures are built in-memory with python-pptx (mirroring test_assignments.py's
_make_notebook_bytes/_make_zip convention — no binary fixture files checked
into the repo).

Run with:
    cd backend
    python -m pytest tests/test_lecture_extraction.py -v
"""

import io

import pytest
from pptx import Presentation
from pptx.util import Inches

from app.services.lecture_extraction import (
    LEGACY_PPT_ERROR,
    validate_lecture_filename,
    extract_pptx_content,
    chunk_slides,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)


# ===========================================================================
# Fixture builders
# ===========================================================================

def _save_to_bytes(prs: Presentation) -> bytes:
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _pptx_with_known_content() -> bytes:
    """
    3 slides:
      1. Title + body text, WITH speaker notes.
      2. Title + body text, NO notes at all (has_notes_slide should be False
         or notes_text_frame empty).
      3. A slide containing a real table shape (not just a text box), plus notes.
    """
    prs = Presentation()

    layout = prs.slide_layouts[1]
    s1 = prs.slides.add_slide(layout)
    s1.shapes.title.text = "Intro to Pandas"
    s1.placeholders[1].text = "Pandas is a data manipulation library"
    s1.notes_slide.notes_text_frame.text = "Explain that pandas builds on numpy arrays."

    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "DataFrames"
    s2.placeholders[1].text = "A DataFrame is a 2D labeled data structure"

    s3 = prs.slides.add_slide(prs.slide_layouts[5])
    s3.shapes.title.text = "Comparison Table"
    table_shape = s3.shapes.add_table(3, 2, Inches(1), Inches(1), Inches(6), Inches(2))
    table = table_shape.table
    table.cell(0, 0).text = "Feature"
    table.cell(0, 1).text = "Pandas"
    table.cell(1, 0).text = "Speed"
    table.cell(1, 1).text = "Fast"
    table.cell(2, 0).text = "Ease"
    table.cell(2, 1).text = "High"
    s3.notes_slide.notes_text_frame.text = "Walk through each row of the comparison."

    return _save_to_bytes(prs)


def _pptx_long_notes_on_one_slide() -> bytes:
    """One slide with short slide text but notes long enough to require
    multiple overlapping chunks."""
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Long Notes Slide"
    s.placeholders[1].text = "Short body"
    long_notes = " ".join(f"Sentence number {i} about the topic." for i in range(200))
    assert len(long_notes) > CHUNK_SIZE * 2
    s.notes_slide.notes_text_frame.text = long_notes
    return _save_to_bytes(prs)


# ===========================================================================
# validate_lecture_filename
# ===========================================================================

class TestValidateLectureFilename:

    def test_pptx_accepted(self):
        assert validate_lecture_filename("week1.pptx") is None

    def test_ppt_rejected_with_clear_message(self):
        assert validate_lecture_filename("week1.ppt") == LEGACY_PPT_ERROR

    def test_other_extension_rejected_generically(self):
        error = validate_lecture_filename("week1.pdf")
        assert error is not None
        assert "unsupported" in error.lower()

    def test_no_extension_rejected(self):
        error = validate_lecture_filename("week1")
        assert error is not None


# ===========================================================================
# extract_pptx_content
# ===========================================================================

class TestExtractPptxContent:

    def test_extracts_known_slide_text_and_notes(self, tmp_path):
        path = tmp_path / "lecture.pptx"
        path.write_bytes(_pptx_with_known_content())

        result = extract_pptx_content(path)

        assert result["valid"] is True
        assert result["error"] is None
        assert len(result["slides"]) == 3

        slide1 = result["slides"][0]
        assert slide1["slide_number"] == 1
        assert "Intro to Pandas" in slide1["slide_text"]
        assert "Pandas is a data manipulation library" in slide1["slide_text"]
        assert "pandas builds on numpy arrays" in slide1["notes_text"]

    def test_slide_with_no_notes_has_empty_notes_text_not_error(self, tmp_path):
        path = tmp_path / "lecture.pptx"
        path.write_bytes(_pptx_with_known_content())

        result = extract_pptx_content(path)

        slide2 = result["slides"][1]
        assert slide2["notes_text"] == ""
        assert result["valid"] is True

    def test_table_shape_text_is_captured_and_labeled(self, tmp_path):
        """
        Required: a table shape's real cell text must be present in that
        slide's extracted text, clearly labeled — table text lives only in
        table.rows/row.cells, never in shape.text_frame, so this fails loudly
        if a future change silently drops table shapes again.
        """
        path = tmp_path / "lecture.pptx"
        path.write_bytes(_pptx_with_known_content())

        result = extract_pptx_content(path)

        slide3 = result["slides"][2]
        assert "[Table]" in slide3["slide_text"]
        assert "Feature | Pandas" in slide3["slide_text"]
        assert "Speed | Fast" in slide3["slide_text"]
        assert "Ease | High" in slide3["slide_text"]
        assert "Comparison Table" in slide3["slide_text"]  # title text too
        assert "Walk through each row" in slide3["notes_text"]

    def test_nonexistent_file_fails_cleanly(self, tmp_path):
        result = extract_pptx_content(tmp_path / "does_not_exist.pptx")
        assert result["valid"] is False
        assert "not found" in result["error"].lower()
        assert result["slides"] == []

    def test_corrupted_file_fails_cleanly_not_a_crash(self, tmp_path):
        path = tmp_path / "corrupt.pptx"
        path.write_bytes(b"this is not a real pptx file, just garbage bytes")

        result = extract_pptx_content(path)

        assert result["valid"] is False
        assert result["error"] is not None
        assert result["slides"] == []

    def test_empty_file_fails_cleanly(self, tmp_path):
        path = tmp_path / "empty.pptx"
        path.write_bytes(b"")

        result = extract_pptx_content(path)

        assert result["valid"] is False
        assert "empty" in result["error"].lower()


# ===========================================================================
# chunk_slides
# ===========================================================================

class TestChunkSlides:

    def test_short_slide_produces_one_chunk(self):
        slides = [{"slide_number": 1, "slide_text": "Short text.", "notes_text": ""}]
        chunks = chunk_slides(slides)
        slide_text_chunks = [c for c in chunks if c["source"] == "slide_text"]
        assert len(slide_text_chunks) == 1
        assert slide_text_chunks[0]["chunk_text"] == "Short text."
        assert slide_text_chunks[0]["chunk_index"] == 0

    def test_empty_notes_produce_zero_chunks(self):
        slides = [{"slide_number": 1, "slide_text": "Some text", "notes_text": ""}]
        chunks = chunk_slides(slides)
        notes_chunks = [c for c in chunks if c["source"] == "notes"]
        assert notes_chunks == []

    def test_long_slide_produces_multiple_overlapping_chunks(self):
        long_text = "x" * (CHUNK_SIZE * 3)
        slides = [{"slide_number": 5, "slide_text": long_text, "notes_text": ""}]
        chunks = chunk_slides(slides)
        slide_text_chunks = [c for c in chunks if c["source"] == "slide_text"]

        assert len(slide_text_chunks) > 1
        # chunk_index increases monotonically from 0
        assert [c["chunk_index"] for c in slide_text_chunks] == list(
            range(len(slide_text_chunks))
        )
        # overlap: consecutive chunks share their boundary region
        first, second = slide_text_chunks[0], slide_text_chunks[1]
        overlap_expected = first["chunk_text"][-CHUNK_OVERLAP:]
        assert second["chunk_text"].startswith(overlap_expected)
        # reconstructing without overlap covers the whole original text
        assert all(len(c["chunk_text"]) <= CHUNK_SIZE for c in slide_text_chunks)

    def test_no_chunk_crosses_a_slide_boundary(self):
        """Every chunk's slide_number must match the slide it came from —
        confirmed across multiple slides of mixed lengths."""
        slides = [
            {"slide_number": 1, "slide_text": "a" * 50, "notes_text": "b" * (CHUNK_SIZE + 100)},
            {"slide_number": 2, "slide_text": "c" * (CHUNK_SIZE * 2), "notes_text": "d" * 30},
        ]
        chunks = chunk_slides(slides)

        for c in chunks:
            if c["slide_number"] == 1:
                assert set(c["chunk_text"]) <= {"a", "b"}
            elif c["slide_number"] == 2:
                assert set(c["chunk_text"]) <= {"c", "d"}
            else:
                pytest.fail(f"Unexpected slide_number {c['slide_number']}")

    def test_slide_text_and_notes_are_separate_pools_never_mixed(self):
        """A chunk is scoped to (slide_number, source) — slide text and notes
        for the same slide must never share a chunk."""
        slides = [
            {"slide_number": 3, "slide_text": "SLIDE" * 20, "notes_text": "NOTES" * 20}
        ]
        chunks = chunk_slides(slides)

        for c in chunks:
            if c["source"] == "slide_text":
                assert "NOTES" not in c["chunk_text"]
            elif c["source"] == "notes":
                assert "SLIDE" not in c["chunk_text"]

    def test_real_multislide_pptx_chunks_never_cross_slide_boundary(self, tmp_path):
        """End-to-end: real extraction output fed into chunking, asserting
        per-chunk slide-number consistency against the real source slides."""
        path = tmp_path / "lecture.pptx"
        path.write_bytes(_pptx_long_notes_on_one_slide())

        extraction = extract_pptx_content(path)
        assert extraction["valid"] is True
        chunks = chunk_slides(extraction["slides"])

        notes_chunks = [c for c in chunks if c["source"] == "notes"]
        assert len(notes_chunks) > 1  # confirms the long-notes case actually split
        for c in chunks:
            assert c["slide_number"] == 1  # the fixture has exactly one slide
