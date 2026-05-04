"""APScheduler boot. Phase 7 wires the jobs.

Pattern from the WCC reference codebase (Flask app factory's APScheduler
integration block). A single BackgroundScheduler in-process; gunicorn
worker pinned to 1 to avoid duplicate runs. Scripts skip the scheduler
via FLASK_SKIP_SCHEDULER.

Two jobs:
- gmail_ingest: every 15 minutes (per binding-scope item 16).
- retention_sweep: daily at 03:00 UTC, deletes messages > 90d, orphan
  conversations, chat_rate_log > 48h (binding-scope item 6 +
  plan-stage decision 4).

APScheduler runs in its own threads, separate from the gunicorn request
threads. Each job opens a fresh DB connection (the per-request g-bound
connection is not available in the scheduler thread).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def start_scheduler(app) -> BackgroundScheduler:
    """Build and start a BackgroundScheduler attached to the Flask app.

    Returns the scheduler so the caller (typically the app factory) can
    keep a reference for clean shutdown if needed. v1 lets the process
    exit drop the scheduler with no cleanup.
    """
    scheduler = BackgroundScheduler(timezone="UTC")

    # Gmail ingest, every 15 minutes (binding-scope item 16).
    scheduler.add_job(
        func=lambda: _job_gmail_ingest(app),
        trigger="interval",
        minutes=15,
        id="gmail_ingest",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    # Retention sweep, daily at 03:00 UTC.
    scheduler.add_job(
        func=lambda: _job_retention_sweep(app),
        trigger="cron",
        hour=3,
        minute=0,
        id="retention_sweep",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.start()
    app.apscheduler = scheduler
    logger.info("APScheduler started: gmail_ingest (15m), retention_sweep (daily 03:00 UTC)")
    return scheduler


def _job_gmail_ingest(app) -> None:
    """Wrap services.gmail_ingest.run_once with an app context + DB connection."""
    from db.connection import open_connection
    from services.gmail_ingest import run_once

    with app.app_context():
        conn = open_connection(app.config["DATABASE_PATH"])
        try:
            result = run_once(conn, app.logger)
            app.logger.info("gmail_ingest result: %s", result)
        except Exception as exc:  # noqa: BLE001
            # Never let a job exception kill the scheduler thread.
            app.logger.error("gmail_ingest failed: %s", exc, exc_info=True)
        finally:
            conn.close()


def _job_retention_sweep(app) -> None:
    """Delete old messages, orphan conversations, old chat_rate_log rows."""
    from db.connection import open_connection, transaction

    with app.app_context():
        conn = open_connection(app.config["DATABASE_PATH"])
        try:
            with transaction(conn):
                # 90-day messages retention (brief section 6.8 + binding scope 6).
                cur = conn.execute(
                    "DELETE FROM messages WHERE created_at < datetime('now', '-90 days');"
                )
                msg_deleted = cur.rowcount

                # Drop conversations with no remaining messages.
                cur = conn.execute(
                    """
                    DELETE FROM conversations
                    WHERE id NOT IN (SELECT DISTINCT conversation_id FROM messages);
                    """
                )
                conv_deleted = cur.rowcount

                # 48-hour chat_rate_log retention (plan-stage decision 4).
                cur = conn.execute(
                    "DELETE FROM chat_rate_log WHERE created_at < datetime('now', '-48 hours');"
                )
                rate_deleted = cur.rowcount

            app.logger.info(
                "retention_sweep: messages=%d conversations=%d chat_rate_log=%d",
                msg_deleted, conv_deleted, rate_deleted,
            )
        except Exception as exc:  # noqa: BLE001
            app.logger.error("retention_sweep failed: %s", exc, exc_info=True)
        finally:
            conn.close()
