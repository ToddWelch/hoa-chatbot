"""Hand-rolled CSRF tokens, session-bound.

Plan rejects Flask-WTF (avoid one extension; small surface area). Tokens
are a single secrets.token_urlsafe(32) per session, stored in
session["csrf_token"] and reused across every POST in that session.

Validation uses hmac.compare_digest to avoid timing leaks; never `==`.

Scope: every admin POST, the public /api/chat POST, and the address-gate
POST. Wiring detail:
- HTML forms render `<input type="hidden" name="csrf_token" value="...">`
  via the inject_csrf_token context processor.
- chat.html renders `<meta name="csrf-token" content="...">` and
  static/chat.js sends the value as the X-CSRF-Token header.
- The require_csrf decorator reads the form field first, the header
  second, and `abort(403)` on mismatch (rendered as csrf_403.html via
  the global error handler).
"""

from __future__ import annotations

import functools
import hmac
import secrets

from flask import abort, request, session


def generate_token() -> str:
    """Return a fresh CSRF token. 32 bytes of entropy URL-safe encoded."""
    return secrets.token_urlsafe(32)


def ensure_session_token() -> str:
    """Return the session's CSRF token, generating it lazily.

    The first template render in a fresh session populates the slot;
    subsequent renders read it. Keeping the same token across the entire
    session lets a multi-step flow reuse the same hidden input value.
    """
    token = session.get("csrf_token")
    if not token:
        token = generate_token()
        session["csrf_token"] = token
    return token


def validate(submitted: str | None) -> bool:
    """Constant-time compare of submitted vs session token.

    `hmac.compare_digest` rejects equal-length strings via constant-time
    compare. We pass empty strings on the missing-side rather than None
    so the comparator does not blow up; the empty-vs-real string compare
    will of course return False.
    """
    expected = session.get("csrf_token") or ""
    return hmac.compare_digest(submitted or "", expected)


def require_csrf(view_func):
    """Decorator: enforce CSRF on POST/PUT/PATCH/DELETE requests.

    Reads the form field first (HTML form posts), header second
    (the `/api/chat` JSON post sends X-CSRF-Token). On failure we
    `abort(403)`, which the error handler renders as csrf_403.html.
    """

    @functools.wraps(view_func)
    def _wrapped(*args, **kwargs):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            submitted = request.form.get("csrf_token") or request.headers.get(
                "X-CSRF-Token"
            )
            if not validate(submitted):
                abort(403)
        return view_func(*args, **kwargs)

    return _wrapped


def inject_csrf_token() -> dict:
    """Jinja context processor: every template gets `csrf_token`.

    Registered by app.py via `app.context_processor(inject_csrf_token)`.
    Templates render the value into either a hidden input (HTML forms)
    or a meta tag (chat.html, read by chat.js).
    """
    return {"csrf_token": ensure_session_token()}
