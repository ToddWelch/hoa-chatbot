"""Shared helpers for the admin blueprint package.

`require_admin` enforces the session flag + idle timeout (binding-scope
item 9). Every protected admin route uses it.

`ADMIN_IDLE_TIMEOUT_SEC` is 1 hour per the brief. The pattern is a
manual timestamp stored in `session["last_admin_seen"]` so the admin
idle is independent of `session.permanent` (which the chat gate sets).
"""

from __future__ import annotations

import functools
import time

from flask import flash, redirect, session, url_for

ADMIN_IDLE_TIMEOUT_SEC = 3600  # 1 hour per binding-scope item 9


def require_admin(view):
    """Gate admin routes via session flag + idle timeout."""

    @functools.wraps(view)
    def _wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin.login_view"))
        last_seen = session.get("last_admin_seen", 0)
        if time.time() - last_seen > ADMIN_IDLE_TIMEOUT_SEC:
            session.pop("admin_authenticated", None)
            session.pop("last_admin_seen", None)
            flash("Your admin session expired. Please log in again.", "info")
            return redirect(url_for("admin.login_view", reason="idle"))
        # Slide the window forward so active use stays logged in.
        session["last_admin_seen"] = time.time()
        return view(*args, **kwargs)

    return _wrapped
