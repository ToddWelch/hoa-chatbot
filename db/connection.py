"""SQLite connection management with WAL mode and locked-retry helpers.

The plan calls for raw sqlite3 (not SQLAlchemy). This module is the single
place that opens a connection, configures pragmas, and wraps writes in the
3-retry backoff Trinity's plan specifies (100ms, 250ms, 500ms) on
`OperationalError: database is locked`.

Per-request connections live on Flask's `g`; teardown closes them.
Standalone scripts (init_db, set_admin_password, gmail_oauth_setup) use
`open_connection(path)` directly because they run outside an app context.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from flask import current_app, g

logger = logging.getLogger(__name__)

_RETRY_BACKOFFS_SEC = (0.100, 0.250, 0.500)


def _row_factory_dict(cursor: sqlite3.Cursor, row: tuple) -> dict:
    """Convert each row to a plain dict keyed by column name.

    Saves the caller from sqlite3.Row tuple gymnastics. Trade-off is a small
    per-row allocation; v1 traffic is tiny so this is not a bottleneck.
    """
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def open_connection(database_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL + foreign keys + dict rows.

    Used by scripts that run outside the Flask app context. The Flask
    request path uses get_db() instead.
    """
    parent = os.path.dirname(os.path.abspath(database_path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(
        database_path,
        timeout=5.0,
        isolation_level=None,  # autocommit; we manage transactions explicitly
        check_same_thread=False,
    )
    conn.row_factory = _row_factory_dict
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def get_db() -> sqlite3.Connection:
    """Return a per-request connection, attached to flask.g.

    First call in a request opens; subsequent calls reuse. close_db is
    registered as the app teardown handler.
    """
    if "db_conn" not in g:
        path = current_app.config["DATABASE_PATH"]
        g.db_conn = open_connection(path)
    return g.db_conn


def close_db(error: BaseException | None = None) -> None:
    """Teardown handler: close the per-request connection if present."""
    conn = g.pop("db_conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error as exc:
            logger.warning("error closing sqlite connection: %s", exc)


def execute_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    params: Iterable[Any] | None = None,
) -> sqlite3.Cursor:
    """Run a single statement with retry on `database is locked`.

    Reads do not normally hit lock contention under WAL, but writes can
    (especially with the scheduler running concurrent ingest jobs). One
    helper covers both; reads simply never trigger the retry path.
    """
    last_exc: sqlite3.OperationalError | None = None
    attempts = (0.0,) + _RETRY_BACKOFFS_SEC  # first attempt has no sleep
    for delay in attempts:
        if delay > 0:
            time.sleep(delay)
        try:
            return conn.execute(sql, params or ())
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_exc = exc
            logger.warning("sqlite lock contention, retrying after %.0fms", delay * 1000)
    assert last_exc is not None
    raise last_exc


def executemany_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    seq: Iterable[Iterable[Any]],
) -> sqlite3.Cursor:
    """executemany variant of execute_with_retry."""
    rows = list(seq)
    last_exc: sqlite3.OperationalError | None = None
    attempts = (0.0,) + _RETRY_BACKOFFS_SEC
    for delay in attempts:
        if delay > 0:
            time.sleep(delay)
        try:
            return conn.executemany(sql, rows)
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_exc = exc
            logger.warning("sqlite lock contention (many), retrying after %.0fms", delay * 1000)
    assert last_exc is not None
    raise last_exc


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN/COMMIT/ROLLBACK with the same locked-retry semantics on BEGIN.

    Intentionally simple: callers do their own statement execution inside.
    On any exception we roll back and re-raise so write paths fail loudly.
    """
    last_exc: sqlite3.OperationalError | None = None
    started = False
    attempts = (0.0,) + _RETRY_BACKOFFS_SEC
    for delay in attempts:
        if delay > 0:
            time.sleep(delay)
        try:
            conn.execute("BEGIN IMMEDIATE;")
            started = True
            break
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_exc = exc
    if not started:
        assert last_exc is not None
        raise last_exc
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    conn.execute("COMMIT;")
