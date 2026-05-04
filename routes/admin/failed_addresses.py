"""Admin failed-address attempts: list + one-click whitelist promotion."""

from __future__ import annotations

from flask import (
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from db.connection import get_db
from routes.admin import admin_bp
from routes.admin._common import require_admin
from services import address_store
from services.csrf import require_csrf


@admin_bp.route("/failed-addresses", methods=["GET"])
@require_admin
def failed_addresses_list():
    """List failed gate attempts with one-click whitelist promotion."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, raw_address, normalized_address, created_at
          FROM failed_address_attempts
         ORDER BY id DESC
         LIMIT 200;
        """
    ).fetchall()
    return render_template("admin/failed_addresses.html", attempts=rows)


@admin_bp.route("/failed-addresses/whitelist", methods=["POST"])
@require_csrf
@require_admin
def failed_address_to_whitelist():
    """Promote a failed-attempt address into the whitelist."""
    conn = get_db()
    raw = (request.form.get("raw_address") or "").strip()
    normalized = (request.form.get("normalized_address") or "").strip()
    if not normalized:
        flash("Cannot whitelist an empty normalized address.", "error")
        return redirect(url_for("admin.failed_addresses_list"))
    inserted = address_store.add(
        conn, normalized, raw=raw, source="failed_promotion"
    )
    if inserted:
        flash(f'Whitelisted "{normalized}".', "success")
    else:
        flash(f'"{normalized}" was already in the whitelist.', "info")
    return redirect(url_for("admin.failed_addresses_list"))
