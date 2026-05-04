"""Admin blueprint package.

The original plan listed `routes/admin.py` as one file with a note that
it would split if it crossed 300 LOC. It crossed 300 LOC, so we apply
the planned split now: one file per area, all sharing one Blueprint
instance and the `require_admin` decorator.

Caller import: `from routes.admin import admin_bp`. Side effect: each
submodule registers its routes against admin_bp on import, so importing
the package wires the whole admin surface.
"""

from __future__ import annotations

from flask import Blueprint

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# Side-effect imports register routes against admin_bp.
from routes.admin import login  # noqa: F401, E402
from routes.admin import dashboard  # noqa: F401, E402
from routes.admin import documents  # noqa: F401, E402
from routes.admin import conversations  # noqa: F401, E402
from routes.admin import failed_addresses  # noqa: F401, E402
from routes.admin import addresses  # noqa: F401, E402
from routes.admin import config  # noqa: F401, E402
