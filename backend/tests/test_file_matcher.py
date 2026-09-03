"""
Tests for backend/app/services/file_matcher.py -- Phase 2, Sub-feature 2.

All DB-dependent tests use an in-memory SQLite database built from the real
ORM models so there are zero external dependencies.  Run with:

    cd backend
    python -m pytest tests/test_file_matcher.py -v

Cases covered
-------------
normalize_text
  1. Lowercases input
  2. Strips markdown symbols (#, *, -, _, backticks)
  3. Collapses all whitespace (spaces, newlines, tabs) to single spaces
  4. Returns empty string for empty/whitespace-only input
  5. Combined cleanup

content_similarity_score
  6. Empty unsolved_text returns 0.0
  7. None unsolved_text returns 0.0
  8. Whitespace-only unsolved returns 0.0
  9. Identical texts return 1.0
  10. Completely different texts return a low score (< 0.4)
  11. Partial overlap returns intermediate score
  12. Empty submitted with non-empty unsolved returns 0.0

filename_similarity_score
  13. Identical stems return 1.0
  14. Completely different stems return low score
  15. Extensions are ignored
  16. Non-alphanumeric characters stripped before comparison
  17. Case-insensitive

match_submission_file_to_unsolved
  18. Empty candidates list -> status "no_unsolved_files", confidence 0.0
  19. Single candidate -> always matched (confidence 1.0), regardless of content
  20. Single candidate with matching content -> also auto-matched
  21. HIGH content overlap -> status "matched", picks the right candidate
  22. No content overlap with any candidate -> status "ambiguous"
  23. Multiple similar-looking filenames but different content -> picks by content
  24. Content wins even when filename matches the wrong candidate
  25. combined_score at exactly 0.55 threshold -> "matched"
  26. combined_score just below 0.55 -> "ambiguous"

match_all_files_in_submission  (in-memory SQLite + real models)
  27. Single unsolved file -> auto-matched (confidence 1.0)
  28. Multiple unsolved files, clearly matching one -> matched to correct one
  29. No unsolved files with parsed text -> "no_unsolved_files"
  30. Multiple submission files -> each matched to its correct unsolved file
  31. Unknown submission_id -> returns [] without raising
  32. matched_unsolved_file_id written to DB after call
"""

import io
import sys
from pathlib import Path
from unittest.mock import patch

import nbformat
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Make `app` importable when running pytest from backend/
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.file_matcher import (
    content_similarity_score,
    filename_similarity_score,
    match_all_files_in_submission,
    match_submission_file_to_unsolved,
    normalize_text,
)


# ===========================================================================
# Shared helpers
# ===========================================================================

def _make_notebook_bytes(
    markdown_cells: list[str] | None = None,
    code_cells: list[str] | None = None,
) -> bytes:
    """Build a minimal valid .ipynb as raw bytes."""
    nb = nbformat.v4.new_notebook()
    for md in (markdown_cells or []):
        nb.cells.append(nbformat.v4.new_markdown_cell(md))
    for src in (code_cells or []):
        nb.cells.append(nbformat.v4.new_code_cell(src))
    buf = io.StringIO()
    nbformat.write(nb, buf)
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """
    Fresh in-memory SQLite database using the project's real SQLAlchemy models.
    Yields a connected Session; tears it down after each test.
    """
    from app.database import Base
    # Import every model so its table is registered on Base.metadata
    import app.models.user           # noqa: F401
    import app.models.session        # noqa: F401
    import app.models.unsolved_file  # noqa: F401
    import app.models.submission     # noqa: F401
    import app.models.submission_file  # noqa: F401
    import app.models.grade          # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------

def _seed_db(db, *, session_id: int = 1, student_id: int = 10):
    from app.models.session import LMSSession
    from app.models.user import User, UserRole

    user = User(
        id=student_id,
        name=f"Student {student_id}",
        email=f"s{student_id}@test.com",
        hashed_password="x",
        role=UserRole.student,
    )
    # LMSSession has: id, title (unique) — no instructor FK at the model level
    lms_session = LMSSession(id=session_id, title=f"Test Session {session_id}")
    db.add_all([user, lms_session])
    db.flush()
    return lms_session, user


def _add_unsolved(db, *, session_id: int, filename: str, requirements_text):
    from app.models.unsolved_file import UnsolvedFile
    uf = UnsolvedFile(
        session_id=session_id,
        original_filename=filename,
        file_path=f"{session_id}/assignments/{filename}",
        parsed_requirements_text=requirements_text,
    )
    db.add(uf)
    db.flush()
    return uf


def _add_submission_with_file(db, *, session_id, student_id, submission_filename, extracted_path):
    from app.models.submission import Submission
    from app.models.submission_file import SubmissionFile

    sub = Submission(
        session_id=session_id, student_id=student_id,
        original_filename=submission_filename, uploaded_file_path=extracted_path,
    )
    db.add(sub)
    db.flush()

    sf = SubmissionFile(
        submission_id=sub.id, original_filename=submission_filename,
        extracted_ipynb_path=extracted_path,
    )
    db.add(sf)
    db.flush()
    return sub, sf


# ===========================================================================
# 1-5: normalize_text
# ===========================================================================

class TestNormalizeText:

    def test_lowercases_input(self):
        assert normalize_text("Hello World") == "hello world"

    def test_removes_markdown_hash(self):
        result = normalize_text("# Heading")
        assert "#" not in result
        assert "heading" in result

    def test_removes_markdown_asterisks(self):
        result = normalize_text("**bold** and *italic*")
        assert "*" not in result

    def test_removes_backticks(self):
        result = normalize_text("`code block`")
        assert "`" not in result
        assert "code block" in result

    def test_removes_underscores_and_dashes(self):
        result = normalize_text("some_variable and some-thing")
        assert "_" not in result
        assert "-" not in result

    def test_collapses_multiple_spaces(self):
        result = normalize_text("too   many   spaces")
        assert "  " not in result
        assert result == "too many spaces"

    def test_collapses_newlines(self):
        result = normalize_text("line one\nline two\n\nline three")
        assert "\n" not in result
        assert result == "line one line two line three"

    def test_empty_string_returns_empty(self):
        assert normalize_text("") == ""

    def test_whitespace_only_returns_empty(self):
        assert normalize_text("   \n\t  ") == ""

    def test_combined_markdown_cleanup(self):
        text = "# Title\n## Section\n- item one\n- **item two**\n`code`"
        result = normalize_text(text)
        assert result == "title section item one item two code"


# ===========================================================================
# 6-12: content_similarity_score
# ===========================================================================

class TestContentSimilarityScore:

    def test_empty_unsolved_text_returns_zero(self):
        assert content_similarity_score("some submitted text", "") == 0.0

    def test_none_unsolved_text_returns_zero(self):
        assert content_similarity_score("some submitted text", None) == 0.0

    def test_whitespace_only_unsolved_returns_zero(self):
        assert content_similarity_score("submitted", "   \n  ") == 0.0

    def test_identical_texts_return_one(self):
        text = "This is the assignment description for week one."
        score = content_similarity_score(text, text)
        assert score == pytest.approx(1.0)

    def test_completely_different_texts_return_low_score(self):
        # Use long texts from completely different technical domains so
        # SequenceMatcher cannot find incidental short-sequence matches.
        unsolved = (
            "numpy pandas matplotlib seaborn scikit learn regression supervised "
            "learning cross validation gradient descent weight update loss function "
            "train test split mean squared error feature engineering"
        )
        submitted = (
            "javascript typescript react node express mongodb REST API frontend "
            "backend component state props hooks event listener async await fetch "
            "promise callback routing middleware authentication JWT token"
        )
        score = content_similarity_score(submitted, unsolved)
        assert score < 0.4

    def test_partial_overlap_returns_intermediate_score(self):
        unsolved = "implement a function to calculate the mean and standard deviation"
        submitted = (
            "implement a function to calculate the mean and standard deviation "
            "using numpy arrays and return the result as a tuple"
        )
        score = content_similarity_score(submitted, unsolved)
        assert 0.3 < score < 1.0

    def test_empty_submitted_with_non_empty_unsolved(self):
        score = content_similarity_score("", "some unsolved requirements")
        assert score == pytest.approx(0.0)


# ===========================================================================
# 13-17: filename_similarity_score
# ===========================================================================

class TestFilenameSimilarityScore:

    def test_identical_stems_return_one(self):
        score = filename_similarity_score("week1_lab.ipynb", "week1_lab.ipynb")
        assert score == pytest.approx(1.0)

    def test_completely_different_stems_return_low_score(self):
        score = filename_similarity_score("alpha.ipynb", "zeta.ipynb")
        assert score < 0.3

    def test_extensions_are_ignored(self):
        score = filename_similarity_score("homework1.ipynb", "homework1.py")
        assert score == pytest.approx(1.0)

    def test_non_alphanumeric_stripped_before_comparison(self):
        # "week1lab" vs "week1lab" after stripping _ and -
        score = filename_similarity_score("week1_lab.ipynb", "week1-lab.ipynb")
        assert score == pytest.approx(1.0)

    def test_case_insensitive(self):
        score = filename_similarity_score("Week1Lab.ipynb", "week1lab.ipynb")
        assert score == pytest.approx(1.0)


# ===========================================================================
# 18-26: match_submission_file_to_unsolved
# ===========================================================================

class TestMatchSubmissionFileToUnsolved:

    # ── 18: empty candidates ─────────────────────────────────────────────────

    def test_empty_candidates_returns_no_unsolved_files(self):
        result = match_submission_file_to_unsolved("some text", "hw.ipynb", [])
        assert result["status"] == "no_unsolved_files"
        assert result["matched_unsolved_file_id"] is None
        assert result["confidence"] == 0.0

    # ── 19-20: single candidate ───────────────────────────────────────────────

    def test_single_candidate_auto_matched_with_full_confidence(self):
        """Even with zero content overlap, one candidate means no ambiguity."""
        candidates = [{"id": 7, "filename": "hw1.ipynb", "requirements_text": "numpy stuff"}]
        result = match_submission_file_to_unsolved(
            "javascript react", "solution.ipynb", candidates
        )
        assert result["status"] == "matched"
        assert result["matched_unsolved_file_id"] == 7
        assert result["confidence"] == 1.0

    def test_single_candidate_with_matching_content_also_auto_matched(self):
        candidates = [{"id": 3, "filename": "lab.ipynb", "requirements_text": "pandas data frame"}]
        result = match_submission_file_to_unsolved(
            "pandas data frame analysis", "lab_solved.ipynb", candidates
        )
        assert result["status"] == "matched"
        assert result["matched_unsolved_file_id"] == 3

    # ── 21: high content overlap picks correct candidate ─────────────────────

    def test_high_content_overlap_matches_correct_candidate(self):
        """
        REQUIRED SCENARIO 1: submission clearly matches one specific unsolved
        file by content.  Requirements are long/dense prose (as they appear
        after extract_requirements_text) so the similarity ratio clears 0.55.
        """
        # File A: regression lab
        regression_requirements = (
            "Implement linear regression from scratch using numpy "
            "Calculate mean squared error loss on the validation set "
            "Plot the regression line using matplotlib to visualize the fit "
            "Compare your results with sklearn LinearRegression "
            "compute gradient descent weight updates learning rate "
            "split dataset into train and test sets "
            "report final test MSE and R-squared score "
            "normalize input features before training "
            "use numpy dot product for matrix multiplication "
            "implement predict function and fit function"
        )
        # File B: NLP lab (completely different domain)
        nlp_requirements = (
            "Tokenise the input corpus using NLTK word tokenize "
            "Build a TF-IDF feature matrix using sklearn TfidfVectorizer "
            "Train a Naive Bayes classifier on the feature matrix "
            "Evaluate precision recall and F1 score on the test set "
            "remove stop words and apply stemming with PorterStemmer "
            "load text data from CSV using pandas read csv "
            "split into train test using train test split stratify "
            "print classification report and confusion matrix"
        )
        # File C: CNN lab (completely different domain)
        cnn_requirements = (
            "Build a convolutional neural network in Keras for image classification "
            "Apply data augmentation with ImageDataGenerator rotation zoom flip "
            "Evaluate on CIFAR-10 test set accuracy and loss curves "
            "add batch normalisation dropout regularisation layers "
            "compile model with categorical crossentropy Adam optimizer "
            "plot training and validation accuracy per epoch "
            "load CIFAR-10 dataset and normalise pixel values"
        )

        # Student submission mirrors the regression lab closely
        submitted_markdown = (
            "Implement linear regression from scratch using numpy "
            "I used numpy dot product for matrix multiplication and normal equation "
            "Calculate mean squared error loss on the held out validation set "
            "Plot the regression line using matplotlib to visualize the fit clearly "
            "Compare your results with sklearn LinearRegression they match closely "
            "compute gradient descent weight updates with a fixed learning rate "
            "split dataset into train and test sets with train test split "
            "report final test MSE and R-squared score on the test partition "
            "normalize input features before training using standard scaler "
            "implement predict function and fit function as class methods"
        )

        candidates = [
            {"id": 1, "filename": "Week3_LinearRegression.ipynb",
             "requirements_text": regression_requirements},
            {"id": 2, "filename": "Week7_TextClassification.ipynb",
             "requirements_text": nlp_requirements},
            {"id": 3, "filename": "Week9_CNN.ipynb",
             "requirements_text": cnn_requirements},
        ]

        result = match_submission_file_to_unsolved(
            submitted_markdown,
            "Week3_LinearRegression_solved.ipynb",
            candidates,
        )

        assert result["status"] == "matched"
        assert result["matched_unsolved_file_id"] == 1
        assert result["confidence"] >= 0.55

    # ── 22: no content overlap → ambiguous ───────────────────────────────────

    def test_no_content_overlap_returns_ambiguous(self):
        """
        REQUIRED SCENARIO 2: submission has no meaningful overlap with any
        unsolved file -> status should be 'ambiguous'.
        """
        candidates = [
            {"id": 10, "filename": "DataEngineering.ipynb",
             "requirements_text": (
                 "kafka spark streaming pipeline ETL batch processing "
                 "hadoop hdfs distributed computing data lake warehouse"
             )},
            {"id": 11, "filename": "MachineLearning.ipynb",
             "requirements_text": (
                 "random forest gradient boosting cross validation "
                 "hyperparameter tuning feature importance sklearn pipeline"
             )},
        ]
        # Completely off-topic submission
        submitted_markdown = (
            "Hello World exercise "
            "print your name to the screen "
            "basic python variables and loops "
            "simple arithmetic addition subtraction"
        )
        result = match_submission_file_to_unsolved(
            submitted_markdown, "random_notebook.ipynb", candidates
        )
        assert result["status"] == "ambiguous"
        assert result["matched_unsolved_file_id"] is None
        assert result["confidence"] < 0.55

    # ── 23: multiple similar filenames, correct pick by content ──────────────

    def test_picks_by_content_not_filename(self):
        """
        REQUIRED SCENARIO 4: unsolved files have nearly identical filenames
        (Day1, Day2, Day3) -- the matcher must pick by content, not filename.
        Uses longer dense text so scores clear the 0.55 threshold.
        """
        day1_requirements = (
            "Python fundamentals variables lists dictionaries control flow "
            "for loops while loops functions exercises basic python programming "
            "string formatting input output print debugging"
        )
        day2_requirements = (
            "numpy arrays broadcasting and vectorised operations "
            "pandas dataframe load CSV groupby aggregations "
            "merge join concat data manipulation series indexing "
            "apply lambda functions column operations read csv"
        )
        day3_requirements = (
            "matplotlib bar charts histograms scatter plots line graphs "
            "seaborn heatmaps pairplots exploratory data analysis "
            "colour palettes subplots figure axes tick labels legend"
        )

        # Student submitted Day 2 lab (edited in place, retaining requirement text)
        day2_submission = (
            "numpy arrays broadcasting and vectorised operations "
            "pandas dataframe load CSV groupby aggregations "
            "merge join concat data manipulation series indexing "
            "apply lambda functions column operations read csv "
            "Student solution: implemented vectorised operations with numpy broadcasting, "
            "loaded CSV into pandas dataframe, applied groupby aggregations and lambda functions."
        )

        candidates = [
            {"id": 101, "filename": "Bootcamp_Week1_Day1_Lab.ipynb",
             "requirements_text": day1_requirements},
            {"id": 102, "filename": "Bootcamp_Week1_Day2_Lab.ipynb",
             "requirements_text": day2_requirements},
            {"id": 103, "filename": "Bootcamp_Week1_Day3_Lab.ipynb",
             "requirements_text": day3_requirements},
        ]

        result = match_submission_file_to_unsolved(
            day2_submission,
            "Bootcamp_Week1_Day2_solved.ipynb",
            candidates,
        )

        assert result["status"] == "matched"
        assert result["matched_unsolved_file_id"] == 102

    # ── 24: content wins even when filename matches wrong candidate ───────────

    def test_picks_by_content_even_when_filename_matches_wrong_candidate(self):
        """
        Content score (0.8 weight) must beat filename signal (0.2 weight).
        File is named like Day1 but has Day3 content -> should pick Day3.
        """
        day1_requirements = (
            "Python fundamentals variables lists dictionaries control flow "
            "for loops while loops functions exercises"
        )
        day3_requirements = (
            "matplotlib bar charts histograms scatter plots "
            "seaborn heatmaps pairplots EDA exploratory data analysis "
            "visualisation colour palettes subplots"
        )

        # Submission has Day3 content but a Day1-sounding filename
        day3_content_but_day1_name = (
            "matplotlib bar charts and histograms "
            "scatter plots for exploratory data analysis "
            "seaborn heatmaps and pairplots "
            "colour palettes and subplots"
        )

        candidates = [
            {"id": 201, "filename": "Bootcamp_Week1_Day1_Lab.ipynb",
             "requirements_text": day1_requirements},
            {"id": 203, "filename": "Bootcamp_Week1_Day3_Lab.ipynb",
             "requirements_text": day3_requirements},
        ]

        result = match_submission_file_to_unsolved(
            day3_content_but_day1_name,
            "Bootcamp_Week1_Day1_solved.ipynb",  # misleading filename
            candidates,
        )

        assert result["matched_unsolved_file_id"] == 203

    # ── 25-26: threshold boundary ─────────────────────────────────────────────

    def test_score_at_exactly_0_55_is_matched(self):
        """Score at the boundary (>= 0.55) -> matched."""
        candidates = [
            {"id": 5, "filename": "test.ipynb", "requirements_text": "x"},
            {"id": 6, "filename": "other.ipynb", "requirements_text": "y"},
        ]
        with (
            patch("app.services.file_matcher.content_similarity_score",
                  return_value=0.55 / 0.8),
            patch("app.services.file_matcher.filename_similarity_score",
                  return_value=0.0),
        ):
            # combined = 0.8 * (0.55/0.8) + 0.2*0.0 = 0.55 exactly
            result = match_submission_file_to_unsolved("any", "any.ipynb", candidates)
        assert result["status"] == "matched"

    def test_score_just_below_0_55_is_ambiguous(self):
        """Score just below threshold -> ambiguous."""
        candidates = [
            {"id": 1, "filename": "a.ipynb", "requirements_text": "x"},
            {"id": 2, "filename": "b.ipynb", "requirements_text": "y"},
        ]
        with (
            patch("app.services.file_matcher.content_similarity_score",
                  return_value=0.0),
            patch("app.services.file_matcher.filename_similarity_score",
                  return_value=0.0),
        ):
            result = match_submission_file_to_unsolved("any", "any.ipynb", candidates)
        assert result["status"] == "ambiguous"
        assert result["matched_unsolved_file_id"] is None


# ===========================================================================
# 27-32: match_all_files_in_submission (in-memory SQLite + real ORM models)
# ===========================================================================

class TestMatchAllFilesInSubmission:
    """
    End-to-end tests for the orchestrator.  Each test writes real .ipynb files
    to tmp_path and patches absolute_path to redirect the function there.
    """

    # ── 27: single unsolved file -> auto-match ────────────────────────────────

    def test_single_unsolved_file_auto_matched(self, db, tmp_path):
        """
        REQUIRED SCENARIO 3: only one unsolved file -> always matched,
        confidence = 1.0, regardless of content.
        """
        _seed_db(db, session_id=1, student_id=10)

        uf = _add_unsolved(
            db, session_id=1, filename="Assignment1.ipynb",
            requirements_text="Write a function to sort a list",
        )

        nb_bytes = _make_notebook_bytes(
            markdown_cells=["## My solution", "I sorted the list using bubble sort"]
        )
        nb_rel = "1/submissions/10/solution.ipynb"
        nb_abs = tmp_path / nb_rel
        nb_abs.parent.mkdir(parents=True, exist_ok=True)
        nb_abs.write_bytes(nb_bytes)

        sub, sf = _add_submission_with_file(
            db, session_id=1, student_id=10,
            submission_filename="solution.ipynb",
            extracted_path=nb_rel,
        )
        db.commit()

        with patch("app.services.storage.absolute_path", lambda rel: tmp_path / rel):
            results = match_all_files_in_submission(db, sub.id)

        assert len(results) == 1
        r = results[0]
        assert r["status"] == "matched"
        assert r["matched_unsolved_file_id"] == uf.id
        assert r["confidence"] == pytest.approx(1.0)

    # ── 28: correct pick among multiple unsolved files ─────────────────────────

    def test_correct_match_among_multiple_unsolved_files(self, db, tmp_path):
        """
        REQUIRED SCENARIO 1 (orchestrator): multiple unsolved files -- submission
        clearly matches the regression one, not the NLP one.
        """
        _seed_db(db, session_id=2, student_id=20)

        uf_regression = _add_unsolved(
            db, session_id=2, filename="Week3_Regression.ipynb",
            requirements_text=(
                "Implement linear regression using numpy "
                "Calculate MSE loss "
                "Plot regression line with matplotlib "
                "Compare with sklearn LinearRegression "
                "gradient descent weight update train test split"
            ),
        )
        uf_nlp = _add_unsolved(
            db, session_id=2, filename="Week7_NLP.ipynb",
            requirements_text=(
                "Tokenise corpus with NLTK "
                "Build TF-IDF matrix with TfidfVectorizer "
                "Train Naive Bayes classifier "
                "Evaluate with precision recall F1 "
                "stop words stemming PorterStemmer confusion matrix"
            ),
        )

        # Submission clearly mirrors the regression lab
        nb_bytes = _make_notebook_bytes(
            markdown_cells=[
                "Implement linear regression using numpy normal equation",
                "Calculate MSE loss on the test set",
                "Plot regression line with matplotlib scatter",
                "Compare with sklearn LinearRegression results match",
                "gradient descent weight update learning rate train test split",
            ]
        )
        nb_rel = "2/submissions/20/regression_solution.ipynb"
        nb_abs = tmp_path / nb_rel
        nb_abs.parent.mkdir(parents=True, exist_ok=True)
        nb_abs.write_bytes(nb_bytes)

        sub, sf = _add_submission_with_file(
            db, session_id=2, student_id=20,
            submission_filename="regression_solution.ipynb",
            extracted_path=nb_rel,
        )
        db.commit()

        with patch("app.services.storage.absolute_path", lambda rel: tmp_path / rel):
            results = match_all_files_in_submission(db, sub.id)

        assert len(results) == 1
        r = results[0]
        assert r["status"] == "matched"
        assert r["matched_unsolved_file_id"] == uf_regression.id
        assert r["matched_unsolved_file_id"] != uf_nlp.id

    # ── 29: no parsed text on any unsolved file ───────────────────────────────

    def test_no_unsolved_files_with_text_returns_no_unsolved_files(self, db, tmp_path):
        """
        Session has an unsolved file but parsed_requirements_text is None
        -> treated as if no candidates exist.
        """
        _seed_db(db, session_id=3, student_id=30)
        _add_unsolved(db, session_id=3, filename="Assignment.ipynb",
                      requirements_text=None)

        nb_bytes = _make_notebook_bytes(markdown_cells=["## My work"])
        nb_rel = "3/submissions/30/work.ipynb"
        nb_abs = tmp_path / nb_rel
        nb_abs.parent.mkdir(parents=True, exist_ok=True)
        nb_abs.write_bytes(nb_bytes)

        sub, sf = _add_submission_with_file(
            db, session_id=3, student_id=30,
            submission_filename="work.ipynb",
            extracted_path=nb_rel,
        )
        db.commit()

        with patch("app.services.storage.absolute_path", lambda rel: tmp_path / rel):
            results = match_all_files_in_submission(db, sub.id)

        assert len(results) == 1
        assert results[0]["status"] == "no_unsolved_files"
        assert results[0]["matched_unsolved_file_id"] is None

    # ── 30: multiple submission files -> each matched to correct unsolved ──────

    def test_multiple_submission_files_each_matched_correctly(self, db, tmp_path):
        """
        REQUIRED SCENARIO 4 (orchestrator): zip produced three SubmissionFiles;
        each must match its correct UnsolvedFile by content.
        """
        _seed_db(db, session_id=4, student_id=40)

        uf1 = _add_unsolved(
            db, session_id=4, filename="Day1_Python_Fundamentals.ipynb",
            requirements_text=(
                "Python fundamentals variables lists dictionaries control flow "
                "for loops while loops functions exercises basic python programming "
                "string formatting input output print debugging"
            ),
        )
        uf2 = _add_unsolved(
            db, session_id=4, filename="Day2_NumPy_Pandas.ipynb",
            requirements_text=(
                "numpy arrays broadcasting vectorised operations "
                "pandas dataframe load CSV groupby aggregations "
                "merge join concat data manipulation apply lambda"
            ),
        )
        uf3 = _add_unsolved(
            db, session_id=4, filename="Day3_Visualisation.ipynb",
            requirements_text=(
                "matplotlib bar charts histograms scatter plots line graphs "
                "seaborn heatmaps pairplots exploratory data analysis "
                "colour palettes subplots figure axes tick labels legend"
            ),
        )

        notebooks = [
            (
                "Day1_solution.ipynb",
                "4/submissions/40/extracted/Day1_solution.ipynb",
                [
                    "Python fundamentals variables lists dictionaries control flow",
                    "for loops while loops functions exercises basic programming",
                    "string formatting input output print debugging techniques",
                ],
            ),
            (
                "Day2_solution.ipynb",
                "4/submissions/40/extracted/Day2_solution.ipynb",
                [
                    "numpy arrays broadcasting vectorised operations fast computation",
                    "pandas dataframe load CSV groupby aggregations per group",
                    "merge join concat data manipulation apply lambda columns",
                ],
            ),
            (
                "Day3_solution.ipynb",
                "4/submissions/40/extracted/Day3_solution.ipynb",
                [
                    "matplotlib bar charts histograms scatter plots line graphs",
                    "seaborn heatmaps pairplots exploratory data analysis EDA",
                    "colour palettes subplots figure axes tick labels legend",
                ],
            ),
        ]

        from app.models.submission import Submission
        from app.models.submission_file import SubmissionFile

        sub = Submission(
            session_id=4, student_id=40,
            original_filename="Week1.zip",
            uploaded_file_path="4/submissions/40/Week1.zip",
        )
        db.add(sub)
        db.flush()

        for filename, rel_path, md_cells in notebooks:
            nb_abs = tmp_path / rel_path
            nb_abs.parent.mkdir(parents=True, exist_ok=True)
            nb_abs.write_bytes(_make_notebook_bytes(markdown_cells=md_cells))
            sf = SubmissionFile(
                submission_id=sub.id,
                original_filename=filename,
                extracted_ipynb_path=rel_path,
            )
            db.add(sf)
        db.commit()

        with patch("app.services.storage.absolute_path", lambda rel: tmp_path / rel):
            results = match_all_files_in_submission(db, sub.id)

        assert len(results) == 3

        matched = {r["original_filename"]: r["matched_unsolved_file_id"] for r in results}
        statuses = {r["original_filename"]: r["status"] for r in results}

        assert all(s == "matched" for s in statuses.values()), (
            f"Not all files matched: {statuses}"
        )
        assert matched["Day1_solution.ipynb"] == uf1.id
        assert matched["Day2_solution.ipynb"] == uf2.id
        assert matched["Day3_solution.ipynb"] == uf3.id

    # ── 31: unknown submission_id -> empty list, no exception ─────────────────

    def test_unknown_submission_id_returns_empty_list(self, db, tmp_path):
        results = match_all_files_in_submission(db, submission_id=999999)
        assert results == []

    # ── 32: matched_unsolved_file_id persisted in DB ──────────────────────────

    def test_match_result_written_to_db(self, db, tmp_path):
        """After match_all_files_in_submission, the DB row reflects the match."""
        _seed_db(db, session_id=5, student_id=50)

        uf = _add_unsolved(
            db, session_id=5, filename="Assignment.ipynb",
            requirements_text=(
                "implement bubble sort algorithm in python step by step "
                "compare adjacent elements and swap if out of order "
                "repeat passes until no swaps occur return sorted list"
            ),
        )

        nb_bytes = _make_notebook_bytes(
            markdown_cells=[
                "Bubble Sort Implementation",
                "implement bubble sort algorithm in python step by step",
                "compare adjacent elements and swap if out of order",
                "repeat passes until no swaps occur and return sorted list",
            ]
        )
        nb_rel = "5/submissions/50/bubble_sort.ipynb"
        nb_abs = tmp_path / nb_rel
        nb_abs.parent.mkdir(parents=True, exist_ok=True)
        nb_abs.write_bytes(nb_bytes)

        sub, sf = _add_submission_with_file(
            db, session_id=5, student_id=50,
            submission_filename="bubble_sort.ipynb",
            extracted_path=nb_rel,
        )
        db.commit()

        # Confirm NULL before matching
        db.refresh(sf)
        assert sf.matched_unsolved_file_id is None

        with patch("app.services.storage.absolute_path", lambda rel: tmp_path / rel):
            match_all_files_in_submission(db, sub.id)

        db.refresh(sf)
        assert sf.matched_unsolved_file_id == uf.id
