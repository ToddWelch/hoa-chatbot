"""Gmail message extraction helpers: subject, body, PDF attachments.

Split from services/gmail_ingest.py per the pre-identified split point.
Pure helpers around the Gmail API response shape; no DB writes.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Iterable

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


def load_whitelisted_senders(data_dir: str | None = None) -> list[str]:
    """Read whitelisted_senders.txt from the configured DATA_DIR.

    Returns an empty list if the file does not exist; the resulting query
    will not match anything, which is the safe default.
    """
    if data_dir is None:
        data_dir = os.environ.get("DATA_DIR", "./data")
    path = os.path.join(data_dir, "whitelisted_senders.txt")
    if not os.path.isfile(path):
        return []
    out: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
    return out


def build_query(senders: list[str], processed_label: str) -> str:
    """Compose the Gmail search query per binding-scope item 16."""
    if not senders:
        # Defensive: with no senders the query would match everything; force no-match.
        return (
            "from:(no-such-sender@example.invalid) is:unread "
            f"-label:{processed_label}"
        )
    sender_clause = " OR ".join(senders)
    return f"is:unread -label:{processed_label} from:({sender_clause})"


def _walk_parts(payload: dict) -> Iterable[dict]:
    """Yield every part in a multipart message recursively."""
    yield payload
    for part in payload.get("parts") or []:
        yield from _walk_parts(part)


def extract_subject(payload: dict) -> str:
    """Pull the Subject header out of the payload."""
    for h in (payload.get("headers") or []):
        if (h.get("name") or "").lower() == "subject":
            return h.get("value") or ""
    return ""


def extract_text(payload: dict) -> str:
    """Concatenate text/plain bodies from a Gmail payload."""
    parts: list[str] = []
    for part in _walk_parts(payload):
        if part.get("mimeType") != "text/plain":
            continue
        body = (part.get("body") or {}).get("data")
        if not body:
            continue
        try:
            decoded = base64.urlsafe_b64decode(body.encode("ascii")).decode(
                "utf-8", errors="replace"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("gmail body decode failed: %s", exc)
            continue
        parts.append(decoded)
    return "\n\n".join(parts).strip()


def extract_pdf_attachments(service, message_id: str, payload: dict) -> list[dict]:
    """Return [{filename, file_bytes}] for every PDF attachment in the message."""
    out: list[dict] = []
    for part in _walk_parts(payload):
        filename = part.get("filename")
        if not filename:
            continue
        mime = part.get("mimeType") or ""
        if not (filename.lower().endswith(".pdf") or "pdf" in mime.lower()):
            continue
        body = part.get("body") or {}
        att_id = body.get("attachmentId")
        if not att_id:
            data = body.get("data")
            if not data:
                continue
            try:
                file_bytes = base64.urlsafe_b64decode(data.encode("ascii"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("attachment decode (inline) failed: %s", exc)
                continue
        else:
            try:
                resp = service.users().messages().attachments().get(
                    userId="me", messageId=message_id, id=att_id
                ).execute()
                file_bytes = base64.urlsafe_b64decode(resp["data"].encode("ascii"))
            except (HttpError, KeyError, ValueError) as exc:
                logger.warning("attachment fetch failed: %s", exc)
                continue
        out.append({"filename": filename, "file_bytes": file_bytes})
    return out
