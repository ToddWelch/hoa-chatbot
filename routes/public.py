"""Public-side routes: address gate + chat UI + chat API.

Phase 1 ships the blueprint stub. Phase 2 wires the address gate and
chat shell. Phase 3 wires `/api/chat`.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

public_bp = Blueprint("public", __name__)


@public_bp.route("/healthz", methods=["GET"])
def healthz():
    """Lightweight readiness check. Used by Railway and smoke tests."""
    return jsonify({"status": "ok"}), 200
