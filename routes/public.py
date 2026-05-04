"""Public-side routes: address gate + chat shell + chat API.

Phase 2: address gate (`/`, `/gate`, `/gate/failed`) + chat GET (`/chat`).
Phase 3: `/api/chat` POST.

Session model (binding-scope item 9):
- On gate hit, set session["chat_authenticated"] = True, session["normalized_address"] = ...
- session.permanent = True drives the 30-day Flask permanent_session_lifetime cookie.
- The admin path uses a manual idle timestamp (last_admin_seen) and never
  sets/touches session.permanent; the two paths coexist on one session.
"""

from __future__ import annotations

import functools

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from db.connection import get_db
from services import address_normalize, address_store, config_store, mailto
from services.admin_auth import hash_ip
from services.csrf import require_csrf

public_bp = Blueprint("public", __name__)


# === Decorators ===

def require_chat_session(view):
    """Redirect to the address gate if session is not chat-authenticated."""
    @functools.wraps(view)
    def _wrapped(*args, **kwargs):
        if not session.get("chat_authenticated"):
            return redirect(url_for("public.index"))
        return view(*args, **kwargs)
    return _wrapped


# === Helpers ===

def _hoa_contact_email() -> str:
    """Read the configured HOA contact email; fall back to placeholder."""
    conn = get_db()
    return config_store.get(conn, config_store.KEY_HOA_CONTACT_EMAIL, "board@example.com")


def _community_name() -> str:
    """Read the configured community name; fall back to placeholder."""
    conn = get_db()
    return config_store.get(conn, config_store.KEY_COMMUNITY_NAME, "your HOA")


def _failed_address_mailto() -> str:
    """Mailto link rendered on the failed-address page."""
    to = _hoa_contact_email()
    subject = f"{_community_name()}: chatbot address verification"
    body = (
        "Hi,\n\n"
        "I tried to use the HOA chatbot but my address was not recognized. "
        "Could you confirm the address you have on file? Thanks."
    )
    return mailto.build_mailto(to, subject, body)


# === Routes ===

@public_bp.route("/healthz", methods=["GET"])
def healthz():
    """Lightweight readiness check."""
    return jsonify({"status": "ok"}), 200


@public_bp.route("/", methods=["GET"])
def index():
    """Render the address gate, or redirect to chat if already gated."""
    if session.get("chat_authenticated"):
        return redirect(url_for("public.chat_view"))
    return render_template(
        "address_gate.html",
        community_name=_community_name(),
    )


@public_bp.route("/gate", methods=["POST"])
@require_csrf
def gate_submit():
    """Validate the submitted address; redirect to chat or to failed page."""
    raw = (request.form.get("address") or "").strip()
    normalized = address_normalize.normalize(raw)
    conn = get_db()

    if normalized and address_store.is_valid(conn, normalized):
        session["chat_authenticated"] = True
        session["normalized_address"] = normalized
        session.permanent = True  # binding-scope item 9: 30-day chat cookie
        return redirect(url_for("public.chat_view"))

    # Log the miss for admin review.
    ip_hash = hash_ip(request.remote_addr)
    conn.execute(
        """
        INSERT INTO failed_address_attempts (raw_address, normalized_address, ip_hash)
        VALUES (?, ?, ?);
        """,
        (raw, normalized, ip_hash),
    )
    return redirect(url_for("public.gate_failed"))


@public_bp.route("/gate/failed", methods=["GET"])
def gate_failed():
    """Render the polite "address not recognized" page with mailto fallback."""
    return render_template(
        "address_failed.html",
        community_name=_community_name(),
        mailto_link=_failed_address_mailto(),
        contact_email=_hoa_contact_email(),
    )


@public_bp.route("/logout", methods=["POST"])
@require_csrf
def logout():
    """Clear the chat-authenticated flag. Useful for testing; not surfaced in UI yet."""
    session.pop("chat_authenticated", None)
    session.pop("normalized_address", None)
    return redirect(url_for("public.index"))


@public_bp.route("/chat", methods=["GET"])
@require_chat_session
def chat_view():
    """Render the chat UI shell. Phase 3 wires the POST API."""
    return render_template(
        "chat.html",
        community_name=_community_name(),
        contact_email=_hoa_contact_email(),
    )


# === Phase 3: /api/chat handler ===

@public_bp.route("/api/chat", methods=["POST"])
@require_csrf
@require_chat_session
def api_chat():
    """Phase 3 handler. Returns JSON.

    Local imports to avoid pulling Anthropic/Voyage clients at app boot
    when they're not needed (e.g. the gate flow).
    """
    from services import chat as chat_svc
    from services.chat import (
        ChatNoChunks,
        ChatRateLimited,
        ChatServiceUnavailable,
    )

    payload = request.get_json(silent=True) or {}
    user_msg = (payload.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "empty_message"}), 400
    if len(user_msg) > 4000:
        return jsonify({"error": "message_too_long"}), 400

    normalized = session.get("normalized_address") or ""
    history = session.get("chat_history") or []  # [{role, content}, ...]
    conn = get_db()

    try:
        result = chat_svc.handle_chat(
            conn=conn,
            normalized_address=normalized,
            user_msg=user_msg,
            history=history,
            session_id=session.get("_id") or session.get("normalized_address") or "",
        )
    except ChatRateLimited:
        return jsonify({
            "kind": "rate_limited",
            "message": (
                "You have reached today's chat limit. Please email the HOA for "
                "further questions."
            ),
            "mailto": _build_chat_mailto(user_msg, "rate-limit reached"),
        }), 200
    except ChatNoChunks:
        return jsonify({
            "kind": "setup_in_progress",
            "message": (
                "The chatbot is still being set up. Please email the HOA in "
                "the meantime."
            ),
            "mailto": _build_chat_mailto(user_msg, "chatbot still setting up"),
        }), 200
    except ChatServiceUnavailable as exc:
        current_app.logger.error("chat service unavailable: %s", exc, exc_info=True)
        return jsonify({
            "kind": "service_unavailable",
            "message": (
                "The AI service is having trouble right now. Please try "
                "again in a few minutes, or email the HOA."
            ),
            "mailto": _build_chat_mailto(user_msg, "AI service unavailable"),
        }), 200

    # Update the in-cookie hot cache (last 6 message pairs = 12 entries).
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": result["response"]})
    session["chat_history"] = history[-12:]

    return jsonify({
        "kind": "ok",
        "response": result["response"],
        "cited_documents": result.get("cited_documents") or [],
    }), 200


def _build_chat_mailto(user_msg: str, reason: str) -> str:
    """Compose a mailto fallback that prefills with the user's last message."""
    to = _hoa_contact_email()
    subject = f"{_community_name()}: chatbot follow-up ({reason})"
    body = (
        "Hi,\n\n"
        "I was using the HOA chatbot and ran into an issue. My question was:\n\n"
        f"{user_msg}\n\n"
        "Reason: " + reason + ".\n\n"
        "Could you help? Thanks."
    )
    return mailto.build_mailto(to, subject, body)
