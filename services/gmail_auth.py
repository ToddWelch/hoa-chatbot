"""Gmail OAuth credentials + service-client builder.

Split from services/gmail_ingest.py per the pre-identified split point in
plan section 5 (services/gmail_ingest.py was approaching/over the 300
LOC cap).

Refresh token is decrypted via Fernet on every call; tiny cost compared
to the API roundtrip. The Credentials object refreshes automatically
when the access token expires.
"""

from __future__ import annotations

import logging
import os
import sqlite3

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as build_gmail_service

from services import config_store

logger = logging.getLogger(__name__)


GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
_TOKEN_URI = "https://oauth2.googleapis.com/token"


class GmailIngestError(Exception):
    """Raised when ingestion cannot proceed (auth missing, API down, malformed config)."""


def build_credentials(conn: sqlite3.Connection) -> Credentials:
    """Build google.oauth2 credentials from the encrypted refresh token + env client."""
    refresh_token = config_store.get_secret(
        conn, config_store.KEY_GMAIL_REFRESH_TOKEN
    )
    if not refresh_token:
        raise GmailIngestError(
            "Gmail refresh token is not configured. Bootstrap with "
            "`railway run python scripts/gmail_oauth_setup.py`."
        )

    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise GmailIngestError(
            "GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET env vars are not set."
        )

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=GMAIL_SCOPES,
    )


def get_gmail_service(conn: sqlite3.Connection):
    """Return a Gmail service client. Raises GmailIngestError on auth failure."""
    creds = build_credentials(conn)
    try:
        creds.refresh(GoogleAuthRequest())
    except Exception as exc:  # noqa: BLE001
        raise GmailIngestError(f"Gmail token refresh failed: {exc}") from exc
    return build_gmail_service(
        "gmail", "v1", credentials=creds, cache_discovery=False
    )
