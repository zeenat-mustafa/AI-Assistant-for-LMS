"""
Notebook service — Phase 2, Sub-feature 1.

Provides three public functions:
  - parse_notebook_file(ipynb_path)       → structured dict, never raises
  - extract_requirements_text(ipynb_path) → markdown text string, never raises
  - extract_notebooks_from_zip(source, extract_dir) → list of absolute Paths

All functions are deliberately exception-safe: any internal failure is caught,
logged as a warning, and communicated through the return value rather than by
propagating the exception to callers.
"""

import io
import logging
import os
import zipfile
from pathlib import Path
from typing import Union

import nbformat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def parse_notebook_file(ipynb_path: Union[str, Path]) -> dict:
    """
    Parse a Jupyter notebook file and return a structured dict.

    Returns
    -------
    {
        "markdown_text": str,            # all markdown cells concatenated
        "code_cells":    list[dict],     # [{"source": str, "outputs": list[str]}]
        "valid":         bool,
        "error":         str | None,
    }

    This function NEVER raises an exception.  Any failure (file not found,
    malformed JSON, wrong nbformat version, etc.) is captured and returned as
    ``valid=False`` with a descriptive ``error`` string.
    """
    result: dict = {
        "markdown_text": "",
        "code_cells": [],
        "valid": False,
        "error": None,
    }

    try:
        path = Path(ipynb_path)

        # ── Basic existence / readability check ───────────────────────────
        if not path.exists():
            result["error"] = f"File not found: {ipynb_path}"
            return result

        if path.stat().st_size == 0:
            result["error"] = f"File is empty: {ipynb_path}"
            return result

        # ── Read raw bytes first so we can give a clear error on binary garbage
        raw = path.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            result["error"] = (
                f"File does not appear to be a valid UTF-8 text file (binary or corrupted): "
                f"{ipynb_path}"
            )
            return result

        # ── Parse with nbformat ───────────────────────────────────────────
        # as_version=4 normalises older formats; NO_CONVERT lets nbformat keep
        # the native version when it's already ≥4.
        try:
            nb = nbformat.read(
                io.StringIO(raw.decode("utf-8")),
                as_version=nbformat.NO_CONVERT,
            )
        except nbformat.reader.NotJSONError:
            result["error"] = (
                f"Notebook file contains invalid JSON (corrupted or not a real .ipynb): "
                f"{ipynb_path}"
            )
            return result
        except Exception as exc:
            result["error"] = f"nbformat could not parse notebook ({type(exc).__name__}): {exc}"
            return result

        # ── Extract cell content ──────────────────────────────────────────
        markdown_parts: list[str] = []
        code_cells: list[dict] = []

        for cell in nb.get("cells", []):
            cell_type = cell.get("cell_type", "")
            source: str = cell.get("source", "") or ""

            if cell_type == "markdown":
                if source.strip():
                    markdown_parts.append(source)

            elif cell_type == "code":
                outputs: list[str] = []
                for output in cell.get("outputs", []):
                    # nbformat output types: stream, display_data, execute_result, error
                    output_type = output.get("output_type", "")

                    if output_type == "stream":
                        text = output.get("text", "")
                        if isinstance(text, list):
                            text = "".join(text)
                        if text.strip():
                            outputs.append(text)

                    elif output_type in ("display_data", "execute_result"):
                        # Prefer plain text; fall back to repr of other mime types
                        data = output.get("data", {})
                        text = data.get("text/plain", "")
                        if isinstance(text, list):
                            text = "".join(text)
                        if text.strip():
                            outputs.append(text)

                    elif output_type == "error":
                        ename = output.get("ename", "")
                        evalue = output.get("evalue", "")
                        tb_lines = output.get("traceback", [])
                        # Strip ANSI escape codes from traceback for clean text
                        import re as _re
                        ansi_escape = _re.compile(r"\x1b\[[0-9;]*m")
                        tb_clean = [
                            ansi_escape.sub("", line) for line in tb_lines
                        ]
                        error_text = f"{ename}: {evalue}"
                        if tb_clean:
                            error_text += "\n" + "\n".join(tb_clean)
                        outputs.append(error_text)

                code_cells.append({"source": source, "outputs": outputs})

        result["markdown_text"] = "\n\n".join(markdown_parts)
        result["code_cells"] = code_cells
        result["valid"] = True
        result["error"] = None

    except Exception as exc:
        # Catch-all: should never reach here given the guards above, but we
        # guarantee no exception escapes this function.
        logger.warning(
            "Unexpected error while parsing notebook '%s': %s",
            ipynb_path, exc, exc_info=True,
        )
        result["valid"] = False
        result["error"] = f"Unexpected parse error ({type(exc).__name__}): {exc}"

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_requirements_text(ipynb_path: Union[str, Path]) -> str:
    """
    Return the concatenated markdown text from a notebook.

    Calls ``parse_notebook_file`` internally.  If the notebook is invalid or
    unreadable, logs a warning and returns an empty string — never raises.

    Parameters
    ----------
    ipynb_path:
        Absolute path to the ``.ipynb`` file.

    Returns
    -------
    str
        All markdown cell content joined by double newlines, or ``""`` on
        any failure.
    """
    parsed = parse_notebook_file(str(ipynb_path))
    if not parsed["valid"]:
        logger.warning(
            "extract_requirements_text: could not parse '%s': %s",
            ipynb_path, parsed["error"],
        )
        return ""
    return parsed["markdown_text"]


def extract_notebooks_from_zip(
    source: Union[bytes, str, Path],
    extract_dir: Union[str, Path],
) -> list[Path]:
    """
    Extract all ``.ipynb`` files from a ZIP archive and return their paths.

    The function walks every file in the archive at any nesting depth and
    extracts only ``.ipynb`` files, ignoring everything else (images, .py,
    .csv, __MACOSX metadata, etc.).

    Parameters
    ----------
    source:
        Either raw ZIP bytes (as received from an HTTP upload) **or** a
        filesystem path (``str`` / ``Path``) pointing to a ``.zip`` file.
    extract_dir:
        Directory where extracted notebooks will be written.  Created if it
        does not exist.

    Returns
    -------
    list[Path]
        Absolute paths to every ``.ipynb`` file found.  Empty list if the
        archive contains no notebooks or if extraction fails.

    This function NEVER raises an exception.  Failures are logged as warnings.
    """
    extract_path = Path(extract_dir)
    found: list[Path] = []

    try:
        extract_path.mkdir(parents=True, exist_ok=True)

        # ── Validate and open the ZIP ─────────────────────────────────────
        if isinstance(source, (bytes, bytearray)):
            buf = io.BytesIO(source)
            if not zipfile.is_zipfile(io.BytesIO(source)):  # separate read head
                logger.warning(
                    "extract_notebooks_from_zip: source bytes do not appear to be "
                    "a valid ZIP file."
                )
                return found
            buf.seek(0)
            zip_file_obj = zipfile.ZipFile(buf)
        else:
            src_path = Path(source)
            if not zipfile.is_zipfile(src_path):
                logger.warning(
                    "extract_notebooks_from_zip: '%s' does not appear to be a "
                    "valid ZIP file.", src_path,
                )
                return found
            zip_file_obj = zipfile.ZipFile(src_path)

        with zip_file_obj as zf:
            for member in zf.infolist():
                # Skip directories, macOS metadata, and non-.ipynb files
                if member.is_dir():
                    continue
                member_path = Path(member.filename)
                if member_path.suffix.lower() != ".ipynb":
                    continue
                # Skip __MACOSX resource fork entries
                if any(part.startswith("__MACOSX") for part in member_path.parts):
                    continue

                # ── Flatten the archive path into extract_dir ─────────────
                # Use only the filename (not the full archive path) to avoid
                # recreating deep directory structures that complicate later
                # file handling.  If two notebooks share a filename, append a
                # numeric suffix to avoid collisions.
                safe_name = _safe_extract_name(member_path.name, extract_path, found)
                dest = extract_path / safe_name

                try:
                    dest.write_bytes(zf.read(member.filename))
                    found.append(dest.resolve())
                    logger.debug(
                        "Extracted notebook from zip: %s → %s",
                        member.filename, dest,
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not extract '%s' from zip: %s", member.filename, exc
                    )
                    # Skip this entry and continue with the rest

    except zipfile.BadZipFile as exc:
        logger.warning(
            "extract_notebooks_from_zip: not a valid ZIP file: %s", exc
        )
    except Exception as exc:
        logger.warning(
            "extract_notebooks_from_zip: unexpected error: %s",
            exc, exc_info=True,
        )

    return found


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _safe_extract_name(
    filename: str,
    extract_dir: Path,
    already_found: list[Path],
) -> str:
    """
    Return a filename that does not collide with files already extracted to
    ``extract_dir``.  If ``filename`` is taken, appends ``_1``, ``_2``, etc.
    before the extension.

    Example: two notebooks both named ``solution.ipynb`` → ``solution.ipynb``
    and ``solution_1.ipynb``.
    """
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = filename
    counter = 0

    existing_names = {p.name for p in already_found}
    while (extract_dir / candidate).exists() or candidate in existing_names:
        counter += 1
        candidate = f"{stem}_{counter}{suffix}"

    return candidate
