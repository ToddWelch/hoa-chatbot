"""Set or reset the admin password.

Reads from stdin (or --password for non-interactive CI). Writes the
bcrypt hash to config.admin_password_hash via services.admin_auth +
services.config_store. Same code path as /admin/config/password
(binding-scope item 10) so the hash format is byte-identical.

Usage:
    railway run python scripts/set_admin_password.py
    # or non-interactive:
    python scripts/set_admin_password.py --password "literal-pw"
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FLASK_SKIP_SCHEDULER"] = "1"

from dotenv import load_dotenv  # noqa: E402

from db.connection import open_connection  # noqa: E402
from services import admin_auth, config_store  # noqa: E402


def _read_password_interactive() -> str:
    """Prompt twice and confirm match. Refuses empty + < 8 chars."""
    while True:
        p1 = getpass.getpass("New admin password: ")
        if len(p1) < 8:
            print("Too short, please use at least 8 characters.", file=sys.stderr)
            continue
        p2 = getpass.getpass("Confirm: ")
        if p1 != p2:
            print("Mismatch, try again.", file=sys.stderr)
            continue
        return p1


def main() -> int:
    parser = argparse.ArgumentParser(description="Set HOA admin password")
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Non-interactive: pass the password as an argument. Use only in CI.",
    )
    args = parser.parse_args()

    load_dotenv()
    db_path = os.environ.get("DATABASE_PATH", "./hoa_bot.db")

    if args.password:
        plaintext = args.password
        if len(plaintext) < 8:
            print("Refused: password under 8 characters.", file=sys.stderr)
            return 1
    else:
        plaintext = _read_password_interactive()

    hashed = admin_auth.hash_password(plaintext)

    conn = open_connection(db_path)
    try:
        config_store.set(conn, config_store.KEY_ADMIN_PASSWORD_HASH, hashed)
    finally:
        conn.close()

    print("Admin password updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
