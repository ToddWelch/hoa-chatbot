"""Per-address chat limit + admin login rate limit.

Two pairs of helpers, one DB table each:
- chat: 50/24h per normalized address. Table chat_rate_log.
- admin login: 5 per IP per 15 minutes. Table admin_login_attempts.

Per binding-scope item 18: every admin login attempt is logged with a
`succeeded` flag (0 or 1) so the admin can audit attempts. An exceeded
limit returns a friendly cooldown message (the 429 itself is rendered
by the route, not by this module).

The retention sweep in scheduler.py deletes chat_rate_log rows older
than 48 hours (plan-stage decision 4). admin_login_attempts is not
swept; row count grows extremely slowly (single-admin product) and the
data is useful for ops review.
"""

from __future__ import annotations

import sqlite3


# === Chat rate limit (per normalized address) ===

CHAT_DEFAULT_LIMIT = 50
CHAT_DEFAULT_WINDOW_HOURS = 24


def check_chat_rate(
    conn: sqlite3.Connection,
    normalized_address: str,
    limit: int = CHAT_DEFAULT_LIMIT,
    window_hours: int = CHAT_DEFAULT_WINDOW_HOURS,
) -> bool:
    """Return True if the address may send another chat.

    Counts rows in chat_rate_log for this address newer than the window.
    The compare uses sqlite's `datetime('now', '-N hours')` so DST and
    server-time mismatches do not matter.
    """
    if not normalized_address:
        return False
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM chat_rate_log
         WHERE normalized_address = ?
           AND created_at >= datetime('now', '-{int(window_hours)} hours');
        """,
        (normalized_address,),
    ).fetchone()
    return int(row["c"]) < limit


def record_chat(conn: sqlite3.Connection, normalized_address: str) -> None:
    """Record a chat occurrence. One row per chat send."""
    conn.execute(
        "INSERT INTO chat_rate_log (normalized_address) VALUES (?);",
        (normalized_address,),
    )


def chat_count_window(
    conn: sqlite3.Connection,
    normalized_address: str,
    window_hours: int = CHAT_DEFAULT_WINDOW_HOURS,
) -> int:
    """Return the chat count inside the rolling window. Used for admin display."""
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM chat_rate_log
         WHERE normalized_address = ?
           AND created_at >= datetime('now', '-{int(window_hours)} hours');
        """,
        (normalized_address,),
    ).fetchone()
    return int(row["c"]) if row else 0


# === Admin login rate limit (per IP hash) ===

ADMIN_DEFAULT_LIMIT = 5
ADMIN_DEFAULT_WINDOW_MINUTES = 15


def check_admin_login(
    conn: sqlite3.Connection,
    ip_hash: str,
    limit: int = ADMIN_DEFAULT_LIMIT,
    window_minutes: int = ADMIN_DEFAULT_WINDOW_MINUTES,
) -> bool:
    """Return True if this IP may attempt another admin login.

    Counts ALL attempts (succeeded or failed) inside the window. Two
    successful logins back-to-back will not lock anyone out at limit=5;
    we count every attempt because a brute-forcer could otherwise game
    it by interspersing intentional successes (which is paranoid for a
    single-admin product but cheap to enforce).
    """
    if not ip_hash:
        return False
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM admin_login_attempts
         WHERE ip_hash = ?
           AND created_at >= datetime('now', '-{int(window_minutes)} minutes');
        """,
        (ip_hash,),
    ).fetchone()
    return int(row["c"]) < limit


def record_admin_login(
    conn: sqlite3.Connection, ip_hash: str, succeeded: bool
) -> None:
    """Log an admin login attempt with the success flag."""
    conn.execute(
        "INSERT INTO admin_login_attempts (ip_hash, succeeded) VALUES (?, ?);",
        (ip_hash, 1 if succeeded else 0),
    )


def admin_login_attempts_in_window(
    conn: sqlite3.Connection,
    ip_hash: str,
    window_minutes: int = ADMIN_DEFAULT_WINDOW_MINUTES,
) -> int:
    """Return the total attempts (any status) in the window. For UI feedback."""
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM admin_login_attempts
         WHERE ip_hash = ?
           AND created_at >= datetime('now', '-{int(window_minutes)} minutes');
        """,
        (ip_hash,),
    ).fetchone()
    return int(row["c"]) if row else 0
