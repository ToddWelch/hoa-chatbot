"""Voyage embeddings client.

Phase 1: ships EMBEDDING_DIM constant and validate_existing_chunks so the
app factory boots. Phase 3 fills in embed_texts/embed_one against the
Voyage API.

EMBEDDING_DIM is a module constant rather than env-driven because:
- changing it without re-embedding corrupts retrieval (binding-scope item 7);
- swapping it should require a deliberate code change reviewed by the
  operator (CO + Trinity), not a silent env tweak;
- the boot-time validation below catches the case where the operator
  flipped the constant but forgot to re-embed the documents.

Voyage `voyage-3-lite` returns 512-dim float32 vectors per
https://docs.voyageai.com/docs/embeddings (confirmed at brief open
decision 2; revisit if the model is swapped).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# COST FLAG: Voyage embeddings, free tier covers v1 traffic.
EMBEDDING_DIM = 512  # voyage-3-lite default; matches API response shape.
_EMBEDDING_BYTES = EMBEDDING_DIM * 4  # float32 = 4 bytes

_VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_TIMEOUT_SEC = 15.0


class EmbeddingsUnavailable(Exception):
    """Raised when the Voyage API call fails or returns malformed data.

    Catch surface for callers: every Voyage failure mode (HTTP error,
    timeout, dim mismatch, missing API key) bubbles as this single
    exception type. routes/public.py turns this into the friendly
    "AI service is having trouble" copy.
    """


def embed_texts(texts: list[str]) -> list:
    """Return a list of np.ndarray vectors, one per input string.

    Phase 3 implementation. Stub here so Phase 1 imports do not fail.
    """
    raise NotImplementedError("Phase 3 wires the Voyage call")


def embed_one(text: str) -> "np.ndarray":
    """Convenience wrapper for embedding a single string."""
    return embed_texts([text])[0]


def serialize(vec) -> bytes:
    """Convert a float32 numpy array into the BLOB representation.

    Validates shape and dtype before persisting so the boot-time
    validator can rely on every BLOB being EMBEDDING_DIM * 4 bytes.
    """
    import numpy as np  # local import to avoid hard dep at Phase 1 boot

    if vec.dtype != np.float32:
        vec = vec.astype(np.float32)
    if vec.shape != (EMBEDDING_DIM,):
        raise EmbeddingsUnavailable(
            f"embedding shape {vec.shape} does not match EMBEDDING_DIM={EMBEDDING_DIM}"
        )
    return vec.tobytes()


def deserialize(blob: bytes):
    """Reverse of serialize."""
    import numpy as np

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


def _voyage_api_key() -> str:
    """Fetch the Voyage key from env. Raises EmbeddingsUnavailable if absent."""
    key = os.environ.get("VOYAGE_API_KEY", "")
    if not key:
        raise EmbeddingsUnavailable("VOYAGE_API_KEY env var is not set")
    return key
