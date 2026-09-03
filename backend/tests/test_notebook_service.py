"""
Tests for backend/app/services/notebook.py — Phase 2, Sub-feature 1.

All fixtures are created on-the-fly in a tmp_path so there are no
pre-existing data dependencies.  Run with:

    cd backend
    python -m pytest tests/test_notebook_service.py -v

Cases covered
─────────────
parse_notebook_file
  1. Valid notebook  → valid=True, markdown_text populated, code_cells populated
  2. Malformed JSON  → valid=False, error message, no exception raised
  3. Empty file      → valid=False, error message, no exception raised
  4. Binary garbage  → valid=False, error message, no exception raised
  5. Missing file    → valid=False, error message, no exception raised
  6. Notebook with code outputs (stream + execute_result + error type)

extract_requirements_text
  7. Valid notebook  → returns markdown text
  8. Malformed file  → returns "" (no exception)

extract_notebooks_from_zip
  9.  Flat zip (notebooks at root)               → finds all .ipynb
  10. Nested zip (notebooks 2+ folders deep)     → finds all .ipynb recursively
  11. Mixed zip (non-.ipynb files ignored)       → only .ipynb returned
  12. Two notebooks with the same filename       → both extracted, no collision
  13. Bad zip (random bytes)                     → returns [], no exception
  14. Empty zip                                  → returns []
  15. __MACOSX entries skipped
"""

import io
import json
import zipfile
from pathlib import Path

import nbformat
import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import sys, os
# Make sure `app` is importable when running pytest from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.notebook import (
    extract_notebooks_from_zip,
    extract_requirements_text,
    parse_notebook_file,
)


# ===========================================================================
# Fixture helpers
# ===========================================================================

def _make_notebook(
    markdown_cells: list[str] | None = None,
    code_cells: list[dict] | None = None,
    nbformat_version: int = 4,
    nbformat_minor: int = 5,
) -> bytes:
    """
    Build a minimal valid .ipynb as bytes using nbformat so it matches
    exactly what nbformat.read() expects.
    """
    nb = nbformat.v4.new_notebook()
    for md in (markdown_cells or []):
        nb.cells.append(nbformat.v4.new_markdown_cell(md))
    for cc in (code_cells or []):
        cell = nbformat.v4.new_code_cell(cc.get("source", ""))
        # Attach pre-built outputs if provided
        cell.outputs = cc.get("outputs", [])
        nb.cells.append(cell)
    buf = io.StringIO()
    nbformat.write(nb, buf)
    return buf.getvalue().encode("utf-8")


def _make_zip(members: dict[str, bytes]) -> bytes:
    """
    Build an in-memory ZIP.
    ``members`` maps archive path → file bytes.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in members.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


# ===========================================================================
# parse_notebook_file
# ===========================================================================

class TestParseNotebookFile:

    def test_valid_notebook_returns_parsed_content(self, tmp_path):
        """Case 1: A well-formed notebook is parsed correctly."""
        nb_bytes = _make_notebook(
            markdown_cells=["# Assignment 1\n\nWrite a function that adds two numbers."],
            code_cells=[{"source": "def add(a, b):\n    return a + b"}],
        )
        nb_file = tmp_path / "valid.ipynb"
        nb_file.write_bytes(nb_bytes)

        result = parse_notebook_file(nb_file)

        assert result["valid"] is True
        assert result["error"] is None
        assert "Assignment 1" in result["markdown_text"]
        assert "Write a function" in result["markdown_text"]
        assert len(result["code_cells"]) == 1
        assert "def add" in result["code_cells"][0]["source"]

    def test_malformed_json_returns_invalid(self, tmp_path):
        """Case 2: Corrupted JSON → valid=False with descriptive error, no exception."""
        bad_file = tmp_path / "corrupt.ipynb"
        bad_file.write_text("{this is not valid json !!!}", encoding="utf-8")

        result = parse_notebook_file(bad_file)

        assert result["valid"] is False
        assert result["error"] is not None
        assert result["markdown_text"] == ""
        assert result["code_cells"] == []

    def test_empty_file_returns_invalid(self, tmp_path):
        """Case 3: Zero-byte file → valid=False."""
        empty = tmp_path / "empty.ipynb"
        empty.write_bytes(b"")

        result = parse_notebook_file(empty)

        assert result["valid"] is False
        assert "empty" in result["error"].lower()

    def test_binary_garbage_returns_invalid(self, tmp_path):
        """Case 4: Binary data (e.g. a PNG accidentally named .ipynb) → valid=False."""
        binary_file = tmp_path / "binary.ipynb"
        binary_file.write_bytes(bytes(range(256)) * 10)

        result = parse_notebook_file(binary_file)

        assert result["valid"] is False
        assert result["error"] is not None

    def test_missing_file_returns_invalid(self, tmp_path):
        """Case 5: Non-existent path → valid=False with 'not found' message."""
        result = parse_notebook_file(tmp_path / "nonexistent.ipynb")

        assert result["valid"] is False
        assert "not found" in result["error"].lower()

    def test_notebook_with_various_output_types(self, tmp_path):
        """Case 6: Outputs of stream, execute_result and error type are extracted."""
        stream_output = nbformat.v4.new_output(
            output_type="stream",
            name="stdout",
            text="Hello from print\n",
        )
        exec_result = nbformat.v4.new_output(
            output_type="execute_result",
            data={"text/plain": "42"},
            metadata={},
            execution_count=1,
        )
        error_output = nbformat.v4.new_output(
            output_type="error",
            ename="ZeroDivisionError",
            evalue="division by zero",
            traceback=["Traceback ...", "ZeroDivisionError: division by zero"],
        )
        nb_bytes = _make_notebook(
            code_cells=[
                {"source": "print('Hello')", "outputs": [stream_output]},
                {"source": "1 + 1",           "outputs": [exec_result]},
                {"source": "1 / 0",            "outputs": [error_output]},
            ]
        )
        nb_file = tmp_path / "outputs.ipynb"
        nb_file.write_bytes(nb_bytes)

        result = parse_notebook_file(nb_file)

        assert result["valid"] is True
        assert len(result["code_cells"]) == 3
        assert any("Hello from print" in o for o in result["code_cells"][0]["outputs"])
        assert any("42" in o for o in result["code_cells"][1]["outputs"])
        assert any("ZeroDivisionError" in o for o in result["code_cells"][2]["outputs"])

    def test_multiple_markdown_cells_concatenated(self, tmp_path):
        """Markdown from several cells is joined with double newlines."""
        nb_bytes = _make_notebook(
            markdown_cells=[
                "# Part 1\nDo this.",
                "# Part 2\nDo that.",
                "## Notes\nAdditional context.",
            ]
        )
        nb_file = tmp_path / "multi_md.ipynb"
        nb_file.write_bytes(nb_bytes)

        result = parse_notebook_file(nb_file)

        assert result["valid"] is True
        assert "Part 1" in result["markdown_text"]
        assert "Part 2" in result["markdown_text"]
        assert "Notes" in result["markdown_text"]
        # Cells are separated by double newlines
        assert "\n\n" in result["markdown_text"]


# ===========================================================================
# extract_requirements_text
# ===========================================================================

class TestExtractRequirementsText:

    def test_returns_markdown_from_valid_notebook(self, tmp_path):
        """Case 7: Valid notebook → markdown text returned."""
        nb_bytes = _make_notebook(
            markdown_cells=["## Task\nImplement a linked list."],
            code_cells=[{"source": "# starter code"}],
        )
        nb_file = tmp_path / "assignment.ipynb"
        nb_file.write_bytes(nb_bytes)

        text = extract_requirements_text(nb_file)

        assert "Task" in text
        assert "linked list" in text

    def test_returns_empty_string_for_malformed_file(self, tmp_path):
        """Case 8: Malformed notebook → empty string, no exception."""
        bad_file = tmp_path / "bad.ipynb"
        bad_file.write_text("totally broken content %%%", encoding="utf-8")

        text = extract_requirements_text(bad_file)

        assert text == ""

    def test_returns_empty_string_for_missing_file(self, tmp_path):
        """Missing file → empty string, no exception."""
        text = extract_requirements_text(tmp_path / "ghost.ipynb")
        assert text == ""

    def test_accepts_path_object(self, tmp_path):
        """Accepts pathlib.Path (not just str)."""
        nb_bytes = _make_notebook(markdown_cells=["# Hello"])
        nb_file = tmp_path / "nb.ipynb"
        nb_file.write_bytes(nb_bytes)

        text = extract_requirements_text(nb_file)  # Path, not str
        assert "Hello" in text

    def test_accepts_str_path(self, tmp_path):
        """Accepts plain str path."""
        nb_bytes = _make_notebook(markdown_cells=["# World"])
        nb_file = tmp_path / "nb2.ipynb"
        nb_file.write_bytes(nb_bytes)

        text = extract_requirements_text(str(nb_file))
        assert "World" in text


# ===========================================================================
# extract_notebooks_from_zip
# ===========================================================================

class TestExtractNotebooksFromZip:

    def test_flat_zip_finds_all_notebooks(self, tmp_path):
        """Case 9: Notebooks at archive root are all found."""
        nb1 = _make_notebook(markdown_cells=["# NB1"])
        nb2 = _make_notebook(markdown_cells=["# NB2"])
        zip_bytes = _make_zip({
            "notebook1.ipynb": nb1,
            "notebook2.ipynb": nb2,
        })

        found = extract_notebooks_from_zip(zip_bytes, tmp_path / "out")

        assert len(found) == 2
        names = {p.name for p in found}
        assert "notebook1.ipynb" in names
        assert "notebook2.ipynb" in names
        for p in found:
            assert p.exists()

    def test_nested_zip_finds_notebooks_at_any_depth(self, tmp_path):
        """Case 10: Notebooks nested 2+ folders deep are found recursively."""
        nb_top   = _make_notebook(markdown_cells=["# Top"])
        nb_deep  = _make_notebook(markdown_cells=["# Deep"])
        nb_deeper = _make_notebook(markdown_cells=["# Deeper"])
        zip_bytes = _make_zip({
            "top_level.ipynb":                      nb_top,
            "subdir/deep.ipynb":                    nb_deep,
            "subdir/level2/level3/deeper.ipynb":    nb_deeper,
        })
        out_dir = tmp_path / "nested_out"

        found = extract_notebooks_from_zip(zip_bytes, out_dir)

        assert len(found) == 3
        names = {p.name for p in found}
        assert "top_level.ipynb" in names
        assert "deep.ipynb" in names
        assert "deeper.ipynb" in names

    def test_mixed_zip_ignores_non_notebooks(self, tmp_path):
        """Case 11: .py, .csv, .png and other files in the archive are silently ignored."""
        nb = _make_notebook(markdown_cells=["# Only me"])
        zip_bytes = _make_zip({
            "solution.ipynb": nb,
            "helper.py":      b"def foo(): pass",
            "data.csv":       b"col1,col2\n1,2",
            "image.png":      bytes(range(50)),
            "README.txt":     b"ignore me",
        })

        found = extract_notebooks_from_zip(zip_bytes, tmp_path / "mixed_out")

        assert len(found) == 1
        assert found[0].name == "solution.ipynb"

    def test_filename_collision_resolved_without_data_loss(self, tmp_path):
        """Case 12: Two notebooks with the same filename both survive."""
        nb_a = _make_notebook(markdown_cells=["# Version A"])
        nb_b = _make_notebook(markdown_cells=["# Version B"])
        zip_bytes = _make_zip({
            "folder_a/solution.ipynb": nb_a,
            "folder_b/solution.ipynb": nb_b,
        })

        found = extract_notebooks_from_zip(zip_bytes, tmp_path / "collision_out")

        assert len(found) == 2
        # Both files must exist on disk
        for p in found:
            assert p.exists()
            assert p.suffix == ".ipynb"
        # Names must differ (collision avoidance)
        assert found[0].name != found[1].name

    def test_bad_zip_bytes_returns_empty_list(self, tmp_path):
        """Case 13: Garbage bytes → empty list, no exception."""
        garbage = b"this is not a zip file at all" * 20

        found = extract_notebooks_from_zip(garbage, tmp_path / "bad_out")

        assert found == []

    def test_empty_zip_returns_empty_list(self, tmp_path):
        """Case 14: ZIP with no entries → empty list."""
        empty_zip = _make_zip({})

        found = extract_notebooks_from_zip(empty_zip, tmp_path / "empty_out")

        assert found == []

    def test_macosx_entries_are_skipped(self, tmp_path):
        """Case 15: __MACOSX resource fork entries are not extracted."""
        nb = _make_notebook(markdown_cells=["# Real notebook"])
        zip_bytes = _make_zip({
            "real.ipynb":                    nb,
            "__MACOSX/._real.ipynb":         b"resource fork garbage",
            "__MACOSX/subdir/._other.ipynb": b"more garbage",
        })

        found = extract_notebooks_from_zip(zip_bytes, tmp_path / "mac_out")

        assert len(found) == 1
        assert found[0].name == "real.ipynb"

    def test_accepts_path_object_as_source(self, tmp_path):
        """source can be a Path pointing to a .zip file on disk."""
        nb = _make_notebook(markdown_cells=["# From file"])
        zip_bytes = _make_zip({"nb.ipynb": nb})
        zip_file = tmp_path / "archive.zip"
        zip_file.write_bytes(zip_bytes)

        found = extract_notebooks_from_zip(zip_file, tmp_path / "file_out")

        assert len(found) == 1
        assert found[0].name == "nb.ipynb"

    def test_zip_with_only_non_notebooks_returns_empty(self, tmp_path):
        """ZIP containing only .py files → empty list."""
        zip_bytes = _make_zip({
            "script.py": b"print('hi')",
            "README.md": b"# readme",
        })

        found = extract_notebooks_from_zip(zip_bytes, tmp_path / "no_nb_out")

        assert found == []
