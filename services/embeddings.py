"""Voyage embeddings client.

Pattern lifted from
`/home/todd/projects/Welch-Command-Center/backend/app/services/embeddings_service.py`,
collapsed to module-level free functions because there is no Flask
extension lifecycle to attach to.

Single exception type (EmbeddingsUnavailable) bubbles every Voyage
failure mode (HTTP 4xx/5xx, timeout, missing API key, dim mismatch).
Routes turn this into the friendly "AI service is having trouble"
fallback per binding-scope items (chat error UX).

EMBEDDING_DIM is a module constant rather than env-driven because:
- changing it without re-embedding corrupts retrieval (binding-scope item 7);
- swapping it should require a deliberate code change reviewed by the
  operator (CO + Trinity), not a silent env tweak;
- the boot-time validation below catches the case where the operator
  flipped the constant but forgot to re-embed the documents.

Voyage `voyage-3-lite` returns 512-dim float32 vectors per
https://docs.voyageai.com/docs/embeddings .
"""

from __future__ import annotations

import logging
import os
import sqlite3

import httpx
import numpy as np

logger = logging.getLogger(__name__)

# COST FLAG: Voyage embeddings, free tier covers v1 traffic.
EMBEDDING_DIM = 512  # voyage-3-lite default; matches API response shape.
_EMBEDDING_BYTES = EMBEDDING_DIM * 4  # float32 = 4 bytes

_VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_TIMEOUT_SEC = 15.0
_BATCH_MAX = 128  # Voyage's per-request input cap; we batch larger lists.


class EmbeddingsUnavailable(Exception):
    """Raised on any Voyage failure mode (HTTP error, timeout, malformed response)."""


def _voyage_api_key() -> str:
    """Fetch the Voyage key from env. Raises EmbeddingsUnavailable if absent."""
    key = os.environ.get("VOYAGE_API_KEY", "")
    if not key:
        raise EmbeddingsUnavailable("VOYAGE_API_KEY env var is not set")
    return key


def _voyage_model() -> str:
    """Read the model id from env, defaulting to voyage-3-lite."""
    return os.environ.get("VOYAGE_MODEL", "voyage-3-lite")


def _post_voyage(texts: list[str], input_type: str = "document") -> list[np.ndarray]:
    """POST a batch of texts to Voyage. Returns float32 vectors.

    Raises EmbeddingsUnavailable on any HTTP failure or dim mismatch.
    `input_type` is "document" for indexing and "query" for retrieval;
    Voyage uses different prompt prefixes internally.
    """
    api_key = _voyage_api_key()
    model = _voyage_model()
    body = {
        "model": model,
        "input": texts,
        "input_type": input_type,
    }
    try:
        with httpx.Client(timeout=_VOYAGE_TIMEOUT_SEC) as client:
            resp = client.post(
                _VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            )
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        raise EmbeddingsUnavailable(f"Voyage HTTP error: {exc}") from exc

    if resp.status_code != 200:
        raise EmbeddingsUnavailable(
            f"Voyage returned {resp.status_code}: {resp.text[:200]}"
        )

    try:
        payload = resp.json()
        rows = payload["data"]
    except (KeyError, ValueError) as exc:
        raise EmbeddingsUnavailable(f"Voyage malformed response: {exc}") from exc

    if len(rows) != len(texts):
        raise EmbeddingsUnavailable(
            f"Voyage returned {len(rows)} embeddings for {len(texts)} inputs"
        )

    vectors: list[np.ndarray] = []
    for r in rows:
        emb = r.get("embedding")
        if emb is None:
            raise EmbeddingsUnavailable("Voyage response row missing 'embedding' field")
        vec = np.asarray(emb, dtype=np.float32)
        if vec.shape != (EMBEDDING_DIM,):
            raise EmbeddingsUnavailable(
                f"Voyage returned dim {vec.shape} but EMBEDDING_DIM={EMBEDDING_DIM}"
            )
        vectors.append(vec)
    return vectors


def embed_texts(texts: list[str], input_type: str = "document") -> list[np.ndarray]:
    """Embed a list of strings. Batches into _BATCH_MAX-sized chunks.

    `input_type` is "document" for indexing pipelines and "query" for
    retrieval. Voyage applies different prompt prefixes internally.
    """
    if not texts:
        return []
    out: list[np.ndarray] = []
    for i in range(0, len(texts), _BATCH_MAX):
        batch = texts[i : i + _BATCH_MAX]
        out.extend(_post_voyage(batch, input_type=input_type))
    return out


def embed_one(text: str, input_type: str = "document") -> np.ndarray:
    """Embed a single string. Convenience wrapper."""
    return embed_texts([text], input_type=input_type)[0]


def serialize(vec: np.ndarray) -> bytes:
    """Convert a float32 numpy array into the BLOB representation."""
    if vec.dtype != np.float32:
        vec = vec.astype(np.float32)
    if vec.shape != (EMBEDDING_DIM,):
        raise EmbeddingsUnavailable(
            f"embedding shape {vec.shape} does not match EMBEDDING_DIM={EMBEDDING_DIM}"
        )
    return vec.tobytes()


def deserialize(blob: bytes) -> np.ndarray:
    """Reverse of serialize."""
    if len(blob) != _EMBEDDING_BYTES:
        raise EmbeddingsUnavailable(
            f"chunk BLOB length {len(blob)} does not match expected {_EMBEDDING_BYTES}"
        )
    return np.frombuffer(blob, dtype=np.float32)


def validate_existing_chunks(conn: sqlite3.Connection) -> None:
    """Boot-time check (binding-scope item 7).

    Scan the first BLOB length in `chunks` and refuse to start if it
    does not match the current EMBEDDING_DIM. On a fresh DB this is a
    no-op (no chunks). After documents have been ingested, this catches
    a model swap that would silently corrupt retrieval.

    Raises RuntimeError on mismatch (caught nowhere; the app fails to
    boot, which is the desired loud failure).
    """
    cur = conn.execute("SELECT length(embedding) AS blen FROM chunks LIMIT 1;")
    row = cur.fetchone()
    if row is None:
        return  # empty table, fresh DB, nothing to validate
    blen = row["blen"]
    if blen != _EMBEDDING_BYTES:
        raise RuntimeError(
            f"existing chunks have BLOB length {blen} but EMBEDDING_DIM={EMBEDDING_DIM} "
            f"implies length {_EMBEDDING_BYTES}. The embedding model or dim was changed "
            f"without re-embedding. Run: DELETE FROM chunks; then click 'reembed' on each "
            f"document in /admin/documents."
        )
