"""Fernet symmetric encryption helpers.

Pattern lifted from the WCC reference codebase (Fernet helper module
in mcp-gmail) and collapsed for HOA's simpler needs (single key, no
rotation surface in v1, single key-value config table).

The only encrypted value in v1 is the Gmail refresh token, stored as a
Fernet token string in `config.value_encrypted` for `key='gmail_refresh_token'`.
The plan also reserves room to encrypt `gmail_oauth_client_secret` if the
operator chooses to migrate it from env to DB.

ENCRYPTION_KEY format: 32 bytes URL-safe base64-encoded (44 ASCII chars).
Generate with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

A malformed or missing key raises CryptoError at first encrypt/decrypt.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    """Raised when encryption or decryption fails."""


def _get_fernet() -> Fernet:
    """Build a Fernet instance from the ENCRYPTION_KEY env var.

    Raises CryptoError on missing or malformed key. Called at every
    encrypt/decrypt site; the cost is negligible compared to AES.
    """
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise CryptoError("ENCRYPTION_KEY env var is not set")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise CryptoError(f"ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt plaintext, return Fernet token as ASCII string.

    Storing as TEXT keeps the config table schema uniform; Fernet tokens
    are URL-safe base64 so the round-trip is loss-less.
    """
    fernet = _get_fernet()
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet token string, return plaintext.

    Raises CryptoError on tampered HMAC, wrong key, or malformed token.
    """
    fernet = _get_fernet()
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("decrypt failed: token invalid or wrong key") from exc
