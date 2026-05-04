"""Wrapper around the `config` key-value table.

All config reads and writes go through here so the encryption decision
(plaintext vs Fernet) lives in one place. The plan calls out specific
key constants so call sites cannot typo them.

Plaintext values: anything safe to read in a backup or log.
Encrypted values: secrets (Gmail refresh token, optional client secret).

Per plan-stage decision 2: the Gmail refresh token is stored at
key='gmail_refresh_token' in the value_encrypted column.
"""

from __future__ import annotations

import sqlite3

from services import crypto

# === Known config keys (single source of truth for typo safety) ===
KEY_ADMIN_PASSWORD_HASH = "admin_password_hash"
KEY_HOA_CONTACT_EMAIL = "hoa_contact_email"
KEY_COMMUNITY_NAME = "community_name"
KEY_GMAIL_REFRESH_TOKEN = "gmail_refresh_token"
KEY_GMAIL_OAUTH_CLIENT_ID = "gmail_oauth_client_id"
KEY_GMAIL_OAUTH_CLIENT_SECRET = "gmail_oauth_client_secret"
KEY_GMAIL_LAST_INGEST_AT = "gmail_last_ingest_at"
KEY_GMAIL_LAST_INGEST_STATUS = "gmail_last_ingest_status"


def get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Return the plaintext value for key, or default if absent."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = ? LIMIT 1;", (key,)
    ).fetchone()
    if row is None or row.get("value") is None:
        return default
    return row["value"]


def set(conn: sqlite3.Connection, key: str, value: str) -> None:  # noqa: A001 (intentional)
    """Upsert plaintext value at key. Clears value_encrypted to keep one column truthy.

    The shadowing of the builtin `set` is contained to this module; callers
    use module-prefixed `config_store.set(...)`.
    """
    conn.execute(
        """
        INSERT INTO config (key, value, value_encrypted, updated_at)
        VALUES (?, ?, NULL, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
          value = excluded.value,
          value_encrypted = NULL,
          updated_at = datetime('now');
        """,
        (key, value),
    )


def get_secret(conn: sqlite3.Connection, key: str) -> str | None:
    """Return the decrypted value at key, or None if absent."""
    row = conn.execute(
        "SELECT value_encrypted FROM config WHERE key = ? LIMIT 1;", (key,)
    ).fetchone()
    if row is None or row.get("value_encrypted") is None:
        return None
    return crypto.decrypt(row["value_encrypted"])


def set_secret(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert encrypted value at key. Clears plaintext column."""
    ciphertext = crypto.encrypt(value)
    conn.execute(
        """
        INSERT INTO config (key, value, value_encrypted, updated_at)
        VALUES (?, NULL, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
          value = NULL,
          value_encrypted = excluded.value_encrypted,
          updated_at = datetime('now');
        """,
        (key, ciphertext),
    )


def has(conn: sqlite3.Connection, key: str) -> bool:
    """Return True if key exists with a non-null value (plaintext or encrypted)."""
    row = conn.execute(
        "SELECT value, value_encrypted FROM config WHERE key = ? LIMIT 1;", (key,)
    ).fetchone()
    if row is None:
        return False
    return row.get("value") is not None or row.get("value_encrypted") is not None


def delete(conn: sqlite3.Connection, key: str) -> None:
    """Remove a config row. Used by tests and the password-reset flow."""
    conn.execute("DELETE FROM config WHERE key = ?;", (key,))
