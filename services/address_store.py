"""DB-backed whitelist of resident addresses (plan-stage decision 1).

Replaces the brief's data/valid_addresses.txt flat file. The DB choice
gives us:
- one-transaction add via the failed-address admin flow
- no file-load-on-startup race
- one less moving part on the Railway volume

Schema lives in db/migrations/0001_init.sql. This module is the only
place that touches the addresses table; routes and admin flows go
through these helpers.
"""

from __future__ import annotations

import sqlite3


def is_valid(conn: sqlite3.Connection, normalized: str) -> bool:
    """Return True if the normalized address is in the whitelist."""
    if not normalized:
        return False
    row = conn.execute(
        "SELECT 1 FROM addresses WHERE normalized_address = ? LIMIT 1;",
        (normalized,),
    ).fetchone()
    return row is not None


def add(
    conn: sqlite3.Connection,
    normalized: str,
    raw: str | None = None,
    source: str = "admin",
    note: str | None = None,
) -> bool:
    """Insert a new whitelisted address. Returns True if inserted, False if duplicate."""
    if not normalized:
        return False
    try:
        conn.execute(
            """
            INSERT INTO addresses (normalized_address, raw_address, source, note)
            VALUES (?, ?, ?, ?);
            """,
            (normalized, raw, source, note),
        )
        return True
    except sqlite3.IntegrityError:
        # UNIQUE conflict on normalized_address: already whitelisted, no-op.
        return False


def list_all(conn: sqlite3.Connection) -> list[dict]:
    """Return every address row, newest first. Used by /admin/addresses."""
    cur = conn.execute(
        """
        SELECT id, normalized_address, raw_address, source, note, created_at
          FROM addresses
         ORDER BY created_at DESC;
        """
    )
    return list(cur.fetchall())


def remove(conn: sqlite3.Connection, address_id: int) -> bool:
    """Delete by id. Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM addresses WHERE id = ?;", (address_id,))
    return cur.rowcount > 0


def count(conn: sqlite3.Connection) -> int:
    """Total whitelisted addresses (for the admin dashboard)."""
    row = conn.execute("SELECT COUNT(*) AS c FROM addresses;").fetchone()
    return int(row["c"]) if row else 0
