"""Cosine top-k retrieval over the chunks table (plan-stage decision 3).

Split out of services/chat.py so a future caller (e.g. an admin "preview
this query" tool) can reuse retrieval without dragging in the chat
layer.

For v1 with ~3000 chunks the full table scan is fine. ANN indexing
(faiss, sqlite-vss) is deferred to v2 if chunks > ~50k.

Returned dict shape per chunk:
    {
        "chunk_id": int,
        "document_id": int,
        "title": str,            # parent document title, joined in
        "text": str,             # chunk text
        "score": float,          # cosine similarity, 0..1
    }
"""

from __future__ import annotations

import logging
import sqlite3

import numpy as np

from services.embeddings import deserialize, embed_one

logger = logging.getLogger(__name__)


def _normalize_vec(v: np.ndarray) -> np.ndarray:
    """Unit-norm a vector for cosine via dot product. Defensive against zero vectors."""
    n = float(np.linalg.norm(v))
    if n <= 0.0:
        return v
    return v / n


def _cosine_search(
    query_vec: np.ndarray,
    rows: list[dict],
    k: int,
) -> list[tuple[float, dict]]:
    """Score each row by cosine and return top-k.

    `rows` is the raw fetched rows from the chunks join (each row dict
    has chunk_id, document_id, title, text, embedding). The embedding
    column is bytes; we deserialize on the fly.

    Returns list of (score, row) tuples sorted descending. The `text`
    and `embedding` keys are preserved on the row; the caller drops the
    embedding when shaping the chat-layer response.
    """
    if not rows:
        return []
    qn = _normalize_vec(query_vec)

    # Stack embeddings into a (N, D) matrix for vectorized dot.
    matrix = np.empty((len(rows), len(qn)), dtype=np.float32)
    for i, row in enumerate(rows):
        vec = deserialize(row["embedding"])
        n = float(np.linalg.norm(vec))
        matrix[i] = vec if n <= 0.0 else (vec / n)

    scores = matrix @ qn  # cosine between unit vectors
    # argpartition is faster than full sort but argsort on small N is fine.
    order = np.argsort(-scores)
    top = order[:k]
    return [(float(scores[i]), rows[i]) for i in top]


def top_k(
    conn: sqlite3.Connection,
    query: str,
    k: int = 6,
) -> list[dict]:
    """Embed the query, scan active chunks, return top k.

    "active" = parent document has is_active=1. Soft-deleted documents
    are excluded so retrieval respects the admin's delete.

    Raises EmbeddingsUnavailable upstream from embed_one if Voyage is
    down; the chat layer catches that and renders the friendly fallback.
    """
    query_vec = embed_one(query, input_type="query")

    cur = conn.execute(
        """
        SELECT
            c.id          AS chunk_id,
            c.document_id AS document_id,
            d.title       AS title,
            c.text        AS text,
            c.embedding   AS embedding
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE d.is_active = 1
         ORDER BY c.id;
        """
    )
    rows = list(cur.fetchall())
    if not rows:
        return []

    scored = _cosine_search(query_vec, rows, k)
    out: list[dict] = []
    for score, row in scored:
        out.append({
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "title": row["title"],
            "text": row["text"],
            "score": score,
        })
    return out


def chunk_count(conn: sqlite3.Connection) -> int:
    """Count active chunks. Used by the chat layer to detect the empty-table state."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE d.is_active = 1;
        """
    ).fetchone()
    return int(row["c"]) if row else 0
