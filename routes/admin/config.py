"""Admin config: HOA email, community name, password change, gmail test-run.

Binding-scope item 10: this module's `config_change_password` calls the
exact same `services.admin_auth.hash_password` that
`scripts/set_admin_password.py` calls, and writes to the same
`config.admin_password_hash` row, so the bcrypt hash is byte-identical
between bootstrap and runtime change.
"""

from __future__ import annotations

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from db.connection import get_db
from routes.admin import admin_bp
from routes.admin._common import require_admin
from services import admin_auth, config_store
from services.csrf import require_csrf


@admin_bp.route("/config", methods=["GET"])
@require_admin
def config_view():
    """Render the settings panel."""
    conn = get_db()
    return render_template(
        "admin/config.html",
        hoa_contact_email=config_store.get(
            conn, config_store.KEY_HOA_CONTACT_EMAIL, ""
        ),
        community_name=config_store.get(
            conn, config_store.KEY_COMMUNITY_NAME, ""
        ),
        gmail_token_present=config_store.has(
            conn, config_store.KEY_GMAIL_REFRESH_TOKEN
        ),
        gmail_last_at=config_store.get(
            conn, config_store.KEY_GMAIL_LAST_INGEST_AT
        ),
        gmail_last_status=config_store.get(
            conn, config_store.KEY_GMAIL_LAST_INGEST_STATUS
        ),
    )


@admin_bp.route("/config/email", methods=["POST"])
@require_csrf
@require_admin
def config_update_email():
    """Update the HOA contact email + community name."""
    conn = get_db()
    email = (request.form.get("hoa_contact_email") or "").strip()
    name = (request.form.get("community_name") or "").strip()
    if email:
        config_store.set(conn, config_store.KEY_HOA_CONTACT_EMAIL, email)
    if name:
        config_store.set(conn, config_store.KEY_COMMUNITY_NAME, name)
    flash("Configuration updated.", "success")
    return redirect(url_for("admin.config_view"))


@admin_bp.route("/config/password", methods=["POST"])
@require_csrf
@require_admin
def config_change_password():
    """Update the admin password (binding-scope item 10)."""
    conn = get_db()
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    stored = config_store.get(conn, config_store.KEY_ADMIN_PASSWORD_HASH)
    if not stored or not admin_auth.verify_password(current, stored):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("admin.config_view"))
    if len(new) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect(url_for("admin.config_view"))
    if new != confirm:
        flash("New password and confirmation do not match.", "error")
        return redirect(url_for("admin.config_view"))
    new_hash = admin_auth.hash_password(new)
    config_store.set(conn, config_store.KEY_ADMIN_PASSWORD_HASH, new_hash)
    flash("Password updated.", "success")
    return redirect(url_for("admin.config_view"))


@admin_bp.route("/config/gmail/test-run", methods=["POST"])
@require_csrf
@require_admin
def gmail_test_run():
    """Trigger a manual gmail ingest run.

    Phase 6 fills in the implementation; the route is wired here so the
    button on /admin/config can call it as soon as Phase 6 lands.
    """
    from services import gmail_ingest as gi

    conn = get_db()
    try:
        result = gi.run_once(conn, current_app.logger)
        flash(f"Gmail ingest result: {result}", "info")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error(
            "manual gmail ingest failed: %s", exc, exc_info=True
        )
        flash(f"Gmail ingest failed: {exc}", "error")
    return redirect(url_for("admin.config_view"))
