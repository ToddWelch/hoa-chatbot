"""Admin-side routes. Phase 1 ships the blueprint stub.

Phase 4 wires login + dashboard + documents.
Phase 5 wires conversations + failed addresses + addresses + config.
Phase 6 wires the gmail test-run button.
"""

from __future__ import annotations

from flask import Blueprint

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
