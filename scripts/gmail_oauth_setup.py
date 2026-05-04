"""Bootstrap the Gmail OAuth refresh token.

Per binding-scope item 8: this script is invoked via
`railway run python scripts/gmail_oauth_setup.py` against the prod DB.

Flow:
1. Read GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET from env.
2. Build a google-auth-oauthlib InstalledAppFlow with `gmail.modify` scope.
3. Print the authorization URL; the operator opens it in a browser,
   grants the scope, and pastes back the redirect code.
4. Exchange the code for tokens (access + refresh).
5. Encrypt the refresh token via Fernet (services.crypto.encrypt) and
   write to config.value_encrypted for key='gmail_refresh_token'
   (binding-scope item 4).

The script does NOT spin up a local web server. It uses the manual
copy-paste OOB flow, which works inside `railway run` over SSH. Newer
Google docs deprecate OOB but desktop-app-type clients still accept it
for installed-app use.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FLASK_SKIP_SCHEDULER"] = "1"

from dotenv import load_dotenv  # noqa: E402

from db.connection import open_connection  # noqa: E402
from services import config_store  # noqa: E402
from services.crypto import CryptoError, _get_fernet  # noqa: E402


def _redirect_uri() -> str:
    """Return the manual-copy-paste OOB redirect URI Google's installed-app flow accepts."""
    # Google's "out of band" redirect for installed apps; surfaces the
    # auth code on a page so the operator can paste it back.
    return "urn:ietf:wg:oauth:2.0:oob"


def main() -> int:
    load_dotenv()

    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set "
            "in the environment before running this script.\n"
            "Set them in .env (local) or on Railway, then re-run.",
            file=sys.stderr,
        )
        return 1

    # Validate ENCRYPTION_KEY now so we fail before the OAuth roundtrip
    # rather than after.
    try:
        _get_fernet()
    except CryptoError as exc:
        print(
            f"ENCRYPTION_KEY invalid: {exc}\n"
            "Set ENCRYPTION_KEY before running this script. Generate with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"",
            file=sys.stderr,
        )
        return 1

    # Local imports because google_auth_oauthlib is not free; avoid pulling
    # the Google SDK at module import time when the script never runs.
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_redirect_uri()],
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
        redirect_uri=_redirect_uri(),
    )

    auth_url, _state = flow.authorization_url(
        access_type="offline",
        prompt="consent",  # force a refresh_token even if the user has consented before
        include_granted_scopes="true",
    )
    print()
    print("=" * 60)
    print("Open the following URL in a browser and grant the gmail.modify scope:")
    print()
    print(auth_url)
    print()
    print(
        "If Google shows 'this app isn't verified', click Advanced -> "
        "Continue. The HOA's Gmail account is single-user; Google's "
        "verification process is not required for v1."
    )
    print("=" * 60)
    print()

    code = input("Paste the authorization code shown by Google: ").strip()
    if not code:
        print("No code entered. Aborting.", file=sys.stderr)
        return 1

    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to exchange code for tokens: {exc}", file=sys.stderr)
        return 1

    creds = flow.credentials
    refresh_token = getattr(creds, "refresh_token", None)
    if not refresh_token:
        print(
            "No refresh_token returned. This usually means the user has "
            "previously granted consent and Google is reusing the existing "
            "grant. Revoke the app at "
            "https://myaccount.google.com/permissions and re-run this script.",
            file=sys.stderr,
        )
        return 1

    db_path = os.environ.get("DATABASE_PATH", "./hoa_bot.db")
    conn = open_connection(db_path)
    try:
        # Binding-scope item 4: store at config.value_encrypted for
        # key='gmail_refresh_token' via config_store.set_secret.
        config_store.set_secret(conn, config_store.KEY_GMAIL_REFRESH_TOKEN, refresh_token)
    finally:
        conn.close()

    print()
    print("Gmail refresh token saved to config (encrypted).")
    print("You can now click 'Run ingest now' on /admin/config to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
