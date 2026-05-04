"""Admin login + logout.

Login flow:
1. Check IP rate limit. Locked-out IP gets a friendly cooldown message
   (NOT a 429 per binding-scope item 18).
2. Verify password with bcrypt. Both success and failure are logged to
   `admin_login_attempts` with the success flag.
3. On success, set session["admin_authenticated"] and the idle timestamp.
"""

from __future__ import annotations

import time

from flask import (
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from db.connection import get_db
from routes.admin import admin_bp
from services import admin_auth, config_store, rate_limit
from services.csrf import require_csrf


@admin_bp.route("/login", methods=["GET"])
def login_view():
    """Render the login page. Optional ?reason=idle shows the timeout flash."""
    return render_template(
        "admin/login.html",
        reason=request.args.get("reason"),
    )


@admin_bp.route("/login", methods=["POST"])
@require_csrf
def login_submit():
    """Validate credentials, log the attempt, set the admin flag on success."""
    conn = get_db()
    ip_hash = admin_auth.hash_ip(request.remote_addr)

    # Rate-limit first; never run bcrypt for an IP that is already locked out.
    if not rate_limit.check_admin_login(conn, ip_hash):
        rate_limit.record_admin_login(conn, ip_hash, succeeded=False)
        return render_template(
            "admin/login.html",
            error=(
                "Too many recent login attempts from this network. "
                "Please wait 15 minutes and try again."
            ),
        ), 200  # 200 not 429 per binding-scope item 18

    password = request.form.get("password") or ""
    stored = config_store.get(conn, config_store.KEY_ADMIN_PASSWORD_HASH)
    success = bool(stored) and admin_auth.verify_password(password, stored)

    rate_limit.record_admin_login(conn, ip_hash, succeeded=success)

    if not success:
        return render_template(
            "admin/login.html",
            error="Invalid credentials.",
        ), 200

    session["admin_authenticated"] = True
    session["last_admin_seen"] = time.time()
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/logout", methods=["POST"])
@require_csrf
def logout():
    """Clear the admin flag. No require_admin so a stale session can still log out."""
    session.pop("admin_authenticated", None)
    session.pop("last_admin_seen", None)
    flash("Logged out.", "info")
    return redirect(url_for("admin.login_view"))
