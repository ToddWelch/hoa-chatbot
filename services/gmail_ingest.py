"""Gmail ingest pipeline. Phase 6 implementation.

Phase 1: stub so scheduler.py imports cleanly.
Phase 6 fills in:
- _get_gmail_service: build google-api-python-client from refresh token
  (decrypted from config.gmail_refresh_token)
- run_once: search for unread, whitelisted, un-labeled messages, ingest
  each one through services.documents, dedup by gmail_message_id and
  attachment file_hash, apply hoa-bot-processed label on success
- helpers for label create/lookup, message extraction, sender filter
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


class GmailIngestError(Exception):
    """Raised when the ingest pipeline cannot proceed (auth missing,
    API down, malformed config)."""


def run_once(conn: sqlite3.Connection, app_logger=None) -> dict:
    """Stub. Phase 6 fills in.

    Returns the shape the scheduler logs:
        {"fetched": N, "ingested": M, "skipped_dedup": K, "errors": [...]}
    """
    return {
        "fetched": 0,
        "ingested": 0,
        "skipped_dedup": 0,
        "errors": ["gmail ingest not yet wired (Phase 6)"],
    }
