"""Admin dashboard: counts cards + Gmail status panel."""

from __future__ import annotations

from flask import render_template

from db.connection import get_db
from routes.admin import admin_bp
from routes.admin._common import require_admin
from services import address_store, config_store, documents as docs_svc


@admin_bp.route("/dashboard", methods=["GET"])
@require_admin
def dashboard():
    """Render counts cards and the latest gmail-ingest status."""
    conn = get_db()
    chats_today = conn.execute(
        "SELECT COUNT(*) AS c FROM messages "
        "WHERE created_at >= datetime('now', '-1 day') AND role = 'user';"
    ).fetchone()["c"]
    chats_30d = conn.execute(
        "SELECT COUNT(*) AS c FROM messages "
        "WHERE created_at >= datetime('now', '-30 days') AND role = 'user';"
    ).fetchone()["c"]
    failed_addr_count = conn.execute(
        "SELECT COUNT(*) AS c FROM failed_address_attempts;"
    ).fetchone()["c"]
    return render_template(
        "admin/dashboard.html",
        documents_count=docs_svc.documents_count(conn),
        chunks_count=docs_svc.chunk_count_total(conn),
        chats_today=chats_today,
        chats_30d=chats_30d,
        failed_addr_count=failed_addr_count,
        needs_ocr_count=docs_svc.needs_ocr_count(conn),
        addresses_count=address_store.count(conn),
        gmail_last_at=config_store.get(conn, config_store.KEY_GMAIL_LAST_INGEST_AT),
        gmail_last_status=config_store.get(conn, config_store.KEY_GMAIL_LAST_INGEST_STATUS),
    )
