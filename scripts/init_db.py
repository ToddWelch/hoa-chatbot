"""Initialize the SQLite database: apply migrations + seed config keys.

Idempotent. Safe to run on a fresh DB and on a populated one. Called by
scripts/start.py at container boot, and by Todd manually via
`railway run python scripts/init_db.py` at first deploy.

Optional: --seed-addresses <path> imports a text file (one normalized
address per line) into the `addresses` table. Used at first deploy only;
afterwards admin uses /admin/failed-addresses or /admin/addresses.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running from project root without `pip install -e .`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mark scheduler skip BEFORE importing the app modules. Belt-and-braces;
# init_db never imports app.py directly but a future refactor might.
os.environ["FLASK_SKIP_SCHEDULER"] = "1"

from dotenv import load_dotenv  # noqa: E402

from db.connection import open_connection  # noqa: E402
from db.migrations import apply_pending  # noqa: E402
from services import config_store  # noqa: E402


def _ensure_encryption_key_warning() -> None:
    """If ENCRYPTION_KEY is unset or malformed, surface a clear error before
    we let the operator continue (the error will hit at first OAuth otherwise).

    Pattern from WCC backend/app/commands.py:655-664.
    """
    from services.crypto import CryptoError, _get_fernet

    try:
        _get_fernet()
    except CryptoError as exc:
        print(
            "WARNING: ENCRYPTION_KEY check failed: "
            f"{exc}\n"
            "Set ENCRYPTION_KEY in .env (or Railway env vars) before running\n"
            "scripts/gmail_oauth_setup.py. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
            file=sys.stderr,
        )
        # Not a hard failure: init_db's primary job is migrations + config
        # placeholders, which work without ENCRYPTION_KEY. The Gmail OAuth
        # bootstrap script will fail loudly if the key is bad.


def _seed_config_placeholders(conn) -> None:
    """Seed default values for non-secret config keys if missing."""
    defaults = {
        config_store.KEY_HOA_CONTACT_EMAIL: os.environ.get(
            "HOA_CONTACT_EMAIL", "board@example.com"
        ),
        config_store.KEY_COMMUNITY_NAME: os.environ.get(
            "COMMUNITY_NAME", "Example HOA"
        ),
    }
    for key, default_val in defaults.items():
        if not config_store.has(conn, key):
            config_store.set(conn, key, default_val)
            print(f"seeded config: {key} = {default_val}")


def _seed_addresses(conn, path: str) -> int:
    """Read addresses from a text file, one per line, into the addresses table.

    Lines are trimmed, blank lines and lines starting with '#' are skipped.
    The raw text and the normalized form are both stored. UNIQUE constraint
    on normalized_address means re-running with the same file is a no-op.
    """
    # Local import: services.address_normalize ships in Phase 2; this script
    # works on the Phase 1 schema but the seed feature only matters once
    # the address gate exists.
    from services.address_normalize import normalize

    if not os.path.exists(path):
        print(f"seed file not found: {path}", file=sys.stderr)
        return 0
    inserted = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            norm = normalize(raw)
            try:
                conn.execute(
                    """
                    INSERT INTO addresses (normalized_address, raw_address, source)
                    VALUES (?, ?, 'seed');
                    """,
                    (norm, raw),
                )
                inserted += 1
            except Exception as exc:  # likely UNIQUE conflict, ignore
                if "UNIQUE" in str(exc):
                    continue
                raise
    print(f"seeded {inserted} new address(es) from {path}")
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize HOA chatbot DB")
    parser.add_argument(
        "--seed-addresses",
        type=str,
        default=None,
        help="Path to a text file with one address per line; seeds the addresses table.",
    )
    args = parser.parse_args()

    load_dotenv()
    db_path = os.environ.get("DATABASE_PATH", "./hoa_bot.db")

    parent = os.path.dirname(os.path.abspath(db_path))
    if not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
        print(f"created data dir: {parent}")

    print(f"opening DB: {db_path}")
    conn = open_connection(db_path)
    try:
        applied = apply_pending(conn)
        if applied:
            print(f"applied {len(applied)} migration(s)")

        _seed_config_placeholders(conn)

        if args.seed_addresses:
            _seed_addresses(conn, args.seed_addresses)

        _ensure_encryption_key_warning()

        # Reminder for the operator on a fresh deploy.
        if not config_store.has(conn, config_store.KEY_ADMIN_PASSWORD_HASH):
            print(
                "\nNo admin password set yet. Run:\n"
                "  python scripts/set_admin_password.py\n"
                "before opening /admin/login.",
            )
    finally:
        conn.close()

    print("init_db complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
