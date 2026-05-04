"""Document store: ingest pipelines + DB writes + volume save.

Split from the original combined services/documents.py per the pre-
identified split point in plan section 5. Parsing helpers (PDF extract,
chunking, file hash, needs_ocr decision) live in services/document_parse.py.

This module:
- accepts already-extracted text or raw bytes
- delegates parsing to document_parse
- writes the documents row, the file (if PDF), and chunk + embedding rows
- exposes the admin helpers (list, toggle, soft-delete, reembed)
- exposes the Phase 6 dedup helpers used by services/gmail_ingest.py

Caller controls the data_dir path because routes have it on app.config
and the gmail ingest job has it on the worker; passing it in keeps this
module independent of Flask globals.
"""

from __future__ import annotations

import logging
import os
import sqlite3

from db.connection import transaction
from services import embeddings
from services.document_parse import (
    chunk_text,
    compute_file_hash,
    extract_pdf_text,
    needs_ocr as _needs_ocr,
)

logger = logging.getLogger(__name__)


# === Volume save ===

def _save_pdf_to_volume(
    data_dir: str,
    file_bytes: bytes,
    doc_id: int,
) -> str:
    """Save the PDF bytes under {data_dir}/documents/{doc_id}.pdf. Returns relative path."""
    folder = os.path.join(data_dir, "documents")
    os.makedirs(folder, exist_ok=True)
    rel_path = os.path.join("documents", f"{doc_id}.pdf")
    abs_path = os.path.join(data_dir, rel_path)
    with open(abs_path, "wb") as fh:
        fh.write(file_bytes)
    return rel_path


# === Embed + persist ===

def _persist_chunks(
    conn: sqlite3.Connection,
    doc_id: int,
    chunks: list[str],
) -> None:
    """Embed and insert chunk rows. Caller controls the surrounding transaction.

    On Voyage failure (EmbeddingsUnavailable), the partial chunk inserts
    are rolled back by the surrounding transaction. The document row
    remains; admin can click "reembed" later.
    """
    if not chunks:
        return
    vectors = embeddings.embed_texts(chunks, input_type="document")
    rows = []
    for idx, (text, vec) in enumerate(zip(chunks, vectors)):
        rows.append((doc_id, idx, text, embeddings.serialize(vec)))
    conn.executemany(
        """
        INSERT INTO chunks (document_id, chunk_index, text, embedding)
        VALUES (?, ?, ?, ?);
        """,
        rows,
    )


def _delete_existing_chunks(conn: sqlite3.Connection, doc_id: int) -> None:
    """Drop all existing chunks for one document. Used by reembed."""
    conn.execute("DELETE FROM chunks WHERE document_id = ?;", (doc_id,))


# === Public entry points ===

def ingest_pdf_bytes(
    conn: sqlite3.Connection,
    data_dir: str,
    title: str,
    file_bytes: bytes,
    source: str,
    gmail_message_id: str | None = None,
) -> dict:
    """Ingest a PDF: extract text, chunk, embed, store.

    Returns:
        {"doc_id": int, "chunks": int, "needs_ocr": bool}
    """
    file_hash = compute_file_hash(file_bytes)
    full_text = extract_pdf_text(file_bytes)
    needs_ocr_flag = _needs_ocr(file_bytes, full_text)

    with transaction(conn):
        is_active = 0 if needs_ocr_flag else 1
        cur = conn.execute(
            """
            INSERT INTO documents
              (title, source, full_text, file_hash, gmail_message_id, is_active, needs_ocr)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                title,
                source,
                full_text,
                file_hash,
                gmail_message_id,
                is_active,
                1 if needs_ocr_flag else 0,
            ),
        )
        doc_id = int(cur.lastrowid)

        rel_path = _save_pdf_to_volume(data_dir, file_bytes, doc_id)
        conn.execute(
            "UPDATE documents SET file_path = ? WHERE id = ?;",
            (rel_path, doc_id),
        )

        chunks: list[str] = []
        if not needs_ocr_flag and full_text:
            chunks = chunk_text(full_text)
            _persist_chunks(conn, doc_id, chunks)

    return {"doc_id": doc_id, "chunks": len(chunks), "needs_ocr": needs_ocr_flag}


def ingest_text(
    conn: sqlite3.Connection,
    title: str,
    full_text: str,
    source: str,
    gmail_message_id: str | None = None,
) -> dict:
    """Ingest pasted text or extracted email body. No volume save."""
    full_text = (full_text or "").strip()
    if not full_text:
        raise ValueError("ingest_text: full_text is empty")

    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO documents (title, source, full_text, gmail_message_id, is_active)
            VALUES (?, ?, ?, ?, 1);
            """,
            (title, source, full_text, gmail_message_id),
        )
        doc_id = int(cur.lastrowid)
        chunks = chunk_text(full_text)
        _persist_chunks(conn, doc_id, chunks)

    return {"doc_id": doc_id, "chunks": len(chunks), "needs_ocr": False}


def reembed_document(conn: sqlite3.Connection, doc_id: int) -> int:
    """Drop existing chunks and re-embed from `documents.full_text`.

    Returns the new chunk count. Raises if the document has no full_text.
    """
    row = conn.execute(
        "SELECT full_text, is_active, needs_ocr FROM documents WHERE id = ?;",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"document {doc_id} not found")
    full_text = (row.get("full_text") or "").strip()
    if not full_text:
        raise ValueError(f"document {doc_id} has no full_text; paste text before reembedding")

    chunks = chunk_text(full_text)
    with transaction(conn):
        _delete_existing_chunks(conn, doc_id)
        _persist_chunks(conn, doc_id, chunks)
        # If the doc was marked needs_ocr but admin pasted text, clear the flag.
        conn.execute(
            "UPDATE documents SET needs_ocr = 0, is_active = 1 WHERE id = ?;",
            (doc_id,),
        )
    return len(chunks)


def update_full_text(conn: sqlite3.Connection, doc_id: int, full_text: str) -> None:
    """Update an existing document's full_text. Used by the OCR-paste flow.

    Does not re-chunk; admin clicks "reembed" after pasting.
    """
    conn.execute(
        "UPDATE documents SET full_text = ? WHERE id = ?;",
        (full_text, doc_id),
    )


def soft_delete(conn: sqlite3.Connection, doc_id: int) -> None:
    """Set `is_active=0`. Retrieval excludes inactive documents."""
    conn.execute(
        "UPDATE documents SET is_active = 0 WHERE id = ?;",
        (doc_id,),
    )


def toggle_active(conn: sqlite3.Connection, doc_id: int) -> int:
    """Flip is_active. Returns new value (0 or 1)."""
    row = conn.execute(
        "SELECT is_active FROM documents WHERE id = ?;",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"document {doc_id} not found")
    new_val = 0 if int(row["is_active"]) else 1
    conn.execute(
        "UPDATE documents SET is_active = ? WHERE id = ?;",
        (new_val, doc_id),
    )
    return new_val


def list_documents(conn: sqlite3.Connection) -> list[dict]:
    """Return every document with its chunk count. Newest first."""
    cur = conn.execute(
        """
        SELECT
            d.id, d.title, d.source, d.is_active, d.needs_ocr, d.file_path,
            d.gmail_message_id, d.created_at,
            (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
          FROM documents d
         ORDER BY d.created_at DESC;
        """
    )
    return list(cur.fetchall())


def get_document(conn: sqlite3.Connection, doc_id: int) -> dict | None:
    """Fetch a single document row (without chunks)."""
    return conn.execute(
        "SELECT * FROM documents WHERE id = ?;",
        (doc_id,),
    ).fetchone()


def is_attachment_seen(conn: sqlite3.Connection, file_hash: str) -> bool:
    """Phase 6 dedup helper: True if any document already has this file_hash."""
    if not file_hash:
        return False
    row = conn.execute(
        "SELECT 1 FROM documents WHERE file_hash = ? LIMIT 1;",
        (file_hash,),
    ).fetchone()
    return row is not None


def is_gmail_message_seen(conn: sqlite3.Connection, gmail_message_id: str) -> bool:
    """Phase 6 dedup helper: True if any document already has this gmail_message_id."""
    if not gmail_message_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM documents WHERE gmail_message_id = ? LIMIT 1;",
        (gmail_message_id,),
    ).fetchone()
    return row is not None


def chunk_count_total(conn: sqlite3.Connection) -> int:
    """Total chunk row count. Used by the admin dashboard."""
    row = conn.execute("SELECT COUNT(*) AS c FROM chunks;").fetchone()
    return int(row["c"]) if row else 0


def documents_count(conn: sqlite3.Connection) -> int:
    """Total documents (active + inactive). Admin dashboard."""
    row = conn.execute("SELECT COUNT(*) AS c FROM documents;").fetchone()
    return int(row["c"]) if row else 0


def needs_ocr_count(conn: sqlite3.Connection) -> int:
    """Count documents flagged as needing OCR."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM documents WHERE needs_ocr = 1;"
    ).fetchone()
    return int(row["c"]) if row else 0
