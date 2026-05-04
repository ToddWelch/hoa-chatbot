"""Flask application factory.

Pattern lifted from
`/home/todd/projects/Welch-Command-Center/backend/app/__init__.py`,
collapsed for HOA's smaller surface area.

Phase 1: factory, blueprint registration, error handlers, logging,
session lifetime config. Phase 3 adds EMBEDDING_DIM validation. Phase 7
wires the scheduler.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from db.connection import close_db, get_db, open_connection
from db.migrations import apply_pending


def create_app() -> Flask:
    """Build and return the Flask app."""
    load_dotenv()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    _configure(app)
    _configure_logging(app)
    _ensure_db_ready(app)
    _register_blueprints(app)
    _register_csrf(app)
    _register_error_handlers(app)
    _validate_embeddings(app)

    app.teardown_appcontext(close_db)

    if not os.environ.get("FLASK_SKIP_SCHEDULER"):
        _start_scheduler(app)

    app.logger.info("HOA chatbot app factory complete")
    return app


def _configure(app: Flask) -> None:
    """Pull config out of env into app.config."""
    secret = os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "FLASK_SECRET_KEY env var is not set. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    app.config["SECRET_KEY"] = secret

    db_path = os.environ.get("DATABASE_PATH", "./hoa_bot.db")
    app.config["DATABASE_PATH"] = db_path

    data_dir = os.environ.get("DATA_DIR", "./data")
    app.config["DATA_DIR"] = data_dir

    # Item 9 of binding scope: 30-day chat session via Flask permanent_session_lifetime;
    # admin uses a manual idle timestamp instead of permanent lifetime.
    app.permanent_session_lifetime = timedelta(days=30)

    # Cookie hardening. Secure=True is the right value in prod (HTTPS); in
    # local dev over plain HTTP the browser drops the cookie unless we
    # relax it. FLASK_ENV=development -> Secure=False so dev workflow works.
    is_dev = os.environ.get("FLASK_ENV", "production").lower() == "development"
    app.config["SESSION_COOKIE_SECURE"] = not is_dev
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Cap upload size at 16MB; pypdf has plenty of room and we don't want
    # someone uploading an arbitrary blob.
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


def _configure_logging(app: Flask) -> None:
    """Route logs to stdout at LOG_LEVEL."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # APScheduler has chatty INFO logs; pin to WARNING in prod.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    app.logger.setLevel(level)


def _ensure_db_ready(app: Flask) -> None:
    """Apply migrations on boot.

    Idempotent: applied_migrations ledger means re-running on an existing
    DB does nothing. This means the app can boot on a fresh Railway
    volume without a manual `railway run python scripts/init_db.py` first
    (though we still document that script in the README for clarity).
    """
    db_path = app.config["DATABASE_PATH"]
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    conn = open_connection(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()


def _register_blueprints(app: Flask) -> None:
    """Register public and admin blueprints."""
    from routes.public import public_bp
    from routes.admin import admin_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)


def _register_csrf(app: Flask) -> None:
    """Hook the CSRF context processor so every template gets a token."""
    from services.csrf import inject_csrf_token

    app.context_processor(inject_csrf_token)


def _register_error_handlers(app: Flask) -> None:
    """Friendly fallback pages and JSON for the API endpoints.

    The brief is explicit: residents must never see a 500. Templates
    render the mailto fallback when the failure is on the chat side;
    admin paths get a generic message.
    """

    def _wants_json() -> bool:
        # /api/* always returns JSON; everything else gets HTML.
        return request.path.startswith("/api/")

    @app.errorhandler(404)
    def _not_found(_err):
        if _wants_json():
            return jsonify({"error": "not_found"}), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def _forbidden(_err):
        if _wants_json():
            return jsonify({"error": "forbidden"}), 403
        return render_template("errors/csrf_403.html"), 403

    @app.errorhandler(429)
    def _too_many(_err):
        if _wants_json():
            return jsonify({"error": "rate_limited"}), 429
        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def _internal(err):
        app.logger.error("500: %s", err, exc_info=True)
        if _wants_json():
            return jsonify({"error": "service_unavailable"}), 500
        return render_template("errors/500.html"), 500

    @app.errorhandler(Exception)
    def _unhandled(err):
        # Anything that escapes the route handler. Log + render friendly.
        app.logger.error("unhandled: %s", err, exc_info=True)
        if _wants_json():
            return jsonify({"error": "service_unavailable"}), 500
        return render_template("errors/500.html"), 500


def _validate_embeddings(app: Flask) -> None:
    """Item 7 of binding scope: validate EMBEDDING_DIM against existing chunks.

    On a fresh DB the chunks table is empty and this is a no-op. After
    documents have been embedded, a model swap that changes the dim
    would corrupt retrieval; this check fails fast at boot instead.
    """
    from services.embeddings import validate_existing_chunks

    db_path = app.config["DATABASE_PATH"]
    conn = open_connection(db_path)
    try:
        validate_existing_chunks(conn)
    finally:
        conn.close()


def _start_scheduler(app: Flask) -> None:
    """Boot APScheduler with gmail-ingest and retention jobs.

    Phase 7 wires this. Skipped in scripts via FLASK_SKIP_SCHEDULER=1.
    """
    from scheduler import start_scheduler

    start_scheduler(app)


# Re-export for `from app import get_db` convenience.
__all__ = ["create_app", "get_db"]
