"""Document parsing helpers: PDF extraction, chunking, file hashing.

Split from services/documents.py per the pre-identified split point in
plan section 5 (services/documents.py was approaching the 300 LOC cap).
This module is pure: no DB access, no Voyage calls, no file system writes.

Constants:
- CHUNK_TOKENS: 800 (per brief 6.5)
- CHUNK_OVERLAP_TOKENS: 100 (per brief 6.5)
- OCR_NEEDED_THRESHOLD_BYTES: 100_000 (per brief 6.5)
- OCR_NEEDED_MIN_CHARS: 50 (per brief 6.5)

Token approximation: 1 token ~= 4 characters. Voyage tokenizes properly
on its end for embedding; v1 chunk boundaries do not need a real tokenizer.
"""

from __future__ import annotations

import hashlib
import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


CHUNK_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 100
OCR_NEEDED_THRESHOLD_BYTES = 100_000
OCR_NEEDED_MIN_CHARS = 50
_TOKENS_TO_CHARS = 4


def extract_pdf_text(file_bytes: bytes) -> str:
    """Run pypdf over the bytes; return concatenated page text.

    Returns empty string on a parse failure rather than raising; the
    caller decides whether to flag needs_ocr or surface a friendly error.
    """
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        parts: list[str] = []
        for page in reader.pages:
            try:
                txt = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning("pypdf page extract failed: %s", exc)
                txt = ""
            if txt:
                parts.append(txt)
        return "\n\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pypdf failed to read PDF: %s", exc)
        return ""


def chunk_text(text: str) -> list[str]:
    """Split text into ~800 token chunks with ~100 token overlap.

    Boundaries snap to whitespace where possible to avoid splitting words.
    Empty input returns empty list. Single-window input returns one chunk.
    """
    text = (text or "").strip()
    if not text:
        return []

    window_chars = CHUNK_TOKENS * _TOKENS_TO_CHARS
    step_chars = (CHUNK_TOKENS - CHUNK_OVERLAP_TOKENS) * _TOKENS_TO_CHARS

    if len(text) <= window_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + window_chars, len(text))
        # Snap to last whitespace within last 200 chars for clean boundaries.
        if end < len(text):
            snap_window = text[max(end - 200, start):end]
            last_space = snap_window.rfind(" ")
            if last_space > 0:
                end = max(end - 200, start) + last_space
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = end - (CHUNK_OVERLAP_TOKENS * _TOKENS_TO_CHARS)
        if start < 0:
            start = 0
    return chunks


def compute_file_hash(file_bytes: bytes) -> str:
    """SHA-256 hex digest of the file bytes. Used for attachment dedup."""
    return hashlib.sha256(file_bytes).hexdigest()


def needs_ocr(file_bytes: bytes, extracted_text: str) -> bool:
    """Return True if a PDF should be flagged needs_ocr.

    Brief 6.5: any PDF over 100KB whose pypdf extract is shorter than
    50 chars is presumed to be image-only (scanned). v1 has no OCR
    pipeline; admin pastes text manually.
    """
    return (
        len(file_bytes) > OCR_NEEDED_THRESHOLD_BYTES
        and len(extracted_text) < OCR_NEEDED_MIN_CHARS
    )
