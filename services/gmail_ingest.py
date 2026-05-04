"""Gmail ingest orchestrator (binding-scope item 16).

Split per the pre-identified plan section 5 split point: auth lives in
`services/gmail_auth.py`, message extraction in `services/gmail_fetch.py`.
This module is `run_once` plus the per-message ingest + label-apply
helpers.

Behavior per binding-scope item 16:
- Query: `is:unread -label:hoa-bot-processed from:(<whitelisted_senders>)`
- Dedup: `documents.gmail_message_id` UNIQUE constraint AND attachment
  `file_hash`.
- On success: apply the `hoa-bot-processed` label. Do NOT mark as read
  (preserves the operator's inbox state).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

from googleapiclient.errors import HttpError

from services import config_store, documents as docs_svc
from services.document_parse import compute_file_hash
from services.gmail_auth import GmailIngestError, get_gmail_service
from services.gmail_fetch import (
    build_query,
    extract_pdf_attachments,
    extract_subject,
    extract_text,
    load_whitelisted_senders,
)

logger = logging.getLogger(__name__)


PROCESSED_LABEL = "hoa-bot-processed"


# === Label helpers ===

def _ensure_processed_label(service) -> str:
    """Find or create the hoa-bot-processed label; return its id."""
    try:
        resp = service.users().labels().list(userId="me").execute()
    except HttpError as exc:
        raise GmailIngestError(f"Gmail labels list failed: {exc}") from exc
    for label in resp.get("labels") or []:
        if label.get("name") == PROCESSED_LABEL:
            return label["id"]
    body = {
        "name": PROCESSED_LABEL,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    try:
        created = service.users().labels().create(
            userId="me", body=body
        ).execute()
    except HttpError as exc:
        raise GmailIngestError(f"Gmail label create failed: {exc}") from exc
    return created["id"]


def _apply_processed_label(service, message_id: str, label_id: str) -> None:
    """Apply the hoa-bot-processed label. Do NOT touch UNREAD."""
    body = {"addLabelIds": [label_id], "removeLabelIds": []}
    service.users().messages().modify(
        userId="me", id=message_id, body=body
    ).execute()


# === Run-once orchestrator ===

def run_once(conn: sqlite3.Connection, app_logger=None) -> dict:
    """Fetch unread + whitelisted + un-labeled messages, ingest each, label on success.

    Returns:
        {"fetched": N, "ingested": M, "skipped_dedup": K, "errors": [...]}
    """
    log = app_logger or logger

    senders = load_whitelisted_senders()
    if not senders:
        log.info("gmail_ingest: no whitelisted senders configured; nothing to do")
        _record_status(conn, "ok: no senders")
        return {"fetched": 0, "ingested": 0, "skipped_dedup": 0, "errors": []}

    try:
        service = get_gmail_service(conn)
    except GmailIngestError as exc:
        log.warning("gmail_ingest: %s", exc)
        _record_status(conn, f"error: {exc}")
        return {
            "fetched": 0, "ingested": 0, "skipped_dedup": 0,
            "errors": [str(exc)],
        }

    label_id = _ensure_processed_label(service)
    query = build_query(senders, PROCESSED_LABEL)

    try:
        list_resp = service.users().messages().list(
            userId="me", q=query, maxResults=50,
        ).execute()
    except HttpError as exc:
        log.error("gmail messages.list failed: %s", exc, exc_info=True)
        _record_status(conn, f"error: list {exc}")
        return {
            "fetched": 0, "ingested": 0, "skipped_dedup": 0,
            "errors": [str(exc)],
        }

    msg_refs = list_resp.get("messages") or []
    fetched = 0
    ingested = 0
    skipped_dedup = 0
    errors: list[str] = []

    for ref in msg_refs:
        msg_id = ref["id"]
        fetched += 1
        if docs_svc.is_gmail_message_seen(conn, msg_id):
            skipped_dedup += 1
            try:
                _apply_processed_label(service, msg_id, label_id)
            except HttpError as exc:
                log.warning("re-label seen msg failed: %s", exc)
            continue
        try:
            ingested += _ingest_one(conn, service, msg_id, log)
            _apply_processed_label(service, msg_id, label_id)
        except Exception as exc:  # noqa: BLE001
            log.error("gmail ingest one msg failed (%s): %s", msg_id, exc, exc_info=True)
            errors.append(f"{msg_id}: {exc}")

    summary = (
        f"ok: fetched={fetched} ingested={ingested} dedup={skipped_dedup} "
        f"errors={len(errors)}"
    )
    _record_status(conn, summary)
    return {
        "fetched": fetched,
        "ingested": ingested,
        "skipped_dedup": skipped_dedup,
        "errors": errors,
    }


def _ingest_one(conn: sqlite3.Connection, service, msg_id: str, log) -> int:
    """Fetch one message, ingest body + PDF attachments. Return count of new documents."""
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()
    payload = msg.get("payload") or {}
    subject = extract_subject(payload) or "(no subject)"
    body_text = extract_text(payload)

    new_docs = 0

    # 1. Body text as a document (if non-empty).
    if body_text:
        title = f"Gmail: {subject}"
        docs_svc.ingest_text(
            conn=conn,
            title=title,
            full_text=body_text,
            source="gmail",
            gmail_message_id=msg_id,
        )
        new_docs += 1

    # 2. PDF attachments. Each gets its own documents row, dedup by file_hash.
    data_dir = os.environ.get("DATA_DIR", "./data")
    for att in extract_pdf_attachments(service, msg_id, payload):
        file_hash = compute_file_hash(att["file_bytes"])
        if docs_svc.is_attachment_seen(conn, file_hash):
            log.info("attachment dedup skip: %s", att["filename"])
            continue
        title = f"Gmail attachment: {att['filename']} (re: {subject})"
        # gmail_message_id is set on the body row only; using it on
        # attachment rows would conflict with the UNIQUE constraint when
        # one email has multiple PDFs. Attachment dedup leans on file_hash.
        docs_svc.ingest_pdf_bytes(
            conn=conn,
            data_dir=data_dir,
            title=title,
            file_bytes=att["file_bytes"],
            source="gmail",
            gmail_message_id=None,
        )
        new_docs += 1

    return new_docs


def _record_status(conn: sqlite3.Connection, status: str) -> None:
    """Persist the latest run timestamp + status for the admin dashboard."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    config_store.set(conn, config_store.KEY_GMAIL_LAST_INGEST_AT, now)
    config_store.set(conn, config_store.KEY_GMAIL_LAST_INGEST_STATUS, status)
