"""Admin address whitelist management (plan-stage decision 1)."""

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
from services.address_normalize import normalize as norm
from services.csrf import require_csrf


@admin_bp.route("/addresses", methods=["GET"])
@require_admin
def addresses_list():
    """View the address whitelist."""
    conn = get_db()
    return render_template(
        "admin/addresses.html", addresses=address_store.list_all(conn)
    )


@admin_bp.route("/addresses/add", methods=["POST"])
@require_csrf
@require_admin
def addresses_add():
    """Manually add an address to the whitelist."""
    conn = get_db()
    raw = (request.form.get("raw_address") or "").strip()
    if not raw:
        flash("Address is required.", "error")
        return redirect(url_for("admin.addresses_list"))
    normalized = norm(raw)
    inserted = address_store.add(
        conn, normalized, raw=raw, source="admin"
    )
    if inserted:
        flash(f'Added "{normalized}".', "success")
    else:
        flash(f'"{normalized}" already in whitelist.', "info")
    return redirect(url_for("admin.addresses_list"))


@admin_bp.route("/addresses/<int:address_id>/remove", methods=["POST"])
@require_csrf
@require_admin
def addresses_remove(address_id):
    """Drop a row from the whitelist."""
    conn = get_db()
    address_store.remove(conn, address_id)
    flash(f"Removed address {address_id}.", "info")
    return redirect(url_for("admin.addresses_list"))
