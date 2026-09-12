"""
Embedding + vector store service — Phase 7, Sub-feature 7.2.

Stands up a single global Chroma collection and a local sentence-transformers
model, and exposes:
  - embed_text(text)                                  → embedding vector
  - get_chroma_collection()                           → the one global collection
  - upsert_chunk(chunk_id, text, metadata)             → embed + store one chunk
  - retrieve(query, session_id, top_k, min_similarity) → scoped, thresholded search

Embedding model is local sentence-transformers (all-MiniLM-L6-v2) — never
Gemini's embedding API (embedding is a higher-volume workload than grading,
and this project's own fallback chain exists because of a prior Gemini quota
exhaustion incident).

Only LectureChunk text and UnsolvedFile (unsolved assignment) cell text are
ever embedded here. Submission/SubmissionFile content — a student's actual
work — must NEVER reach this module. Nothing in this file imports or accepts
those models; callers are responsible for only ever passing lecture/unsolved
content in. This is enforced by construction (no Submission-shaped parameter
exists to accidentally wire up), not by a runtime check, but every call site
must stay that way.
"""

import logging
from pathlib import Path

from app.services.storage import chroma_dir

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "lms_content_chunks"

# Starting cutoff, chosen ahead of real verification (Step 6): all-MiniLM-L6-v2
# cosine similarity for genuinely related paraphrase-level content typically
# lands 0.4-0.8+, while unrelated text commonly lands 0.0-0.3. 0.35 is picked
# as a starting point that should exclude clearly-unrelated matches while
# still admitting loosely-worded but on-topic queries — tuned against real
# retrieval results in Step 6, see that step's report for the final value.
DEFAULT_MIN_SIMILARITY = 0.35
DEFAULT_TOP_K = 5

_model = None
_client = None
_collection = None


# ── Model ─────────────────────────────────────────────────────────────────────

def _get_model():
    """Lazily load the sentence-transformers model once, reuse across calls."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s (first use only)...", EMBEDDING_MODEL_NAME)
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    """
    Return the embedding vector for a single string.

    Can raise (model load failure, encode failure) — callers that need to
    record a per-row embedding_error (Step 3/4) catch around this, matching
    this project's "never silently drop, never fabricate" convention: a
    failure here is a real, visible failure, not swallowed into a fake result.
    """
    model = _get_model()
    vector = model.encode(text, convert_to_numpy=True)
    return vector.tolist()


# ── Chroma collection ────────────────────────────────────────────────────────

def get_chroma_collection():
    """
    Initialize (or connect to, if already persisted) the single global Chroma
    collection at storage/chroma/, creating it on first use.

    One global collection, never per-session — every embedded chunk carries
    session_id as metadata instead, and retrieve() filters on that metadata.
    hnsw:space is set to cosine explicitly since embeddings are compared by
    cosine similarity in retrieve(), not Chroma's default L2 distance.
    """
    global _client, _collection
    if _collection is None:
        import chromadb
        persist_dir: Path = chroma_dir()
        _client = chromadb.PersistentClient(path=str(persist_dir))
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


# ── Write path ────────────────────────────────────────────────────────────────

def upsert_chunk(chunk_id: str, text: str, metadata: dict) -> None:
    """
    Embed `text` and upsert it into the Chroma collection under `chunk_id`.

    `chunk_id` must be stable and deterministic (e.g. f"lecture:{lecture_chunk_id}"
    or f"notebook:{unsolved_file_id}:{cell_index}") so re-running this for the
    same chunk updates the existing vector rather than duplicating it.

    Can raise — deliberately not exception-safe internally. Callers (the
    lecture upload flow, the notebook embedding trigger, the backfill script)
    catch around this and record embedding_error on the owning row, matching
    LectureFile's existing extracted/extraction_error convention: a real
    failure reason, visible per-row, never silently dropped.
    """
    embedding = embed_text(text)
    collection = get_chroma_collection()
    collection.upsert(
        ids=[chunk_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[metadata],
    )


# ── Read path ─────────────────────────────────────────────────────────────────

def retrieve(
    query: str,
    session_id: int | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[dict]:
    """
    Embed `query`, search the Chroma collection, and return the top matches.

    Filtered by session_id metadata when provided, unfiltered otherwise.
    Results below `min_similarity` are excluded — this never force-returns
    weak matches just to fill out top_k, matching this project's established
    "never guess on low confidence" pattern (the file matcher, the session
    matcher). Returning fewer than top_k results, including zero, is correct
    behavior, not a bug.

    Each result dict carries full metadata, never bare text:
        source_type ("lecture" | "notebook"), source_file_id, session_id,
        plus the source-specific location fields (slide_number + source for
        lecture chunks; cell_index + cell_type for notebook chunks),
        chunk_text, and similarity (float).

    Exception-safe by design, matching this project's established convention
    (notebook.py, lecture_extraction.py) — never raises on a Chroma/model
    error; logs a warning and returns [] instead.
    """
    try:
        collection = get_chroma_collection()
        query_embedding = embed_text(query)
        where = {"session_id": session_id} if session_id is not None else None
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=max(top_k, 1),
            where=where,
        )
    except Exception as exc:  # noqa: BLE001 — deliberate catch-all, see docstring
        logger.warning("retrieve() failed for query %r: %s", query, exc)
        return []

    ids = (results.get("ids") or [[]])[0]
    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]

    output: list[dict] = []
    for chunk_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
        similarity = 1.0 - distance
        if similarity < min_similarity:
            continue
        output.append({
            **(metadata or {}),
            "chunk_id": chunk_id,
            "chunk_text": document,
            "similarity": round(similarity, 4),
        })

    return output
