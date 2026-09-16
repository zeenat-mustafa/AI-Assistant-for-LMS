"""
Tests for app.services.embeddings — Phase 7, Sub-feature 7.2.

embed_text/upsert_chunk are tested with the Chroma collection and the
sentence-transformers model mocked (fast, no model download, no disk I/O).
retrieve() is tested the same way for its filtering/scoping logic, PLUS one
real end-to-end test using the real model and a real temporary Chroma
instance — embedding quality/behavior can't be meaningfully verified against
a mock, matching this project's testing philosophy.

Run with:
    cd backend
    python -m pytest tests/test_embeddings.py -v
"""

import inspect

import pytest

import app.services.embeddings as embeddings_module
from app.services.embeddings import embed_text, get_chroma_collection, retrieve, upsert_chunk


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def _reset_module_globals():
    """embeddings.py caches the model/client/collection as module globals —
    reset them around every test so tests don't leak state into each other."""
    saved = (embeddings_module._model, embeddings_module._client, embeddings_module._collection)
    embeddings_module._model = None
    embeddings_module._client = None
    embeddings_module._collection = None
    yield
    embeddings_module._model, embeddings_module._client, embeddings_module._collection = saved


class _FakeCollection:
    """Records upsert() calls and returns a canned query() result."""

    def __init__(self):
        self.upsert_calls: list[dict] = []
        self.query_result: dict = {
            "ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]],
        }

    def upsert(self, ids, embeddings, documents, metadatas):
        self.upsert_calls.append(
            {"ids": ids, "embeddings": embeddings, "documents": documents, "metadatas": metadatas}
        )

    def query(self, query_embeddings, n_results, where=None):
        self.last_query_kwargs = {
            "query_embeddings": query_embeddings, "n_results": n_results, "where": where,
        }
        return self.query_result


@pytest.fixture()
def fake_collection(monkeypatch):
    collection = _FakeCollection()
    monkeypatch.setattr(embeddings_module, "get_chroma_collection", lambda: collection)
    return collection


@pytest.fixture()
def fake_embed(monkeypatch):
    """embed_text returns a fixed, cheap vector instead of loading the real model."""
    monkeypatch.setattr(embeddings_module, "embed_text", lambda text: [0.1, 0.2, 0.3])


# ===========================================================================
# embed_text
# ===========================================================================

def test_embed_text_returns_vector_from_model(monkeypatch):
    class _FakeVector:
        def tolist(self):
            return [0.5, 0.25, -0.1]

    class _FakeModel:
        def encode(self, text, convert_to_numpy=True):
            assert text == "hello world"
            return _FakeVector()

    monkeypatch.setattr(embeddings_module, "_get_model", lambda: _FakeModel())
    result = embed_text("hello world")
    assert result == [0.5, 0.25, -0.1]


# ===========================================================================
# upsert_chunk
# ===========================================================================

def test_upsert_chunk_embeds_and_upserts_with_deterministic_id(fake_collection, fake_embed):
    upsert_chunk(
        chunk_id="lecture:7",
        text="Photosynthesis converts light energy into chemical energy.",
        metadata={"source_type": "lecture", "source_file_id": 1, "session_id": 3,
                   "slide_number": 2, "source": "slide_text"},
    )
    assert len(fake_collection.upsert_calls) == 1
    call = fake_collection.upsert_calls[0]
    assert call["ids"] == ["lecture:7"]
    assert call["embeddings"] == [[0.1, 0.2, 0.3]]
    assert call["documents"] == ["Photosynthesis converts light energy into chemical energy."]
    assert call["metadatas"] == [{"source_type": "lecture", "source_file_id": 1, "session_id": 3,
                                   "slide_number": 2, "source": "slide_text"}]


def test_upsert_chunk_reupsert_same_id_updates_not_duplicates(fake_collection, fake_embed):
    upsert_chunk("notebook:5:0", "first version", {"source_type": "notebook"})
    upsert_chunk("notebook:5:0", "second version", {"source_type": "notebook"})
    assert len(fake_collection.upsert_calls) == 2
    assert fake_collection.upsert_calls[0]["ids"] == fake_collection.upsert_calls[1]["ids"] == ["notebook:5:0"]


def test_upsert_chunk_propagates_failure(monkeypatch):
    """upsert_chunk is deliberately NOT exception-safe — callers (router,
    backfill script) catch around it to record embedding_error per row."""
    def _boom():
        raise RuntimeError("chroma is down")
    monkeypatch.setattr(embeddings_module, "get_chroma_collection", _boom)
    monkeypatch.setattr(embeddings_module, "embed_text", lambda text: [0.1])
    with pytest.raises(RuntimeError, match="chroma is down"):
        upsert_chunk("lecture:1", "text", {})


# ===========================================================================
# retrieve
# ===========================================================================

def test_retrieve_excludes_results_below_min_similarity(fake_collection, fake_embed):
    # distance 0.1 -> similarity 0.9 (kept); distance 0.9 -> similarity 0.1 (excluded)
    fake_collection.query_result = {
        "ids": [["a", "b"]],
        "documents": [["relevant text", "weak match text"]],
        "metadatas": [[{"source_type": "lecture"}, {"source_type": "lecture"}]],
        "distances": [[0.1, 0.9]],
    }
    results = retrieve("some query", min_similarity=0.35)
    assert len(results) == 1
    assert results[0]["chunk_id"] == "a"
    assert results[0]["similarity"] == 0.9


def test_retrieve_returns_empty_when_all_below_threshold(fake_collection, fake_embed):
    fake_collection.query_result = {
        "ids": [["a"]], "documents": [["off topic"]],
        "metadatas": [[{"source_type": "notebook"}]], "distances": [[0.95]],
    }
    results = retrieve("irrelevant query", min_similarity=0.35)
    assert results == []


def test_retrieve_scopes_by_session_id_when_provided(fake_collection, fake_embed):
    retrieve("query", session_id=42)
    assert fake_collection.last_query_kwargs["where"] == {"session_id": 42}


def test_retrieve_unscoped_when_session_id_omitted(fake_collection, fake_embed):
    retrieve("query")
    assert fake_collection.last_query_kwargs["where"] is None


def test_retrieve_never_raises_on_internal_error(monkeypatch):
    def _boom(text):
        raise RuntimeError("model failed to load")
    monkeypatch.setattr(embeddings_module, "embed_text", _boom)
    results = retrieve("any query")
    assert results == []


def test_retrieve_result_shape_includes_full_metadata_and_score(fake_collection, fake_embed):
    fake_collection.query_result = {
        "ids": [["notebook:5:2"]],
        "documents": [["your code here"]],
        "metadatas": [[{
            "source_type": "notebook", "source_file_id": 5, "session_id": 3,
            "cell_index": 2, "cell_type": "code",
        }]],
        "distances": [[0.2]],
    }
    results = retrieve("query", min_similarity=0.0)
    assert results == [{
        "source_type": "notebook", "source_file_id": 5, "session_id": 3,
        "cell_index": 2, "cell_type": "code",
        "chunk_id": "notebook:5:2", "chunk_text": "your code here", "similarity": 0.8,
    }]


# ===========================================================================
# Real end-to-end test (no mocks) — required since embedding quality can't
# be meaningfully verified against a fake model.
# ===========================================================================

def test_real_embedding_and_retrieval_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings_module, "chroma_dir", lambda: tmp_path)

    upsert_chunk(
        "lecture:1",
        "Photosynthesis is the process plants use to convert sunlight into chemical energy.",
        {"source_type": "lecture", "source_file_id": 1, "session_id": 1,
         "slide_number": 1, "source": "slide_text"},
    )
    upsert_chunk(
        "lecture:2",
        "A car engine converts fuel into mechanical motion through internal combustion.",
        {"source_type": "lecture", "source_file_id": 1, "session_id": 1,
         "slide_number": 2, "source": "slide_text"},
    )

    results = retrieve("How do plants get energy from the sun?", top_k=2, min_similarity=0.2)
    assert len(results) >= 1
    assert results[0]["chunk_id"] == "lecture:1"
    assert results[0]["similarity"] > 0.2


# ===========================================================================
# Never touches Submission/SubmissionFile content
# ===========================================================================

def test_embeddings_module_never_imports_submission_models():
    """Static guard: this module must never import Submission/SubmissionFile
    — only LectureChunk/UnsolvedFile content may ever be embedded. Enforced
    by construction (no such parameter exists to wire up by accident); this
    test just makes that guarantee explicit and checked. The module DOES
    discuss Submission/SubmissionFile in prose (explaining the guard), so
    this checks for an actual import statement, not bare word absence."""
    source = inspect.getsource(embeddings_module)
    assert "from app.models.submission" not in source
    assert "import Submission" not in source
